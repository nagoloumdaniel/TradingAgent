# État de l'agent démo au 2026-10-08 — ce qui était cassé, ce qui est corrigé, ce qui reste

**Demande d'origine** : « comment tu n'arrives pas à rendre ce robot fonctionnel ? Je veux qu'il
fasse du scalping avec le moindre gain, peu importe la stratégie, je veux juste les petits gains
très répétitifs […] qu'on puisse commencer à travailler avec mon compte démo. »

**Réponse en une phrase** : le robot **tourne** sur le compte démo, trois défauts qui l'auraient
arrêté ou fait mentir sont corrigés et vérifiés par un tiers, et la demande de « petits gains très
répétitifs » est **mesurée comme perdante** sur ce courtier, avec les chiffres en §3.

Ce document ne remplace pas les trois rapports détaillés qu'il résume :

| Document | Contenu |
|---|---|
| `docs/reports/2026-10-08-audit-execution.md` | audit adversarial du chemin d'exécution, 7 pistes, 5 correctifs |
| `docs/research/2026-10-08-petits-gains-repetitifs.md` | la mesure de la demande de scalping |
| `docs/reports/2026-10-08-verification-clock-et-execution.md` | vérification indépendante des correctifs |

---

## 1. Ce qui était réellement cassé — constaté, pas supposé

### 1.1 L'agent s'est arrêté deux heures sans le dire (corrigé)

Dans `system_events` : **383 événements CRITICAL `clock_mismatch`**, du 2026-10-07 21:51:54 au
23:59:39 UTC, soit **2 h 08**, tous avec le même motif : `no tick on BTCUSD to verify the server
clock`. Chaque cycle de 20 secondes était abandonné avant la lecture des bougies, avant la
réconciliation, avant le battement des EA, avant les rapports.

Deux causes, toutes deux dans le code :

1. `MarketDataClient.verify_clock()` levait **la même erreur** pour « le serveur a changé
   d'offset » (danger réel, il faut arrêter) et pour « le symbole sonde n'a aucun tick à lire »
   (aucune information, donc aucune conclusion).
2. À la reconnexion, l'horloge était vérifiée **avant** que le symbole sonde ne soit sélectionné.
   Le tick attendu ne pouvait donc jamais arriver : l'agent ne pouvait pas se rétablir seul.

**Corrigé** : `ClockUnverifiableError` distincte de `ClockMismatchError` ; la sonde est
sélectionnée avant d'être lue, avec des relectures bornées ; l'épisode est signalé **une fois**
(avertissement) puis escaladé une fois au-delà de dix minutes ; la collecte des données continue,
seule la décision de trading est suspendue ; la reprise est automatique, sans redémarrage.
Un offset qui change réellement arrête toujours l'agent — vérifié sur les trois chemins
(`connect`, `verify_clock`, `ensure_connected`).

**Mesure** : 5 cycles avant correctif = 5 CRITICAL, 0 bougie collectée, 0 stratégie appelée.
100 cycles après correctif = **2** événements, collecte maintenue, trading suspendu, reprise
automatique.

### 1.2 Le premier stop touché arrêtait l'agent définitivement (corrigé)

`MT5Broker.reconcile()` signalait une **divergence RM-014** pour une position que le courtier
avait simplement fermée sur son stop : la collecte des clôtures ne passait qu'à la bougie M15
close, jusqu'à 15 minutes après le stop. Une divergence déclenche `guardian.on_divergence` → **arrêt
global, que le gardien ne lève jamais tout seul**. Il fallait une intervention manuelle.

**Corrigé** : `reconcile()` collecte les clôtures avant de comparer, et la boucle draine les
clôtures **à chaque cycle**, avant la réconciliation.

### 1.3 Une clôture sur trois était perdue définitivement (corrigé)

`_collect_closures` lisait les deals une fois par ticket et avançait le curseur sur le **maximum du
lot** avant que les autres tickets n'aient été examinés. Sur trois positions fermées à trois
secondes d'intervalle, celle du milieu n'était **plus jamais relue** : position laissée ouverte en
base → divergence → arrêt. Corrigé par une lecture unique du lot et un curseur avancé après examen
complet.

### 1.4 Une position protégée pouvait être fermée au marché (corrigé)

`_stop_present` lisait les positions **une seule fois**. Une lecture périmée (cache du terminal)
faisait conclure « pas de stop » → fermeture immédiate d'une position **protégée**, plus une alerte
critique. Corrigé par une relecture bornée (3 tentatives, 0,25 s).

### 1.5 Un volume illégal pouvait atteindre le terminal (corrigé)

0,015 (hors pas), 0,001 (sous le minimum) et 500 (au-dessus du maximum) étaient transmis à
`order_send` et exécutés. Le dimensionnement du risque, lui, était sain : c'était l'absence de
garde dans la dernière porte avant le terminal. Corrigé : le volume est confronté à la
spécification du courtier avant tout envoi.

### 1.6 Ce que l'opérateur ne voyait pas (corrigé)

- Une clôture faite par le courtier était enregistrée en base mais **n'atteignait ni le message
  Telegram, ni la télémétrie, ni le bucket journalier**. Corrigé : la boucle draine les clôtures
  elle-même à chaque cycle.
