# Protocole du pont Python ↔ Expert Advisors Guardian

Ce document est le **contrat exact** entre l'agent Python
(`src/tradingagent/ea/bridge.py`) et les deux Expert Advisors MQL5
(`mt5/Experts/TradingAgent/`). Toute modification d'un champ doit être répercutée ici et
dans `PROTOCOL_VERSION`.

## 1. Principe non négociable

**Un Guardian n'est pas un cerveau.** Il n'ouvre jamais une position de sa propre
initiative, ne calcule aucun signal et ne prend aucune décision de marché. Il fait quatre
choses, et seulement celles-là :

| Fonction | Ce que cela veut dire |
|---|---|
| **Exécution** | il envoie les ordres présents dans `orders[]` du fichier d'état, et rien d'autre |
| **Vérification** | volume, pas de lot, stop obligatoire, exposition, nombre de positions, **puis relecture du stop et de l'objectif sur la position** après exécution |
| **Surveillance / protection** | il compare `positions[]` attendues à l'état réel, bloque les nouveaux ordres sur divergence, referme toute position de son magic sans stop |
| **Remontée** | ordre, exécution, prix, volume, SL, TP, résultat, slippage, erreurs et état de connexion, dans un rapport et un journal |

Le seul chemin par lequel un ordre peut partir est `orders[]`. Aucun autre code de l'EA
n'appelle une fonction d'envoi d'ordre.

## 2. Où vivent les fichiers

Les EAs écrivent dans le bac à sable de fichiers du terminal, donc sous le **dossier de
données** du terminal :

```
<données terminal>\MQL5\Files\TradingAgent\
├── state\      <SYMBOLE>_state.json     écrit par Python
├── reports\    <SYMBOLE>_report.json    écrit par l'EA (battement de cœur + état observé)
│               <SYMBOLE>_events.jsonl   écrit par l'EA (journal en ajout seul)
└── control\    <SYMBOLE>_applied.txt    ordres déjà appliqués (idempotence, survit au redémarrage)
                <SYMBOLE>_halt.txt       arrêt local persistant (kill switch local)
```

Sur ce poste, le dossier de données du terminal est :

```
C:\Users\<utilisateur>\AppData\Roaming\MetaQuotes\Terminal\<instance>\MQL5\Files\TradingAgent
```

