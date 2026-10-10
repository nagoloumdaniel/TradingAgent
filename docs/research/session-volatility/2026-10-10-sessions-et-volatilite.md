# Sessions et volatilité : mesurer avant d'activer

**Date** : 2026-10-10 · **Tâche** : Stream B (TASK partagée `task-2`) · **Nature** : mesure en
lecture seule, puis câblage d'un filtre **laissé désactivé**.

**Question de l'opérateur** : « prends en compte aussi les sessions pour trader uniquement
lorsque le marché est volatile ». Ce document ne répond pas à la question par une opinion : il
la traduit en un tableau mesuré sur données réelles, puis en une décision chiffrée.

> **DÉCISION : AUCUN FILTRE DE SÉANCE OU DE VOLATILITÉ N'EST ACTIVÉ.**
> Sur 1 809 opérations BTCUSD et 978 opérations XAUUSD, l'espérance par opération est négative
> dans **toutes les cases conclusives**, et les meilleures règles de séance/volatilité
> choisies sur l'entraînement s'effondrent en validation (BTC : PF 1,22 → 0,53 ; or :
> PF 1,30 → 0,13). Aucune ne survit à la correction de tests multiples. Le mécanisme est câblé,
> testé, et **désactivé par défaut** ; son activation est une décision d'opérateur qui reste
> ouverte.

---

## 1. Ce qui a été mesuré, sur quoi, et avec quels coûts

| | BTCUSD | XAUUSD |
|---|---|---|
| Stratégie configurée (`config/agent.yaml`) | `trend_breakout@1.0.1` | `witness@1.1.1` |
| Jeu gelé | `docs/research/datasets-volume/BTCUSD-M15-mt5-BTCUSD-M15-2026-10-09.jsonl` | `docs/research/datasets-volume/XAUUSD-M15-mt5-XAUUSD-M15-2026-10-09.jsonl` |
| Bougies | 59 999 (M15) | 59 999 (M15) |
| Empreinte | `2f04c3b23c31…` | `d847b980c680…` |
| Période | 2025-01-21 → 2026-10-09 | 2024-03-25 → 2026-10-09 |
| Coûts | `campaign_costs` du dépôt : spread 2,24 bp du prix, slippage 2 bp, commission 0,50 €/opération, `mode=SIGNAL`, une position à la fois | idem |

Aucun jeu gelé n'a été réécrit. Aucun seuil de `docs/research/thresholds.json` n'a été touché.
Aucune stratégie n'a été promue. Le harnais est `backtest/harness.py` via la voie d'évaluation
partagée `strategies/evaluation.py::evaluate`.

### Le découpage temporel, et ce qui n'a **jamais** été regardé

Découpage du dépôt (`research/protocol.py::split_dataset`, ancré, 60 % / 20 % / 20 %) :

| Fenêtre | BTCUSD | XAUUSD | Bougies |
|---|---|---|---|
| entraînement | 2025-01-21 16:30 → 2026-01-31 20:30 | 2024-03-25 13:00 → 2025-10-02 11:45 | 35 999 |
| validation | 2026-01-31 20:30 → 2026-06-05 23:15 | 2025-10-02 11:45 → 2026-04-08 18:15 | 11 999 |
| **hors échantillon (SCELLÉ)** | **2026-06-05 23:15 → 2026-10-09 01:45** | **2026-04-08 18:15 → 2026-10-09 03:30** | **12 001** |

**Le jeu scellé n'a pas été ouvert.** Le script n'appelle jamais `SealedSet.unlock` : il lit la
fenêtre du sceau (`DataSplit.windows`) pour pouvoir nommer la période réservée, et rien d'autre.
Les 12 001 dernières bougies de chaque marché n'ont donc joué aucun rôle dans la décision.

### Les deux définitions de régime, et pourquoi les deux

* **Terciles du rapport ATR courant / moyenne de l'ATR**, coupures **dérivées de la fenêtre
  d'entraînement seule** puis appliquées telles quelles à la validation : BTC 0,9626 / 1,0624 ;
  or 0,9523 / 1,0117. Recalculer les coupures par fenêtre en aurait fait une fonction du futur.
