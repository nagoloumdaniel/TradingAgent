//+------------------------------------------------------------------+
//|                                        TradingAgentGuardian.mqh  |
//|                                    Copyright 2026, TradingAgent  |
//|                    https://github.com/  /  voir docs/ea/README.md |
//+------------------------------------------------------------------+
//  Socle commun des Expert Advisors "Guardian" (cahier v3 §18, §53 phase 7).
//
//  UN GUARDIAN N'EST PAS UN CERVEAU. Il n'ouvre jamais une position de sa propre
//  initiative et ne prend aucune decision de marche. Il fait exactement quatre choses :
//
//    1. EXECUTION  — il envoie les ordres que le backend a explicitement autorises dans
//                    le fichier d'etat, et rien d'autre. Un ordre est identifie par son
//                    commentaire ; un ordre deja applique n'est jamais renvoye.
//    2. VERIFICATION — volume, stop, objectif, exposition, nombre de positions : tout est
//                    controle avant l'envoi, puis relu sur la position apres execution.
//    3. SURVEILLANCE / PROTECTION — l'etat attendu est compare a l'etat reel ; toute
//                    divergence bloque les nouveaux ordres. Un stop absent est ferme
//                    immediatement. Un kill switch local, persistant, peut etre declenche.
//    4. REMONTEE   — chaque ordre, execution, prix, volume, SL, TP, resultat, slippage,
//                    erreur et changement de connexion est journalise dans un fichier de
//                    remontee et dans le rapport de battement de coeur.
//
//  Le pont de fichiers est decrit dans docs/ea/protocole-pont.md ; le cote Python est
//  src/tradingagent/ea/bridge.py. Tous les fichiers sont en ASCII pur (le JSON echappe le
//  reste), ce qui permet de les lire en mode texte ANSI de MQL5.
//+------------------------------------------------------------------+
#property copyright "TradingAgent"
#property link      "https://github.com/"
#property version   "1.00"

#include <Trade\Trade.mqh>

//--- protocole -------------------------------------------------------
#define TA_PROTOCOL_VERSION   1
#define TA_EA_VERSION         "1.0.0"

//--- arborescence, relative a MQL5\Files -----------------------------
#define TA_DIR_ROOT           "TradingAgent"
#define TA_DIR_STATE          "TradingAgent\\state"
#define TA_DIR_REPORTS        "TradingAgent\\reports"
#define TA_DIR_CONTROL        "TradingAgent\\control"

//--- cadences --------------------------------------------------------
#define TA_TIMER_SECONDS      1     // une passe par seconde
#define TA_REPORT_SECONDS     2     // un battement de coeur toutes les deux secondes
#define TA_STATE_STALE_SECONDS 30   // backend silencieux au-dela : arret local
#define TA_EVENTS_IN_MEMORY   25    // evenements rappeles dans le rapport
#define TA_COMMENT_LIMIT      31    // limite imposee par le serveur MT5

//--- types JSON ------------------------------------------------------
#define TA_JSON_NULL          0
#define TA_JSON_BOOL          1
#define TA_JSON_NUMBER        2
#define TA_JSON_STRING        3
#define TA_JSON_ARRAY         4
#define TA_JSON_OBJECT        5

//--- severites -------------------------------------------------------
#define TA_INFO               "INFO"
#define TA_WARNING            "WARNING"
#define TA_CRITICAL           "CRITICAL"

//--- natures d'evenement ---------------------------------------------
#define TA_EV_INIT            "INIT"
#define TA_EV_CONNECTION      "CONNECTION"
#define TA_EV_ORDERS          "ORDERS"
#define TA_EV_EXECUTION       "EXECUTION"
#define TA_EV_REFUSED         "ORDER_REFUSED"
#define TA_EV_EXECUTION_FAILED "EXECUTION_FAILED"
#define TA_EV_DIVERGENCE      "DIVERGENCE"
#define TA_EV_PROTECTION      "PROTECTION"
#define TA_EV_KILL_SWITCH     "KILL_SWITCH"
#define TA_EV_STATE           "STATE"
#define TA_EV_ERROR           "ERROR"


//+------------------------------------------------------------------+
//| Un noeud JSON minimal. Le terminal ne fournit pas de bibliotheque |
//| JSON : elle est ecrite ici, volontairement petite, pour que le    |
//| format du pont soit verifiable d'un seul coup d'oeil.             |
//+------------------------------------------------------------------+
class CTaJson
{
public:
   int      m_type;
   bool     m_bool;
   double   m_number;
   string   m_string;
   CTaJson *m_items[];      // tableau
   string   m_keys[];       // objet : cles
   CTaJson *m_values[];     // objet : valeurs

   CTaJson()
   {
      m_type   = TA_JSON_NULL;
      m_bool   = false;
      m_number = 0.0;
      m_string = "";
   }

   ~CTaJson()
   {
      for(int i = 0; i < ArraySize(m_items); i++)
         if(CheckPointer(m_items[i]) == POINTER_DYNAMIC)
            delete m_items[i];
      for(int i = 0; i < ArraySize(m_values); i++)
         if(CheckPointer(m_values[i]) == POINTER_DYNAMIC)
            delete m_values[i];
   }

   bool IsObject() const { return m_type == TA_JSON_OBJECT; }
   bool IsArray()  const { return m_type == TA_JSON_ARRAY;  }

   int ObjectSize() const { return ArraySize(m_keys); }
   int Count() const { return ArraySize(m_items); }

   //--- acces objet
   CTaJson *Get(const string key) const
   {
      for(int i = 0; i < ArraySize(m_keys); i++)
         if(m_keys[i] == key)
            return m_values[i];
      return NULL;
   }

   //--- acces tableau
   CTaJson *At(const int index) const
   {
      if(index < 0 || index >= ArraySize(m_items))
         return NULL;
      return m_items[index];
   }

   string AsString(const string fallback = "") const
   {
      if(m_type == TA_JSON_STRING)
         return m_string;
      return fallback;
   }

   double AsDouble(const double fallback = 0.0) const
   {
      if(m_type == TA_JSON_NUMBER)
         return m_number;
      if(m_type == TA_JSON_BOOL)
         return m_bool ? 1.0 : 0.0;
      return fallback;
   }

   long AsLong(const long fallback = 0) const
   {
      if(m_type == TA_JSON_NUMBER)
         return (long)MathRound(m_number);
      return fallback;
   }

   bool AsBool(const bool fallback = false) const
   {
      if(m_type == TA_JSON_BOOL)
         return m_bool;
      if(m_type == TA_JSON_NUMBER)
         return m_number != 0.0;
      return fallback;
   }
};


//+------------------------------------------------------------------+
//| Parseur JSON recursif descendant.                                 |
//+------------------------------------------------------------------+
class CTaJsonParser
{
private:
   string m_text;
   int    m_pos;
   int    m_len;
   bool   m_failed;

   void SkipSpace()
   {
      while(m_pos < m_len)
      {
         ushort c = StringGetCharacter(m_text, m_pos);
         if(c == ' ' || c == '\t' || c == '\r' || c == '\n')
            m_pos++;
         else
            break;
      }
   }

   bool Match(const string literal)
   {
      int n = StringLen(literal);
      if(m_pos + n > m_len)
         return false;
      if(StringSubstr(m_text, m_pos, n) != literal)
         return false;
      m_pos += n;
      return true;
   }

   string ParseStringBody()
   {
      string out = "";
      while(m_pos < m_len)
      {
         ushort c = StringGetCharacter(m_text, m_pos);
         m_pos++;
         if(c == '"')
            return out;
         if(c != '\\')
         {
            out += ShortToString(c);
            continue;
         }
         if(m_pos >= m_len)
            break;
         ushort e = StringGetCharacter(m_text, m_pos);
         m_pos++;
         if(e == '"')       out += "\"";
         else if(e == '\\') out += "\\";
         else if(e == '/')  out += "/";
         else if(e == 'b')  out += ShortToString(8);
         else if(e == 'f')  out += ShortToString(12);
         else if(e == 'n')  out += "\n";
         else if(e == 'r')  out += "\r";
         else if(e == 't')  out += "\t";
         else if(e == 'u')
         {
            if(m_pos + 4 > m_len)
            {
               m_failed = true;
               break;
            }
            ushort code = 0;
            bool   ok   = true;
            for(int i = 0; i < 4; i++)
            {
               ushort h = StringGetCharacter(m_text, m_pos + i);
               code = (ushort)(code * 16);
               if(h >= '0' && h <= '9')      code += (ushort)(h - '0');
               else if(h >= 'a' && h <= 'f') code += (ushort)(h - 'a' + 10);
               else if(h >= 'A' && h <= 'F') code += (ushort)(h - 'A' + 10);
               else { ok = false; break; }
            }
            if(!ok)
            {
               m_failed = true;
               break;
            }
            m_pos += 4;
            out += ShortToString(code);
         }
      }
      m_failed = true;
      return out;
   }