- Un **refus du courtier** (code `retcode`) était journalisé et jamais notifié ; l'alerte
  « position ouverte sans stop-loss » de EF-015 n'allait qu'au journal. Corrigé : ces deux cas
  envoient désormais un message à l'opérateur.
- Une exception de l'exécuteur laissait le signal en `ORDER_SENT` — un état qui prétend qu'un ordre
  est en vol — avec seulement une ligne CRITICAL. Corrigé : le signal passe en `ERROR` et le
  message dit honnêtement « aucun ordre confirmé », jamais « refusé ».

---

## 2. Où en est le robot, maintenant

| | Valeur vérifiée |
|---|---|
| Mode | **DEMO** (`TRADING_MODE=DEMO`, `LIVE_TRADING_ENABLED=false`) |
| Compte | démo Deriv, **EUR**, solde ≈ **5 496,67 €** (0,5 % de risque par opération = 27,48 €) |
| Instance | **une seule**, vivante, `uv run tradingagent-run` |
| Connexion | MT5 démo vérifiée de bout en bout : lecture de bougies UTC closes et **ouverture d'un ordre démo avec stop natif, relecture, clôture** (`RUN_MT5_LIVE=1 uv run pytest -m mt5_live -v`, 2 tests verts) |
| Données | bougies XAUUSD et BTCUSD M15 stockées, à jour à la dernière bougie close |
| Positions | **0** ouverte, 0 ordre, 0 exécution, 0 trade |
| Signaux | 2 depuis l'origine : 1 envoyé (or, en mode SIGNAL), 1 refusé par le contrôle de zone d'entrée (BTC) |
| Erreurs d'horloge depuis le redémarrage | **aucune** |

**Pourquoi aucun trade encore ?** Trois raisons factuelles, aucune n'est un défaut :

1. le mode DEMO n'est actif que depuis 02:41 UTC ; auparavant l'agent était en mode SIGNAL, qui
   notifie sans exécuter ;
2. les deux stratégies livrées sont en **M15** : elles ne peuvent produire un signal qu'à la
   clôture d'une bougie de 15 minutes, et elles sont sélectives ;
3. un signal sur deux a été **refusé par le contrôle de zone d'entrée** : le prix était sorti de
   la zone entre la clôture de la bougie et l'évaluation du risque. C'est volontaire — on ne
   poursuit pas le prix — et cela réduit mécaniquement le nombre d'ordres.

**À attendre** : quelques signaux par jour et par marché, pas davantage. Il n'y a rien à réparer
de plus pour que le premier trade arrive ; il faut qu'une configuration se présente aux conditions
de la stratégie.

---

## 3. La demande de « petits gains très répétitifs » — la mesure

Mesuré sur les six jeux gelés du dépôt (11 999 bougies, XAUUSD et BTCUSD en M15, M5, M1) avec le
modèle de coûts réel du projet (spread 0,5 bp, slippage 0,2 bp, commission 0,50 € par opération).
Détail complet et reproductible : `docs/research/2026-10-08-petits-gains-repetitifs.md` §
`uv run python scripts/analysis/scalping_truth.py`.

| Question | Réponse mesurée |
|---|---|
| Combien faudrait-il gagner de plus par opération pour être rentable ? | En **M1 sur l'or : ×4,84** l'espérance brute mesurée (0,0432 → 0,2091 R/op). **×4,10** au dimensionnement réel du compte. M1 BTC : ×1,89 |
| Que devient une suite de petits gains à espérance négative ? | P(compte encore positif) après 100 / 500 / 1000 opérations, M1 or : **0,108 / 0,003 / 0,000** |
| Que fait un doublement de mise après une perte ? | **7 pertes consécutives bloquent** la série (3 517,87 € à engager pour 2 006,28 € restants), **8 l'effacent** ; la probabilité d'une telle suite sur 1000 opérations est **1,0000** ; ruine simulée **97,1 % à 99,9 %**, contre 0,0 % à 12,0 % à mise constante |
| Au-delà de quelle fréquence la vitesse devient-elle destructrice ? | Le coefficient est **négatif** : 0 opération/jour est le seul régime non perdant. Au rythme mesuré, **−127,03 €/jour** en M1 or (−2,31 %/jour) ; le frein de perte quotidienne de 2 % tombe en **0,87 jour de marché** |

**Le mécanisme, en une phrase** : le spread et le slippage sont des distances de prix fixes, alors
que la distance de stop est un multiple d'ATR. En descendant d'unité de temps, l'ATR fond, le stop
fond, et **la même charge en points devient une part de plus en plus grande du risque engagé** :
0,086 R par opération en M15, 0,109 en M5, 0,209 en M1 sur l'or — pendant que le gain brut par
opération, lui, **baisse** (0,077 → 0,048 → 0,043 R).

