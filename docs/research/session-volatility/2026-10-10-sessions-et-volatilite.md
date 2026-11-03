# Sessions et volatilité : mesurer avant d'activer

**Date** : 2026-10-10 · **Tâche** : Stream B (TASK partagée `task-2`) · **Nature** : mesure en
lecture seule, puis câblage d'un filtre **laissé désactivé**.

**Question de l'opérateur** : « prends en compte aussi les sessions pour trader uniquement
lorsque le marché est volatile ». Ce document ne répond pas à la question par une opinion : il
la traduit en un tableau mesuré sur données réelles, puis en une décision chiffrée.

> **DÉCISION : AUCUN FILTRE DE SÉANCE OU DE VOLATILITÉ N'EST ACTIVÉ.**
> Sur 978 opérations XAUUSD, l'espérance par opération est négative dans **la quasi-totalité
> des cases conclusives**, et la meilleure règle de séance/volatilité choisie sur
> l'entraînement s'effondre en validation (PF 1,30 → 0,13). Aucune ne survit à la correction de
> tests multiples. Le mécanisme est câblé, testé, et **désactivé par défaut** ; son activation
> est une décision d'opérateur qui reste ouverte.

---

## 1. Ce qui a été mesuré, sur quoi, et avec quels coûts

| | XAUUSD |
|---|---|
| Stratégie configurée (`config/agent.yaml`) | `witness@1.1.1` |
| Jeu gelé | `docs/research/datasets-volume/XAUUSD-M15-mt5-XAUUSD-M15-2026-10-09.jsonl` |
| Bougies | 59 999 (M15) |
| Empreinte | `d847b980c680…` |
| Période | 2024-03-25 → 2026-10-09 |
| Coûts | `campaign_costs` du dépôt : spread 2,24 bp du prix, slippage 2 bp, commission 0,50 €/opération, `mode=SIGNAL`, une position à la fois |

Aucun jeu gelé n'a été réécrit. Aucun seuil de `docs/research/thresholds.json` n'a été touché.
Aucune stratégie n'a été promue. Le harnais est `backtest/harness.py` via la voie d'évaluation
partagée `strategies/evaluation.py::evaluate`.

### Le découpage temporel, et ce qui n'a **jamais** été regardé

Découpage du dépôt (`research/protocol.py::split_dataset`, ancré, 60 % / 20 % / 20 %) :

| Fenêtre | XAUUSD | Bougies |
|---|---|---|
| entraînement | 2024-03-25 13:00 → 2025-10-02 11:45 | 35 999 |
| validation | 2025-10-02 11:45 → 2026-04-08 18:15 | 11 999 |
| **hors échantillon (SCELLÉ)** | **2026-04-08 18:15 → 2026-10-09 03:30** | **12 001** |

**Le jeu scellé n'a pas été ouvert.** Le script n'appelle jamais `SealedSet.unlock` : il lit la
fenêtre du sceau (`DataSplit.windows`) pour pouvoir nommer la période réservée, et rien d'autre.
Les 12 001 dernières bougies n'ont donc joué aucun rôle dans la décision.

### Les deux définitions de régime, et pourquoi les deux

* **Terciles du rapport ATR courant / moyenne de l'ATR**, coupures **dérivées de la fenêtre
  d'entraînement seule** puis appliquées telles quelles à la validation : 0,9523 / 1,0117. Recalculer les coupures par fenêtre en aurait fait une fonction du futur.
* **Régimes nommés du dépôt** (`indicators/regime.py`) : calme < 0,7, agité > 1,5, sinon médian.
  Cette lecture est publiée pour être honnête sur un point : sur l'or, elle ne
  sépare **presque rien**. Le rapport ATR/moyenne est borné par construction (la moyenne inclut
  la valeur courante) ; le seuil « agité » n'est franchi **0 fois** sur l'or. C'est un fait sur
  le seuil, pas sur le marché, et il est écrit ici plutôt que passé sous silence.

---

## 2. Le tableau mesuré

Espérance = PnL moyen par opération. IC 95 % = espérance ± 1,96 × erreur standard de la
moyenne. **En dessous de 30 opérations, on observe et on ne conclut pas** — le seuil est celui
du dépôt (`min_trades: 30`).

### 2.1 XAUUSD — `witness@1.1.1`, échantillon complet (978 opérations)

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

### 2.2 Fenêtre de validation seule (celle qui n'a servi à rien choisir)