   CTaJson *NewNode(const int type)
   {
      CTaJson *node = new CTaJson();
      node.m_type = type;
      return node;
   }

   CTaJson *ParseObject()
   {
      CTaJson *node = NewNode(TA_JSON_OBJECT);
      m_pos++;                       // '{'
      SkipSpace();
      if(m_pos < m_len && StringGetCharacter(m_text, m_pos) == '}')
      {
         m_pos++;
         return node;
      }
      while(true)
      {
         SkipSpace();
         if(m_pos >= m_len || StringGetCharacter(m_text, m_pos) != '"')
         {
            m_failed = true;
            break;
         }
         m_pos++;
         string key = ParseStringBody();
         if(m_failed)
            break;
         SkipSpace();
         if(m_pos >= m_len || StringGetCharacter(m_text, m_pos) != ':')
         {
            m_failed = true;
            break;
         }
         m_pos++;
         CTaJson *value = ParseValue();
         if(value == NULL)
         {
            m_failed = true;
            break;
         }
         int n = ArraySize(node.m_keys);
         ArrayResize(node.m_keys, n + 1);
         ArrayResize(node.m_values, n + 1);
         node.m_keys[n]   = key;
         node.m_values[n] = value;
         SkipSpace();
         if(m_pos >= m_len)
         {
            m_failed = true;
            break;
         }
         ushort c = StringGetCharacter(m_text, m_pos);
         if(c == ',')
         {
            m_pos++;
            continue;
         }
         if(c == '}')
         {
            m_pos++;
            break;
         }
         m_failed = true;
         break;
      }
      if(m_failed)
      {
         delete node;
         return NULL;
      }
      return node;
   }

   CTaJson *ParseArray()
   {
      CTaJson *node = NewNode(TA_JSON_ARRAY);
      m_pos++;                       // '['
      SkipSpace();
      if(m_pos < m_len && StringGetCharacter(m_text, m_pos) == ']')
      {
         m_pos++;
         return node;
      }
      while(true)
      {
         CTaJson *value = ParseValue();
         if(value == NULL)
         {
            m_failed = true;
            break;
         }
         int n = ArraySize(node.m_items);
         ArrayResize(node.m_items, n + 1);
         node.m_items[n] = value;
         SkipSpace();
         if(m_pos >= m_len)
         {
            m_failed = true;
            break;
         }
         ushort c = StringGetCharacter(m_text, m_pos);
         if(c == ',')
         {
            m_pos++;
            continue;
         }
         if(c == ']')
         {
            m_pos++;
            break;
         }
         m_failed = true;
         break;
      }
      if(m_failed)
      {
         delete node;
         return NULL;
      }
      return node;
   }

   CTaJson *ParseNumber()
   {
      int start = m_pos;
      while(m_pos < m_len)
      {
         ushort c = StringGetCharacter(m_text, m_pos);
         if((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E')
            m_pos++;
         else
            break;
      }
      string token = StringSubstr(m_text, start, m_pos - start);
      if(StringLen(token) == 0)
      {
         m_failed = true;
         return NULL;
      }
      CTaJson *node = NewNode(TA_JSON_NUMBER);
      node.m_number = StringToDouble(token);
      return node;
   }

   CTaJson *ParseValue()
   {
      SkipSpace();
      if(m_pos >= m_len)
      {
         m_failed = true;
         return NULL;
      }
      ushort c = StringGetCharacter(m_text, m_pos);
      if(c == '{')
         return ParseObject();
      if(c == '[')
         return ParseArray();
      if(c == '"')
      {
         m_pos++;
         CTaJson *node = NewNode(TA_JSON_STRING);
         node.m_string = ParseStringBody();
         if(m_failed)
         {
            delete node;
            return NULL;
         }
         return node;
      }
      if(c == 't')
      {
         if(!Match("true"))
         {
            m_failed = true;
            return NULL;
         }
         CTaJson *node = NewNode(TA_JSON_BOOL);
         node.m_bool = true;
         return node;
      }
      if(c == 'f')
      {
         if(!Match("false"))
         {
            m_failed = true;
            return NULL;
         }
         CTaJson *node = NewNode(TA_JSON_BOOL);
         node.m_bool = false;
         return node;
      }
      if(c == 'n')
      {
         if(!Match("null"))
         {
            m_failed = true;
            return NULL;
         }
         return NewNode(TA_JSON_NULL);
      }
      return ParseNumber();
   }

public:
   //--- NULL des que le document est illisible : jamais d'exception, jamais de plantage.
   CTaJson *Parse(const string text)
   {
      m_text   = text;
      m_pos    = 0;
      m_len    = StringLen(text);
      m_failed = false;
      CTaJson *root = ParseValue();
      if(m_failed)
      {
         if(CheckPointer(root) == POINTER_DYNAMIC)
            delete root;
         return NULL;
      }
      SkipSpace();
      if(m_pos < m_len)
      {
         if(CheckPointer(root) == POINTER_DYNAMIC)
            delete root;
         return NULL;
      }
      return root;
   }
};


//+------------------------------------------------------------------+
//| Une position que le backend croit ouverte.                        |
//+------------------------------------------------------------------+
struct TaExpectedPosition
{
   long   ticket;
   string direction;
   double volume;
   double stopLoss;
   double takeProfit;
   string comment;
   bool   hasStop;
   bool   hasTarget;
};

//+------------------------------------------------------------------+
//| Un ordre autorise par le backend.                                 |
//+------------------------------------------------------------------+
struct TaOrder
{
   string id;
   string action;          // OPEN | CLOSE
   string direction;       // BUY | SELL (sens de la position visee)
   double volume;
   double stopLoss;
   double takeProfit;
   string comment;
   long   positionTicket;  // pour CLOSE
   bool   hasStop;
   bool   hasTarget;
};

//+------------------------------------------------------------------+
//| Une ligne du journal de remontee.                                 |
//+------------------------------------------------------------------+
struct TaEvent
{
   int      seq;
   datetime at;
   string   kind;
   string   severity;
   string   message;
   long     ticket;
   string   data;          // objet JSON deja serialise
};


//+------------------------------------------------------------------+
//| Helpers de formatage.                                             |
//+------------------------------------------------------------------+
string TaIsoUtc(const datetime moment)
{
   MqlDateTime parts;
   TimeToStruct(moment, parts);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       parts.year, parts.mon, parts.day, parts.hour, parts.min, parts.sec);
}

string TaJsonString(const string value)
{
   string out = "\"";
   int    len = StringLen(value);
   for(int i = 0; i < len; i++)
   {
      ushort c = StringGetCharacter(value, i);
      if(c == '"')            out += "\\\"";
      else if(c == '\\')      out += "\\\\";
      else if(c == '\n')      out += "\\n";
      else if(c == '\r')      out += "\\r";
      else if(c == '\t')      out += "\\t";
      else if(c < 32 || c > 126) out += StringFormat("\\u%04x", (int)c);
      else                    out += ShortToString(c);
   }
   out += "\"";
   return out;
}

string TaJsonNumber(const double value)
{
   if(!MathIsValidNumber(value))
      return "0";
   return DoubleToString(value, 8);
}

string TaJsonBool(const bool value)
{
   return value ? "true" : "false";
}


//+------------------------------------------------------------------+
//| L'Expert Advisor Guardian.                                        |
//+------------------------------------------------------------------+
class CTradingAgentGuardian
{
private:
   //--- identite
   string   m_symbol;
   long     m_magic;
   string   m_stateFile;
   string   m_reportFile;
   string   m_eventsFile;
   string   m_appliedFile;
   string   m_haltFile;

   //--- execution
   CTrade   m_trade;
   long     m_deviation;

   //--- cadence
   int      m_reportSeconds;
   int      m_staleSeconds;
   datetime m_lastCycle;
   datetime m_lastReport;

   //--- dernier etat publie
   bool     m_haveState;
   bool     m_stateValid;
   long     m_revision;
   datetime m_publishedAt;
   bool     m_killSwitch;
   int      m_maxPositions;
   double   m_maxTotalVolume;
   TaExpectedPosition m_expected[];
   TaOrder            m_orders[];

   //--- protection locale
   bool     m_halt;
   string   m_haltReason;

   //--- memoire de travail
   string   m_applied[];
   string   m_reported[];
   TaEvent  m_events[];
   int      m_eventSeq;
   string   m_lastDivergence;

   //--- compteurs
   int      m_ordersSent;
   int      m_ordersRefused;
   int      m_divergenceCount;
   int      m_stopsProtected;
   int      m_errors;

   //--- connexion
   bool     m_lastConnected;
   bool     m_connectedKnown;
   bool     m_killReported;

   //--- etat courant pour le rapport
   bool     m_connected;
   bool     m_tradeAllowed;

public:
                     CTradingAgentGuardian();
                    ~CTradingAgentGuardian();

