# Deux ans et demi — le verdict tient-il ? (task-6)

**Date du run :** 2026-10-08 · **Atelier :** `scripts/backtest/discover.py` (laboratoire de découverte,
§7) · **Aucune promotion, aucun seuil touché, aucun fichier source modifié.**

| unité | commande | rapport |
|---|---|---|
| **M15 long** (principal) | `uv run python scripts/backtest/discover.py --datasets docs/research/datasets-long` | `docs/research/2026-10-08-discovery.json` |
| H1 long | `… --datasets docs/research/datasets-h1/long-60000/H1 --output docs/research/discovery-h1` | `docs/research/discovery-h1/2026-10-08-discovery.json` |
| H4 long | `… --datasets docs/research/datasets-h1/long-60000/H4 --output docs/research/discovery-h4` | `docs/research/discovery-h4/2026-10-08-discovery.json` |
| H1 court | `… --datasets docs/research/datasets-h1/short-11999/H1 --output docs/research/discovery-h1-short` | `docs/research/discovery-h1-short/2026-10-08-discovery.json` |
| H4 court | `… --datasets docs/research/datasets-h1/short-11999/H4 --output docs/research/discovery-h4-short` | `docs/research/discovery-h4-short/2026-10-08-discovery.json` |
| M15 11 999 (témoin) | run antérieur, préservé | `docs/research/archive/2026-10-08-discovery-11999bars-M15.json` |

Le rapport de 11 999 bougies occupait `docs/research/2026-10-08-discovery.json` ; il a été
**archivé, pas détruit**, avant le run long (le chemin canonique est écrasé à chaque run du jour).
`docs/research/2026-10-08-discovery-plan-corrige.json` reste sur place, intact.

---

## 1. Les jeux gelés, rechargés et revérifiés ici

Rechargés par `DatasetStore(...).load_all()` (pas de confiance accordée au manifeste seul) :
**2 jeux chargés** pour `datasets-long`, 2 pour chaque dossier H1/H4.

| marché | dataset_id | bougies | période (UTC) | empreinte |
|---|---|---|---|---|
| BTCUSD | `mt5-BTCUSD-M15-2026-10-08` | 60 000 | 2025-01-21 07:00 → 2026-10-08 16:15 | `95e4e18b4bed1ef6…` |
| XAUUSD | `mt5-XAUUSD-M15-2026-10-08` | 60 000 | 2024-03-25 02:45 → 2026-10-08 16:15 | `06e1e39aaffb5b47…` |

Découpage du protocole (`train_fraction=0.6`, `validation_fraction=0.2`) :

| marché | entraînement | validation | scellé | plis | ouvertures du scellé |
|---|---|---|---|---|---|
| BTCUSD | 36 000 (2025-01-21 → 2026-01-31) | 12 000 (2026-01-31 → 2026-06-05) | 12 000 | 6 | **0** |
| XAUUSD | 36 000 (2024-03-25 → 2025-10-02) | 12 000 (2025-10-02 → 2026-04-08) | 12 000 | 6 | **0** |

Les six familles passent la même grille : `trend_following` (16 candidats),
`momentum` (4), `mean_reversion` (4), `breakout` (4), `volatility_breakout` (4),
`ensemble` (2) — **34 candidats**, soit 17 par marché × 2 marchés.

---

## 2. Le verdict principal : M15, 4 mois → 2 ans et demi

| | **11 999 bougies** (≈ 4 mois) | **60 000 bougies** (≈ 2,5 ans) |
|---|---|---|
| familles explorées | 6 | 6 |
| candidats testés | **34** | **34** |
| retenus avant correction | **0** | **0** |
| retenus après correction (Benjamini-Hochberg, α = 0,10) | **0** | **0** |
| écartés | 34 | 34 |
| fausses découvertes attendues du seul hasard | 3,40 | 3,40 |
| seuil de Bonferroni (α/m) | 0,002941 | 0,002941 |
| candidats ayant atteint le scellé (ouvertures) | 2 (BTCUSD) | **0** |

### Causes de rejet, avec leurs comptes

| cause | 11 999 bougies | 60 000 bougies | mouvement |
|---|---|---|---|
| `overfitting` (walk-forward non profitable) | 10 | **17** | ▲ +7 |
| `unstable` (score de stabilité insuffisant) | 7 | **12** | ▲ +5 |
| `parameter_dispersion` (îlot instable) | **15** | 5 | ▼ −10 |
| `out_of_sample_negative` (scellé négatif / rétention nulle) | 2 | **0** | ▼ −2 |
| `too_few_trades` | 0 | 0 | = |
| `false_discovery` (survivant démoli par la correction) | 0 | 0 | = |

Détail long, par famille (avant / après correction) : `trend_following` 0/0
(surapprentissage 8, instables 4, dispersion 4) · `momentum` 0/0 (surapprentissage 4) ·
`mean_reversion` 0/0 (instables 3, surapprentissage 1) · `breakout` 0/0 (instables 2,
surapprentissage 2) · `volatility_breakout` 0/0 (surapprentissage 1, instables 3) ·
`ensemble` 0/0 (surapprentissage 1, dispersion 1).

**Ce que le quadruplement de l'historique a réellement changé :** la *composition* des rejets,
pas leur issue. Sur 4 mois, le rejet dominant était la **dispersion des paramètres** (15/34 :
des îlots de chance). Sur 2,5 ans, ce sont les **plis walk-forward non rentables** (17/34) et le
**score de stabilité** (12/34) qui dominent, et plus aucun candidat n'atteint même le scellé
(ouvertures 2 → 0) : la correction de sélection multiple n'a **rien eu à démettre**, faute de
survivant. Le nombre d'hypothèses testées est identique (34), donc la correction n'est ni plus
clémente ni plus dure — elle est simplement sans objet.