Il **ne** se déduit pas de `MT5_TERMINAL_PATH` : un terminal installé dans
`C:\Program Files\...` garde ses données sous `%APPDATA%\MetaQuotes\Terminal\<instance>\`
(le seul chemin que MetaEditor connaisse vraiment). L'instance exacte se lit dans
MetaEditor : `Fichier` > `Ouvrir le dossier de données`, ou en compilant n'importe quel
fichier — le journal de compilation cite le chemin des `Include`.

Côté Python, le répertoire racine du pont est un **paramètre** (`directory`) : c'est
l'appelant qui passe `...\MQL5\Files\TradingAgent`. Le paquet `tradingagent.ea` ne
devine jamais ce chemin.

## 3. Fichier d'état (`state/<SYMBOLE>_state.json`)

Écrit par `publish_state(...)`, lu par l'EA. **Écriture atomique** : fichier temporaire
dans le même répertoire puis remplacement, donc jamais de document à moitié écrit.

| Champ | Type | Sens |
|---|---|---|
| `protocol_version` | entier | version du contrat, actuellement `1`. Un EA qui ne la connaît pas s'arrête localement |
| `symbol` | chaîne | le symbole de ce Guardian |
| `magic` | entier | magic des ordres (`3031` pour ce projet) |
| `revision` | entier | incrémenté à chaque publication ; l'EA le rappelle dans son rapport |
| `published_at` | chaîne ISO-8601 UTC | horodatage lisible par un humain et par Python |
| `published_epoch` | entier | le même instant en epoch Unix : c'est ce que MQL5 lit |
| `heartbeat_timeout_seconds` | nombre | tolérance affichée côté agent (information) |
| `kill_switch` | booléen | `true` : plus aucun nouvel ordre, immédiatement. Non persistant : il retombe quand le backend republie `false` |
| `limits.max_positions` | entier | nombre maximal de positions simultanées de ce magic |
| `limits.max_total_volume` | nombre ou `null` | exposition maximale cumulée, en lots (`null` ou `0` = pas de plafond) |
| `positions[]` | tableau | **état attendu** : ce que le backend croit ouvert, avec le stop qu'il attend |
| `orders[]` | tableau | **ordres autorisés** : la seule source d'ordres de l'EA |

### `positions[]` — l'état attendu

| Champ | Type | Sens |
|---|---|---|
| `ticket` | entier | ticket de la position tel que le backend le connaît. **`0` = « je veux cette position mais je n'en connais pas encore le ticket »** : la surveillance ignore cette ligne, les deux côtés ne peuvent pas la comparer |
| `direction` | `"BUY"` \| `"SELL"` | sens attendu |
| `volume` | nombre | volume attendu, en lots |
| `stop_loss` | nombre ou `null` | stop attendu (`null` = aucun stop attendu) |
| `take_profit` | nombre ou `null` | objectif attendu |
| `comment` | chaîne | commentaire posé à l'ouverture |

**Ordre de publication.** `positions[]` ne décrit que ce que le backend sait **déjà**
ouvert, donc avec un ticket réel. Une position fraîchement autorisée n'entre dans
`positions[]` qu'**après** le rapport d'exécution qui en donne le ticket : la publier avant
serait une divergence annoncée. En attendant, on publie `"ticket": 0`, ou rien du tout.

### `orders[]` — les ordres autorisés

| Champ | Type | Sens |
|---|---|---|
| `id` | chaîne | identifiant **stable** de l'ordre, unique. C'est la clé d'idempotence |
| `action` | `"OPEN"` \| `"CLOSE"` | seule alternative autorisée |
| `direction` | `"BUY"` \| `"SELL"` | sens de la position visée |
| `volume` | nombre | volume en lots |
| `stop_loss` | nombre ou `null` | **obligatoire pour `OPEN`** (RM-004) : sans stop, l'ordre est refusé |
| `take_profit` | nombre ou `null` | objectif, optionnel |
| `comment` | chaîne | commentaire MT5, tronqué à 31 caractères par l'EA. Sert aussi à reconnaître un ordre déjà arrivé au serveur |
| `position_ticket` | entier ou `null` | pour `CLOSE` : la position à fermer. Si `null`, l'EA cherche par `comment` |

### Exemple réel

```json
{
  "protocol_version": 1,
  "symbol": "BTCUSD",
  "magic": 3031,
  "revision": 1,
  "published_at": "2026-10-07T06:00:00+00:00",
  "published_epoch": 1791352800,
  "heartbeat_timeout_seconds": 15.0,
  "kill_switch": false,
  "limits": {
    "max_positions": 1,
    "max_total_volume": 0.05
  },
  "positions": [
    {
      "ticket": 5001,
      "direction": "BUY",
      "volume": 0.01,
      "stop_loss": 62000.0,
      "take_profit": 65000.0,
      "comment": "ta-0123456789abcdef"
    }
  ],
  "orders": [
    {
      "id": "open-1",
      "action": "OPEN",
      "direction": "BUY",
      "volume": 0.01,
      "stop_loss": 62000.0,
      "take_profit": 65000.0,
      "comment": "ta-0123456789abcdef",
      "position_ticket": null
    }
  ]
}
```

> **ASCII uniquement.** MQL5 n'a pas de mode texte UTF-8 : le fichier est écrit en ASCII
> pur, tout caractère hors ASCII voyage en échappement `\uXXXX`. Ne pas passer
> `ensure_ascii=False` dans `publish_state` sans changer le mode d'ouverture côté MQL5.

## 4. Rapport (`reports/<SYMBOLE>_report.json`)

Écrit par l'EA à chaque battement de cœur, **écriture atomique** (temporaire puis
remplacement). Lu par `read_reports(...)` et `ea_health(...)`.

| Champ | Type | Sens |
|---|---|---|
| `protocol_version`, `ea_version` | entier, chaîne | versions du contrat et de l'EA |
| `symbol`, `chart_symbol` | chaîne | le symbole de ce Guardian et celui du graphique |
| `magic` | entier | magic utilisé |
| `updated_at` | chaîne ISO-8601 UTC | **le battement de cœur** : c'est lui qui décide ONLINE/OFFLINE |
| `server_time` | chaîne ISO-8601 | heure du serveur du courtier, pour diagnostic |
| `applied_revision` | entier | dernière `revision` du fichier d'état effectivement lue ; `-1` si aucun état n'a jamais été lu |
| `state_age_seconds` | nombre | âge du dernier état lu ; `-1` si inconnu |
| `connected` | booléen | terminal connecté |
| `trade_allowed` | booléen | autorisation de trading (Algo Trading, compte, symbole) |
| `symbol_tradable` | booléen | le symbole accepte le trading complet |
| `kill_switch` | booléen | kill switch reçu du backend |
| `local_halt` | booléen | arrêt local de l'EA, persistant |
| `halt_reason` | chaîne | motif de l'arrêt local, vide sinon |
| `last_event_seq` | entier | numéro du dernier événement journalisé |
| `account` | objet | `login`, `server`, `company`, `currency`, `demo`, `build` |
| `positions[]` | tableau | positions **réellement ouvertes** pour ce symbole et ce magic |
| `counters` | objet | `orders_sent`, `orders_refused`, `divergences`, `stops_protected`, `errors` |
| `events[]` | tableau | les 25 derniers événements |

`positions[]` reprend `ticket`, `symbol`, `direction`, `volume`, `price_open`,
`stop_loss`, `take_profit`, `profit`, `comment`.

```json
{
  "protocol_version": 1,
  "ea_version": "1.0.0",
  "symbol": "BTCUSD",
  "magic": 3031,
  "chart_symbol": "BTCUSD",
  "updated_at": "2026-10-07T06:00:02Z",
  "server_time": "2026-10-07T06:00:02Z",
  "applied_revision": 1,
  "state_age_seconds": 2.0,
  "connected": true,
  "trade_allowed": true,
  "symbol_tradable": true,
  "kill_switch": false,
  "local_halt": false,
  "halt_reason": "",
  "last_event_seq": 4,
  "account": {
    "login": 201827970,
    "server": "Deriv-Demo",
    "company": "Deriv.com Limited",
    "currency": "EUR",
    "demo": true,
    "build": 6235
  },
  "positions": [],
  "counters": {
    "orders_sent": 0,
    "orders_refused": 0,
    "divergences": 0,
    "stops_protected": 0,
    "errors": 0
  },
  "events": []
}
```

## 5. Journal (`reports/<SYMBOLE>_events.jsonl`)

Le rapport n'en garde que les 25 derniers ; **le journal, lui, n'oublie rien** : un objet
JSON par ligne, en ajout seul. C'est le fichier de remontée à lire pour reconstituer une
séance. `read_events(reports_dir, symbol)` le relit et ignore toute ligne illisible.

Un événement :

| Champ | Type | Sens |
|---|---|---|
| `seq` | entier | numéro croissant, jamais réutilisé |
| `at` | chaîne ISO-8601 UTC | instant de l'événement (`TimeGMT`) |
| `kind` | chaîne | nature, voir ci-dessous |
| `severity` | `INFO` \| `WARNING` \| `CRITICAL` | gravité |
| `ticket` | entier | ticket concerné, `0` si aucun |
| `message` | chaîne | phrase lisible par l'opérateur |
| `data` | objet | charge utile : `requested_price`, `executed_price`, `slippage`, `volume`, `stop_loss`, `take_profit`, `retcode`, `comment`, `order_id`… |

Natures d'événement : `INIT`, `CONNECTION`, `ORDERS`, `EXECUTION`, `ORDER_REFUSED`,
`EXECUTION_FAILED`, `DIVERGENCE`, `PROTECTION`, `KILL_SWITCH`, `STATE`, `ERROR`.

## 6. Fréquences

| Quoi | Cadence | Qui |
|---|---|---|
| Passe complète (lire l'état, exécuter, surveiller, protéger) | **1 seconde** | EA (`OnTimer`, et `OnTick` au même rythme) |
| Écriture du rapport (battement de cœur) | **2 secondes** | EA |
| Écriture du journal | à chaque événement | EA |
| Fraîcheur exigée du fichier d'état | **30 secondes** | EA : au-delà, arrêt local |
| Péremption du battement de cœur | **15 secondes** (`DEFAULT_HEARTBEAT_TIMEOUT_SECONDS`) | `ea_health` / `read_reports` |

Le backend doit donc republier l'état **au moins toutes les 30 secondes**, et plutôt à
chaque cycle de la boucle d'exécution. Republier sans rien changer est utile : la
`revision` change, l'EA sait que le backend est vivant.

## 7. Perte de contact — comportement exact

| Situation | Ce que fait l'EA | Ce que voit l'agent | Comment on repart |
|---|---|---|---|
| **Backend muet** (état jamais rafraîchi depuis plus de 30 s) | **Arrêt local persistant** : plus aucun nouvel ordre. Les positions ouvertes ne sont pas fermées (RM-015) ; la protection des stops reste active | `local_halt: true`, `halt_reason` explicite, `state_age_seconds` qui grimpe | réparer le backend, puis **supprimer `control\<SYMBOLE>_halt.txt`** et redémarrer l'EA |
| **Fichier d'état disparu ou illisible** après avoir été lu | même arrêt local | `local_halt: true` | idem |
| **Aucun état encore publié** (première installation) | l'EA reste simplement inactif : il n'a aucun ordre à exécuter, donc rien à faire | `applied_revision: -1` | publier un état |
| **Terminal déconnecté** | aucun ordre n'est envoyé ; les ordres ne sont **pas** marqués appliqués, ils repartiront à la reconnexion | `connected: false` | attendre la reconnexion |
| **Algo Trading désactivé / compte non autorisé** | aucun ordre n'est envoyé ; ordre signalé une fois en `ORDER_REFUSED` | `trade_allowed: false` | activer Algo Trading |
| **Fichier d'état corrompu / rapport corrompu** | l'EA s'arrête localement pour un état corrompu ; côté Python, un rapport corrompu est ignoré sans exception | `ea_health` : **OFFLINE**, jamais d'exception | réparer, republier |
| **Battement de cœur périmé** (> 15 s) | l'EA peut très bien tourner, mais sa parole ne vaut plus rien | `EaStatus.OFFLINE` | vérifier que l'EA est bien attaché au graphique |
| **Divergence d'état** (attendu ≠ réel) | **arrêt local persistant**, aucune correction automatique (RM-014) ; chaque différence est journalisée | événements `DIVERGENCE`, `local_halt: true` | l'opérateur réconcilie à la main, puis supprime le fichier d'arrêt |
| **Stop absent sur une position** | **fermeture immédiate** de la position, en `CRITICAL`, et ce même quand l'EA est arrêté | événement `PROTECTION` | — |
| **Objectif (TP) absent ou décalé après exécution** | simple `WARNING` : un objectif manquant n'expose aucun argent, il ne justifie pas une fermeture | événement `PROTECTION` en `WARNING` | corriger le backend, ou constater que le courtier a refusé le TP |

**Repartir d'un arrêt local** : le cas dépend du **motif**, et il n'y en a qu'un qui se
répare tout seul.

| Motif de l'arrêt | Qui le lève |
|---|---|
| `backend silencieux depuis N s` | **l'EA lui-même**, dès que le battement de cœur repasse sous la limite |
| `divergence d'etat…` (RM-014) | l'opérateur, après réconciliation à la main |
| `kill switch publie par le backend` | le backend, en republiant `kill_switch: false` |

