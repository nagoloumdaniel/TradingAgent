# Couche risque — inventaire des contrôles

Le moteur de risque est le dernier mot avant tout ordre (F-011). `decide(ctx, login)`
exécute chaque contrôle de `risk/checks.py`, puis calcule la taille (`risk/sizing.py`) et
rend **autorisé**, **réduit** ou **refusé**, avec tous les motifs. Le moteur reste pur :
aucune horloge, aucun réseau, aucune base ; `RiskContext` porte l'instant (`now`) et
l'état d'arrêt (`halt`).

Un seul contrôle n'est pas un verdict mais une erreur fatale : RM-017, la cohérence
compte/mode, levée par `core/account.py` et non listée ci-dessous.

## Les 17 contrôles

| # | Contrôle (`risk/checks.py`) | Règle métier | Ce qu'il refuse |
|---|---|---|---|
| 1 | `check_not_halted` | RM-015 — arrêt d'urgence | tout ordre tant qu'un arrêt est actif, quelle qu'en soit la source |
| 2 | `check_stop_loss` | RM-004 — stop-loss | stop absent, du mauvais côté, ou plus proche que le minimum du courtier |
| 3 | `check_entry_zone` | RM-012 — qualité d'exécution | prix exécutable hors de la zone d'entrée du signal |
| 4 | `check_slippage` | RM-012 — slippage | slippage attendu au-delà du plafond, exprimé en multiples du spread observé |
| 5 | `check_daily_loss` | RM-006 — perte quotidienne | perte du jour plus le risque entier de l'opération au-delà du seuil |
| 6 | `check_weekly_loss` | RM-007 — perte hebdomadaire | perte de la semaine plus le risque de l'opération au-delà du seuil |
| 7 | `check_drawdown` | RM-007 — drawdown | baisse depuis le pic d'équité au-delà du seuil |
| 8 | `check_open_positions` | RM-008 — nombre de positions | nombre maximal de positions simultanées atteint, tous marchés |
| 9 | `check_market_positions` | RM-008 — une position par marché | une position déjà ouverte sur le marché visé |
| 10 | `check_total_exposure` | RM-008 — exposition totale | exposition notionnelle ouverte **plus** celle de l'ordre au-delà du plafond |
| 11 | `check_trades_today` | RM-008 — opérations du jour | nombre maximal d'opérations quotidiennes atteint |
| 12 | `check_spread` | RM-012 — spread | spread observé supérieur au pourcentage configuré de la distance de stop |
| 13 | `check_margin` | RM-012 — marge | marge libre insuffisante pour porter le lot minimal |
| 14 | `check_account_currency` | Invariant — unités | compte dans une devise autre que l'EUR (les montants seraient mélangés) |
| 15 | `check_trading_hours` | F-005 — horaires | marché fermé ou non prouvé ouvert |
| 16 | `check_cooldown` | Refroidissement | reprise trop tôt après une série de pertes |
| 17 | `check_live_eligibility` | RM-019 — éligibilité au réel | en `LIVE`, lot minimal déjà au-dessus du risque autorisé |

S'y ajoute le contrôle `sizing` (RM-005 et section 9 du cahier), exécuté après les
17 autres : il borne la taille par le risque, la marge, la taille maximale de
l'instrument et le plafond configuré, et refuse si le lot minimal est inatteignable.
`decide` enregistre donc 18 verdicts ; la décision, les motifs et l'état du signal sont
persistés ensemble par `storage/risk_decisions.py`.

## Couverture du §21 du cahier

| Exigence du §21 | Où elle est couverte |
|---|---|
| Risque maximal par trade | `sizing` (RM-005), seuils par mode dans `RiskLimits` |
| Exposition maximale | `check_total_exposure` |
| Nombre maximal de positions | `check_open_positions`, `check_market_positions` |
| Perte maximale quotidienne | `check_daily_loss` |
| Perte maximale hebdomadaire | `check_weekly_loss` |
| Drawdown maximal | `check_drawdown` |
| Exposition simultanée BTC / XAU | `check_total_exposure` (somme tous marchés, avant comparaison) |
| Limite de taille | `sizing` (`volume_max`, `max_volume`) |
| Limite de slippage | `check_slippage`, complété par `check_entry_zone` |
| Limite de spread | `check_spread` |
| Arrêt d'urgence | `check_not_halted` (RM-015) |

## `check_total_exposure` — choix documentés

