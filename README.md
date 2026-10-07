<p align="center">
  <img src="src/tradingagent/web/static/nexagold.png" alt="NexaGold" width="88" height="88">
</p>

# TradingAgent

Plateforme de trading algorithmique **BTCUSD + XAUUSD** : recherche de stratégies assistée
par IA, backtest réaliste, validation anti-surapprentissage, exécution MT5 protégée par deux
Expert Advisors, monitoring web et amélioration continue.

Le projet est né d'un cahier des charges initial (`CAHIER_DES_CHARGES.md`, signaux Deriv
notifiés sur Telegram) puis a été étendu par le cahier **v3**
« AI Scalping Trading System » (`docs/v3/ARCHITECTURE.md` en est la carte, section par
section). L'ordre de développement est dans [`ROADMAP.md`](ROADMAP.md).

## Philosophie

> L'IA recherche et améliore les stratégies ; elle ne décide pas directement de chaque trade.

| Couche | Rôle | Ce qu'elle ne peut pas faire |
|---|---|---|
| Recherche IA | chercher, analyser, **proposer** | passer un ordre, modifier un stop, promouvoir |
| Backtest & validation | mesurer, éprouver la robustesse | décider seule d'une promotion |
| Risk engine | limiter, refuser, réduire | être contourné par une stratégie |
| Trading engine | exécuter la stratégie **validée** | exécuter une version non promue |
| Expert Advisors MT5 | exécuter, surveiller, protéger | décider d'un trade |
| Analytics & web | mesurer, observer | recalculer un chiffre critique |

## Installation

Prérequis : Windows, [uv](https://docs.astral.sh/uv/) (`winget install --id astral-sh.uv -e`)
et le terminal MetaTrader 5 installé depuis l'espace client Deriv.

```powershell
pwsh -File scripts/install_windows.ps1 -WhatIf   # simulation
pwsh -File scripts/install_windows.ps1           # installation reproductible depuis uv.lock
Copy-Item .env.example .env                      # puis renseigner les clés (jamais versionné)
uv run tradingagent status                       # vérifie l'accès à la base
```

`scripts/install_deps.ps1` ajoute les hooks pre-commit sur un poste de développement.

## Commandes

```bash
# Qualité
uv run pytest -q             # tests
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy                  # typage

# Exécution
uv run tradingagent-run                      # l'agent (boucle complète, 24/7)
uv run tradingagent-run --once               # un seul cycle, pour vérifier
uv run tradingagent-run --cycles 3           # nombre borné de cycles

# Monitoring (lecture seule)
uv run tradingagent-web                      # http://127.0.0.1:8787
uv run python scripts/preview_dashboard.py   # même dashboard, base jetable + fixtures

# Contrôle
uv run tradingagent doctor                   # quelles clés manquent, et où les trouver
uv run tradingagent status                   # arrêt d'urgence
uv run tradingagent halt --reason "..."      # arrêt côté serveur
uv run tradingagent resume --reason "..."    # reprise

# Stratégies
uv run python -m tradingagent.registry.cli registry-status
uv run python -m tradingagent.registry.cli registry-validate --ref ... --market ... --stage risk
uv run python -m tradingagent.registry.cli registry-promote --ref ... --market ... --actor ...

# Recherche
uv run python scripts/backtest/run_campaign.py --datasets docs/research/datasets
uv run python scripts/paper_campaign.py      # état de la campagne de paper trading
```

## Expert Advisors

Deux EA MQL5 (`mt5/Experts/TradingAgent/`) tournent dans le terminal : un par marché. Ils
exécutent les ordres autorisés par le backend, vérifient les protections, déclenchent leur
arrêt local si le backend se tait, et remontent chaque événement. La compilation est
documentée dans [`docs/ea/README.md`](docs/ea/README.md) ; le protocole d'échange dans
[`docs/ea/protocole-pont.md`](docs/ea/protocole-pont.md).

Le pont entre l'agent et les EA passe par `EA_FILES_DIR`, à pointer sur
`%APPDATA%\MetaQuotes\Terminal\<instance>\MQL5\Files\TradingAgent`. Sans cette variable,
l'agent tourne sans EA — c'est un état valide.

## Exploitation

| Besoin | Commande | Documentation |
|---|---|---|
| Installer | `pwsh -File scripts/install_windows.ps1` | [installation](docs/operations/installation.md) |
| Configurer | `.env` | [configuration](docs/operations/configuration.md) |
| Enregistrer le service | `pwsh -File scripts/register_service.ps1` | [exploitation](docs/operations/exploitation.md) |
| Surveiller | `pwsh -File scripts/check_health.ps1 -CheckTask` | [exploitation](docs/operations/exploitation.md) |
| Sauvegarder | `pwsh -File scripts/backup.ps1` | [sauvegarde et restauration](docs/operations/backup-restore.md) |
| Restaurer | `pwsh -File scripts/restore.ps1 -BackupFile ... -Force` | [sauvegarde et restauration](docs/operations/backup-restore.md) |
| Arrêter d'urgence | `/emergency_stop` ou `uv run tradingagent halt` | [arrêt d'urgence](docs/operations/arret-urgence.md) |
| Campagne paper | `uv run python scripts/paper_campaign.py` | [campagne paper](docs/operations/campagne-paper.md) |
| Réagir à un incident | — | [incidents](docs/operations/incidents.md) |
| Utiliser le bot | `/help` | [commandes Telegram](docs/operations/commandes-telegram.md) |
| Lire le dashboard | `uv run tradingagent-web` | [web](docs/web/README.md) · [design](docs/web/DESIGN.md) |
| Cycle de vie des stratégies | `python -m tradingagent.registry.cli` | [registre](docs/registry/README.md) |
| Amélioration IA | passage quotidien automatique | [AI Lab](docs/ai_lab/README.md) |
| Intégration continue | — | [CI](docs/operations/ci.md) |

## Invariants

- Seul `risk` importe `execution` (exception : la racine de composition `tradingagent.app`).
- L'IA ne peut que rejeter un signal : jamais en créer, augmenter une taille ou desserrer un stop.
- Une stratégie n'exécute qu'au niveau de l'échelle qu'elle a atteint (§14).
- Toute date est en UTC ; les datetimes naïfs sont interdits.
- Toute décision est persistée avec son motif, refus compris.
- Le dashboard ne calcule aucun indicateur et ne peut pas écrire en base.
- Aucun secret dans le dépôt, seulement le gabarit [`.env.example`](.env.example).