L'EA relit `control\<SYMBOLE>_halt.txt` au démarrage et **reste arrêté** tant que ce fichier
existe. Les deux derniers motifs restent des verrous d'opérateur : une divergence doit être
comprise, et un kill switch appartient à celui qui l'a posé.

**Pourquoi le silence se lève seul, et pas les autres.** Du 2026-10-08, mesuré sur 60 s :
l'âge de l'état oscillait entre 4 et 16 s (jamais près de la limite de 30 s), le backend
republiait normalement, et `local_halt` restait vrai avec `orders_sent: 0`. L'arrêt armé
pendant une coupure ne se relevait jamais, donc **plus aucun ordre ne pouvait partir alors
qu'aucune condition de sécurité n'était remplie**. Un filet de sécurité qui ne se relâche
pas devient un blocage qu'on finit par contourner à la main, ce qui est plus dangereux que
le défaut d'origine. Le retour de la liaison, lui, est une chose que l'EA constate par
lui-même — c'est la seule condition qu'il peut vérifier sans aide.

## 8. Idempotence

Un ordre ne part **jamais** deux fois :

1. l'EA tient la liste des `id` déjà appliqués, persistée dans
   `control\<SYMBOLE>_applied.txt` et rechargée au démarrage ;
2. avant tout envoi, il cherche une position portant le même commentaire : si elle existe,
   l'ordre est **adopté** et l'envoi est abandonné ;