* **Régimes nommés du dépôt** (`indicators/regime.py`) : calme < 0,7, agité > 1,5, sinon médian.
  Cette lecture est publiée pour être honnête sur un point : sur ces deux marchés, elle ne
  sépare **presque rien**. Le rapport ATR/moyenne est borné par construction (la moyenne inclut
  la valeur courante), et 1 804 des 1 809 opérations BTC tombent dans « médian » ; le seuil
  « agité » n'est franchi que 5 fois, et 0 fois sur l'or. C'est un fait sur le seuil, pas sur le
  marché, et il est écrit ici plutôt que passé sous silence.

---

## 2. Le tableau mesuré

Espérance = PnL moyen par opération. IC 95 % = espérance ± 1,96 × erreur standard de la
moyenne. **En dessous de 30 opérations, on observe et on ne conclut pas** — le seuil est celui
du dépôt (`min_trades: 30`).

### 2.1 BTCUSD — `trend_breakout@1.0.1`, échantillon complet (1 809 opérations)

| Case | n | g/p | réussite | PF net | PnL € | esp/op € | esp/R | IC 95 % de l'espérance |
|---|---|---|---|---|---|---|---|---|
| **par séance UTC** | | | | | | | | |
| tokyo | 459 | 156/303 | 34,0 % | 0,86 | −446,24 | −0,97 | −0,10 | [−2,23 ; +0,29] |
| london | 396 | 126/270 | 31,8 % | 0,77 | −688,46 | **−1,74** | −0,17 | [−3,07 ; −0,40] |
| overlap | 411 | 140/271 | 34,1 % | 0,85 | −450,29 | −1,10 | −0,11 | [−2,45 ; +0,26] |
| new_york | 403 | 134/269 | 33,3 % | 0,85 | −445,03 | −1,10 | −0,11 | [−2,45 ; +0,24] |
| off | 140 | 48/92 | 34,3 % | 0,87 | −132,38 | −0,95 | −0,09 | [−3,26 ; +1,37] |
| **par tercile de volatilité** | | | | | | | | |
| 1 — calme | 581 | 189/392 | 32,5 % | 0,77 | −996,27 | **−1,71** | −0,17 | [−2,81 ; −0,62] |
| 2 — médian | 669 | 216/453 | 32,3 % | 0,80 | −970,55 | −1,45 | −0,15 | [−2,49 ; −0,41] |
| 3 — agité | 559 | 199/360 | 35,6 % | 0,95 | −195,58 | **−0,35** | −0,03 | [−1,52 ; +0,82] |
| **total** | 1 809 | 604/1 205 | 33,4 % | **0,84** | **−2 162,39** | −1,20 | −0,12 | [−1,83 ; −0,56] |

### 2.2 XAUUSD — `witness@1.1.1`, échantillon complet (978 opérations)

| Case | n | g/p | réussite | PF net | PnL € | esp/op € | esp/R | IC 95 % de l'espérance |
|---|---|---|---|---|---|---|---|---|
| **par séance UTC** | | | | | | | | |
| tokyo | 319 | 116/203 | 36,4 % | 0,92 | −182,45 | −0,57 | −0,06 | [−2,09 ; +0,95] |
| london | 249 | 79/170 | 31,7 % | 0,73 | −508,46 | −2,04 | −0,20 | [−3,73 ; −0,36] |
| overlap | 190 | 59/131 | 31,1 % | 0,73 | −403,16 | −2,12 | −0,21 | [−4,08 ; −0,16] |
| new_york | 166 | 45/121 | 27,1 % | 0,62 | −508,78 | **−3,06** | −0,31 | [−5,05 ; −1,08] |
| off | 54 | 18/36 | 33,3 % | 0,86 | −54,93 | −1,02 | −0,10 | [−4,82 ; +2,79] |
| **par tercile de volatilité** | | | | | | | | |
| 1 — calme | 311 | 109/202 | 35,0 % | 0,85 | −336,86 | −1,08 | −0,11 | [−2,60 ; +0,44] |
| 2 — médian | 340 | 105/235 | 30,9 % | 0,72 | −721,36 | **−2,12** | −0,21 | [−3,55 ; −0,69] |
| 3 — agité | 327 | 103/224 | 31,5 % | 0,76 | −599,56 | −1,83 | −0,18 | [−3,34 ; −0,33] |
| **total** | 978 | 317/661 | 32,4 % | **0,77** | **−1 657,78** | −1,70 | −0,17 | [−2,55 ; −0,84] |

