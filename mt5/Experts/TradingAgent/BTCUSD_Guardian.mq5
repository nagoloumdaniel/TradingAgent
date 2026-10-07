//+------------------------------------------------------------------+
//|                                            BTCUSD_Guardian.mq5   |
//|                                    Copyright 2026, TradingAgent  |
//|                          https://github.com/  voir docs/ea/      |
//+------------------------------------------------------------------+
//  Guardian BTCUSD — cahier v3 §18, §19, §53 phase 7.
//
//  Cet EA n'est PAS un cerveau : il n'attache que le symbole BTCUSD, ne lit que le
//  fichier d'etat que le backend publie pour lui, et n'envoie que les ordres qui y sont
//  explicitement autorises. Il ne prend aucune decision de marche. Toute la logique vit
//  dans TradingAgentGuardian.mqh ; ce fichier ne fait que fixer son identite.
//+------------------------------------------------------------------+
#property copyright "TradingAgent"
#property link      "https://github.com/"
#property version   "1.00"
#property description "Guardian TradingAgent pour BTCUSD : execute les ordres autorises, surveille, protege et remonte."

#include "TradingAgentGuardian.mqh"

//--- identite de ce Guardian : un symbole, un magic, un fichier d'etat.
#define GUARDIAN_SYMBOL     "BTCUSD"
#define GUARDIAN_MAGIC      3031
#define GUARDIAN_DEVIATION  50

input long InpMagic            = GUARDIAN_MAGIC;  // magic des ordres de cet EA
input int  InpReportSeconds    = 2;               // periode du battement de coeur (s)
input int  InpStateStaleSeconds = 30;             // backend muet au-dela: arret local (s)

CTradingAgentGuardian Guardian;

//+------------------------------------------------------------------+
int OnInit()
{
   if(_Symbol != GUARDIAN_SYMBOL)
   {
      Print("BTCUSD_Guardian: cet EA ne s'attache qu'au graphique ",
            GUARDIAN_SYMBOL, " (graphique courant: ", _Symbol, ")");
      return INIT_FAILED;
   }
   if(!Guardian.Init(GUARDIAN_SYMBOL, InpMagic, GUARDIAN_DEVIATION,
                     InpReportSeconds, InpStateStaleSeconds))
      return INIT_FAILED;
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Guardian.Deinit(reason);
}

//+------------------------------------------------------------------+
void OnTimer()
{
   Guardian.OnTimer();
}

//+------------------------------------------------------------------+
void OnTick()
{
   Guardian.OnTick();
}
//+------------------------------------------------------------------+
