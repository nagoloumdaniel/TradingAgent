# Recherche et backtesting (TASK-060 → TASK-066)

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
| `research/protocol.py` | découpage train/validation/hors-échantillon scellé, walk-forward, perturbation, Monte-Carlo (bootstrap **et** p-value par inversion de signe), contrôle de Benjamini-Hochberg, stabilité paramétrique et par période, verdicts de porte (`GateVerdict`) |
| `research/campaign.py` | campagne par marché, sélection sur la robustesse, corrélation inter-marchés, évaluation des neuf portes de §49 (sept mesurables hors ligne, deux déclarées non évaluables) |
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

# Campagne sur un jeu réel figé (TASK-060), neuf portes de §49 (TASK-066)
uv run python scripts/backtest/fetch_mt5_dataset.py --symbol XAUUSD --bars 4000
uv run python scripts/backtest/run_campaign.py --datasets docs/research/datasets

# Plus de tirages Monte-Carlo (la p-value ne résout pas sous 1/(tirages+1))
uv run python scripts/backtest/run_campaign.py --datasets docs/research/datasets \
    --monte-carlo-iterations 5000

# Garde-fous
uv run pytest tests/backtest tests/research -q
```

## Jeu réel de démonstration

`docs/research/datasets/XAUUSD-M15-mt5-2026-10-07.jsonl` — 3999 bougies M15, du
2026-08-06 au 2026-10-07 (UTC), lues via `MarketDataClient` (lecture seule), horloge serveur
vérifiée à UTC, 18 trous recensés par le calendrier appris (4 semaines) et **non comblés**.
Empreinte SHA-256 : `f0d6c90ff767…`. Les noms MT5 (`XAUUSD`) diffèrent des noms API Deriv
(`frxXAUUSD`) : le harnais travaille sur le symbole du jeu fourni.

## Les neuf portes de §49 (TASK-066)

Une campagne n'explore pas seulement : elle **valide**. Chaque candidat est mesuré sur les
portes qu'une histoire figée peut trancher, et le rapport porte le verdict de chacune des
neuf, avec le chiffre, le seuil, **la fonction qui l'a produite** et la raison. Deux portes
ne sont pas évaluables ici, et le rapport le dit au lieu de les compter comme franchies :
une porte « passée » sans preuve serait le pire résultat possible d'une validation.

| Porte | Évaluée par | Chiffre | Seuil | Verdict |
|---|---|---|---|---|
| `backtest` | `campaign._run_candidate` → `harness.run_backtest` (fenêtre d'apprentissage) | 38 opérations (XAUUSD) / 60 (BTCUSD) | 30 | **passée** |
| `costs` | `run_backtest` avec le `CostModel` facturé | PF net 1.18 (XAUUSD), 1.34 (BTCUSD) | 1.20 | échouée sur XAUUSD |
| `walk_forward` | `protocol.walk_forward` à origine glissante, 6 plis de 250 bougies | 1/6 plis profitables (17 %) | 50 % | échouée |
| `out_of_sample` | `protocol.confirm` — **une** ouverture du scellé par marché, pour le sélectionné | 14 opérations, rétention 0.43 (XAUUSD) | 30 op. / 0.50 | échouée |
| `monte_carlo` | `protocol.monte_carlo` (bootstrap) **et** `protocol.monte_carlo_p_value` (inversion de signe) | P(profit) 0.62 (XAUUSD), 0.83 (BTCUSD) | 0.50 | **passée** |
| `stress` | `CostModel.stressed(2.0)` rejoué par `run_backtest` | PF 1.18 à coûts ×2 (XAUUSD) | 1.20 | échouée sur XAUUSD |
| `parameter_robustness` | `protocol.perturb_parameters` (±10 %) → `protocol.parameter_dispersion` | dispersion 0.62 (XAUUSD), 0.22 (BTCUSD) | 0.50 | échouée sur XAUUSD |
| `paper` | aucune : TASK-071 mesure 30 jours calendaires et 30 opérations par stratégie (Q-15) | — | — | **non évaluable** |
| `risk` | aucune : le moteur de risque de production écrit ses verdicts via `StrategyRegistry.record_validation` | — | — | **non évaluable** |

Le tableau ci-dessus est l'agrégat de la campagne : une porte n'est « passée » que si le
**candidat sélectionné par chaque marché** la franchit. Les chiffres de chaque marché et de
chaque candidat — y compris ceux qui n'ont pas été sélectionnés — sont dans le JSON, sous
`markets[].gates` et `markets[].candidates[].gates`.

Trois propriétés portent le reste, et chacune a ses tests :

1. **La p-value existe et le contrôle l'applique.** `run_campaign.py` transmet la probabilité
   Monte-Carlo à `promotion.evidence_from_campaign`. Sans elle, `evaluate_promotion` **saute**
   le contrôle : c'était le défaut d'origine. `require_monte_carlo_evidence` refuse désormais
   de décider si la probabilité manque.
2. **La correction de sélection multiple paie chaque essai.** Un test par couple
   (candidat, marché) : 6 ici. Un candidat démoli par la correction **perd sa revendication**
   (`significant = False`, donc absent de `CampaignReport.claims()`) sans que ses mesures
   soient réécrites : une correction de sélection ne falsifie pas un chiffre de backtest.
3. **La sélection ne lit aucun scellé.** Le découpage, le classement et les mesures se font
   d'abord ; le scellé n'est ouvert qu'ensuite, une fois par marché, et seulement parce que
   `run_campaign` le demande (`confirm_holdout=True`).

### Ce que vaut la p-value sur les données réelles

Campagne du 2026-10-08, XAUUSD et BTCUSD M15 (3 999 bougies chacun), `witness`, 3 jeux de
paramètres, alpha = 0.10, six hypothèses :

| Candidat | P(profit) | p-value | Rang | Seuil BH de son rang | Significatif |
|---|---|---:|---:|---:|---|
| BTCUSD `fast-1.5R` | 0.833 | 0.1638 | 1 | 0.0167 | non |
| BTCUSD `balanced-2R` | 0.010 | 0.9780 | 6 | 0.1000 | non |
| BTCUSD `slow-2.5R` | 0.131 | 0.8032 | 5 | 0.0833 | non |
| XAUUSD `fast-1.5R` | 0.201 | 0.8022 | 4 | 0.0667 | non |
| XAUUSD `balanced-2R` | 0.616 | 0.2957 | 2 | 0.0333 | non |
| XAUUSD `slow-2.5R` | 0.327 | 0.4865 | 3 | 0.0500 | non |

Survivants avant correction **6** → après correction **0**. Autrement dit : sur ces deux
jeux, **aucun candidat ne se distingue du hasard du nombre d'essais**, et le seuil de
Bonferroni affiché pour comparaison vaut 0.0167. C'est le résultat attendu d'une stratégie de
référence sans avantage démontré, et c'est exactement ce que la campagne laissait implicite
avant.

La p-value est mesurée sur la **fenêtre de validation**, pour **tous** les candidats, avant
toute ouverture du scellé. Mesurer la p-value du seul sélectionné sur un scellé ouvert pour
lui reviendrait à le noter sur des données que sa propre sélection a déjà vues ; mesurer les
six sur le scellé coûterait six ouvertures. La mesure est donc une borne de validation, et
elle est nommée comme telle dans chaque verdict (`measurement`).

Deux détails qui expliquent les chiffres :

- `BTCUSD fast-1.5R` affiche une probabilité de profit de 0.833 (le bootstrap du P&L réalisé
  répond à « quels autres ordres de ces mêmes opérations ? ») **et** échoue la porte : sa
  p-value de 0.1638 ne franchit pas le seuil de son rang, et ses 6 plis de walk-forward ne
  sont profitables qu'une fois. Une probabilité de profit élevée n'est pas un edge.
- `walk_forward` compte les plis **joués**. Un pli trop court pour nourrir l'historique
  déclaré du manifeste est *ignoré* et compté séparément : un bloc de 100 bougies ne laisse
  pas la stratégie de référence ouvrir une seule opération, et compter six plis vides comme
  six pertes serait un verdict sur la largeur de la fenêtre, pas sur la règle. Si aucun pli
  n'est jouable, la porte est **non évaluable**, pas « échouée ».

### Ce qu'il faudrait pour évaluer `paper` et `risk`

- **`paper`** : faire tourner l'agent en mode `PAPER` (`uv run tradingagent-run`) pendant au
  moins 30 jours calendaires, avec au moins 30 opérations par stratégie (Q-15), puis comparer
  au backtest de référence (`reporting/comparison.py`, TASK-093) et enregistrer le verdict.
  Aucun backtest ne peut produire du temps réel écoulé.
- **`risk`** : faire passer de vrais signaux par le moteur de risque de production (capital,
  marge, perte quotidienne, exposition) et enregistrer les verdicts
  (`StrategyRegistry.record_validation`). Une campagne observe une simulation à taille
  simulée et sans compte : mesurer cette porte ici noterait un autre objet que celui dont
  elle parle.

Tant que ces deux portes restent ouvertes, `registry.can_promote` refuse la mise en
production, et c'est correct : la campagne ne peut pas les fermer à la place de
l'exploitation.

### Relire une campagne

`docs/research/<AAAA-MM-JJ>-campaign.json` contient `gates` (les neuf, agrégées),
`markets[].gates` (les neuf par marché), `markets[].candidates[].gates` (les neuf par
candidat), `multiple_testing` et `gate_protocol` (plan de walk-forward, tirages Monte-Carlo,
multiplicateur de stress, alpha). Chaque `gate` porte `status`, `evaluated`, `passed`,
`evaluator`, `reason`, `threshold` et `evidence` : le rapport répond aux quatre questions
« laquelle, avec quel chiffre, par quelle fonction, et pourquoi ».

## Résultats de campagne (2026-10-08)

Jeu réel XAUUSD et BTCUSD M15 ci-dessus, stratégie de référence `witness`, 3 jeux de
paramètres, coûts : spread 5 bp, slippage 2 bp, commission 0,5 €, plan de walk-forward
350/250/200 (max 6 plis), 1 000 tirages Monte-Carlo.

Sélection par marché : `balanced-2R` sur XAUUSD (stabilité 0.41), `fast-1.5R` sur BTCUSD
(stabilité 0.87) — **sur la stabilité**, jamais sur le profit net. Corrélation BTC/XAU
`+0.333` sur 2 637 barres (pas de risque cumulé).

Portes franchies par la campagne : `backtest` et `monte_carlo`. Promotion **refusée**,
et le refus est enregistré avec ses motifs : 14 opérations hors échantillon < 30, rétention
0.43 < 0.50, dispersion 0.62 > 0.50, score 0.41 < 0.50, PF net 1.18 < 1.20, puis « le candidat
a gagné son marché mais la correction de Benjamini-Hochberg l'a démoli : ce n'est pas une
découverte ». La probabilité Monte-Carlo vérifiée vaut **0.616** : elle est désormais
transmise, lue et appliquée, au lieu d'être absente et silencieusement sautée.

Le second motif compte autant que les seuils : la campagne a **deux** portes franchies et
**zéro** découverte. Une porte franchie sur un candidat n'est pas une promotion — la
correction de sélection multiple a démoli les six candidats, et le rapport le dit avant les
seuils plutôt qu'après.

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
- **Défaut connexe relevé, non corrigé ici — `discovery.py`.** Le plan de walk-forward par
  défaut du laboratoire (`DiscoveryProtocol.walk_forward`, 250/100/150) laisse 100 bougies de
  validation. Mesuré sur les deux jeux réels, la stratégie de référence n'y ouvre **aucune**
  opération : les plis existent mais ne tradent pas, donc `walk_forward.ratio` vaut 0 et la
  porte roulante est décidée sur une fenêtre vide. Depuis le correctif de TASK-066 un pli
  **absent** est `insufficient_data` (et non `overfitting`), ce qui est plus honnête mais pas
  encore une mesure. Le correctif complet consiste à aligner `DiscoveryProtocol.walk_forward`
  sur les 250 bougies de validation de la campagne, puis à régénérer
  `docs/research/<date>-discovery.json` : cela change le tableau des causes du laboratoire et
  mérite sa propre revue, donc il n'a pas été fait dans cette session. La campagne, elle,
  utilise déjà un plan qui trade (350/250/200).
- Les rapports `docs/research/<date>-campaign.json` et `<date>-discovery.json` contiennent les
  empreintes SHA-256 des jeux : `detect-secrets` les voit comme des chaînes à forte entropie
  (faux positif connu). Le fichier étant un artefact généré, le Lead l'ajoute au
  `.secrets.baseline` comme les autres rapports, ou ne le versionne pas.
- Les rapports `<date>-campaign.json` et `decisions/*.json` sont **écrasés** à chaque
  exécution : le fichier du jour correspond à la dernière commande lancée, pas à un historique.