### 2.3 Fenêtre de validation seule (celle qui n'a servi à rien choisir)

| Marché | n | PF net | PnL € | esp/op € | IC 95 % |
|---|---|---|---|---|---|
| BTCUSD | 362 | 0,78 | −592,49 | −1,64 | [−3,04 ; −0,23] |
| XAUUSD | 193 | 0,69 | −456,05 | −2,36 | [−4,26 ; −0,46] |

### 2.4 Le croisement séance × tercile : ce que le tableau dit vraiment

| | BTCUSD (case conclusive ≥ 30) | XAUUSD (case conclusive ≥ 30) |
|---|---|---|
| cases conclusives | 15 / 15 | 10 / 15 |
| dont espérance **négative** | 13 | 9 |
| dont espérance positive | 2 : `off\|agité` (n=50, PF 1,06, **IC [−3,58 ; +4,37]**) et `tokyo\|agité` (n=156, PF 1,10, **IC [−1,56 ; +2,93]**) | 1 : `tokyo\|médian` (n=107, PF 1,15, **IC [−1,76 ; +3,65]**) |
| cases positives à IC entièrement > 0 | **0** | **0** |

**Aucune case de marché n'a une espérance dont l'intervalle de confiance exclut zéro par le
haut.** Autrement dit : il n'existe, dans ces données, aucune séance ni aucun régime de
volatilité où la stratégie gagne avec une certitude mesurable.

---

## 3. La décision, et son chiffre

### 3.1 Le critère, fixé avant de regarder les chiffres

1. **effectif** ≥ 30 opérations (`thresholds.json::min_trades`) sur la fenêtre retenue ;
2. **facteur de profit net ≥ 1,20** (`thresholds.json::min_profit_factor_net`) en
   **entraînement ET en validation** ;
3. **p-value** (test de randomisation par retournement de signe, `protocol.monte_carlo_p_value`,
   1 000 itérations, germe déterministe) survivant à la correction de Benjamini-Hochberg au taux
   du protocole (FDR 0,10) sur **toutes** les hypothèses essayées.

Aucun seuil n'a été ajusté après avoir vu un résultat. Le classement vient de l'entraînement
seul ; la validation ne sert qu'à juger.

### 3.2 Le résultat

| | BTCUSD | XAUUSD |
|---|---|---|
| hypothèses essayées (sous-ensembles de cases) | 32 767 | 32 767 |
| éligibles en entraînement (≥ 30 op., PF ≥ 1,20) | 2 | 4 |
| meilleure règle d'entraînement | `new_york\|agité` | `new_york\|calme`, `off\|calme`, `off\|agité` |
| — entraînement | 120 op., PF **1,22**, espérance **+1,40 €** | 32 op., PF **1,30**, espérance **+1,88 €** |
| — validation | 26 op., PF **0,53**, espérance **−3,84 €** | 12 op., PF **0,13**, espérance **−8,58 €** |
| p-value | 0,1279 (seuil BH 0,05) → non significatif | 0,2318 (seuil BH 0,075) → non significatif |
| survivants après correction | **0 / 2** | **0 / 4** |
| **verdict** | **REFUSÉ** | **REFUSÉ** |

**Le chiffre qui décide** : la meilleure règle du BTC passe d'un PF net de **1,22** en
entraînement à **0,53** en validation, et sa p-value (0,128) reste au-dessus du seuil corrigé
(0,05). Sur l'or, la chute est plus brutale encore : **1,30 → 0,13**. Une règle qui ne tient pas
hors de l'échantillon qui l'a fait naître n'est pas une règle : c'est un ajustement au passé.

### 3.3 La thèse inverse, et pourquoi elle est écartée — pas ignorée

