# Registre de stratégies versionné

Paquet `tradingagent.registry` — cahier v3 §14 (cycle de vie), §30 (registre de production),
§35 (journal des décisions), §49 (protocole de validation à neuf portes).

## Modèle

Une ligne de `strategy_registry` par couple `(market, ref)`, avec `ref = id@version`. Une
`ref` est créée une seule fois. Le statut est l'état de déploiement, et **seul un `LIVE` a le
droit de trader**. Un `LIVE` est immuable : toute modification de paramètres, de code ou de
comportement se publie sous une **nouvelle `ref`** dont `parent_ref` désigne la version
remplacée. `active(market)` ne renvoie donc jamais qu'une seule `ref` par marché ; une
tentative de promotion d'une seconde `ref` sur un marché déjà `LIVE` est refusée
(`MarketAlreadyLive`) tant que la version en place n'est pas `DEPRECATED`.

Cycle de vie, imposé par `core.states.ALLOWED_STATUS_TRANSITIONS` :

```
DISCOVERED → EXPERIMENTAL → BACKTESTING → VALIDATING → PAPER → CANDIDATE → LIVE → DEPRECATED
```

Depuis n'importe quel statut non terminal, `DEPRECATED` reste accessible ; `DEPRECATED` est
terminal. Toute autre arête est refusée par `IllegalStrategyTransition`.

## Fichiers

| Fichier | Rôle |
|---|---|
| `gates.py` | `missing_gates(passed_stages)`, `can_promote(passed_stages)` : deux fonctions pures, sans horloge ni base. Les neuf portes sont `tuple(ValidationStage)`, dans l'ordre du protocole. |
| `store.py` | `StrategyRegistry(engine, clock=None)` : `register`, `transition`, `promote`, `record_backtest`, `record_validation`, `active`, `history`, `markets`, `list_market`, `get`, `passed_stages`. |
| `cli.py` | `registry-status`, `registry-validate`, `registry-promote`, utilisables par `python -m tradingagent.registry.cli`. |

## Portes de promotion (§49)

`BACKTEST, COSTS, WALK_FORWARD, OUT_OF_SAMPLE, MONTE_CARLO, STRESS, PARAMETER_ROBUSTNESS,
PAPER, RISK`. Une porte n'est franchie que si un `validation_runs` **passé** existe pour la
`ref` et le marché ; un échec ultérieur **révoque** un succès antérieur (seul le dernier run
d'une porte compte). `promote()` refuse tant qu'une porte manque et **nomme toutes** les
portes manquantes ; `transition(..., LIVE, ...)` applique exactement les mêmes contrôles,
donc la porte bas niveau n'est pas un contournement. `record_validation(..., passed=False, ...)`
n'ouvre jamais de porte.

## Journalisation des décisions

Le choix retenu est `audit_log` (la table append-only de `storage/audit.py`) :

* `strategy.registered` à la création ;
* `strategy.transition` à chaque changement de statut, avec `market`, `ref`, `from`, `to`,
  `reason` dans `detail`, plus `actor` et `occurred_at`.

Le registre écrit `AuditLogRow` **dans la même transaction** que le changement d'état, au lieu
d'appeler `AuditStore.record` (qui ouvre sa propre transaction). L'invariant visé — une
transition ne peut pas exister sans son acteur ni son motif — ne tient que si les deux
écritures sont atomiques. `history(ref, market)` relit ces lignes par leur `detail` pour
reconstituer la vie de la `ref`, à côté des validations et des backtests.

## Horodatages

Aucune lecture d'horloge implicite dans les méthodes métier : le constructeur prend un
`clock` (par défaut `datetime.now(UTC)`), et les méthodes de transition, de validation et de
promotion exigent un `at` explicite. Un datetime naïf ou non-UTC est refusé (`ValueError`).

## Utilisation

```bash
uv run python -m tradingagent.registry.cli registry-status [--market XAUUSD]
uv run python -m tradingagent.registry.cli registry-validate \
    --market XAUUSD --ref witness@1.1.0 --stage risk --detail '{"max_drawdown": 0.12}'
uv run python -m tradingagent.registry.cli registry-promote \
    --market XAUUSD --ref witness@1.1.0 --reason "neuf portes franchies" --actor operator
```

La CLI écrit directement en base via `DATABASE_URL` (comme `control/cli.py`), pour fonctionner
quand l'agent est arrêté. Codes de sortie : `0` succès, `1` refus métier (porte manquante,
transition illégale, `ref` inconnue), `2` configuration ou argument invalide.

## Tests

`uv run pytest -q tests/registry` couvre : transition illégale refusée, promotion refusée
puis acceptée quand les neuf portes passent, révocation d'une porte par un échec ultérieur,
`LIVE` immuable, redémarrage sur une nouvelle instance d'`Engine`, `active()` par marché,
indépendance de deux marchés, audit et horodatage UTC. La base de test est construite par les
migrations Alembic (`tests/registry/conftest.py`), jamais par `create_all` : les déclencheurs
d'immuabilité n'existent que là.

## Hors périmètre de cette tâche

Le raccordement du registre au runtime de production (`app.py`, sélection de la stratégie
`LIVE` par marché) reste à faire, ainsi que la commande CLI de transition de statut —
les transitions se pilotent pour l'instant depuis le code ou une session Python.
