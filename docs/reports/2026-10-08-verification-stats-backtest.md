# Vérification indépendante des statistiques de backtest — XAUUSD / BTCUSD

- **Vérificateur** : `verificateur-stats` (task-3), rôle contradicteur.
- **Date** : 2026-10-08, 07:30 → 07:36 UTC+2.
- **Livrables vérifiés** : `docs/research/stats/XAUUSD-witness@1.1.1.json`,
  `docs/research/stats/BTCUSD-trend_breakout@1.0.1.json` (non modifiés, SHA256 identiques
  avant/après — voir § Intégrité).
- **Contraintes tenues** : lecture seule sur la production, aucun fichier source ni JSON
  modifié, aucune suite de tests complète, aucune promotion. Un seul fichier écrit dans le
  dépôt : ce rapport.

## Verdict global

| # | Point de contrôle | Verdict |
|---|---|---|
| 1 | Identité (JSON ↔ `agent.yaml` ↔ manifestes ↔ jeu gelé) | **CONFORME** (2 défauts de forme signalés) |
| 2 | Reproduction par un second chemin (`improve.py --no-record`) | **CONFORME** — écart nul à la précision pleine |
| 3 | Plomberie (SIGNAL, 1 position, coûts, `config/strategies/`, `--no-record`) | **CONFORME** (2 réserves nommées) |
| 4 | Cohérence interne arithmétique | **CONFORME** |
| a | Arbitrage `walk_forward` 0/0 + 45 plis sautés | **Conflit plan ↔ `history_bars`**, pas un manque de données |
| b | Arbitrage chiffres des bandeaux de dérogation | **EXPLIQUÉ** (provenance prouvée) + 1 résidu **NON EXPLIQUÉ** |

Les repères du Lead sont **confirmés** : XAUUSD **-7,09**, BTCUSD **-42,35**.

---

## 1. Identité — CONFORME

Commande (script de contrôle en lecture seule, hors dépôt : `%TEMP%\dsh-verify-stats\check_identity.py`) :

```
.\.venv\Scripts\python.exe "$env:TEMP\dsh-verify-stats\check_identity.py"
```

Extrait :

```
config/agent.yaml -> strategy par marche : {'XAUUSD': 'witness@1.1.1', 'BTCUSD': 'trend_breakout@1.0.1'}

[XAUUSD] XAUUSD-witness@1.1.1.json   <->   config/strategies/witness@1.1.1.yaml
  OK  ref == agent.yaml[market]                JSON='witness@1.1.1'  manifest='witness@1.1.1'
  OK  JSON.strategy_id == manifest.strategy_id JSON='witness'  manifest='witness'
  OK  JSON.version == manifest.version         JSON='1.1.1'  manifest='1.1.1'
  OK  JSON.max_mode == manifest.max_mode       JSON='DEMO'  manifest='DEMO'
  OK  timeframe dans manifest.timeframes       JSON='M15'  manifest=['M15']
  OK  dictionnaire identique, 6 cle(s) : ['atr_period','ema_fast','ema_slow','entry_zone_atr','stop_atr_multiplier','take_profit_rr']
```

Idem BTCUSD (`trend_breakout` / `1.0.1` / `DEMO` / `M15`, 6 paramètres identiques).
Le jeu gelé a été recoupé à la source (en-tête du `.jsonl`) :

```
[XAUUSD] dataset_id JSON=mt5-XAUUSD-2026-10-08   source=mt5-XAUUSD-2026-10-08   OK
         fingerprint OK   bars OK (11999)   window_start OK   window_end OK
         premier close 4660.23 -> spread recalcule=0.233011 (JSON=0.233011 OK)
                                  slippage recalcule=0.093205 (JSON=0.093205 OK)
[BTCUSD] dataset_id OK   fingerprint OK   bars OK (11999)   window_start/end OK
         premier close 63637.76 -> spread recalcule=3.181888 (JSON=3.181888 OK)
                                   slippage recalcule=1.272755 (JSON=1.272755 OK)
```

Les coûts déclarés sont donc **exactement** ceux que `config_for` recalcule depuis le premier
close, et l'empreinte déclarée est bien celle du fichier.

**Défauts signalés (forme), conformément au mandat « toute divergence, même de forme » :**