   bool              Init(const string symbol, const long magic,
                          const long deviation = 50,
                          const int  reportSeconds = TA_REPORT_SECONDS,
                          const int  staleSeconds = TA_STATE_STALE_SECONDS);
   void              Deinit(const int reason);
   void              OnTimer();
   void              OnTick();

private:
   //--- boucle
   void              Cycle();
   //--- fichiers
   bool              EnsureFolders();
   string            ReadText(const string path);
   bool              WriteTextAtomic(const string path, const string text);
   bool              AppendLine(const string path, const string line);
   string            StateFilePath() const;
   string            ReportFilePath() const;
   string            EventsFilePath() const;
   string            AppliedFilePath() const;
   string            HaltFilePath() const;
   //--- etat
   bool              ReadState();
   void              CheckStateFreshness(const datetime now);
   bool              IsBackendSilenceHalt() const;
   void              Resume();
   void              LoadApplied();
   void              LoadHalt();
   void              MarkApplied(const string orderId);
   bool              IsApplied(const string orderId);
   void              MarkReported(const string orderId);
   bool              WasReported(const string orderId);
   //--- marche et compte
   bool              RefreshEnvironment();
   void              OnConnectionChange(const bool connected);
   bool              SymbolTradable();
   double            Ask() const;
   double            Bid() const;
   double            Point() const;
   double            PriceTolerance() const;
   double            VolumeTolerance() const;
   //--- positions
   int               OwnPositionsCount() const;
   double            OwnPositionsVolume() const;
   bool              FindPositionByComment(const string comment, ulong &ticket) const;
   bool              PositionSnapshot(const ulong ticket, string &direction, double &volume,
                                      double &priceOpen, double &stop, double &target,
                                      double &profit, string &comment) const;
   bool              StopPresent(const string comment) const;
   bool              TargetPresent(const string comment, double &target) const;
   //--- execution
   void              ExecuteOrders();
   void              DoOpen(const TaOrder &order);
   void              DoClose(const TaOrder &order);
   bool              AllowedToOpen(const TaOrder &order, string &reason);
   bool              VolumeWithinSpec(const double volume, string &reason);
   //--- surveillance et protection
   void              Watchdog();
   void              ProtectOpenPositions();
   void              Halt(const string reason, const string kind);
   //--- remontee
   void              LogEvent(const string kind, const string severity, const string message,
                              const long ticket = 0, const string data = "{}");
   void              WriteReport(const bool force);

public:
   bool              IsHalted() const { return m_halt; }
   string            HaltReason() const { return m_haltReason; }
};


//+------------------------------------------------------------------+
CTradingAgentGuardian::CTradingAgentGuardian()
{
   m_symbol         = "";
   m_magic          = 0;
   m_deviation      = 50;
   m_reportSeconds  = TA_REPORT_SECONDS;
   m_staleSeconds   = TA_STATE_STALE_SECONDS;
   m_lastCycle      = 0;
   m_lastReport     = 0;
   m_haveState      = false;
   m_stateValid     = false;
   m_revision       = -1;
   m_publishedAt    = 0;
   m_killSwitch     = false;
   m_maxPositions   = 1;
   m_maxTotalVolume = 0.0;
   m_halt           = false;
   m_haltReason     = "";
   m_eventSeq       = 0;
   m_lastDivergence = "";
   m_ordersSent     = 0;
   m_ordersRefused  = 0;
   m_divergenceCount = 0;
   m_stopsProtected = 0;
   m_errors         = 0;
   m_lastConnected  = false;
   m_connectedKnown = false;
   m_killReported   = false;
   m_connected      = false;
   m_tradeAllowed   = false;
}


//+------------------------------------------------------------------+
CTradingAgentGuardian::~CTradingAgentGuardian()
{
}


//+------------------------------------------------------------------+
//| Initialisation. Aucun ordre n'est jamais envoye ici.              |
//+------------------------------------------------------------------+
bool CTradingAgentGuardian::Init(const string symbol, const long magic,
                                 const long deviation,
                                 const int  reportSeconds,
                                 const int  staleSeconds)
{
   m_symbol        = symbol;
   m_magic         = magic;
   m_deviation     = deviation;
   m_reportSeconds = MathMax(1, reportSeconds);
   m_staleSeconds  = MathMax(TA_TIMER_SECONDS * 3, staleSeconds);
   m_lastCycle     = 0;

   m_stateFile   = StateFilePath();
   m_reportFile  = ReportFilePath();
   m_eventsFile  = EventsFilePath();
   m_appliedFile = AppliedFilePath();
   m_haltFile    = HaltFilePath();

   if(!EnsureFolders())
   {
      Print("TradingAgent Guardian: impossible de creer l'arborescence MQL5\\Files\\",
            TA_DIR_ROOT);
      return false;
   }

   m_trade.SetExpertMagicNumber(m_magic);
   m_trade.SetDeviationInPoints(m_deviation);
   m_trade.SetTypeFillingBySymbol(m_symbol);
   m_trade.SetAsyncMode(false);

   LoadApplied();
   LoadHalt();

   string details = "\"symbol\":" + TaJsonString(m_symbol) +
                    ",\"magic\":" + IntegerToString(m_magic) +
                    ",\"ea_version\":" + TaJsonString(TA_EA_VERSION) +
                    ",\"protocol_version\":" + IntegerToString(TA_PROTOCOL_VERSION) +
                    ",\"terminal\":" + TaJsonString(TerminalInfoString(TERMINAL_NAME)) +
                    ",\"build\":" + IntegerToString((int)TerminalInfoInteger(TERMINAL_BUILD)) +
                    ",\"account\":" + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN)) +
                    ",\"server\":" + TaJsonString(AccountInfoString(ACCOUNT_SERVER));
   LogEvent(TA_EV_INIT, TA_INFO, "Guardian demarre sur " + m_symbol, 0, details);
   if(m_halt)
      LogEvent(TA_EV_KILL_SWITCH, TA_CRITICAL,
               "arret local toujours actif au demarrage: " + m_haltReason, 0, "{}");

   WriteReport(true);
   EventSetTimer(TA_TIMER_SECONDS);
   return true;
}


//+------------------------------------------------------------------+
void CTradingAgentGuardian::Deinit(const int reason)
{
   EventKillTimer();
   LogEvent(TA_EV_INIT, TA_INFO,
            "Guardian arrete (" + IntegerToString(reason) + ")", 0, "{}");
   WriteReport(true);
}


//+------------------------------------------------------------------+
//| Le battement de coeur est le meme chemin que le tick : la montre  |
//| reste maitresse pour qu'aucun tick ne soit necessaire.            |
//+------------------------------------------------------------------+
void CTradingAgentGuardian::OnTimer()
{
   Cycle();
}


//+------------------------------------------------------------------+
void CTradingAgentGuardian::OnTick()
{
   Cycle();
}


//+------------------------------------------------------------------+
void CTradingAgentGuardian::Cycle()
{
   datetime now = TimeGMT();
   if(now == m_lastCycle)          // une seule passe par seconde
      return;
   m_lastCycle = now;

   RefreshEnvironment();
   OnConnectionChange(m_connected);

   ReadState();
   CheckStateFreshness(now);

   if(m_killSwitch && !m_killReported)
   {
      m_killReported = true;
      LogEvent(TA_EV_KILL_SWITCH, TA_CRITICAL,
               "kill switch publie par le backend: plus aucun nouvel ordre", 0, "{}");
   }
   if(!m_killSwitch)
      m_killReported = false;

   if(!m_halt)
   {
      ExecuteOrders();
      Watchdog();
   }

   //--- La protection s'applique meme quand tout est arrete : une position sans stop est
   //--- un risque non controle, et la refermer n'est pas ouvrir une position.
   ProtectOpenPositions();

   WriteReport(false);
}


//+------------------------------------------------------------------+
//| Arborescence du pont.                                             |
//+------------------------------------------------------------------+
bool CTradingAgentGuardian::EnsureFolders()
{
   FolderCreate(TA_DIR_ROOT);
   FolderCreate(TA_DIR_STATE);
   FolderCreate(TA_DIR_REPORTS);
   FolderCreate(TA_DIR_CONTROL);
   ResetLastError();
   return true;
}


string CTradingAgentGuardian::StateFilePath() const
{
   return TA_DIR_STATE + "\\" + m_symbol + "_state.json";
}

string CTradingAgentGuardian::ReportFilePath() const
{
   return TA_DIR_REPORTS + "\\" + m_symbol + "_report.json";
}

string CTradingAgentGuardian::EventsFilePath() const
{
   return TA_DIR_REPORTS + "\\" + m_symbol + "_events.jsonl";
}

string CTradingAgentGuardian::AppliedFilePath() const
{
   return TA_DIR_CONTROL + "\\" + m_symbol + "_applied.txt";
}

string CTradingAgentGuardian::HaltFilePath() const
{
   return TA_DIR_CONTROL + "\\" + m_symbol + "_halt.txt";
}


