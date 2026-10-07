# TradingAgent

Agent autonome de signaux de trading sur Deriv (or `frxXAUUSD` et 2 à 4 cryptos en CFD),
notifiés via Telegram. Le terminal MetaTrader 5 impose Windows (contradiction C-010) :
l'agent n'est pas conteneurisé.

Le *pourquoi* est dans [`CAHIER_DES_CHARGES.md`](CAHIER_DES_CHARGES.md), le *quoi et dans
quel ordre* dans [`ROADMAP.md`](ROADMAP.md). Ce fichier ne couvre que la mise en route ;
toute l'exploitation est dans [`docs/operations/`](docs/operations/README.md).

## Installation

Prérequis : Windows, [uv](https://docs.astral.sh/uv/) (`winget install --id astral-sh.uv -e`)
et le terminal MetaTrader 5 installé depuis l'espace client Deriv.

```powershell
# 1. Installation reproductible depuis uv.lock (aucune modification sans -WhatIf).
pwsh -File scripts/install_windows.ps1 -WhatIf
pwsh -File scripts/install_windows.ps1

# 2. Remplir .env (jamais versionné) à partir du modèle.
Copy-Item .env.example .env

# 3. Vérifier l'accès à la base.
uv run tradingagent status
```

`scripts/install_deps.ps1` installe en plus les hooks pre-commit sur un poste de
développement.

## Commandes

```bash
uv sync                      # installer les dépendances verrouillées
uv run pytest -q             # tests
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy                  # typage

uv run tradingagent status                     # état de l'arrêt d'urgence
uv run tradingagent halt --reason "..."        # arrêt d'urgence côté serveur
uv run tradingagent resume --reason "..."      # reprise
```

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
| Réagir à un incident | — | [incidents](docs/operations/incidents.md) |
| Utiliser le bot | `/help` | [commandes Telegram](docs/operations/commandes-telegram.md) |
| Intégration continue | — | [CI](docs/operations/ci.md) |

## Invariants

- Seul `risk` importe `execution` (exception : la racine de composition `tradingagent.app`).
- L'IA ne peut que rejeter un signal : jamais en créer, augmenter une taille ou desserrer un stop.
- Toute date est en UTC ; les datetimes naïfs sont interdits.
- Toute décision est persistée avec son motif, refus compris.
- Aucun secret dans le dépôt, seulement le gabarit [`.env.example`](.env.example).
