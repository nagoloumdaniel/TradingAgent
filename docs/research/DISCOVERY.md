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

Un candidat n'est **retenu** que s'il franchit : walk-forward **et** stabilité des
paramètres **et** hors-échantillon scellé. Aucune exception, aucun raccourci.

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

## Lire le rapport

`DiscoveryReport.to_dict()` — **déterministe**, sans horodatage (le CLI ajoute
`generated_at` à côté, jamais dedans).

```jsonc
{
  "protocol": { "min_trades": 30, "walk_forward": { ... }, "costs": { ... } },
  "markets":  [ { "market": "frxXAUUSD", "bars": 1500, "train_bars": 900,
                  "holdout_bars": 300, "walk_forward_folds": 6,
                  "holdout_unlocks": 1, "skipped": null } ],
  "families": [ { "family": "trend_following", "tested": 24, "retained": 0,
                  "discarded": 24,
                  "failures": [ { "cause": "too_few_trades", "count": 23,
                                  "description": "trop peu d'opérations ..." } ] } ],
  "totals":   { "tested": 51, "retained": 1, "discarded": 50, "failures": [ ... ] },
  "candidates": [ { "market": "...", "family": "...", "label": "...", "retained": false,
                    "cause": "overfitting", "parameters": { ... },
                    "train_net_profit": "...", "out_of_sample_net_profit": null,
                    "walk_forward_ratio": 0.0, "holdout_was_read": false,
                    "stability": { "score": ..., "parameter_dispersion": ...,
                                    "reasons": [ ... ] } } ]
}
```

Trois lectures utiles :

1. `families[].failures` — **le livrable principal du §7** : combien de candidats par
   famille, combien écartés, et pourquoi. Une famille qui ne retient rien n'est pas un bug :
   c'est un résultat.
2. `markets[].holdout_unlocks` — combien de fois le scellé a été ouvert. Zéro signifie
   qu'aucun candidat n'a même mérité d'être confirmé.
3. `walk_forward_ratio` (plis profitables / plis joués) et
   `stability.parameter_dispersion` : les deux signatures du surapprentissage.

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

| Famille | Testés | Retenus | Écartés | Causes |
|---|---:|---:|---:|---|
| `trend_following` | 8 | 0 | 8 | `overfitting` 6, `unstable` 2 |
| `momentum` | 2 | 0 | 2 | `overfitting` 2 |
| `mean_reversion` | 2 | **1** | 1 | `overfitting` 1 |
| `breakout` | 2 | 0 | 2 | `overfitting` 2 |
| `volatility_breakout` | 2 | 0 | 2 | `overfitting` 2 |
| `ensemble` | 1 | 0 | 1 | `overfitting` 1 |
| **Total** | **17** | **1** | **16** | `overfitting` 14, `unstable` 2 |

Le seul retenu est `mean_reversion:00` (RSI 14, 30/70, cible 2R) : +41,98 € hors-échantillon
sur 21 opérations, après coûts. C'est une **hypothèse à instruire**, pas une recommandation :
21 opérations restent peu, et le seuil de promotion exige davantage. Le scellé a été ouvert
une fois, pour ce seul candidat.

### Jeu synthétique à graine fixe (3 marchés, graine 20261007, 1500 bougies M15)

Coûts 5 bp de spread + 2 bp de slippage + 0,50 € de commission, seuils du protocole par
défaut.

| Famille | Testés | Retenus | Écartés | Causes |
|---|---:|---:|---:|---|
| `trend_following` | 24 | 0 | 24 | `too_few_trades` 23, `parameter_dispersion` 1 |
| `momentum` | 6 | 0 | 6 | `overfitting` 5, `parameter_dispersion` 1 |
| `mean_reversion` | 6 | 0 | 6 | `too_few_trades` 6 |
| `breakout` | 6 | 0 | 6 | `too_few_trades` 4, `overfitting` 2 |
| `volatility_breakout` | 6 | 1 | 5 | `overfitting` 2, `unstable` 2, `too_few_trades` 1 |
| `ensemble` | 3 | 0 | 3 | `overfitting` 3 |
| **Total** | **51** | **1** | **50** | `too_few_trades` 34, `overfitting` 12, `parameter_dispersion` 2, `unstable` 2 |

Le seul candidat retenu (`volatility_breakout` sur l'or synthétique) n'est pas non plus une
recommandation. Les écartements sont le résultat le plus utile : ils disent où le laboratoire
a cherché, et pourquoi cela n'a pas tenu.

## Limites et restes

- Aucun historique Deriv versionné au-delà du jeu XAUUSD de démonstration : la découverte
  tourne donc surtout sur du synthétique à graine fixe. Les conclusions de familles ne
  valent que sous réserve de données réelles (plusieurs marchés, plusieurs régimes).
- `min_trades = 30` (seuil du protocole) élimine beaucoup de candidats sur 900 bougies
  d'apprentissage : c'est voulu — un résultat sur 12 opérations n'est pas un résultat.
- Les ensembles sont un consensus de deux indicateurs, pas encore une agrégation de
  stratégies complètes avec quorum ; c'est une extension naturelle du catalogue.
- Le laboratoire ne fait aucune sélection multiple : chaque candidat est jugé seul. Un
  contrôle du taux de fausses découvertes sur le nombre total de candidats testés reste à
  ajouter (voir §7, « ne pas supposer qu'une famille est meilleure qu'une autre »).