//+------------------------------------------------------------------+
//| Lecture texte. Un fichier absent rend une chaine vide.            |
//| Le fichier d'etat est ecrit en ASCII pur cote Python.             |
//+------------------------------------------------------------------+
string CTradingAgentGuardian::ReadText(const string path)
{
   int handle = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE)
      return "";
   string text = "";
   while(!FileIsEnding(handle))
   {
      string part = FileReadString(handle);
      if(StringLen(part) > 0)
      {
         if(StringLen(text) > 0)
            text += "\n";
         text += part;
      }
   }
   FileClose(handle);
   return text;
}


//+------------------------------------------------------------------+
//| Ecriture atomique : fichier temporaire puis remplacement. Le      |
//| lecteur ne voit jamais un document a moitie ecrit.                |
//+------------------------------------------------------------------+
bool CTradingAgentGuardian::WriteTextAtomic(const string path, const string text)
{
   string temporary = path + ".tmp";
   int handle = FileOpen(temporary, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE)
   {
      m_errors++;
      Print("TradingAgent Guardian: ecriture impossible ", temporary,
            " erreur ", GetLastError());
      return false;
   }
   FileWriteString(handle, text);
   FileClose(handle);
   if(!FileMove(temporary, 0, path, FILE_REWRITE))
   {
      m_errors++;
      Print("TradingAgent Guardian: remplacement impossible ", path,
            " erreur ", GetLastError());
      FileDelete(temporary);
      return false;
   }
   return true;
}


//+------------------------------------------------------------------+
bool CTradingAgentGuardian::AppendLine(const string path, const string line)
{
   int handle = FileOpen(path, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE)
      return false;
   FileSeek(handle, 0, SEEK_END);
   FileWriteString(handle, line + "\n");
   FileClose(handle);
   return true;
}


//+------------------------------------------------------------------+
//| Chargement du fichier d'etat publie par le backend.               |
//+------------------------------------------------------------------+
bool CTradingAgentGuardian::ReadState()
{
   string text = ReadText(m_stateFile);
   if(StringLen(text) == 0)
   {
      //--- Aucun etat encore publie : rien a executer, rien a comparer. Si un etat a deja
      //--- ete lu puis disparait, c'est une divergence et l'arret local s'applique.
      if(m_haveState)
      {
         m_stateValid = false;
         Halt("fichier d'etat " + m_symbol + " disparu", TA_EV_STATE);
      }
      return false;
   }

   CTaJsonParser parser;
   CTaJson *root = parser.Parse(text);
   if(root == NULL || !root.IsObject())
   {
      if(CheckPointer(root) == POINTER_DYNAMIC)
         delete root;
      if(m_haveState)
      {
         m_stateValid = false;
         Halt("fichier d'etat " + m_symbol + " illisible", TA_EV_STATE);
      }
      return false;
   }

   long revision = root.Get("revision") != NULL ? root.Get("revision").AsLong(-1) : -1;
   CTaJson *protocol = root.Get("protocol_version");
   if(protocol != NULL)
   {
      long value = protocol.AsLong(0);
      if(value != TA_PROTOCOL_VERSION)
      {
         delete root;
         Halt("protocole " + IntegerToString(value) + " inconnu, attendu "
              + IntegerToString(TA_PROTOCOL_VERSION), TA_EV_STATE);
         return false;
      }
   }

   bool unchanged = (revision == m_revision && m_stateValid && revision >= 0);
   m_haveState  = true;
   m_stateValid = true;
   m_revision   = revision;
   m_publishedAt = 0;
   CTaJson *epoch = root.Get("published_epoch");
   if(epoch != NULL && epoch.AsLong(0) > 0)
      m_publishedAt = (datetime)epoch.AsLong(0);
   m_killSwitch = root.Get("kill_switch") != NULL ? root.Get("kill_switch").AsBool(false) : false;

   CTaJson *limits = root.Get("limits");
   if(limits != NULL && limits.IsObject())
   {
      m_maxPositions   = (int)(limits.Get("max_positions") != NULL ?
                               limits.Get("max_positions").AsLong(1) : 1);
      m_maxTotalVolume = limits.Get("max_total_volume") != NULL ?
                         limits.Get("max_total_volume").AsDouble(0.0) : 0.0;
   }
   else
   {
      m_maxPositions   = 1;
      m_maxTotalVolume = 0.0;
   }
   if(m_maxPositions < 0)
      m_maxPositions = 0;

   ArrayResize(m_expected, 0);
   CTaJson *positions = root.Get("positions");
   if(positions != NULL && positions.IsArray())
   {
      int count = positions.Count();
      ArrayResize(m_expected, count);
      for(int i = 0; i < count; i++)
      {
         CTaJson *item = positions.At(i);
         m_expected[i].ticket     = 0;
         m_expected[i].direction  = "";
         m_expected[i].volume     = 0.0;
         m_expected[i].stopLoss   = 0.0;
         m_expected[i].takeProfit = 0.0;
         m_expected[i].comment    = "";
         m_expected[i].hasStop    = false;
         m_expected[i].hasTarget  = false;
         if(item == NULL || !item.IsObject())
            continue;
         if(item.Get("ticket") != NULL)
            m_expected[i].ticket = item.Get("ticket").AsLong(0);
         if(item.Get("direction") != NULL)
            m_expected[i].direction = item.Get("direction").AsString("");
         if(item.Get("volume") != NULL)
            m_expected[i].volume = item.Get("volume").AsDouble(0.0);
         CTaJson *stop = item.Get("stop_loss");
         if(stop != NULL && stop.m_type == TA_JSON_NUMBER)
         {
            m_expected[i].stopLoss = stop.m_number;
            m_expected[i].hasStop  = true;
         }
         CTaJson *target = item.Get("take_profit");
         if(target != NULL && target.m_type == TA_JSON_NUMBER)
         {
            m_expected[i].takeProfit = target.m_number;
            m_expected[i].hasTarget  = true;
         }
         if(item.Get("comment") != NULL)
            m_expected[i].comment = item.Get("comment").AsString("");
      }
   }

   ArrayResize(m_orders, 0);
   CTaJson *orders = root.Get("orders");
   if(orders != NULL && orders.IsArray())
   {
      int count = orders.Count();
      ArrayResize(m_orders, count);
      for(int i = 0; i < count; i++)
      {
         CTaJson *item = orders.At(i);
         m_orders[i].id             = "";
         m_orders[i].action         = "";
         m_orders[i].direction      = "";
         m_orders[i].volume         = 0.0;
         m_orders[i].stopLoss       = 0.0;
         m_orders[i].takeProfit     = 0.0;
         m_orders[i].comment        = "";
         m_orders[i].positionTicket = 0;
         m_orders[i].hasStop        = false;
         m_orders[i].hasTarget      = false;
         if(item == NULL || !item.IsObject())
            continue;
         if(item.Get("id") != NULL)        m_orders[i].id = item.Get("id").AsString("");
         if(item.Get("action") != NULL)    m_orders[i].action = item.Get("action").AsString("");
         if(item.Get("direction") != NULL) m_orders[i].direction = item.Get("direction").AsString("");
         if(item.Get("volume") != NULL)    m_orders[i].volume = item.Get("volume").AsDouble(0.0);
         CTaJson *stop = item.Get("stop_loss");
         if(stop != NULL && stop.m_type == TA_JSON_NUMBER)
         {
            m_orders[i].stopLoss = stop.m_number;
            m_orders[i].hasStop  = true;
         }
         CTaJson *target = item.Get("take_profit");
         if(target != NULL && target.m_type == TA_JSON_NUMBER)
         {
            m_orders[i].takeProfit = target.m_number;
            m_orders[i].hasTarget  = true;
         }
         if(item.Get("comment") != NULL)
            m_orders[i].comment = item.Get("comment").AsString("");
         if(item.Get("position_ticket") != NULL)
            m_orders[i].positionTicket = item.Get("position_ticket").AsLong(0);
      }
   }

   delete root;

   //--- Un etat nouveau vaut une ligne de journal : l'operateur voit ce que le backend a
   //--- demande, et depuis quand il ne l'a plus fait.
   if(!unchanged)
      LogEvent(TA_EV_STATE, TA_INFO,
               "etat revision " + IntegerToString(revision) + " applique",
               0,
               "\"revision\":" + IntegerToString(revision) +
               ",\"positions\":" + IntegerToString(ArraySize(m_expected)) +
               ",\"orders\":" + IntegerToString(ArraySize(m_orders)) +
               ",\"kill_switch\":" + TaJsonBool(m_killSwitch));
   return true;
}