**La thèse inverse est celle de l'opérateur, et elle n'est pas absurde :** « le BTC gagne quand
la volatilité monte ». Les données lui donnent **partiellement raison sur la direction** : sur
le BTC, l'espérance par opération est **monotone dans le tercile de volatilité** — calme
−1,71 €, médian −1,45 €, agité −0,35 €, et le PF suit 0,77 → 0,80 → 0,95. Sur la fenêtre
d'entraînement, le tercile agité est même le seul à l'équilibre positif (356 op., PF 1,03,
+64,19 €). C'est le signe le plus intéressant de toute la mesure, et il va dans le sens de la
demande de l'opérateur.

**Pourquoi il ne suffit pas, en trois raisons chiffrées :**

1. **Le signe s'inverse en validation.** Le tercile agité du BTC : entraînement PF 1,03 sur
   356 opérations, validation PF 0,72 sur 92 opérations. La direction favorable n'est pas stable,
   elle est propre à la première moitié de l'échantillon.
2. **Même la meilleure case ne franchit pas le seuil du projet.** `tokyo|agité` sur tout
   l'échantillon : PF 1,10 et espérance +0,69 € sur 156 opérations — positif, mais sous 1,20, et
   avec un IC 95 % qui chevauche zéro [−1,56 ; +2,93]. Activer un filtre sur cette case
   supprimerait 90 % des opérations pour un résultat statistiquement indistinguable de zéro.
3. **Sur l'or, la thèse n'a aucun support.** Les trois terciles sont négatifs et quasi plats
   (−1,08 / −2,12 / −1,83 €) : la volatilité n'y ordonne rien. Or c'est exactement le marché où
   la stratégie configure la bande d'entrée la plus serrée (0,1 ATR) — un filtre de volatilité a
   été proposé comme remède à ce problème ; la mesure ne le confirme pas.

**Et le point qui tranche définitivement** : il n'existe aucune case, sur aucun des deux
marchés, dont l'IC 95 % de l'espérance soit entièrement positif. Un filtre ne peut que
**retirer** des opérations : il ne peut donc pas transformer une espérance négative en
espérance positive sur l'ensemble. Le mieux qu'il puisse faire est de laisser de côté les pires
cases — ce que la mesure confirme — mais l'ensemble reste perdant. **Le problème n'est pas dans
le *moment* des entrées, il est dans les entrées elles-mêmes.**

### 3.4 Ce que la mesure a trouvé de solide, et qu'il faut consigner sans l'activer

La monotonie de l'espérance BTC dans la volatilité est **l'observation la plus intéressante de
tout ce travail**, et elle mérite d'être écrite pour ce qu'elle est :

| Tercile BTC | entraînement | validation | tout l'échantillon |
|---|---|---|---|
| calme | 356 op., PF 0,72, −2,10 € | 117 op., PF 0,95, −0,37 € | 581 op., PF 0,77, −1,71 € |
| médian | 356 op., PF 0,85, −1,07 € | 153 op., PF 0,70, −2,29 € | 669 op., PF 0,80, −1,45 € |
| agité | 356 op., PF **1,03**, **+0,18 €** | 92 op., PF 0,72, −2,16 € | 559 op., PF 0,95, −0,35 € |

Trois lectures, dans cet ordre :

1. **en entraînement**, la progression est monotone et le tercile agité est le seul à
   l'équilibre positif — c'est un fait, et il va dans le sens de l'intuition de l'opérateur ;
2. **en validation**, la progression disparaît (0,95 / 0,70 / 0,72) et le tercile agité devient
   le deuxième plus mauvais — c'est le fait qui interdit d'en faire une règle ;
3. **sur tout l'échantillon**, l'ordre revient (0,77 / 0,80 / 0,95) mais reste sous 1,00 : la
   direction est peut-être réelle, l'avantage ne l'est pas.

Consigner cela n'est pas ouvrir une porte : aucun manifeste ne porte ce filtre, aucune règle
n'est activée, et la ligne du dessus (validation) est précisément celle qui a fait refuser la
règle. **Une tendance reconstatée en validation n'est pas une tendance : c'est une observation
à retester sur des données qui n'existent pas encore.**

---

## 4. Ce qui a été câblé (et pourquoi cela ne change rien aujourd'hui)