| Réf | Défaut | Preuve |
|---|---|---|
| D1 | Chaque manifeste se contredit : l'en-tête déclare la dérogation `max_mode: DEMO`, le commentaire de queue affirme encore « max_mode stays SIGNAL until a strategy is validated out of sample and promoted through TASK-065 (RM-016) ». | `witness@1.1.1.yaml:1-18` vs `:20-22` ; `trend_breakout@1.0.1.yaml:1-18` vs `:20-22` |
| D2 | Le JSON ne porte ni `history_bars`, ni `expiry_bars`, ni `allowed_symbols`, ni `timeframes`, ni `ai_filter`. Il ne peut donc pas justifier son propre verdict `walk_forward` : le champ décisif (`history_bars`) est absent. | contrôle ci-dessus, lignes « ABSENT du JSON » |

---

## 2. Reproduction par un second chemin — CONFORME (écart nul)

**Déviation assumée par rapport au mandat** : j'ai ajouté `--output` vers un répertoire
temporaire hors dépôt. Raison prouvée : `--no-record` supprime l'écriture en base **mais pas**
l'écriture sur disque — `improve.py:687` appelle `write_attempts`, qui écrit 1 journal +
1 manifeste candidat par marché. Le répertoire par défaut (`docs/research/candidates/`) contient
déjà les artefacts d'un passage antérieur (05:09 / 05:11 UTC) ; y écrire aurait violé la
contrainte « un seul fichier écrit ». Ce détour **ne peut pas** changer la mesure : l'objectif est
calculé dans `run_market` avant `write_attempts`, et la preuve ci-dessous montre que mes journaux
reproduisent les journaux de production **octet pour octet**.

Commande exacte lancée :

```
$out = Join-Path $env:TEMP 'dsh-verify-stats\candidates'
uv run --frozen python scripts/backtest/improve.py --no-record --output $out
```

Sortie (exit code 0, ≈5 min) :

```
== BTCUSD : amélioration ==
  version en place : trend_breakout@1.0.1 (objectif -42.35)
== XAUUSD : amélioration ==
  version en place : witness@1.1.1 (objectif -7.09)

== Bilan ==
  marchés améliorés : 2 / 2
```

Comparaison au centime avec `result.cost_net.net_profit` :

| Marché | `improve.py` (affichage 2 déc.) | JSON `result.cost_net.net_profit` | Écart à l'affichage | Écart pleine précision |
|---|---|---|---|---|
| XAUUSD | `-7.09` | `-7.08649754359` | 0,00350245641 € (arrondi au centime identique) | **0** |
| BTCUSD | `-42.35` | `-42.35485000941` | 0,00485000941 € (arrondi au centime identique) | **0** |

L'affichage est arrondi à 2 décimales (`improvement.py:243-244`), donc insuffisant seul pour
trancher au centime. La comparaison **pleine précision** est fournie par le champ
`incumbent_objective` des journaux de recherche, que le script écrit sans arrondi :

```
C:\...\dsh-verify-stats\candidates\XAUUSD-witness@1.1.1.json
  "incumbent_objective": -7.08649754359        <- identique au JSON, 11 decimales
C:\...\dsh-verify-stats\candidates\BTCUSD-trend_breakout@1.0.1.json
  "incumbent_objective": -42.35485000941       <- identique au JSON, 11 decimales
```

**Double reproduction indépendante.** Deux passages distincts d'`improve.py` (05:09/05:11 UTC par
un autre agent, 05:33/05:35 UTC par moi) donnent les mêmes valeurs, et les journaux sont
identiques hors horodatage :

```
docs\research\candidates\XAUUSD-witness@1.1.1.json   "incumbent_objective": -7.08649754359
docs\research\candidates\BTCUSD-trend_breakout@1.0.1.json  "incumbent_objective": -42.35485000941

witness-1.1.2.yaml          prod=5A6A60A12425D288  monrun=5A6A60A12425D288  identiques=True
trend_breakout-1.0.2.yaml   prod=7C0B3D629AA08FC1  monrun=7C0B3D629AA08FC1  identiques=True
```

Écart non nul : **aucun**. Ni jeu différent, ni fenêtre différente, ni coûts différents — c'est
le même `run_campaign`, le même jeu gelé, le même modèle de coûts, et les deux nombres tombent
au chiffre près.

---

## 3. Plomberie — CONFORME