---

## 3. Les neuf portes : **non lancées**, et pourquoi

`run_campaign.py` n'a **pas** été exécuté. Le mandat le conditionne explicitement à « le
laboratoire retient au moins un candidat après correction » : ici `discoveries_after = 0`, la
condition n'est pas remplie. Enchaîner aurait en outre réécrit `docs/research/thresholds.json`,
`docs/research/decisions/witness@1.1.0.json` et les manifestes de `docs/research/candidates/` —
c'est-à-dire touché à des artefacts de promotion, ce que la tâche interdit (« ne promeus rien »).
Le tableau des neuf portes n'a donc **aucune ligne nouvelle** à montrer pour les 60 000 bougies :
pas de candidat, pas de porte à évaluer.

---

## 4. H1 et H4 : rapport séparé

### H1 long — `docs/research/datasets-h1/long-60000/H1`

XAUUSD 14 894 bougies (`7116718d458f70dd…`), BTCUSD 14 992 (`a15b41b31cded31e…`).
Scellé ouvert 2 fois (XAUUSD) et 1 fois (BTCUSD) — donc des candidats ont bien atteint le
dernier échelon.

**34 testés · 1 retenu avant correction · 0 après correction.**
Le survivant `BTCUSD:trend_following:witness:07` (p = 0,3616, hors-échantillon **+29,54** sur
30 opérations, plis 3/6) est **démis par Benjamini-Hochberg** : sa p-value est très au-dessus du
seuil. Causes : `overfitting` 22, `parameter_dispersion` 6, `unstable` 3,
`out_of_sample_negative` 2, `false_discovery` 1.

### H4 long — `docs/research/datasets-h1/long-60000/H4`

XAUUSD 3 259 bougies (`17b1126f3d2a1af6…`), BTCUSD 3 739 (`f78e350cdbd6da88…`).

**34 testés · 1 retenu avant correction · 0 après correction.**
Survivant `XAUUSD:breakout:trend_breakout:01` (p = 0,2577, hors-échantillon **+41,43** sur
16 opérations, plis 3/6), démis par la correction. Causes : `overfitting` 13,
`parameter_dispersion` 9, `unstable` 6, `out_of_sample_negative` 3, `too_few_trades` 2,
`false_discovery` 1.

### H1 court — `docs/research/datasets-h1/short-11999/H1`

XAUUSD 2 980 · BTCUSD 2 998 bougies. **34 testés · 0 avant · 0 après.** Causes :
`overfitting` 10, `parameter_dispersion` 9, `unstable` 7, `too_few_trades` 5,
`out_of_sample_negative` 3. (Le jeu court se comporte comme le long : rien ne passe.)

### H4 court — `docs/research/datasets-h1/short-11999/H4`

XAUUSD 651 · BTCUSD 747 bougies → **0 candidat testé, les deux marchés sont `INUTILISABLE`** :
`no walk-forward fold fits in 520 bars (train 350, validation 250)` (et 597 bars pour BTCUSD).
Le plan de plis du protocole ne tient pas dans un jeu H4 de quatre mois : ce n'est pas un rejet,
c'est une impossibilité de mesurer. À retenir comme taille plancher, pas comme résultat.

---

## 5. La limite à connaître : ce que le walk-forward regarde vraiment

`DiscoveryProtocol.walk_forward = WalkForwardPlan(train_bars=350, validation_bars=250,
step_bars=200, max_folds=6)` et `walk_forward()` démarre à l'indice 0 puis **s'arrête au
6ᵉ pli**. Les six plis couvrent donc les barres 0 → ~1 600 du *début* de la fenêtre glissante,
soit **3,3 % des 48 000 barres roulantes** des jeux longs :

| marché (M15 long) | fenêtre réellement jugée par les 6 plis |
|---|---|
| XAUUSD | 2024-03-31 22:15 → 2024-04-18 12:00 (≈ 3 semaines, les plus anciennes du jeu) |
| BTCUSD | 2025-01-24 22:30 → 2025-02-06 22:45 (≈ 2 semaines, les plus anciennes du jeu) |

Conséquence à ne pas masquer : les **17 rejets « surapprentissage »** reposent sur deux à trois
semaines situées au tout début d'un historique de deux ans et demi — pas sur la période entière.
Les autres échelons, eux, lisent bien les fenêtres longues (validation 12 000 bougies pour les
p-values, la stabilité et les coûts ; entraînement 36 000). C'est une propriété du protocole
(même constat que `2026-10-08-longer-history-comparison.md` §4), pas un défaut du jeu de données,
et **je n'y ai pas touché** : corriger cela demande une décision du Lead (`max_folds=None` ou
ancrage des plis sur la fin de l'historique), pas une retouche de seuil.

Par ailleurs le scellé n'a **jamais été ouvert** sur le M15 long (0 ouverture pour les deux
marchés) : les 12 000 bougies scellées de chaque marché restent intactes, donc réutilisables pour
une confirmation ultérieure sans avoir été consommées.

---

## 6. Verdict

**Non — le verdict ne change pas : sur 60 000 bougies M15 (2,5 ans) comme sur 11 999 (4 mois),
34 candidats testés, 0 retenu avant correction, 0 après ; le zéro n'était donc pas un artefact
des quatre mois.**

Ce qui a changé, et qu'il faut dire : le moteur de rejet s'est déplacé (dispersion 15 → 5,
walk-forward 10 → 17, stabilité 7 → 12) et plus aucun candidat n'atteint le scellé (2 ouvertures
→ 0). Le laboratoire ne trouve pas « moins » sur plus de données : il trouve **autrement rien**,
et il le prouve sur un historique qui n'est plus une fenêtre de quatre mois.