Le mécanisme existe, il est testé, il est **inerte**.

| Fichier | Ce qu'il porte |
|---|---|
| `src/tradingagent/indicators/entry_filter.py` (nouveau) | La décision pure : `EntryFilter.decide(time, candles)` → `PASS` ou `REFUSED`, avec motif nommé (`session`, `volatility`, `unmeasured`). Aucun champ pour porter un signal, une taille ou un stop. |
| `src/tradingagent/strategies/manifest.py` | Les clés `entry_filter.session.allowed` et `entry_filter.volatility.{allowed,lookback,calm_ratio,volatile_ratio}`, plus `atr_period`. Absentes, le filtre est inerte. |
| `src/tradingagent/strategies/evaluation.py` | Le filtre est appliqué dans `evaluate`, la voie **partagée** par le harnais de backtest (`backtest/harness.py:339`) et le générateur de production (`signals/generator.py:177`) : la parité backtest/production est structurelle, pas déclarative. Nouveau `OutcomeKind.FILTERED`, distinct de `NO_SIGNAL`. |
| `src/tradingagent/signals/generator.py` | Un refus écrit un événement `entry_filtered` (INFO) par le chemin d'événement existant, avec son motif. Aucune table, aucune colonne, aucune migration. |
| `src/tradingagent/backtest/harness.py` | `BacktestResult.filtered_signals` compte les refus séparément des signaux. |
| `scripts/backtest/session_volatility.py` (nouveau) | La mesure. Aucun manifeste, aucun seuil, aucune promotion. |
| `tests/indicators/test_entry_filter.py`, `tests/strategies/test_entry_filter_evaluation.py` (nouveaux) | 44 tests : refus en séance exclue, passage en séance retenue, fail-open quand la mesure manque, forme de la décision (C-002), monotonie du filtre, validation des clés de manifeste. |

### Le filtre est **fail-open**, et c'est une décision de sécurité

Si le rapport ATR/moyenne n'est pas mesurable — historique trop court pour l'ATR — le filtre rend
`PASS` avec le motif `UNMEASURED`, et la phrase le dit. Il ne bloque **jamais** dans le doute :
un filtre qui refuse quand il ne sait pas éteint l'agent en silence, sans qu'aucune alerte ne le
dise. Deux tests le prouvent explicitement, dont un qui vérifie que la séance est jugée même
quand la volatilité, elle, n'est pas mesurable.

### Aucune règle de risque n'a été relâchée

Le filtre ne peut que refuser. Il ne crée aucun signal, n'augmente aucune taille, ne desserre
aucun stop. Un test vérifie la **forme** de `EntryDecision` (trois champs : `verdict`, `reason`,
`detail` — pas de candidat, pas de taille, pas de stop) ; un autre vérifie la **monotonie** sur
390 évaluations consécutives : le filtre retire des signaux, n'en ajoute jamais et n'en modifie
aucun.

---

## 5. Limites, et ce qu'il faudrait pour conclure autrement

* **L'effectif par case reste mince sur la validation** : 12 à 44 opérations par case
  conclusive. C'est pourquoi chaque case publie son IC 95 %, et pourquoi les cases sous 30
  opérations sont marquées « non concluant » plutôt que commentées. Un tableau sans effectif
  aurait laissé croire à des résultats là où il n'y a que du bruit.
* **Les terciles sont ceux de l'échantillon d'entraînement** : ce sont des coupures relatives à
  chaque marché, pas des niveaux universels. Une coupure exprimée en ATR absolu ne voudrait rien
  dire d'un marché à l'autre.
* **Deux marchés, deux stratégies** : la conclusion porte sur `trend_breakout@1.0.1` sur BTCUSD
  et `witness@1.1.1` sur XAUUSD, à leurs paramètres actuels. Elle ne dit rien d'une autre règle
  d'entrée.
* **La mesure du régime utilise le même ATR que celui du stop** (`manifest.atr_period`, 14) :
  un manifeste qui déclarerait deux ATR différents est refusé à la lecture du fichier.