Deux réserves honnêtes, publiées dans le document : une cellule sur dix-huit est nette-positive
(XAUUSD M15 au dimensionnement réel, +2,11 €/jour) et un candidat sur dix-huit franchit la porte
`costs` — tous deux rejetés par la rétention hors échantillon (0,00) et par la correction du taux
de fausses découvertes (0 survivant sur 6). Autrement dit : il n'y a pas de gain caché à aller
chercher, mais il n'y a pas non plus de démonstration qu'aucune stratégie ne puisse jamais
fonctionner — seulement qu'aucune de celles testées ici, sur ces données, ne le fait.

**Et les stratégies actuellement en démonstration ?** `witness@1.1.1` (or) et
`trend_breakout@1.0.1` (BTC) sont déployées en DEMO **par dérogation opérateur écrite**, sans
validation : la campagne du 2026-10-08 a refusé **6 portes sur 9**, avec une p-value minimale de
0,3497 contre une ligne de Bonferroni à 0,016667. Le mode démo sert donc à **répéter la
mécanique** — exécution, stops, clôtures, notifications, statistiques — pas à démontrer une
rentabilité.

---

## 4. Ce qui reste, et qui n'est pas dans mon périmètre

1. **Aucune tâche planifiée n'est enregistrée.** `Get-ScheduledTask` ne trouve rien : l'agent ne
   survit ni à la fermeture de sa console, ni à un redémarrage de la machine. Pour du 24/7 :
   `pwsh -File scripts/register_service.ps1` **en administrateur** (documenté dans
   `docs/operations/exploitation.md` §1).
2. **Deux EA ne sont pas installés** dans le terminal : `ea_offline` a été enregistré. L'agent
   tourne sans (l'EA est un filet de sécurité local, pas une dépendance), mais le kill switch
   local n'existe pas. Procédure : `docs/ea/README.md`.
3. **Défauts consignés, non corrigés** (détail dans le rapport d'audit) : le contrôle croisé de la
   perte par lot dans `risk/sizing.py` est tautologique (il dérive le taux du chiffre qu'il
   vérifie) ; `runtime/pipeline.py` mesure l'exposition avec un taux de 1 quand aucun stop n'est
   fourni, ce qui majore l'exposition d'environ 8 % en EUR — du côté prudent, mais en silence ; la
   réconciliation ne compare ni `open_price` ni `take_profit` ; une clôture portant un magic
   étranger reste invisible et déclenche une divergence.
4. **Un défaut d'encodage hors console.** Le journal de console perd les messages ornés d'emoji
   quand la sortie est **redirigée** vers un fichier sous Windows (page de code cp1252). Le
   message part quand même sur Telegram ; seule la copie console est perdue. Déclenché uniquement
   par un lancement avec redirection — pas par `uv run tradingagent-run` dans un terminal.
5. **Piège à connaître : ne lancez pas le test réel pendant que l'agent tourne.**
   `tests/data/test_mt5_live.py` construit son courtier avec le **magic number de production**
   (`MT5Broker(terminal, FakeLog(), login=..., mode=TradingMode.DEMO)`, sans magic explicite). La
   position qu'il ouvre une fraction de seconde est donc visible par l'agent, qui peut la lire
   comme « une position au courtier que le registre ne connaît pas » → divergence → **arrêt global
   non levable automatiquement**. Fenêtre étroite (le test referme aussitôt, l'agent compare toutes
   les 20 secondes), mais le risque est réel et la conséquence est un arrêt manuel. La commande
   `RUN_MT5_LIVE=1 uv run pytest -m mt5_live -v` n'a donc **pas** été relancée après les
   correctifs : le chemin d'exécution réel a été exercé par la boucle elle-même (l'agent tourne et
   interroge le terminal), et le chemin nominal l'avait été avant les correctifs, agent déjà en
   marche, sans divergence observée. Correctif propre à prévoir : donner au test un magic dédié.
6. **Rien n'est commité.** Le diff est laissé dans l'arbre de travail pour revue : la convention du
   dépôt est « une branche par tâche, fusion après revue », et cette revue est la vôtre.
7. **Un autre écrivain est actif dans le dépôt.** Pendant cette session, `src/tradingagent/ea/bridge.py`
   et `tests/ea/test_bridge.py` ont été modifiés à 06:40 UTC par une autre main (relecture bornée du
   renommage `os.replace`, que Windows refuse quand l'EA tient le fichier ouvert). Ce changement est
   **postérieur** à la porte finale de 06:38 et **hors** de la vérification indépendante décrite
   ci-dessus : `tests/ea` et `tests/test_architecture.py` passent (119 tests) et `ruff` est propre
   sur ce périmètre, mais la suite complète n'a pas été rejouée après lui. L'agent qui tourne
   (démarré à 06:38:45) a chargé la version d'avant cette modification.

---

## 5. La chaîne qualité, sur l'arbre figé

```text
uv run pytest -q              2181 passed, 9 skipped in 309.74s
uv run ruff check .           All checks passed!
uv run ruff format --check .  367 files already formatted
uv run mypy                   Success: no issues found in 313 source files
```

Les 9 tests sautés le sont par construction : 2 exigent `RUN_MT5_LIVE=1` (terminal réel), 1 est un
verrou PostgreSQL, 6 sont réservés à PostgreSQL. Le socle au début de la session comptait
2157 tests ; les 24 ajoutés sont les tests témoins des défauts corrigés.