//+------------------------------------------------------------------+
//| Un etat lu puis jamais rafraichi est une perte de contact avec le |
//| backend (RM-013) : l'EA ne doit plus ouvrir quoi que ce soit.     |
//|                                                                   |
//| La levee est automatique, et c'est le seul motif d'arret qui en   |
//| beneficie. L'arret etait un verrou a sens unique : arme pendant   |
//| une coupure, il restait arme alors que le backend republiait. Le  |
//| 2026-10-08, mesure sur 60 s, l'age de l'etat oscillait entre 4 et |
//| 16 s (jamais 30) et local_halt restait vrai, orders_sent a 0 :    |
//| plus aucun ordre ne pouvait partir, sans qu'aucune condition ne   |
//| soit remplie. Un arret qui ne se leve pas est un arret qu'on finit|
//| par contourner a la main, et c'est ainsi qu'un filet de securite   |
//| devient une source de risque.                                     |
//|                                                                   |
//| Les autres motifs restent des verrous d'operateur : une divergence|
//| d'etat (RM-014) doit etre reconciliee a la main, et un kill switch|
//| publie par le backend doit etre leve par le backend. Seule la     |
//| perte de contact se repare toute seule, parce que le retour de la |
//| liaison est une chose que l'EA constate par lui-meme.             |
//+------------------------------------------------------------------+
bool CTradingAgentGuardian::IsBackendSilenceHalt() const
{
   if(!m_halt)
      return false;
   // La raison est ecrite par Halt() et relue par LoadHalt() : la comparer au prefixe
   // garde le motif d'origine a travers un redemarrage du terminal.
   return StringFind(m_haltReason, "backend silencieux") == 0;
}


//+------------------------------------------------------------------+
//| Leve l'arret local et efface sa trace sur disque. Le journal est  |
//| ecrit par l'appelant, qui sait pourquoi l'arret tombe.             |
//+------------------------------------------------------------------+
void CTradingAgentGuardian::Resume()
{
   m_halt       = false;
   m_haltReason = "";
   if(FileIsExist(m_haltFile))
      FileDelete(m_haltFile);
}


//+------------------------------------------------------------------+
void CTradingAgentGuardian::CheckStateFreshness(const datetime now)
{
   if(!m_haveState || !m_stateValid || m_publishedAt <= 0)
      return;
   double age = (double)(now - m_publishedAt);
   if(age <= (double)m_staleSeconds)
   {
      if(IsBackendSilenceHalt())
      {
         Resume();
         LogEvent(TA_EV_STATE, TA_INFO,
                  "backend de nouveau a l'ecoute (age " + DoubleToString(age, 0)
                  + " s, limite " + IntegerToString(m_staleSeconds)
                  + " s): arret local leve", 0, "{}");
      }
      return;
   }
   Halt("backend silencieux depuis " + DoubleToString(age, 0)
        + " s (limite " + IntegerToString(m_staleSeconds) + ")", TA_EV_STATE);
}


//+------------------------------------------------------------------+
void CTradingAgentGuardian::LoadApplied()
{
   ArrayResize(m_applied, 0);
   string text = ReadText(m_appliedFile);
   if(StringLen(text) == 0)
      return;
   string lines[];
   int count = StringSplit(text, '\n', lines);
   for(int i = 0; i < count; i++)
   {
      string id = lines[i];
      StringTrimLeft(id);
      StringTrimRight(id);
      if(StringLen(id) == 0)
         continue;
      int n = ArraySize(m_applied);
      ArrayResize(m_applied, n + 1);
      m_applied[n] = id;
   }
}


void CTradingAgentGuardian::LoadHalt()
{
   string text = ReadText(m_haltFile);
   if(StringLen(text) == 0)
      return;
   m_halt       = true;
   m_haltReason = text;
   Print("TradingAgent Guardian: arret local persistant: ", m_haltReason);
}


bool CTradingAgentGuardian::IsApplied(const string orderId)
{
   if(StringLen(orderId) == 0)
      return false;
   for(int i = 0; i < ArraySize(m_applied); i++)
      if(m_applied[i] == orderId)
         return true;
   return false;
}


void CTradingAgentGuardian::MarkApplied(const string orderId)
{
   if(StringLen(orderId) == 0 || IsApplied(orderId))
      return;
   int n = ArraySize(m_applied);
   ArrayResize(m_applied, n + 1);
   m_applied[n] = orderId;
   AppendLine(m_appliedFile, orderId);
}


void CTradingAgentGuardian::MarkReported(const string orderId)
{
   int n = ArraySize(m_reported);
   ArrayResize(m_reported, n + 1);
   m_reported[n] = orderId;
}


bool CTradingAgentGuardian::WasReported(const string orderId)
{
   for(int i = 0; i < ArraySize(m_reported); i++)
      if(m_reported[i] == orderId)
         return true;
   return false;
}


//+------------------------------------------------------------------+
//| Connexion, autorisation de trading, specs du symbole.             |
//+------------------------------------------------------------------+
bool CTradingAgentGuardian::RefreshEnvironment()
{
   m_connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   m_tradeAllowed = (bool)MQLInfoInteger(MQL_TRADE_ALLOWED) &&
                    (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) &&
                    (bool)AccountInfoInteger(ACCOUNT_TRADE_EXPERT) &&
                    SymbolTradable();
   return m_connected;
}


bool CTradingAgentGuardian::SymbolTradable()
{
   long mode = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_MODE);
   return mode == SYMBOL_TRADE_MODE_FULL;
}


void CTradingAgentGuardian::OnConnectionChange(const bool connected)
{
   if(m_connectedKnown && connected == m_lastConnected)
      return;
   m_connectedKnown = true;
   m_lastConnected  = connected;
   LogEvent(TA_EV_CONNECTION, connected ? TA_INFO : TA_CRITICAL,
            connected ? "terminal connecte" : "terminal deconnecte",
            0, "\"connected\":" + TaJsonBool(connected));
}


double CTradingAgentGuardian::Ask() const
{
   return SymbolInfoDouble(m_symbol, SYMBOL_ASK);
}


double CTradingAgentGuardian::Bid() const
{
   return SymbolInfoDouble(m_symbol, SYMBOL_BID);
}


double CTradingAgentGuardian::Point() const
{
   return SymbolInfoDouble(m_symbol, SYMBOL_POINT);
}


double CTradingAgentGuardian::PriceTolerance() const
{
   return MathMax(Point() * 2.0, 0.01);
}


double CTradingAgentGuardian::VolumeTolerance() const
{
   return MathMax(SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP) / 2.0, 0.000001);
}


//+------------------------------------------------------------------+
int CTradingAgentGuardian::OwnPositionsCount() const
{
   int count = 0;
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != m_symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != m_magic)
         continue;
      count++;
   }
   return count;
}


double CTradingAgentGuardian::OwnPositionsVolume() const
{
   double volume = 0.0;
   int    total  = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != m_symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != m_magic)
         continue;
      volume += PositionGetDouble(POSITION_VOLUME);
   }
   return volume;
}


bool CTradingAgentGuardian::FindPositionByComment(const string comment, ulong &ticket) const
{
   ticket = 0;
   if(StringLen(comment) == 0)
      return false;
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong candidate = PositionGetTicket(i);
      if(candidate == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != m_symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != m_magic)
         continue;
      if(PositionGetString(POSITION_COMMENT) == comment)
      {
         ticket = candidate;
         return true;
      }
   }
   return false;
}


bool CTradingAgentGuardian::PositionSnapshot(const ulong ticket, string &direction,
                                             double &volume, double &priceOpen,
                                             double &stop, double &target,
                                             double &profit, string &comment) const
{
   if(!PositionSelectByTicket(ticket))
      return false;
   long type        = PositionGetInteger(POSITION_TYPE);
   direction        = (type == POSITION_TYPE_BUY) ? "BUY" : "SELL";
   volume           = PositionGetDouble(POSITION_VOLUME);
   priceOpen        = PositionGetDouble(POSITION_PRICE_OPEN);
   stop             = PositionGetDouble(POSITION_SL);
   target           = PositionGetDouble(POSITION_TP);
   profit           = PositionGetDouble(POSITION_PROFIT);
   comment          = PositionGetString(POSITION_COMMENT);
   return true;
}


bool CTradingAgentGuardian::StopPresent(const string comment) const
{
   ulong ticket = 0;
   if(!FindPositionByComment(comment, ticket))
      return false;              // disparue ou illisible : non confirme n'est pas confirme
   if(!PositionSelectByTicket(ticket))
      return false;
   return PositionGetDouble(POSITION_SL) != 0.0;
}


bool CTradingAgentGuardian::TargetPresent(const string comment, double &target) const
{
   target = 0.0;
   ulong ticket = 0;
   if(!FindPositionByComment(comment, ticket))
      return false;
   if(!PositionSelectByTicket(ticket))
      return false;
   target = PositionGetDouble(POSITION_TP);
   return target != 0.0;
}