| Sous-contrôle | Verdict | Preuve |
|---|---|---|
| Mode SIGNAL (aucune exécution) | CONFORME | `TradingMode.SIGNAL` dans les trois `config_for` (expression AST identique) ; `backtest/harness.py` n'importe **ni** `tradingagent.execution` **ni** `tradingagent.app` (liste d'imports lue) — le harnais ne peut pas router un ordre |
| Une seule position à la fois | CONFORME | `max_concurrent_positions=1` dans les trois `config_for` ; appliqué par `harness.py:233` (`len(pending) + len(open_positions) >= config.max_concurrent_positions`) |
| Coûts conformes à `run_campaign.py` | CONFORME | comparaison AST des 5 expressions décisives : `round(price * 0.00005, 6)`, `round(price * 0.00002, 6)`, `Decimal("0.5")`, `TradingMode.SIGNAL`, `1` — **identiques dans les trois scripts** (les corps diffèrent seulement de forme : `run_campaign.py` hisse le `CostModel` dans une variable locale) |
| Aucun fichier écrit sous `config/strategies/` | CONFORME | SHA256 des 5 fichiers `config/strategies/*.yaml` + `agent.yaml` identiques avant/après ; `LastWriteTime` inchangés ; `git status --porcelain` identique ; de plus `versioning.py:148-153` refuse explicitement ce répertoire |
| `--no-record` n'écrit rien en base | CONFORME | `improve.py:656-664` : `engine = None` ⇒ `engine_from_environment()` jamais appelé ; toute écriture est gardée par `self._engine is not None` (`improvement_cycle.py:445` et `:486`) et `if engine is not None` (`improve.py:560`) ; **mesure** : `SELECT count(*) FROM backtest_runs` = **2 avant**, **2 après** le run |

Extrait de la mesure en base (SELECT en lecture seule) :

```
LIGNES backtest_runs = 2
  id=2 market=XAUUSD ref=witness@1.1.2  dataset=mt5-XAUUSD-2026-10-08 created=2026-10-08 05:05:59
    objective=21.51250164581 comparisons=3.0
  id=1 market=BTCUSD ref=trend_breakout@1.0.2 dataset=mt5-BTCUSD-2026-10-08 created=2026-10-08 05:05:59
    objective=-33.5212985397 comparisons=1.0
```

Les 2 lignes préexistent au run (05:05:59) et portent sur les **candidats**, pas sur les versions
en place : aucun enregistrement nouveau n'a été créé par mon passage en `--no-record`.

**Réserves nommées (franchement, elles ne sont pas des conformités) :**

- **R1 — `--no-record` n'écrit pas « rien ».** Il écrit quand même 4 fichiers sur disque
  (2 journaux + 2 manifestes candidats, 4/4 de tailles identiques à ceux de production). Sa
  docstring (« measure without touching the DB ») est exacte ; « n'écrit rien » serait faux.
- **R2 — garde-fou dépendant du répertoire courant (lecture de code, non testé).**
  `versioning.py:148` construit le chemin interdit en **relatif** : `(Path("config") / "strategies").resolve()`.
  Lancé depuis la racine du dépôt (invocation documentée), le refus est exact. Lancé depuis un
  autre répertoire, la comparaison porterait sur le mauvais chemin absolu et le refus ne se
  déclencherait pas. Je n'ai pas exécuté ce cas (il faudrait écrire un fichier) : c'est une
  observation de lecture, pas un défaut mesuré.

---

## 4. Cohérence interne — CONFORME

Commande (script de contrôle en lecture seule) :
`.\.venv\Scripts\python.exe "$env:TEMP\dsh-verify-stats\check_arithmetic.py"`

| Marché | Fenêtre | gagnantes+perdantes=op. | net = gain+perte | PF>1 ⟺ net>0 | espérance = net/op. |
|---|---|---|---|---|---|
| XAUUSD | train (106) | oui | oui | oui | oui |
| XAUUSD | validation (38) | oui | oui | oui | oui |
| XAUUSD | cost_net (38) | oui | oui | oui | oui |
| BTCUSD | train (209) | oui | oui | oui | oui |
| BTCUSD | validation (47) | oui | oui | oui | oui |
| BTCUSD | cost_net (47) | oui | oui | oui | oui |

