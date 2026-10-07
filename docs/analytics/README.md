# Statistiques de scalping (cahier v3 §32)

Paquet `tradingagent.analytics` — statistiques par heure, session, jour, spread, volatilité,
durée et taille, plus les coûts d'exécution. Deux couches, une seule frontière :

| Couche | Fichier | Règle |
|---|---|---|
| Calcul pur | `src/tradingagent/analytics/scalping.py` | aucune horloge, aucun réseau, aucun I/O. N'importe que `core` (`tests/test_architecture.py`). Typage `mypy` strict. |
| Lecture base | `src/tradingagent/storage/scalping.py` | la **seule** couche qui touche les tables pour ces statistiques. Fournit les lectures manquantes sous forme de callables. |

Le paquet `analytics` ne lit donc jamais la base : une statistique qui a besoin d'une donnée
absente de `Trade` reçoit la lecture **en paramètre**. C'est ce qui permet de mesurer une
fenêtre de backtest et une fenêtre de production avec exactement le même code (C-001).

## Ce qui est calculé, et où

| Statistique §32 | Fonction | Source de la donnée |
|---|---|---|
| Performance par heure | `by_hour(trades)` | `Trade.closed_at.hour`, bucket `"00"`–`"23"` |
| Performance par session | `by_session(trades)` / `session_of(trade)` | `Trade.closed_at`, sessions UTC demi-ouvertes `[0,7[` asie, `[7,13[` londres, `[13,21[` new_york, `[21,24[` apres_cloture |
| Performance par jour | `by_weekday(trades)` (jour de la semaine), `axes.group(trades, Axis.MONTH)` (mois), `storage.daily.DailyPerformanceStore` (jour calendaire agrégé) | `Trade.closed_at` |
| Performance par spread | `by_spread(trades, edges)` | `Trade.spread`, en unités de spread observé |
| Performance par volatilité | `by_volatility(trades, volatility_of, edges)` avec `storage.scalping.volatility_of(engine)` | ATR stocké dans `signals.indicators` (`atr`, `ATR` ou `atr14`), clé `(symbol, positions.opened_at)` |
| Performance par durée | `by_duration(trades, edges)` | `closed_at - opened_at`, seuils en **secondes** |
| Performance par taille | `by_size(trades, size_of, edges)` avec `storage.scalping.size_of(engine)` | `positions.volume`, seuils `Decimal` |
| Slippage moyen et maximal | `cost_summary(trades)` (slippage enregistré par trade), `storage.scalping.execution_costs(engine)` (slippage mesuré des fills) | `Trade.slippage` ; détail de l'événement `filled` dans `execution_events` |
| Spread moyen | `cost_summary(trades).average_spread` | `Trade.spread` |
| Temps moyen d'exécution | `storage.scalping.execution_costs(engine)` : **médiane** et pire latence `order_sent → filled` | `execution_events` (`storage.telemetry.ExecutionEventStore.latency`) |
| Coût moyen par trade (€) | `cost_summary(trades, cost_eur_of=...)` | aucune colonne de coût en euros : la lecture est fournie par l'appelant |

### Conventions, explicites parce qu'elles se voient dans les chiffres

- **Bornes** : une bande est `[e_i, e_i+1[`. La borne basse est incluse, la haute exclue.
  `edges=(0.5, 1.0)` range un spread de `0.5` dans `[0.5,1[` et un spread de `1.0` dans `>=1`.
  Les clés de bande portent la convention : `"<0.5"`, `"[0.5,1["`, `">=1"`, et `"all"` sans seuil.
- **Seuils** : jamais codés en dur, toujours passés en paramètre, et refusés s'ils ne sont pas
  strictement croissants (`ValueError`).
- **Ordre** : chronologique pour l'heure (`00`→`23`), l'ordre des sessions, puis lundi→dimanche ;
  ordre des seuils pour les bandes. À l'intérieur d'un `Bucket`, les trades sont triés par
  `(closed_at, opened_at, symbol, strategy_ref)` : le résultat ne dépend pas de l'ordre d'entrée.
- **Séries vides** : une série vide donne un tuple vide, une bande que personne n'occupe est
  **absente** et non remplie de zéros. `win_rate` n'est `None` que pour un bucket sans trade.
- **Honnêteté des `None`** : un trade sans spread, sans slippage ou sans durée lisible sort de la
  statistique qui en a besoin ; chaque moyenne est accompagnée de son échantillon (`sample`,
  `slippage_sample`, `spread_sample`, `cost_sample`) pour qu'on ne la lise pas comme si elle
  couvrait toute la série. Un `None` ne devient jamais `0`.
- **`Trade.slippage` est une magnitude** (les deux courtiers enregistrent `abs(exécuté - demandé)`) :
  la moyenne et le pire sont pris tels quels.

## Ce qui reste indisponible, faute de donnée

- **Coût moyen par trade en euros.** Aucune table ne stocke le coût d'un trade en euros, et `Trade`
  n'en porte pas. `cost_summary` accepte donc `cost_eur_of` ; sans lui, `average_cost_eur` reste
  `None` et `cost_sample` vaut `0`. Rien n'est dérivé du risque ou de la taille, qui ne sont pas
  des coûts.
- **Volatilité et taille pour un trade hors base.** `volatility_of` et `size_of` s'appuient sur la
  chaîne `positions → orders → signals` encore stockée, et sur la clé `(symbol, opened_at)`. Un
  trade dont la position n'est plus dans la chaîne, ou dont le signal n'a pas stocké d'ATR, lit
  `None` et quitte la bande. Une volatilité n'est pas déduite d'une autre période.
- **Spread.** Il n'existe que si le producteur du `Trade` l'a rempli ; `ai.daily` construit par
  exemple ses trades avec `spread=None`, donc les bandes de spread ne couvrent que les trades qui
  le portent réellement, et `spread_sample` le dit.
- **Sessions de marché réelles.** MT5 n'expose pas les horaires de session
  (`docs/reports/2026-10-03-mt5-capabilities.md`) : les quatre sessions ci-dessus sont une
  convention UTC calculée depuis l'heure de clôture, pas la session annoncée par le courtier.
- **Temps d'exécution moyen au sens arithmétique.** `execution_costs` renvoie la **médiane** et la
  pire latence, pas la moyenne : la latence a une queue épaisse, et un unique décrochage ferait
  mentir une moyenne. La fenêtre de slippage est bornée (`limit`, 1000 événements les plus récents
  par défaut) pour ne pas balayer toute la table.
- **Dérive backtest contre réel, exposition moyenne, disponibilité** : hors du §32, traitées
  ailleurs (§14.2 pour les indicateurs, §47 pour la télémétrie).

## Tests

- `tests/analytics/test_scalping.py` : valeurs calculées à la main, bornes inclusives/exclusives,
  séries vides, déterminisme, `None` propagé.
- `tests/storage/test_scalping.py` : chaîne complète `signals → orders → positions → trades` plus
  événements `execution_events`, latence médiane/pire et slippage recalculés à la main.

```bash
uv run pytest -q tests/analytics tests/storage
```