**La photo porte l'exposition courante.** `PortfolioState` gagne
`open_exposure_eur: Decimal | None`, agrégat notionnel en EUR de toutes les positions
ouvertes, **calculé par l'appelant**. C'est l'appelant — `runtime/portfolio.py` — qui
détient les spécifications d'instruments et les cotations des marchés qu'il n'est pas en
train d'examiner ; `OpenPosition` ne porte ni taille de contrat ni prix, un calcul interne
au moteur serait donc impossible. Le champ vaut `None` par défaut, ce qui laisse valides
tous les appelants existants.

**Le plafond est une fraction du capital**, jamais un montant : `RiskLimits.max_total_exposure`
(nouveau champ, défaut `DEFAULT_MAX_TOTAL_EXPOSURE = 2`) fois `RiskLimits.capital(equity)`,
qui en `LIVE` ne dépasse jamais le capital de référence déclaré.

**L'opération envisagée est comptée à sa taille réelle.** Les contrôles s'exécutent avant
le calcul de taille, mais `size_position` est pure : le contrôle la rejoue sur la même
photo et compte le volume qu'elle autoriserait, pas le lot minimal. Si la taille est
impossible, le lot minimal est compté (l'ordre est refusé par le contrôle `sizing` de
toute façon), pour ne jamais sous-estimer l'exposition.

**Défaut fermé sur une exposition inconnue.** Si des positions sont ouvertes et que
`open_exposure_eur` vaut `None`, le contrôle refuse : le plafond n'est jamais
silencieusement ignoré. Sans position ouverte, l'exposition courante vaut zéro sans
mesure. C'est le choix cohérent avec le reste de la couche (marge inconnue, éligibilité
inconnue : refus).

**PENDING de câblage (hors périmètre `risk/`) :** `runtime/portfolio.py` doit renseigner
`open_exposure_eur`, et `OpenPosition` reste construit avec `(symbole, volume)` par les
exécuteurs. Tant que ce n'est pas fait, le contrôle refuse un nouvel ordre dès qu'une
position est ouverte — le Lead doit câbler l'appelant ou assumer ce défaut fermé.

## `check_slippage` — choix documentés

Le plafond est exprimé **en multiples du spread observé**
(`RiskLimits.max_slippage_to_spread`, défaut `DEFAULT_MAX_SLIPPAGE_TO_SPREAD = 1`) : une
règle relative convient à l'or comme au bitcoin, pour la même raison que
`max_spread_stop_pct`. Le slippage attendu arrive par
`MarketQuote.expected_slippage: Decimal | None` — une estimation de l'appelant (fills
récents, latence du courtier, hypothèse du papier broker) en unités de prix.

Sans estimation, le contrôle **passe** et le dit : le garde-fou inconditionnel reste
`check_entry_zone`, qui refuse tout prix exécutable hors de la zone du signal, et
`MarketQuote` est construit par des appelants hors périmètre qu'un refus systématique
casserait. Le slippage **observé** après exécution est enregistré par l'exécution
(`OrderResult.slippage`) et analysé par `analytics`, pas filtré ici.

## Champs de configuration en attente

`config/agent.py` (`RiskProfile`) est hors du périmètre de la couche risque. Les deux
plafonds nouveaux vivent donc en constantes de module documentées dans `risk/model.py`,
recopiées par `limits_for` dans `RiskLimits` :

- `DEFAULT_MAX_TOTAL_EXPOSURE` → à porter en `RiskProfile.max_total_exposure_pct` ;
- `DEFAULT_MAX_SLIPPAGE_TO_SPREAD` → à porter en `RiskProfile.max_slippage_to_spread`.

`limits_for` est alors la seule ligne à changer.

## Explicitement hors périmètre

- **Plafond d'exposition par marché.** Le plafond agrégé ne distingue pas les marchés ;
  le nombre de positions par marché reste borné par `check_market_positions` (une
  position), pas par un montant notionnel par symbole.
- **Corrélation mesurée.** BTC et XAU sont additionnés sans coefficient : la couche
  suppose la corrélation la plus défavorable (1) au lieu de l'estimer. Une corrélation
  mesurée demanderait une histoire de prix et un modèle, donc du stockage et du calcul
  hors du moteur pur.
- **Slippage observé post-exécution** : mesuré et analysé par `execution` et `analytics`,
  pas par le moteur de risque, qui décide avant l'envoi.
- **Taille de position par marché / limites par instrument** : portées par
  `InstrumentSpec` et la formule de taille, pas par un plafond d'exposition.

## Vérification

```bash
uv run pytest -q tests/risk
uv run ruff check src/tradingagent/risk tests/risk
uv run ruff format --check src/tradingagent/risk tests/risk
uv run mypy
```