//+------------------------------------------------------------------+
//| EXECUTION : uniquement les ordres presents dans le fichier d'etat.|
//+------------------------------------------------------------------+
void CTradingAgentGuardian::ExecuteOrders()
{
   if(ArraySize(m_orders) == 0)
      return;

   for(int i = 0; i < ArraySize(m_orders); i++)
   {
      TaOrder order = m_orders[i];
      if(StringLen(order.id) == 0)
         continue;
      if(IsApplied(order.id))
         continue;

      //--- Un ordre dont le commentaire est deja sur une position a atteint le serveur :
      //--- il est adopte, jamais renvoye (idempotence, comme key_comment cote Python).
      if(StringLen(order.comment) > 0)
      {
         ulong existing = 0;
         if(FindPositionByComment(order.comment, existing))
         {
            MarkApplied(order.id);
            LogEvent(TA_EV_ORDERS, TA_INFO,
                     "ordre " + order.id + " deja present (ticket "
                     + IntegerToString((long)existing) + "), aucun second envoi", existing,
                     "\"order_id\":" + TaJsonString(order.id) +
                     ",\"adopted\":true");
            continue;
         }
      }

      if(!m_connected)
      {
         if(!WasReported(order.id))
         {
            MarkReported(order.id);
            LogEvent(TA_EV_CONNECTION, TA_WARNING,
                     "ordre " + order.id + " en attente: terminal deconnecte", 0,
                     "\"order_id\":" + TaJsonString(order.id));
         }
         continue;                 // rien n'est marque applique : l'ordre repartira
      }

      if(!m_tradeAllowed)
      {
         if(!WasReported(order.id))
         {
            MarkReported(order.id);
            LogEvent(TA_EV_REFUSED, TA_CRITICAL,
                     "ordre " + order.id + " refuse: trading interdit par le terminal", 0,
                     "\"order_id\":" + TaJsonString(order.id));
         }
         continue;
      }

      if(order.action == "OPEN")
         DoOpen(order);
      else if(order.action == "CLOSE")
         DoClose(order);
      else
      {
         MarkApplied(order.id);
         m_ordersRefused++;
         LogEvent(TA_EV_REFUSED, TA_CRITICAL,
                  "ordre " + order.id + " refuse: action inconnue " + order.action, 0,
                  "\"order_id\":" + TaJsonString(order.id) +
                  ",\"action\":" + TaJsonString(order.action));
      }
   }
}


//+------------------------------------------------------------------+
bool CTradingAgentGuardian::AllowedToOpen(const TaOrder &order, string &reason)
{
   reason = "";
   if(m_killSwitch)
   {
      reason = "kill switch publie par le backend";
      return false;
   }
   if(m_halt)
   {
      reason = "arret local: " + m_haltReason;
      return false;
   }
   if(!order.hasStop || order.stopLoss == 0.0)
   {
      reason = "stop-loss obligatoire absent";       // RM-004
      return false;
   }
   if(m_maxPositions > 0 && OwnPositionsCount() >= m_maxPositions)
   {
      reason = "nombre maximal de positions atteint (" + IntegerToString(m_maxPositions) + ")";
      return false;
   }
   if(m_maxTotalVolume > 0.0 && (OwnPositionsVolume() + order.volume) > m_maxTotalVolume + VolumeTolerance())
   {
      reason = "exposition maximale depassee (" + DoubleToString(m_maxTotalVolume, 8) + ")";
      return false;
   }
   if(!VolumeWithinSpec(order.volume, reason))
      return false;
   return true;
}


bool CTradingAgentGuardian::VolumeWithinSpec(const double volume, string &reason)
{
   double minimum = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
   double maximum = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);
   double step    = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      step = 0.01;
   reason = "";
   if(volume <= 0.0)
   {
      reason = "volume nul";
      return false;
   }
   if(volume < minimum - 1e-9)
   {
      reason = "volume " + DoubleToString(volume, 8) + " sous le minimum "
               + DoubleToString(minimum, 8);
      return false;
   }
   if(volume > maximum + 1e-9)
   {
      reason = "volume " + DoubleToString(volume, 8) + " au-dessus du maximum "
               + DoubleToString(maximum, 8);
      return false;
   }
   double steps = volume / step;
   if(MathAbs(steps - MathRound(steps)) > 1e-6)
   {
      reason = "volume " + DoubleToString(volume, 8) + " hors du pas "
               + DoubleToString(step, 8);
      return false;
   }
   return true;
}


//+------------------------------------------------------------------+
void CTradingAgentGuardian::DoOpen(const TaOrder &order)
{
   string reason = "";
   if(!AllowedToOpen(order, reason))
   {
      if(!WasReported(order.id))
      {
         MarkReported(order.id);
         m_ordersRefused++;
         LogEvent(TA_EV_REFUSED, TA_WARNING,
                  "ordre " + order.id + " bloque: " + reason, 0,
                  "\"order_id\":" + TaJsonString(order.id) +
                  ",\"reason\":" + TaJsonString(reason) +
                  ",\"volume\":" + TaJsonNumber(order.volume) +
                  ",\"stop_loss\":" + TaJsonNumber(order.stopLoss));
      }
      return;                    // jamais marque applique : il repartira quand ce sera permis
   }

   bool isBuy = (order.direction == "BUY");
   if(!isBuy && order.direction != "SELL")
   {
      MarkApplied(order.id);
      m_ordersRefused++;
      LogEvent(TA_EV_REFUSED, TA_CRITICAL,
               "ordre " + order.id + " refuse: sens inconnu " + order.direction, 0,
               "\"order_id\":" + TaJsonString(order.id));
      return;
   }

   double requested = isBuy ? Ask() : Bid();
   if(requested <= 0.0)
   {
      if(!WasReported(order.id))
      {
         MarkReported(order.id);
         LogEvent(TA_EV_ERROR, TA_WARNING,
                  "ordre " + order.id + " en attente: aucune cotation", 0,
                  "\"order_id\":" + TaJsonString(order.id));
      }
      return;
   }

   string comment = order.comment;
   if(StringLen(comment) > TA_COMMENT_LIMIT)
      comment = StringSubstr(comment, 0, TA_COMMENT_LIMIT);

   //--- Le volume est ramene au pas de lot du courtier : le backend l'a deja valide, mais
   //--- un flottant qui traine (0.010000000000000002) serait refuse par le serveur.
   double volume = order.volume;
   double step   = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
   if(step > 0.0)
      volume = NormalizeDouble(MathRound(volume / step) * step, 8);
   int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);

   m_trade.SetTypeFillingBySymbol(m_symbol);
   bool sent = m_trade.PositionOpen(m_symbol,
                                    isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,
                                    volume,
                                    NormalizeDouble(requested, digits),
                                    order.hasStop ? NormalizeDouble(order.stopLoss, digits) : 0.0,
                                    order.hasTarget ? NormalizeDouble(order.takeProfit, digits) : 0.0,
                                    comment);
   uint retcode   = m_trade.ResultRetcode();
   double executed = m_trade.ResultPrice();
   double slippage = (executed > 0.0) ? MathAbs(executed - requested) : 0.0;

   string data = "\"order_id\":" + TaJsonString(order.id) +
                 ",\"requested_price\":" + TaJsonNumber(requested) +
                 ",\"executed_price\":" + TaJsonNumber(executed) +
                 ",\"slippage\":" + TaJsonNumber(slippage) +
                 ",\"volume\":" + TaJsonNumber(volume) +
                 ",\"stop_loss\":" + TaJsonNumber(order.stopLoss) +
                 ",\"take_profit\":" + TaJsonNumber(order.takeProfit) +
                 ",\"retcode\":" + IntegerToString((int)retcode) +
                 ",\"comment\":" + TaJsonString(comment);

   if(!sent)
   {
      m_ordersRefused++;
      LogEvent(TA_EV_EXECUTION_FAILED, TA_CRITICAL,
               "ordre " + order.id + " refuse par le serveur: retcode "
               + IntegerToString((int)retcode) + " " + m_trade.ResultComment(),
               0, data);
      MarkApplied(order.id);     // le backend decide de renvoyer : jamais deux fois le meme
      return;
   }

   m_ordersSent++;
   ulong ticket = 0;
   FindPositionByComment(comment, ticket);
   LogEvent(TA_EV_EXECUTION, TA_INFO,
            "ordre " + order.id + " execute a " + DoubleToString(executed, 8)
            + " (demande " + DoubleToString(requested, 8) + ", slippage "
            + DoubleToString(slippage, 8) + ")",
            (long)ticket, data + ",\"ticket\":" + IntegerToString((long)ticket));
   MarkApplied(order.id);

   //--- VERIFICATION : un stop qui n'est pas sur la position n'existe pas. On relit la
   //--- position et, s'il manque, on referme immediatement (comme MT5Broker._stop_present).
   if(!StopPresent(comment))
   {
      m_stopsProtected++;
      LogEvent(TA_EV_PROTECTION, TA_CRITICAL,
               "position " + IntegerToString((long)ticket)
               + " ouverte sans son stop-loss: fermeture immediate",
               (long)ticket, data);
      if(!m_trade.PositionClose(ticket))
         LogEvent(TA_EV_ERROR, TA_CRITICAL,
                  "fermeture de la position " + IntegerToString((long)ticket)
                  + " impossible: retcode " + IntegerToString((int)m_trade.ResultRetcode()),
                  (long)ticket, "{}");
   }

   //--- VERIFICATION de l'objectif : un objectif absent n'expose pas d'argent, il est donc
   //--- signale sans fermeture. Le stop, lui, n'a pas ce droit a l'erreur.
   if(order.hasTarget)
   {
      double target = 0.0;
      if(!TargetPresent(comment, target))
         LogEvent(TA_EV_PROTECTION, TA_WARNING,
                  "objectif absent sur la position " + IntegerToString((long)ticket)
                  + " (demande " + DoubleToString(order.takeProfit, 8) + ")",
                  (long)ticket, data);
      else if(MathAbs(target - order.takeProfit) > PriceTolerance())
         LogEvent(TA_EV_PROTECTION, TA_WARNING,
                  "objectif " + DoubleToString(target, 8) + " pose au lieu de "
                  + DoubleToString(order.takeProfit, 8),
                  (long)ticket, data);
   }
   return;
}