| Marché | n | PF net | PnL € | esp/op € | IC 95 % |
|---|---|---|---|---|---|
| XAUUSD | 193 | 0,69 | −456,05 | −2,36 | [−4,26 ; −0,46] |

### 2.3 Le croisement séance × tercile : ce que le tableau dit vraiment

| | XAUUSD (case conclusive ≥ 30) |
|---|---|
| cases conclusives | 10 / 15 |
| dont espérance **négative** | 9 |
| dont espérance positive | 1 : `tokyo\|médian` (n=107, PF 1,15, **IC [−1,76 ; +3,65]**) |
| cases positives à IC entièrement > 0 | **0** |

**Aucune case n'a une espérance dont l'intervalle de confiance exclut zéro par le
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

| | XAUUSD |
|---|---|
| hypothèses essayées (sous-ensembles de cases) | 32 767 |
| éligibles en entraînement (≥ 30 op., PF ≥ 1,20) | 4 |
| meilleure règle d'entraînement | `new_york\|calme`, `off\|calme`, `off\|agité` |
| — entraînement | 32 op., PF **1,30**, espérance **+1,88 €** |
| — validation | 12 op., PF **0,13**, espérance **−8,58 €** |
| p-value | 0,2318 (seuil BH 0,075) → non significatif |
| survivants après correction | **0 / 4** |
| **verdict** | **REFUSÉ** |

**Le chiffre qui décide** : la meilleure règle d'entraînement passe d'un PF net de **1,30** à
**0,13** en validation, et sa p-value (0,2318) reste au-dessus du seuil corrigé (0,075). Une règle qui ne tient pas
hors de l'échantillon qui l'a fait naître n'est pas une règle : c'est un ajustement au passé.

### 3.3 La thèse de l'opérateur, et pourquoi elle est écartée

**La thèse de l'opérateur n'est pas absurde :** « le marché est plus favorable quand la
volatilité monte ». Les données ne lui donnent aucun support sur l'or : les trois terciles sont
négatifs et quasi plats (−1,08 / −2,12 / −1,83 €), la volatilité n'y ordonne rien. La stratégie
configure pourtant sur l'or une bande d'entrée très serrée (0,1 ATR) — un filtre de volatilité
a été proposé comme remède à ce problème ; la mesure ne le confirme pas.

**Et le point qui tranche définitivement** : il n'existe aucune case dont l'IC 95 % de
l'espérance soit entièrement positif. Un filtre ne peut que **retirer** des opérations : il ne
peut donc pas transformer une espérance négative en espérance positive sur l'ensemble. Le mieux
qu'il puisse faire est de laisser de côté les pires cases — ce que la mesure confirme — mais
l'ensemble reste perdant. **Le problème n'est pas dans le *moment* des entrées, il est dans les
entrées elles-mêmes.**

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
* **Les terciles sont ceux de l'échantillon d'entraînement** : ce sont des coupures relatives au
  marché, pas des niveaux universels.
* **Une stratégie, un marché** : la conclusion porte sur `witness@1.1.1` sur XAUUSD, à ses
  paramètres actuels. Elle ne dit rien d'une autre règle d'entrée.
* **La mesure du régime utilise le même ATR que celui du stop** (`manifest.atr_period`, 14) :
  un manifeste qui déclarerait deux ATR différents est refusé à la lecture du fichier.
* **`overlap` est la fenêtre du dépôt, pas une fenêtre mesurée ici** (13:00–16:00 UTC). **Aucune
  heure n'a été gravée dans du code sur cette base.** La question « nos données montrent-elles
  un pic horaire ? » reste ouverte (voir §6).

---

## 6. Reste ouvert (non traité ici, pour ne pas mélanger les sujets)

1. **Profil horaire interne (00–23 UTC).** Il dirait si un pic d'activité horaire existe sur
   nos propres données. Avec 193 opérations de validation sur l'or, l'effectif par heure serait
   très inférieur au seuil de conclusion (30) : à publier comme exploration et non comme règle.
2. **Un test de jour de la semaine** (week-end, jeudi). Étiqueté d'avance comme dangereux :
   sans hypothèse unique écrite avant, c'est de l'optimisation sur le passé.
3. **Le vrai sujet : les entrées elles-mêmes.** La mesure montre qu'aucun moment n'est rentable ;
   la question utile devient « pourquoi ces entrées perdent-elles ? », pas « quand les
   prendre ? ».

---

## 7. Reproduire

```bash
# la mesure complète (59 999 bougies)
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