Écarts maximaux entre valeur déclarée et valeur recalculée (arrondis à 6 décimales du
producteur) : facteur de profit **3,7e-07**, espérance **2,2e-07**, `win_rate` **exact**,
gains/pertes moyens **exacts**. Exemples :

```
[XAUUSD] cost_net  net=-7.08649754359  gain=263.3359406274  perte=-270.42243817099
                   PF json=0.973795 recalcule=0.973795 ecart=2.87e-07
[BTCUSD] cost_net  net=-42.35485000941 gain=288.31488199405 perte=-330.66973200346
                   PF json=0.871912 recalcule=0.871912 ecart=7.67e-08
```

Aucune incohérence arithmétique. `validation` et `cost_net` sont identiques — c'est le
comportement voulu (`campaign.py:537-540` : `cost_net` **est** le run de validation sous coûts
facturés 1×), et le run stressé à 2× est bien un chiffre distinct (`stress.net_profit`
-39,264 / -71,824).

---

## a. Arbitrage : `walk_forward` 0/0 avec 45 plis sautés — **conflit plan ↔ `history_bars`**

**Ce n'est pas un manque de données.** La chaîne est arithmétiquement close :

| Étape | Valeur | Source |
|---|---|---|
| Plan de walk-forward par défaut | `train_bars=350, validation_bars=250, step_bars=200` → **taille de pli 600** | `campaign.py:88` |
| Fenêtre roulante = train + validation | `int(11999×0.6)=7199` + `int(11999×0.2)=2399` = **9598** | `campaign.py:413` ; le JSON porte `rolling_bars: 9598` |
| Plis générés | `floor((9598−600)/200)+1` = **45** | `protocol.py:272` ; les deux JSON portent `skipped_folds: 45` |
| Bloc de validation par pli | **250 bougies** | `protocol.py:279` |
| Refus du harnais | `history_bars−1 >= 250`, soit `history_bars >= 251` | `harness.py:160-164` |
| `witness@1.1.1` | `history_bars=300` → 299 ≥ 250 → **refusé** | `config/strategies/witness@1.1.1.yaml:29` |
| `trend_breakout@1.0.1` | `history_bars=600` → 599 ≥ 250 → **refusé** | `config/strategies/trend_breakout@1.0.1.yaml:30` |

Les 45 plis sont donc **bien générés** (d'où un compteur identique pour les deux stratégies :
il ne dépend que du plan et de la fenêtre, pas de `history_bars`) et **tous refusés** parce que
leur bloc de validation de 250 bougies ne peut pas alimenter la fenêtre de chauffe déclarée par
le manifeste.

**Contre-preuve décisive — la donnée est largement suffisante.** La campagne du 2026-10-08 a joué
**45 plis** sur la **même bande** (11 999 bougies, même découpage, même plan), avec un manifeste
déclarant `history_bars: 100` (`run_campaign.py:92-103`), donc sous le seuil de 251 :

```
docs\research\2026-10-08-campaign.json
  BTCUSD fast-1.5R : "folds": 45, "profitable_folds": 21, "ratio": 0.4667
  XAUUSD fast-1.5R : "folds": 45, "profitable_folds": 26, "ratio": 0.5778
```

Conclusion : la cause est le `validation_bars=250` du plan par défaut, incompatible avec les
`history_bars` (300 / 600) des manifestes déployés. **Défaut de formulation à corriger** : le
motif `"all 45 fold(s) held fewer bars than the manifest's declared history"` est ambigu — un
pli compte 600 bougies, ce n'est pas le pli qui est trop court mais son bloc de validation
(250) relativement à `history_bars`. C'est précisément ce qui a fait chercher un manque de
données là où il n'y en a pas.

---

## b. Arbitrage : chiffres des bandeaux de dérogation — **EXPLIQUÉ**, un résidu non expliqué

**Les quatre chiffres sont retrouvés à l'identique dans `docs/research/2026-10-08-campaign.json`** :

| Chiffre du bandeau | Valeur exacte trouvée | Où | Candidat concerné |
|---|---|---|---|
| PF 1× **1,04** (or) | `1.0400490455874667` | `:1597` | `XAUUSD:fast-1.5R` |
| PF 1× **0,92** (BTC) | `0.915718432082609` | `:724` | `BTCUSD:fast-1.5R` |
| PF 2× **0,90** (or) | `0.901626406884966` | `:1627` | `XAUUSD:fast-1.5R` (`stressed`) |
| PF 2× **0,81** (BTC) | `0.8121974704824612` | `:754` | `BTCUSD:fast-1.5R` (`stressed`) |
| p-value min **0,3497** | `0.34965034965034963` | `:449` | `XAUUSD:slow-2.5R` |
| Bonferroni 0,016667 | `0.016666666666666666`, `hypotheses: 6`, `discoveries_after: 0` | `:420-424` | 3 candidats × 2 marchés |

