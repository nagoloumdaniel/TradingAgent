# Laboratoire de découverte multi-familles (§7)

Atelier hors production. `tradingagent.research.discovery` n'est jamais importé par un
module de production (`tests/test_architecture.py` : `backtest` et `research` ne chargent
jamais en production). Il ne se contente pas d'optimiser une famille existante : il
**explore plusieurs familles**, et il publie autant les échecs que les succès.

## Règle fondatrice : découvrir n'est pas promouvoir

> Un candidat découvert n'est **pas** une stratégie exécutable.

- Le laboratoire n'écrit **rien** dans `config/strategies/` et ne modifie **jamais**
  `strategies/registry.py`.
- Les classes de familles vivent dans `research/discovery.py` ; elles ne sont pas dans
  `REGISTRY` et aucun code de production ne peut les atteindre (verrou d'architecture).
- Le rapport est écrit sous `docs/research/` et s'arrête là.
- Seul le **Lead** promeut, avec le protocole de `research/promotion.py` (seuils figés avant
  résultats, décision horodatée, manifeste au format de production). La découverte ne fait
  que proposer des hypothèses et documenter leur robustesse.

## Les familles explorées

Aucune famille n'est supposée meilleure qu'une autre : toutes passent par le **même
harnais** (`backtest.harness.run_backtest` → `strategies.evaluation.evaluate`) et le **même
protocole** (`research/protocol.py`). Chaque gabarit est une **fonction pure** de la grille
de paramètres : il ne reçoit aucune bougie, donc il ne peut pas ajuster un paramètre sur les
données qui serviront à le juger.

| Famille | Idée | Implémentation | Classe |
|---|---|---|---|
| `trend_following` | suivi de tendance : croisement d'EMA | classe existante | `Witness` |
| `momentum` | momentum normalisé par l'ATR (déplacement sur N barres) | implémentée ici | `Momentum` |
| `mean_reversion` | retour à la moyenne : RSI survendu/suracheté | implémentée ici | `MeanReversion` |
| `breakout` | cassure de canal Donchian filtrée par une EMA de tendance | classe existante | `TrendBreakout` |
| `volatility_breakout` | cassure de volatilité : expansion d'une barre > k × ATR | implémentée ici | `VolatilityBreakout` |
| `ensemble` | ensemble : deux règles indépendantes (tendance EMA **et** RSI) doivent voter pareil | implémentée ici | `ConsensusEnsemble` |

Couvre ainsi : suivi de tendance, momentum, retour à la moyenne, cassure, cassure de
volatilité, combinaisons d'indicateurs et ensembles. Le catalogue est extensible : une
`FamilyTemplate(family, description, template)` personnalisée peut être passée à
`discover(..., families=...)` sans toucher au reste du module.

## Méthode

1. **Découpage chronologique** (`protocol.split_dataset`) : apprentissage 60 %, validation
   20 %, hors-échantillon **scellé** 20 %. Jamais de mélange.
2. **Pré-vol** (fractionnement, walk-forward) : chaque candidat est rejoué par le harnais
   partagé sur l'apprentissage puis la validation, et sur chaque pli du walk-forward à
   origine glissante. Un pli plus court que l'historique déclaré est *compté comme ignoré*,
   pas comme une perte.
3. **Perturbation de paramètres** (`protocol.perturb_parameters`) : chaque paramètre est
   décalé de ±10 % (un paramètre entier reste entier). Un jeu perturbé refusé par le modèle
   est une preuve de fragilité, pas une raison de planter.
4. **Rapport de stabilité** (`protocol.stability_report`) : rétention, dispersion
   paramétrique, part de périodes profitables. Ce n'est pas un chiffre de profit.
5. **Hors-échantillon scellé** : ouvert **uniquement** pour les candidats ayant déjà franchi
   les barrières roulantes, via `protocol.confirm` (lecture explicite et auditée ; le nombre
   d'ouvertures est reporté par marché).
6. **Contrôle du taux de fausses découvertes** : les survivants de l'échelle ci-dessus sont
   pesés *ensemble*, pas un par un (voir la section dédiée). Un survivant dont la p-value ne
   franchit pas la correction de Benjamini-Hochberg est écarté avec la cause
   `false_discovery`.

### L'échelle des causes d'écartement

Un candidat écarté reçoit **une seule** cause, la première barrière franchie dans cet ordre.
C'est ce qui rend le rapport lisible : une ligne par cause, jamais un fourre-tout.

| Cause | Sens | Barrière |
|---|---|---|
| `invalid_parameters` | le gabarit a produit des paramètres que le modèle pydantic refuse | construction |
| `insufficient_data` | historique trop court pour découper ou pour nourrir le manifeste | pré-vol |
| `too_few_trades` | moins de `min_trades` opérations en apprentissage | apprentissage |
| `overfitting` | walk-forward non profitable (surapprentissage) | roulant |
| `parameter_dispersion` | îlot de paramètres : dispersion > seuil, ou paramètres perturbés invalides | roulant |
| `unstable` | score de stabilité insuffisant ou trop peu de périodes profitables | roulant |
| `out_of_sample_negative` | hors-échantillon scellé ≤ 0, ou rétention < seuil | scellé |
| `false_discovery` | le survivant ne franchit pas la correction de sélection multiple | sélection |

Un candidat n'est **retenu** que s'il franchit : walk-forward **et** stabilité des
paramètres **et** hors-échantillon scellé **et** la correction de sélection multiple. Aucune
exception, aucun raccourci.

### Interdiction structurelle de lire le futur

- Les décisions passent par `evaluate()` : la fenêtre reçue par la stratégie est coupée par
  bisect sur `close_time`, bougies closes au plus tard à `evaluated_at`.
- Le module ne réimplémente **ni** la découpe temporelle **ni** un seul indicateur : il
  importe `atr`, `ema`, `rsi` de `tradingagent.indicators` et `split_dataset`, `walk_forward`,
  `stability_report`, `perturb_parameters`, `confirm` de `research.protocol`.
- `tests/research/test_discovery.py` le prouve : une stratégie sonde enregistre la bougie la
  plus récente qu'elle voit à chaque décision, et le test vérifie qu'elle n'est jamais
  postérieure à `evaluated_at`. Une règle qui aurait besoin de la bougie suivante ne peut
  jamais se déclencher : elle finit en `too_few_trades`, jamais retenue.

## Contrôle du taux de fausses découvertes (sélection multiple)

### Pourquoi

Le laboratoire ne juge pas un candidat : il en juge des dizaines. Le protocole de
`research/protocol.py` note chaque candidat **seul dans son coin** — c'est ce qui rend chaque
verdict honnête — mais il ne dit rien du **nombre d'essais**. Sur le jeu synthétique à graine
fixe, 51 candidats sont essayés ; sur l'or XAUUSD, 17. Avec assez d'essais, le hasard finit
par produire un survivant présentable. « J'ai testé 51 variantes et gardé la meilleure » est
une **sélection**, pas une découverte : sans correction, la probabilité qu'un survivant soit
un faux positif croît avec le nombre de candidats, et le rapport laissait ce chiffre
implicite.

### La p-value, construite explicitement

Le protocole existant ne fournit pas de p-value : `stability_report` est un score de
robustesse, et `protocol.monte_carlo` rééchantillonne le P&L réalisé, donc se centre sur le
total observé — il répond à « quels autres ordres de ces mêmes opérations ? », pas à « qu'aurait
fait une règle sans edge ? ». La p-value est donc construite ici, en clair
(`discovery.monte_carlo_p_value`), à partir des opérations du **hors-échantillon scellé** :

- **hypothèse nulle** : la règle d'entrée n'apporte aucune information directionnelle sur ce
  ruban. Sous H0, le signe de chaque opération est celui d'une pièce équilibrée ; les
  amplitudes, elles, sont ce que le marché et les coûts ont donné ;
- **statistique** : le profit net total hors-échantillon ;
- **loi nulle** : on tire un signe indépendant par opération (test de randomisation par
  inversion de signe, le test de Fisher pour observations appariées) et on compte les tirages
  qui atteignent le total observé ;
- **estimateur** : `(comptes + 1) / (tirages + 1)`. Jamais exactement zéro : un Monte-Carlo ne
  résout pas en dessous de `1 / (tirages + 1)`, et prétendre le contraire serait mentir sur la
  précision. Un échantillon hors-échantillon vide vaut `p = 1`.
- **graine** : dérivée du couple (marché, famille, libellé) par CRC-32 — déterministe d'un
  processus à l'autre, contrairement à `hash()`. Deux exécutions identiques donnent le même
  rapport.

Ce que ce test ne fait pas : il suppose les opérations échangeables, donc des régimes
groupés et des positions qui se chevauchent le rendent **optimiste** ; il ne voit que l'edge
**directionnel** — une règle dont l'edge brut est inférieur aux coûts affiche un total faible
et retombe près de `1.0`, ce qui pèche du côté prudent.

### La correction

- **Benjamini-Hochberg** (taux de fausses découvertes), seuil `alpha` paramétrable via
  `DiscoveryProtocol.false_discovery_rate`, **défaut 0,10**. Step-up : on trie les p-values,
  le plus grand rang `k` tel que `p_(k) <= k/m * alpha` fixe le seuil, et tout candidat dont la
  p-value est en dessous est déclaré découverte. Avec un seul candidat, la règle se réduit à
  `p <= alpha` — la lecture honnête de « aucune sélection n'a eu lieu ».
- **Bonferroni** (`alpha/m`, `discovery.bonferroni_threshold`) est calculé et **affiché pour
  comparaison** : il est plus strict dès que plusieurs candidats semblent prometteurs. Il ne
  décide jamais rien ici.
- `m` est le nombre de **candidats essayés**, pas le nombre de survivants. Un candidat qui n'a
  jamais mérité sa lecture du scellé n'a pas de p-value et compte comme `p = 1` : la
  correction est ainsi **conservatrice**, pas flatteuse.
- Un survivant qui ne franchit pas la correction est **écarté** avec la cause
  `false_discovery`, sa p-value et le seuil de son rang écrits dans `detail`. Jamais de
  disparition silencieuse.

### Comment lire les chiffres

| Champ | Sens |
|---|---|
| `multiple_testing.hypotheses` | nombre de candidats essayés = nombre de tests payés |
| `multiple_testing.discoveries_before` | survivants de l'échelle, **avant** correction |
| `multiple_testing.discoveries_after` | survivants **après** correction (le seul chiffre qui compte) |
| `multiple_testing.rejected_by_correction` | `before - after` |
| `multiple_testing.bonferroni_threshold` | `alpha / m`, pour comparaison |
| `multiple_testing.expected_false_discoveries` | `m * alpha` : combien de candidats un seuil par test naïf laisserait passer par chance |
| `multiple_testing.expected_false_discovery_rate` | l'`alpha` que Benjamini-Hochberg borne réellement |
| `candidates[].p_value` | p-value hors-échantillon du candidat, `null` s'il n'a pas atteint le scellé |
| `families[].retained_before_correction` | survivants de la famille avant correction |

Deux lectures : si `discoveries_before == discoveries_after`, la correction n'a rien coûté et
les survivants ont de la marge. Si `after` tombe à zéro alors que `before` valait 1 ou 2, le
laboratoire vient de dire : « ces survivants ne se distinguent pas du hasard du nombre
d'essais », ce qui est un résultat, pas un bug — et exactement ce que le §7 demandait de ne
pas taire.

### Ce que cela ne prouve pas

Une correction de sélection multiple **ne crée aucun edge** et n'améliore aucune stratégie.
Elle empêche seulement de confondre la chance et un signal : elle rend le seuil plus dur à
mesure qu'on essaie de choses. Trois limites à garder en tête :

- **peu de candidats** : avec un ou deux survivants, Benjamini-Hochberg est numériquement
  presque identique à Bonferroni, et la puissance statistique est faible ; une vraie
  découverte peut être écartée faute de preuve, pas parce qu'elle est fausse. Le rapport ne
  prétend pas que les écartés sont mauvais, seulement que rien ne les distingue encore ;
- **dépendance entre candidats** : BH suppose l'indépendance ou une dépendance positive
  (PRDS). Les candidats d'une même famille partagent la même règle avec des paramètres voisins
  et les marchés partagent une partie du ruban : les tests sont corrélés. C'est une hypothèse
  de travail, pas une démonstration ;
- **aucune hypothèse testée n'est fixée d'avance** : le catalogue explore, il ne pré-enregistre
  pas un test unique. La correction protège la sélection telle qu'elle a eu lieu ; elle ne
  remplace pas un protocole pré-enregistré.

## Lire le rapport

`DiscoveryReport.to_dict()` — **déterministe**, sans horodatage (le CLI ajoute
`generated_at` à côté, jamais dedans).

```jsonc
{
  "protocol": { "min_trades": 30, "false_discovery_rate": 0.1,
                "monte_carlo_iterations": 1000,
                "walk_forward": { ... }, "costs": { ... } },
  "markets":  [ { "market": "frxXAUUSD", "bars": 1500, "train_bars": 900,
                  "holdout_bars": 300, "walk_forward_folds": 6,
                  "holdout_unlocks": 1, "skipped": null } ],
  "families": [ { "family": "trend_following", "tested": 24,
                  "retained_before_correction": 1, "retained": 0,
                  "discarded": 24, "expected_false_discoveries": 2.4,
                  "failures": [ { "cause": "too_few_trades", "count": 23,
                                  "description": "trop peu d'opérations ..." } ] } ],
  "multiple_testing": { "method": "benjamini_hochberg", "alpha": 0.1,
                        "expected_false_discovery_rate": 0.1,
                        "expected_false_discoveries": 5.1, "hypotheses": 51,
                        "bonferroni_threshold": 0.00196078431372549,
                        "discoveries_before": 1, "discoveries_after": 0,
                        "rejected_by_correction": 1 },
  "totals":   { "tested": 51, "retained_before_correction": 1, "retained": 0,
                "discarded": 51, "failures": [ ... ] },
  "candidates": [ { "market": "...", "family": "...", "label": "...", "retained": false,
                    "cause": "overfitting", "parameters": { ... }, "p_value": null,
                    "train_net_profit": "...", "out_of_sample_net_profit": null,
                    "walk_forward_ratio": 0.0, "holdout_was_read": false,
                    "stability": { "score": ..., "parameter_dispersion": ...,
                                    "reasons": [ ... ] } } ]
}
```

Quatre lectures utiles :

1. `families[].failures` — **le livrable principal du §7** : combien de candidats par
   famille, combien écartés, et pourquoi. Une famille qui ne retient rien n'est pas un bug :
   c'est un résultat.
2. `markets[].holdout_unlocks` — combien de fois le scellé a été ouvert. Zéro signifie
   qu'aucun candidat n'a même mérité d'être confirmé.
3. `walk_forward_ratio` (plis profitables / plis joués) et
   `stability.parameter_dispersion` : les deux signatures du surapprentissage.
4. `multiple_testing` et `candidates[].p_value` — ce que la sélection coûte : combien de
   survivants avant correction, combien après, et à quel point le meilleur d'entre eux
   ressemble à un tirage chanceux.

## Commandes

```bash
# Découverte sur données synthétiques à graine fixe (3 marchés, 1500 bougies)
uv run python scripts/backtest/discover.py
uv run python scripts/backtest/discover.py --bars 2500 --seed 20261007

# Découverte sur les jeux réels figés de TASK-060
uv run python scripts/backtest/discover.py --datasets docs/research/datasets

# Sous-ensemble de familles
uv run python scripts/backtest/discover.py --families momentum,mean_reversion,breakout

# Garde-fous
uv run pytest -q tests/research tests/backtest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Le CLI écrit `docs/research/<AAAA-MM-JJ>-discovery.json` et n'écrit rien ailleurs. Il ne
charge même pas `strategies.registry` : aucune promotion ne peut partir d'ici.

Le rapport contient les empreintes SHA-256 des jeux de données : `detect-secrets` les voit
comme des chaînes hexadécimales à forte entropie (faux positif d'empreinte, comme pour
`2026-10-07-campaign.json`). Le fichier étant un artefact généré, le Lead l'ajoute au
`.secrets.baseline` comme les autres rapports, ou ne le versionne pas.

## Résultats réels (2026-10-07)

Le CLI écrase le rapport du jour à chaque exécution (comme `run_campaign.py`) : le fichier
`docs/research/2026-10-07-discovery.json` correspond à la **dernière** commande lancée.

### Jeu réel figé XAUUSD M15 (3999 bougies, empreinte `f0d6c90ff767…`)

1 marché, 17 candidats (6 familles), mêmes coûts et mêmes seuils que ci-dessous.

| Famille | Testés | Retenus **avant** | Retenus **après** | Fausses déc. attendues | Causes |
|---|---:|---:|---:|---:|---|
| `trend_following` | 8 | 0 | 0 | 0,80 | `overfitting` 6, `unstable` 2 |
| `momentum` | 2 | 0 | 0 | 0,20 | `overfitting` 2 |
| `mean_reversion` | 2 | 1 | **0** | 0,20 | `false_discovery` 1, `overfitting` 1 |
| `breakout` | 2 | 0 | 0 | 0,20 | `overfitting` 2 |
| `volatility_breakout` | 2 | 0 | 0 | 0,20 | `overfitting` 2 |
| `ensemble` | 1 | 0 | 0 | 0,10 | `overfitting` 1 |
| **Total** | **17** | **1** | **0** | **1,70** | `overfitting` 14, `unstable` 2, `false_discovery` 1 |

Sélection multiple : `alpha = 0.10`, 17 tests, seuil de Bonferroni `0,005882`,
`discoveries_before = 1`, `discoveries_after = 0`, `rejected_by_correction = 1`.

L'ancien survivant `mean_reversion:00` (RSI 14, 30/70, cible 2R) affichait +41,98 €
hors-échantillon sur 21 opérations, après coûts. Sa p-value hors-échantillon est **0,2438** :
un ruban de 21 opérations dont la direction serait tirée à pile ou face atteint ce total dans
près d'un cas sur quatre. Le seuil que son rang exigeait était `0,10 / 17 = 0,0059`. Le
candidat n'est donc plus retenu du tout : `false_discovery`. C'est exactement la question que
le §7 laissait ouverte — « 17 essais, 1 survivant » — et la réponse honnête est « rien ne le
distingue encore du hasard ». Le scellé a été ouvert une fois, pour ce seul candidat.

### Jeu synthétique à graine fixe (3 marchés, graine 20261007, 1500 bougies M15)

Coûts 5 bp de spread + 2 bp de slippage + 0,50 € de commission, seuils du protocole par
défaut.

| Famille | Testés | Retenus **avant** | Retenus **après** | Fausses déc. attendues | Causes |
|---|---:|---:|---:|---:|---|
| `trend_following` | 24 | 0 | 0 | 2,40 | `too_few_trades` 23, `parameter_dispersion` 1 |
| `momentum` | 6 | 0 | 0 | 0,60 | `overfitting` 5, `parameter_dispersion` 1 |
| `mean_reversion` | 6 | 0 | 0 | 0,60 | `too_few_trades` 6 |
| `breakout` | 6 | 0 | 0 | 0,60 | `too_few_trades` 4, `overfitting` 2 |
| `volatility_breakout` | 6 | 1 | **0** | 0,60 | `overfitting` 2, `unstable` 2, `false_discovery` 1, `too_few_trades` 1 |
| `ensemble` | 3 | 0 | 0 | 0,30 | `overfitting` 3 |
| **Total** | **51** | **1** | **0** | **5,10** | `too_few_trades` 34, `overfitting` 12, `parameter_dispersion` 2, `unstable` 2, `false_discovery` 1 |

Sélection multiple : `alpha = 0.10`, 51 tests, seuil de Bonferroni `0,001961`,
`discoveries_before = 1`, `discoveries_after = 0`, `rejected_by_correction = 1`.

Le survivant `volatility_breakout:00` (or synthétique) portait +17,12 € hors-échantillon sur
9 opérations, p-value **0,3586** : la correction l'écarte, et 9 opérations ne pesaient de
toute façon pas lourd. Les **5,10 fausses découvertes attendues** disent le fond de
l'affaire : à 51 essais et `alpha = 0.10`, environ cinq candidats peuvent franchir un seuil
par test naïf sans le moindre edge. Les écartements restent le résultat le plus utile : ils
disent où le laboratoire a cherché, et pourquoi cela n'a pas tenu.

## Limites et restes

- Aucun historique Deriv versionné au-delà du jeu XAUUSD de démonstration : la découverte
  tourne donc surtout sur du synthétique à graine fixe. Les conclusions de familles ne
  valent que sous réserve de données réelles (plusieurs marchés, plusieurs régimes).
- `min_trades = 30` (seuil du protocole) élimine beaucoup de candidats sur 900 bougies
  d'apprentissage : c'est voulu — un résultat sur 12 opérations n'est pas un résultat.
- Les ensembles sont un consensus de deux indicateurs, pas encore une agrégation de
  stratégies complètes avec quorum ; c'est une extension naturelle du catalogue.
- **Le nombre de candidats reste faible** (17 à 51) et la correction a peu de puissance : avec
  un ou deux survivants, Benjamini-Hochberg est numériquement presque Bonferroni, et un vrai
  signal peut être écarté faute de preuve, pas parce qu'il est faux. Un écartement
  `false_discovery` veut dire « pas encore démontré », jamais « mauvais ».
- **Les candidats ne sont pas indépendants** : même règle à paramètres voisins dans une
  famille, ruban partagé entre marchés. BH suppose l'indépendance ou une dépendance positive
  (PRDS) — hypothèse de travail plausible pour des paramètres voisins, mais non démontrée ici.
- La p-value est un test de randomisation par **inversion de signe** sur les opérations du
  scellé : elle ignore les régimes groupés et les positions qui se chevauchent (donc plutôt
  optimiste), et sa résolution est plafonnée par `monte_carlo_iterations` (défaut 1000).
- Le laboratoire ne teste qu'**un** catalogue pré-défini : la correction protège la sélection
  telle qu'elle a eu lieu, elle ne remplace pas un protocole pré-enregistré.