//+------------------------------------------------------------------+
void CTradingAgentGuardian::DoClose(const TaOrder &order)
{
   ulong ticket = (ulong)order.positionTicket;
   if(ticket == 0 && StringLen(order.comment) > 0)
      FindPositionByComment(order.comment, ticket);

   if(ticket == 0)
   {
      //--- Rien n'est marque applique : la position est peut-etre simplement invisible une
      //--- seconde. L'ordre repartira, et le backend le retirera de lui-meme s'il n'y a
      //--- plus rien a fermer.
      if(!WasReported(order.id))
      {
         MarkReported(order.id);
         LogEvent(TA_EV_REFUSED, TA_WARNING,
                  "ordre " + order.id + " de fermeture: position introuvable pour l'instant",
                  0, "\"order_id\":" + TaJsonString(order.id));
      }
      return;
   }

   if(!PositionSelectByTicket(ticket))
   {
      if(!WasReported(order.id))
      {
         MarkReported(order.id);
         LogEvent(TA_EV_REFUSED, TA_WARNING,
                  "ordre " + order.id + " de fermeture: position "
                  + IntegerToString((long)ticket) + " introuvable pour l'instant",
                  (long)ticket, "{}");
      }
      return;
   }
   if(PositionGetString(POSITION_SYMBOL) != m_symbol ||
      PositionGetInteger(POSITION_MAGIC) != m_magic)
   {
      //--- Refus definitif : on ne touche jamais une position qui n'est pas la notre, et on
      //--- ne redemande jamais.
      MarkApplied(order.id);
      LogEvent(TA_EV_REFUSED, TA_CRITICAL,
               "ordre " + order.id + " de fermeture refuse: la position "
               + IntegerToString((long)ticket) + " n'appartient pas a ce Guardian",
               (long)ticket, "{}");
      return;
   }

   double volume = PositionGetDouble(POSITION_VOLUME);
   double price  = PositionGetDouble(POSITION_PRICE_CURRENT);
   string comment = order.comment;
   if(StringLen(comment) > TA_COMMENT_LIMIT)
      comment = StringSubstr(comment, 0, TA_COMMENT_LIMIT);

   m_trade.SetTypeFillingBySymbol(m_symbol);
   bool sent = m_trade.PositionClose(ticket, (ulong)m_deviation);
   uint retcode = m_trade.ResultRetcode();
   double executed = m_trade.ResultPrice();
   double slippage = (executed > 0.0 && price > 0.0) ? MathAbs(executed - price) : 0.0;

   string data = "\"order_id\":" + TaJsonString(order.id) +
                 ",\"requested_price\":" + TaJsonNumber(price) +
                 ",\"executed_price\":" + TaJsonNumber(executed) +
                 ",\"slippage\":" + TaJsonNumber(slippage) +
                 ",\"volume\":" + TaJsonNumber(volume) +
                 ",\"retcode\":" + IntegerToString((int)retcode) +
                 ",\"comment\":" + TaJsonString(comment);

   if(!sent)
   {
      m_ordersRefused++;
      //--- Signale une fois, mais non marque applique : le backend peut redemander, et une
      //--- fermeture qui n'a pas eu lieu doit rester visible.
      if(!WasReported(order.id))
      {
         MarkReported(order.id);
         LogEvent(TA_EV_EXECUTION_FAILED, TA_CRITICAL,
                  "fermeture de la position " + IntegerToString((long)ticket)
                  + " refusee: retcode " + IntegerToString((int)retcode) + " "
                  + m_trade.ResultComment(), (long)ticket, data);
      }
      return;
   }
   m_ordersSent++;
   LogEvent(TA_EV_EXECUTION, TA_INFO,
            "position " + IntegerToString((long)ticket) + " fermee a "
            + DoubleToString(executed, 8), (long)ticket, data);
   MarkApplied(order.id);
   return;
}


//+------------------------------------------------------------------+
//| SURVEILLANCE : etat attendu contre etat reel. Toute difference    |
//| bloque les nouveaux ordres — et rien n'est corrige tout seul.     |
//+------------------------------------------------------------------+
void CTradingAgentGuardian::Watchdog()
{
   if(!m_stateValid || !m_haveState)
      return;

   string details = "";
   int    found   = 0;

   //--- ce que le terminal a et que le backend n'attend pas
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != m_symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != m_magic)
         continue;
      double volume = PositionGetDouble(POSITION_VOLUME);
      double stop   = PositionGetDouble(POSITION_SL);
      string direction = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      bool matched = false;
      for(int j = 0; j < ArraySize(m_expected); j++)
      {
         //--- ticket 0 : le backend sait qu'il veut cette position mais n'en connait pas
         //--- encore le ticket. Tant qu'il ne le connait pas, il n'y a rien a comparer.
         if(m_expected[j].ticket <= 0)
            continue;
         if(m_expected[j].ticket != (long)ticket)
            continue;
         matched = true;
         if(MathAbs(m_expected[j].volume - volume) > VolumeTolerance())
         {
            found++;
            details += (StringLen(details) > 0 ? " | " : "") +
                       "position " + IntegerToString((long)ticket) + " volume "
                       + DoubleToString(volume, 8) + " au terminal, "
                       + DoubleToString(m_expected[j].volume, 8) + " attendu";
         }
         if(!m_expected[j].hasStop && stop != 0.0)
         {
            found++;
            details += (StringLen(details) > 0 ? " | " : "") +
                       "position " + IntegerToString((long)ticket)
                       + " porte un stop alors que le backend n'en attend aucun";
         }
         if(m_expected[j].hasStop && MathAbs(m_expected[j].stopLoss - stop) > PriceTolerance())
         {
            found++;
            details += (StringLen(details) > 0 ? " | " : "") +
                       "position " + IntegerToString((long)ticket) + " stop "
                       + DoubleToString(stop, 8) + " au terminal, "
                       + DoubleToString(m_expected[j].stopLoss, 8) + " attendu";
         }
         if(m_expected[j].direction != "" && m_expected[j].direction != direction)
         {
            found++;
            details += (StringLen(details) > 0 ? " | " : "") +
                       "position " + IntegerToString((long)ticket) + " sens " + direction
                       + " au terminal, " + m_expected[j].direction + " attendu";
         }
         break;
      }
      if(!matched)
      {
         found++;
         details += (StringLen(details) > 0 ? " | " : "") +
                    "position " + IntegerToString((long)ticket)
                    + " ouverte au terminal mais inconnue du backend";
      }
   }

   //--- ce que le backend attend et que le terminal n'a pas
   for(int j = 0; j < ArraySize(m_expected); j++)
   {
      if(m_expected[j].ticket <= 0)
         continue;
      if(!PositionSelectByTicket((ulong)m_expected[j].ticket))
      {
         found++;
         details += (StringLen(details) > 0 ? " | " : "") +
                    "position " + IntegerToString(m_expected[j].ticket)
                    + " attendue par le backend, absente du terminal";
      }
   }

   if(found == 0)
   {
      m_lastDivergence = "";
      return;
   }

   m_divergenceCount += found;
   //--- La meme divergence n'est pas repetee a chaque seconde : elle est deja persistante
   //--- et le journal garde la premiere occurrence.
   if(details == m_lastDivergence)
   {
      Halt("divergence d'etat sur " + m_symbol + ": " + details, TA_EV_DIVERGENCE);
      return;
   }
   m_lastDivergence = details;
   LogEvent(TA_EV_DIVERGENCE, TA_CRITICAL,
            "divergence d'etat sur " + m_symbol + ": " + details, 0,
            "\"divergences\":" + IntegerToString(found));
   Halt("divergence d'etat sur " + m_symbol + ": " + details, TA_EV_DIVERGENCE);
}