3. un ordre refusé par le serveur est marqué appliqué : c'est au backend de décider d'un
   nouvel ordre, avec un nouvel `id`. On ne renvoie pas — c'est la même règle que
   `MT5Broker` côté Python (« reconcile, never resend »).

Deux exceptions, volontaires et opposées :

- un ordre **bloqué avant tout envoi** (plafond de positions, exposition, volume hors pas,
  terminal déconnecté, aucune cotation) n'est **pas** marqué appliqué : il repart dès que
  la condition disparaît. L'événement n'est écrit qu'une fois, pour ne pas noyer le journal.
- une **fermeture** dont la position est introuvable à l'instant T n'est pas marquée
  appliquée non plus : la position est peut-être simplement invisible une seconde. Si le
  backend n'a plus rien à fermer, il retire l'ordre de `orders[]` et tout s'arrête.

Le commentaire est la clé de reconnaissance (limite MT5 : 31 caractères). Côté Python,
`key_comment(idempotency_key)` produit déjà `ta-<16 hex>`.

## 9. Écriture atomique, des deux côtés

- **Python** : `tempfile.mkstemp` dans le répertoire cible, `flush` + `fsync`, puis
  `os.replace`. Un échec de remplacement laisse le document précédent intact et nettoie le
  temporaire.
- **MQL5** : `FileOpen("<chemin>.tmp", ...)`, écriture, fermeture, puis
  `FileMove(tmp, 0, cible, FILE_REWRITE)`.

Un lecteur voit donc toujours un document complet, ancien ou nouveau.

## 10. Ce qu'un Guardian ne fera jamais

- ouvrir une position sans ordre dans `orders[]` ;
- ouvrir sans stop-loss (RM-004) ;
- desserrer un stop, augmenter une taille, changer de sens ;
- corriger une divergence tout seul (RM-014) ;
- fermer une position parce que le kill switch est actif (RM-015) — la seule fermeture
  automatique est celle d'une position **privée de son stop** ;
- lever un arrêt local sans intervention de l'opérateur ;
- envoyer deux fois le même ordre.
