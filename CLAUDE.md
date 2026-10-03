# TradingAgent

Agent autonome 24/7 de signaux de trading sur Deriv (or `frxXAUUSD` + 2 à 4 cryptos en CFD), notifiés via Telegram.

## Documents de référence

- `CAHIER_DES_CHARGES.md` : le pourquoi. Fonctionnalités `F-xxx`, exigences `EF-xxx`, règles métier `RM-xxx`, contradictions `C-xxx`.
- `ROADMAP.md` : le quoi et l'ordre. Tâches `TASK-xxx`, quality gates, mapping des skills par tâche.

Lire `ROADMAP.md` → section `Project Context` avant toute tâche. Ne pas rouvrir les décisions marquées closes.

## Commandes

```bash
uv sync                      # installer
uv run pytest -q             # tests
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy                  # typage
```

## Invariants non négociables

- Seul `risk` importe `execution` (exception : la racine de composition `tradingagent.app`). Vérifié par `tests/test_architecture.py`.
- Aucun module de production n'importe `backtest` ni `research`. Même test.
- L'IA ne peut que rejeter un signal, jamais en créer un, augmenter une taille ou desserrer un stop (C-002).
- Toute date est en UTC. Les datetimes naïfs sont interdits (règle ruff `DTZ`).
- Fonctions de calcul pures : pas d'état global, pas de lecture de l'heure courante, pas de réseau.
- Toute décision est persistée avec son motif, refus compris.
- Aucun secret dans le dépôt. Gabarit : `.env.example`.

## Méthode

TDD obligatoire sur `indicators`, `risk`, `analytics`, `execution`. Aucune tâche déclarée finie sans sortie de commande à l'appui.