//+------------------------------------------------------------------+
//| PROTECTION : une position de ce Guardian sans stop est fermee.    |
//+------------------------------------------------------------------+
void CTradingAgentGuardian::ProtectOpenPositions()
{
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != m_symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != m_magic)
         continue;
      double stop = PositionGetDouble(POSITION_SL);
      if(stop != 0.0)
         continue;

      m_stopsProtected++;
      string data = "\"ticket\":" + IntegerToString((long)ticket) +
                    ",\"volume\":" + TaJsonNumber(PositionGetDouble(POSITION_VOLUME)) +
                    ",\"price_open\":" + TaJsonNumber(PositionGetDouble(POSITION_PRICE_OPEN));
      LogEvent(TA_EV_PROTECTION, TA_CRITICAL,
               "position " + IntegerToString((long)ticket)
               + " sans stop-loss: fermeture de protection", (long)ticket, data);
      m_trade.SetTypeFillingBySymbol(m_symbol);
      if(!m_trade.PositionClose(ticket, (ulong)m_deviation))
         LogEvent(TA_EV_ERROR, TA_CRITICAL,
                  "fermeture de protection impossible pour la position "
                  + IntegerToString((long)ticket) + ": retcode "
                  + IntegerToString((int)m_trade.ResultRetcode()), (long)ticket, data);
   }
}


//+------------------------------------------------------------------+
//| KILL SWITCH LOCAL : l'arret est persiste sur disque. Il survit au |
//| redemarrage et ne peut etre leve que par une action de           |
//| l'operateur (suppression du fichier), jamais par un redemarrage.  |
//+------------------------------------------------------------------+
void CTradingAgentGuardian::Halt(const string reason, const string kind)
{
   if(m_halt && m_haltReason == reason)
      return;
   m_halt       = true;
   m_haltReason = reason;
   WriteTextAtomic(m_haltFile, reason);
   LogEvent(kind, TA_CRITICAL, "arret local: " + reason, 0,
            "\"reason\":" + TaJsonString(reason));
}


//+------------------------------------------------------------------+
//| REMONTEE : une ligne de journal, en memoire et sur disque.        |
//+------------------------------------------------------------------+
void CTradingAgentGuardian::LogEvent(const string kind, const string severity,
                                     const string message, const long ticket,
                                     const string data)
{
   //--- memoire : fenetre glissante, la plus recente en dernier
   if(ArraySize(m_events) >= TA_EVENTS_IN_MEMORY)
   {
      for(int i = 1; i < ArraySize(m_events); i++)
         m_events[i - 1] = m_events[i];
      ArrayResize(m_events, TA_EVENTS_IN_MEMORY - 1);
   }
   int n = ArraySize(m_events);
   ArrayResize(m_events, n + 1);
   m_eventSeq++;
   m_events[n].seq      = m_eventSeq;
   m_events[n].at       = TimeGMT();
   m_events[n].kind     = kind;
   m_events[n].severity = severity;
   m_events[n].message  = message;
   m_events[n].ticket   = ticket;
   //--- `data` doit etre un OBJET JSON, pas un fragment. Tous les appelants passent
   //--- "cle":valeur,... ; emis tel quel, le rapport devenait du JSON invalide et le
   //--- lecteur Python rejetait le fichier entier (l'EA paraissait hors ligne a jamais).
   //--- Le defaut "{}" et tout appelant deja entre accolades restent intacts.
   if(StringLen(data) == 0)
      m_events[n].data = "{}";
   else if(StringGetCharacter(data, 0) == '{')
      m_events[n].data = data;
   else
      m_events[n].data = "{" + data + "}";

   string line = "{\"seq\":" + IntegerToString(m_eventSeq) +
                 ",\"at\":" + TaJsonString(TaIsoUtc(m_events[n].at)) +
                 ",\"kind\":" + TaJsonString(kind) +
                 ",\"severity\":" + TaJsonString(severity) +
                 ",\"ticket\":" + IntegerToString(ticket) +
                 ",\"message\":" + TaJsonString(message) +
                 ",\"data\":" + m_events[n].data + "}";
   AppendLine(m_eventsFile, line);
}


//+------------------------------------------------------------------+
//| Le rapport : battement de coeur, etat observe, journal.           |
//+------------------------------------------------------------------+
void CTradingAgentGuardian::WriteReport(const bool force)
{
   datetime now = TimeGMT();
   if(!force && (now - m_lastReport) < m_reportSeconds)
      return;
   m_lastReport = now;

   double stateAge = -1.0;
   if(m_publishedAt > 0)
      stateAge = (double)(now - m_publishedAt);

   string json = "{";
   json += "\"protocol_version\":" + IntegerToString(TA_PROTOCOL_VERSION);
   json += ",\"ea_version\":" + TaJsonString(TA_EA_VERSION);
   json += ",\"symbol\":" + TaJsonString(m_symbol);
   json += ",\"magic\":" + IntegerToString(m_magic);
   json += ",\"chart_symbol\":" + TaJsonString(_Symbol);
   json += ",\"updated_at\":" + TaJsonString(TaIsoUtc(now));
   json += ",\"server_time\":" + TaJsonString(TaIsoUtc(TimeCurrent()));
   json += ",\"applied_revision\":" + IntegerToString(m_revision);
   json += ",\"state_age_seconds\":" + TaJsonNumber(stateAge);
   json += ",\"connected\":" + TaJsonBool(m_connected);
   json += ",\"trade_allowed\":" + TaJsonBool(m_tradeAllowed);
   json += ",\"symbol_tradable\":" + TaJsonBool(SymbolTradable());
   json += ",\"kill_switch\":" + TaJsonBool(m_killSwitch);
   json += ",\"local_halt\":" + TaJsonBool(m_halt);
   json += ",\"halt_reason\":" + TaJsonString(m_haltReason);
   json += ",\"last_event_seq\":" + IntegerToString(m_eventSeq);
   json += ",\"account\":{";
   json += "\"login\":" + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));
   json += ",\"server\":" + TaJsonString(AccountInfoString(ACCOUNT_SERVER));
   json += ",\"company\":" + TaJsonString(AccountInfoString(ACCOUNT_COMPANY));
   json += ",\"currency\":" + TaJsonString(AccountInfoString(ACCOUNT_CURRENCY));
   json += ",\"demo\":" + TaJsonBool((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO);
   json += ",\"build\":" + IntegerToString((int)TerminalInfoInteger(TERMINAL_BUILD));
   json += "}";

   //--- positions reellement ouvertes pour ce symbole et ce magic
   json += ",\"positions\":[";
   bool first = true;
   int  total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != m_symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != m_magic)
         continue;
      if(!first)
         json += ",";
      first = false;
      json += "{\"ticket\":" + IntegerToString((long)ticket);
      json += ",\"symbol\":" + TaJsonString(PositionGetString(POSITION_SYMBOL));
      json += ",\"direction\":" + TaJsonString(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL");
      json += ",\"volume\":" + TaJsonNumber(PositionGetDouble(POSITION_VOLUME));
      json += ",\"price_open\":" + TaJsonNumber(PositionGetDouble(POSITION_PRICE_OPEN));
      json += ",\"stop_loss\":" + TaJsonNumber(PositionGetDouble(POSITION_SL));
      json += ",\"take_profit\":" + TaJsonNumber(PositionGetDouble(POSITION_TP));
      json += ",\"profit\":" + TaJsonNumber(PositionGetDouble(POSITION_PROFIT));
      json += ",\"comment\":" + TaJsonString(PositionGetString(POSITION_COMMENT));
      json += "}";
   }
   json += "]";

   json += ",\"counters\":{";
   json += "\"orders_sent\":" + IntegerToString(m_ordersSent);
   json += ",\"orders_refused\":" + IntegerToString(m_ordersRefused);
   json += ",\"divergences\":" + IntegerToString(m_divergenceCount);
   json += ",\"stops_protected\":" + IntegerToString(m_stopsProtected);
   json += ",\"errors\":" + IntegerToString(m_errors);
   json += "}";

   json += ",\"events\":[";
   for(int i = 0; i < ArraySize(m_events); i++)
   {
      if(i > 0)
         json += ",";
      json += "{\"seq\":" + IntegerToString(m_events[i].seq);
      json += ",\"at\":" + TaJsonString(TaIsoUtc(m_events[i].at));
      json += ",\"kind\":" + TaJsonString(m_events[i].kind);
      json += ",\"severity\":" + TaJsonString(m_events[i].severity);
      json += ",\"ticket\":" + IntegerToString(m_events[i].ticket);
      json += ",\"message\":" + TaJsonString(m_events[i].message);
      json += ",\"data\":" + m_events[i].data;
      json += "}";
   }
   json += "]";

   json += "}";
   WriteTextAtomic(m_reportFile, json);
}
//+------------------------------------------------------------------+
