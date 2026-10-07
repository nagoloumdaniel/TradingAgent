# Recherche et backtesting (TASK-060 → TASK-065)

Atelier hors production : `tradingagent.backtest` et `tradingagent.research` ne sont jamais
importés par un module de production (`tests/test_architecture.py`). Le harnais réutilise la
voie de décision de production (`strategies.evaluation.evaluate`) et l'analytique de
production (`analytics.compute_performance`), donc un chiffre de backtest et un chiffre réel
sortent du même code (C-001).

## Composants

| Module | Rôle |
|---|---|
| `backtest/datasets.py` | jeux de données JSONL immuables, empreinte SHA-256, recensement des trous (`data.quality.missing_bars`) |
| `backtest/harness.py` | boucle de simulation (stop, objectifs, sorties partielles, trailing, positions simultanées, limites horaires), sortie `analytics.model.Trade` |
| `backtest/costs.py` | spread observé, slippage, commissions, retard d'exécution, majoration pour tests de robustesse |
| `backtest/randomness.py` | générateur splitmix64 déterministe (Monte-Carlo, données synthétiques) |
| `research/protocol.py` | découpage train/validation/hors-échantillon scellé, walk-forward, perturbation, Monte-Carlo, stabilité paramétrique et par période |
| `research/campaign.py` | campagne par marché, sélection sur la robustesse, corrélation inter-marchés |
| `research/promotion.py` | seuils figés avant résultats (digest), manifeste au format de production, décision horodatée |

## Anti-biais appliqués

- **Anticipation / look-ahead** : à chaque bougie `i`, la fenêtre passée à `evaluate()` est
  coupée par bisect sur `close_time`. La stratégie ne peut voir que des bougies clôturées au
  plus tard à `evaluated_at`. Le harnais ne lit de données postérieures que la bougie dans
  laquelle un ordre est effectivement rempli : c'est de l'exécution, pas de l'information.
- **Survivance** : aucune sélection d'univers rétrospective ; les jeux sont figés avec leur
  source et leur empreinte, les trous sont recensés et jamais comblés.
- **Surapprentissage (R-02)** : le hors-échantillon est scellé derrière un jeton ; toute
  optimisation qui tente de le lire échoue (`SealedAccessError`). La sélection se fait sur un
  score de stabilité (rétention hors échantillon, dispersion paramétrique, régimes
  profitables), pas sur le profit net.
- **Coûts** : spread, slippage, commission et retard d'exécution sont appliqués de façon
  adverse ; une majoration (`CostModel.stressed`) sert de test de robustesse.
- **Statistiques** : sous 30 opérations, l'analytique de production marque déjà
  `insufficient_sample`; le protocole exige `min_trades` avant toute promotion.
- **Méthode** : découpage chronologique (jamais de mélange), walk-forward à origine
  glissante, Monte-Carlo par bootstrap déterministe (graine fixe).

## Reproductibilité

- Les données synthétiques sortent d'un splitmix64 à graine fixe ; deux exécutions du même
  script donnent les mêmes empreintes.
- Chaque jeu porte `dataset_id`, période, source, nombre de bougies et empreinte SHA-256 ;
  `load_dataset` recalcule l'empreinte et refuse tout octet modifié.
- Les seuils de promotion portent un digest ; `evaluate_promotion` refuse une preuve mesurée
  contre un autre jeu de seuils.

## Commandes

```bash
# Campagne sur données synthétiques à graine fixe (par défaut)
uv run python scripts/backtest/run_campaign.py
uv run python scripts/backtest/run_campaign.py --bars 2500

# Campagne sur un jeu réel figé (TASK-060)
uv run python scripts/backtest/fetch_mt5_dataset.py --symbol XAUUSD --bars 4000
uv run python scripts/backtest/run_campaign.py --datasets docs/research/datasets

# Garde-fous
uv run pytest tests/backtest tests/research -q
```

## Jeu réel de démonstration

`docs/research/datasets/XAUUSD-M15-mt5-2026-10-07.jsonl` — 3999 bougies M15, du
2026-08-06 au 2026-10-07 (UTC), lues via `MarketDataClient` (lecture seule), horloge serveur
vérifiée à UTC, 18 trous recensés par le calendrier appris (4 semaines) et **non comblés**.
Empreinte SHA-256 : `f0d6c90ff767…`. Les noms MT5 (`XAUUSD`) diffèrent des noms API Deriv
(`frxXAUUSD`) : le harnais travaille sur le symbole du jeu fourni.

## Résultats de campagne (2026-10-07)

Jeu réel XAUUSD ci-dessus, stratégie de référence `witness` (non destinée au trading),
3 jeux de paramètres, coûts : spread 5 bp, slippage 2 bp, commission 0,5 € :

| Candidat | Stabilité | Train (€) | Validation (€) | Après coûts (€) | PF net | Fragile |
|---|---|---|---|---|---|---|
| fast-1.5R | 0.12 | +11.0 | −48.8 | −66.8 | 0.61 | oui |
| balanced-2R | 0.41 | +65.6 | +27.9 | +15.9 | 1.18 | oui |
| slow-2.5R | 0.00 | +64.9 | −5.7 | −11.5 | 0.80 | oui |

Sélection : `balanced-2R` **sur la stabilité**, pas sur le profit brut. Promotion **refusée**
(14 opérations hors échantillon < 30, rétention 0.43 < 0.50, dispersion 0.62 > 0.50, score
0.41 < 0.50, PF net 1.18 < 1.20) : le protocole refuse de promouvoir une stratégie de
référence sans avantage démontré. C'est le résultat attendu.

Campagne synthétique 8000 bougies (3 marchés, graine 20261007) : sélection également sur la
stabilité, et corrélations mesurées BTCUSD/ETHUSD `+0.770` (**élevée**), BTCUSD/XAUUSD
`+0.394`, ETHUSD/XAUUSD `+0.413` — l'or et la crypto ne cumulent pas le même risque, les
deux cryptos si (Q-07 : à vérifier sur les données définitives).

## Limites et restes

- Aucun historique Deriv versionné au-delà de ces ~4000 bougies : la profondeur
  réglementaire de TASK-060 reste à constituer (collecte continue).
- La sélection définitive des 2 à 4 cryptos (Q-07) n'est pas prononcée : elle exige des jeux
  réels par crypto, pas seulement le jeu XAUUSD de démonstration.
- Aucune promotion n'a été émise vers `config/strategies/` : ce répertoire appartient au
  Lead ; les manifestes candidats s'écrivent sous `docs/research/` et se chargent avec
  `load_strategy_catalog` sans modification de code.
