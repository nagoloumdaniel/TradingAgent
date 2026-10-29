# Contribuer

1. Forkez, puis créez une branche `feat|fix|refactor|test|docs/<slug>` (jamais de travail direct sur `main`).
2. `uv sync`, puis avant chaque PR :
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy
   uv run pytest -q
   ```
3. Lisez les [invariants](README.md#invariants) : seul `risk` importe `execution`, l'IA ne peut que rejeter un signal, toute date est en UTC.
4. TDD obligatoire sur `indicators`, `risk`, `analytics`, `execution`. Un bug corrigé reçoit un test de non-régression.
5. Aucun secret dans le dépôt. Commits en [Conventional Commits](https://www.conventionalcommits.org/).
6. Ouvrez la PR vers `main` : ce qui change, les tests lancés et leur résultat, une capture si l'interface change.

Faille de sécurité : voir [SECURITY.md](SECURITY.md).