**Cause nommée et prouvée** : le bandeau cite les **candidats de la campagne** (`fast-1.5R`,
`slow-2.5R`), pas les **versions en place**. `current_stats.py` et `improve.py` mesurent ce que
`config/agent.yaml` désigne (`witness@1.1.1`, `trend_breakout@1.0.1`). Objets mesurés
différents ⇒ chiffres différents. Rien d'incohérent, mais le libellé du bandeau
(« profit factor net (1x couts) mesuré 0,92 (BTC) et 1,04 (or) ») se lit comme une affirmation
sur les stratégies déployées, ce qu'il n'est pas. **À reformuler.**

**Résidu NON EXPLIQUÉ, et je ne l'invente pas.** Même à paramètres identiques, les deux chemins
divergent :

| Objet | Paramètres | `history_bars` / `expiry_bars` | `cost_net.profit_factor` XAUUSD |
|---|---|---|---|
| Campagne `balanced-2R` | ema 20/50, atr 14, stop 1,5, rr 2,0, zone 0,1 | 100 / 2 | **0.7988743441094445** (`:1810`) |
| Déployé `witness@1.1.1` | **identiques** (contrôle §1) | 300 / 1 | **0.973795** |

Les deux manifestes ne diffèrent que par `history_bars` (100 → 300, donc `evaluation_start`
99 → 299) et `expiry_bars` (2 → 1), tous deux non neutres sur un backtest. Je **nomme** ces deux
causes possibles mais je n'ai **pas isolé** laquelle porte l'écart de PF : cela demanderait une
campagne croisant les deux manifestes, que je n'ai pas lancée. Cette divergence-là reste
**NON EXPLIQUÉE**.

---

## Ce que je n'ai pas pu vérifier

1. **L'isolation de la cause** de l'écart `balanced-2R` (PF 0,7989) vs `witness@1.1.1`
   (PF 0,9738) — hypothèses nommées (`history_bars`, `expiry_bars`), non départagées.
2. **La garde anti-`config/strategies/` depuis un autre répertoire courant** (R2) : lue dans le
   code, non exécutée — la tester supposait d'écrire un fichier.
3. **L'absence d'exécution d'ordre au-delà de la lecture de code** : je constate que
   `harness.py` n'importe pas `execution` et que le mode est `SIGNAL`, mais je n'ai pas
   instrumenté le processus pour prouver qu'aucun appel réseau/ordre n'a eu lieu.
4. **Le contenu de la base avant 05:05:59 UTC** : mon instantané « avant » (2 lignes) est pris
   en cours de run ; il borne la fenêtre d'écriture mais ne dit rien de l'historique antérieur.
   Aucune ligne n'a été ajoutée par mon passage.
5. **Les portes `paper` et `risk`**, `not_evaluable` par construction dans les deux JSON : un
   backtest ne peut pas les produire. Non vérifiables ici, et les JSON le déclarent.
6. **Les autres fichiers de `docs/research/stats/`** : je n'ai vérifié que les deux livrables du
   mandat.

## Intégrité de la production (avant / après, SHA256 tronqués à 16)

```
20AA0F320F5D817A  docs\research\stats\XAUUSD-witness@1.1.1.json
7F1DD5698FB49E8B  docs\research\stats\BTCUSD-trend_breakout@1.0.1.json
E3403F39B52A8CBE  config\strategies\witness@1.1.1.yaml
25444A3B30903A01  config\strategies\trend_breakout@1.0.1.yaml
4DECB62E4EDC9325  config\agent.yaml
```

Identiques avant et après le run ; `git status --porcelain` identique ; `LastWriteTime` de
`config/strategies/` inchangés ; `docs/research/candidates/` non touché (mtimes 07:09/07:11
conservés). Les 4 fichiers produits par mon passage ont été écrits **hors dépôt**, sous
`%TEMP%\dsh-verify-stats\`.