* **`overlap` est la fenêtre du dépôt, pas une fenêtre mesurée ici** (13:00–16:00 UTC). La
  recherche externe suggère que le pic d'activité crypto se situe à 16:00–17:00 UTC, soit une
  heure après la fin de cette fenêtre ; cette source porte sur des plateformes centralisées, pas
  sur les CFD du courtier, et **aucune heure n'a été gravée dans du code sur cette base**. La
  question « nos données montrent-elles un pic horaire ? » reste ouverte (voir §6).

---

## 6. Reste ouvert (non traité ici, pour ne pas mélanger les sujets)

1. **Fréquence horaire du spread BTCUSD chez le courtier.** Les 18,424 $ viennent de onze
   décisions d'une seule journée. Tant que la couverture de la base n'est pas vérifiée, on ne
   sait pas si le spread s'élargit durablement — et c'est une contrainte qui déciderait de la
   *fenêtre de trading*, pas d'un paramètre. Prérequis : compter les décisions de risque
   persistées avec un spread non nul et leurs dates **avant** d'écrire quoi que ce soit.
2. **Profil horaire interne (00–23 UTC).** Il dirait si le pic de 16:00–17:00 UTC de la
   littérature crypto existe sur nos propres données. Sur 362 opérations de validation BTC, cela
   ferait ~15 opérations par heure : sous le seuil de conclusion, à publier comme exploration et
   non comme règle.
3. **Un test de jour de la semaine** (week-end, jeudi) sur BTCUSD. Étiqueté d'avance comme
   dangereux : sans hypothèse unique écrite avant, c'est de l'optimisation sur le passé.
4. **Le vrai sujet : les entrées elles-mêmes.** La mesure montre qu'aucun moment n'est rentable ;
   la question utile devient « pourquoi ces entrées perdent-elles ? », pas « quand les
   prendre ? ».

---

## 7. Reproduire

```bash
# la mesure complète (≈ 20 min, deux marchés, 59 999 bougies chacun)
uv run python scripts/backtest/session_volatility.py

# vérification rapide de plomberie, jeu tronqué (ce n'est PAS une mesure)
uv run python scripts/backtest/session_volatility.py --bars 8000 --no-write

# la réplique sur l'autre jeu gelé (60 000 bougies, 2026-10-08, fenêtres qui se recouvrent)
uv run python scripts/backtest/session_volatility.py --datasets docs/research/datasets-long
```

**Sorties brutes de la mesure publiée ici :**

* `docs/research/session-volatility/session-volatility.json` — la mesure complète : baselines,
  découpages par séance / tercile / régime nommé / croisement, pour les trois fenêtres, plus la
  décision et la correction de tests multiples.
* `docs/research/session-volatility/run-datasets-volume.txt` — la sortie standard intégrale du
  script, telle quelle.

**État de l'arbre au moment de la mesure** : `uv run pytest -q` → **2 789 passed, 9 skipped,
0 failed** (arbre stabilisé, 2026-10-10 05:40 UTC+2) ; `uv run ruff check .` → *All checks
passed!* ; `uv run mypy` → *Success: no issues found in 361 source files*. Sorties brutes
conservées dans `pytest-full.txt` et `static-checks.txt`.

Une réserve honnête sur `ruff format --check .` : il signale **2 fichiers** non conformes,
`scripts/backtest/tune_stop_modes.py` et `scripts/backtest/tune_stop_report.py`. Ils
appartiennent à un autre flux, et une première exécution de `ruff format .` les avait
reformatés : j'ai **annulé cette modification** (`git checkout --`) pour ne pas écrire dans le
travail d'un autre. Les reformater est un geste d'une ligne à faire par leur auteur.

Les 56 tests ajoutés par ce lot couvrent : l'accord/refus par séance, le refus par régime de
volatilité, le **fail-open** quand la mesure manque (aux deux bouts : la porte pure et le
harnais), la forme de la décision (C-002 : aucun champ pour porter un signal, une taille ou un
stop), la monotonie du filtre sur 390 évaluations, la validation des clés de manifeste, le
compteur `filtered_signals` du harnais (dont **zéro quand le filtre est absent**), et la trace
`entry_filtered` en production — avec son pendant : un signal accepté n'écrit aucun événement.
