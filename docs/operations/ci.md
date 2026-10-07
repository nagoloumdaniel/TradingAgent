# Intégration continue (TASK-052, ENF-009)

Le workflow est [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). Il se
déclenche sur `push` vers `main`, sur toute `pull_request` et manuellement
(`workflow_dispatch`).

## Jobs

| Job | Runner | Contenu |
|---|---|---|
| `secrets` | ubuntu | `detect-secrets-hook --no-verify` avec le plugin du projet, puis `detect-private-key` |
| `static` | ubuntu | `ruff check .`, `ruff format --check .`, `mypy` |
| `tests` | **matrice ubuntu + windows** | `uv run pytest -q` |
| `build` | ubuntu | `uv build` et publication de `dist/` comme artefact |

Windows est nécessaire pour exercer réellement le paquet `MetaTrader5` et les scripts
d'exploitation PowerShell (les tests de `tests/test_backup_scripts.py` sont ignorés avec
un motif explicite sur les runners qui n'ont pas PowerShell). Ubuntu garantit que rien ne
dépend de Windows par accident.

**Construction d'image** : le cahier mentionne une image, mais l'agent n'est pas
conteneurisé (C-010, le terminal MT5 exige une session Windows). L'artefact livrable est
donc la distribution Python produite par `uv build`.

## Pourquoi la détection de secrets est explicite

Le hook pre-commit du projet tourne en `--no-verify` : les plugins standards enverraient
chaque candidat à l'API de son fournisseur et **écarteraient silencieusement** ceux qui
échouent, rendant le hook dépendant du réseau. La CI invoque donc explicitement le même
analyseur, hors ligne, avec le plugin du projet
(`tools/detect_secrets_plugins/project_tokens.py`), qui couvre les jetons courts à faible
entropie comme les jetons Deriv.

```bash
git ls-files -z | xargs -0 uv run detect-secrets-hook --no-verify \
  --plugin tools/detect_secrets_plugins/project_tokens.py \
  --baseline .secrets.baseline
git ls-files -z | xargs -0 uv run detect-private-key
```

## Bloquer la fusion en cas d'échec

Un job en échec n'empêche pas la fusion à lui seul. Sur GitHub, dans
`Settings > Branches > Branch protection rules` pour `main` :

1. cocher **Require status checks to pass before merging** ;
2. sélectionner les quatre jobs : `Détection de secrets`, `Analyse statique et typage`,
   `Tests (ubuntu-latest)`, `Tests (windows-latest)` ;
3. cocher **Require branches to be up to date before merging** ;
4. interdire le push direct sur `main` (fusion par pull request uniquement).

## Reproduire la CI localement

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
```

Les mêmes commandes sont exécutées par les hooks pre-commit sur un poste de
développement (`pwsh -File scripts/install_deps.ps1`).
