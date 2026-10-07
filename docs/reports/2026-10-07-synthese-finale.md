# Synthèse finale — complétion de la roadmap, 2026-10-07

> Rapport de clôture de la session d'exécution. Chaque chiffre est produit par une commande
> exécutée ; les commandes sont citées. Les points qui dépendent de l'opérateur, du temps ou
> d'un serveur sont listés explicitement en fin de document, et ne sont pas présentés comme
> faits.

## 1. État de départ et état final

| Indicateur | Début de session | Fin de session |
|---|---|---|
| Tests | 761 passed, 8 skipped | **1048 passed, 38 skipped, 0 failed** |
| `ruff check .` | vert | vert (*All checks passed*) |
| `ruff format --check .` | vert | vert (237 fichiers) |
| `mypy` | vert | vert (209 fichiers sources) |
| Tâches de la roadmap terminées | TASK-001 à TASK-044 | + TASK-038, 050, 052, 053, 054, 055, 060 à 065, 070, 081 à 085, 091 à 093, 100 à 103 |

Commandes de référence, exécutées par le Lead sur l'arbre final, après la vérification
indépendante (qui a ajouté 41 tests sous `tests/verification/`) :

```
uv run pytest -q                  -> 1048 passed, 38 skipped in 57.41s
uv run ruff check .               -> All checks passed!
uv run ruff format --check .      -> 237 files already formatted
uv run mypy                       -> Success: no issues found in 209 source files
RUN_MT5_LIVE=1 uv run pytest -m mt5_live -q   -> 2 passed (compte DÉMO réel, 0 position laissée ouverte)
```

### Incident de procédure, à connaître

La vérification indépendante a commencé alors que l'arbre n'était **pas** gelé. Sa première
passe a donc donné `1 failed, 1006 passed, 38 skipped` : `tests/execution/test_tracking.py`
échouait parce que l'équipier `execution` a corrigé ce test à 04:54:27, **après avoir rendu
son rapport final**. Deux suites de tests tournaient aussi en parallèle, et le présent
rapport a été créé pendant la vérification. La seconde passe, sur un arbre calmé et empreinté
(SHA-256 de 445 fichiers avant et après, aucun changement constaté), donne `1007 passed,
38 skipped` — puis `1048` avec les tests ajoutés par le vérificateur. Le chiffre « 0 failed »
ne vaut donc que pour l'arbre d'après 04:54:27, et l'écart est consigné en sévérité haute
dans `docs/reports/2026-10-07-verification-finale.md`. Leçon : un gel annoncé n'est pas un gel
observé ; la preuve d'immobilité doit être produite avant la première commande, pas après.

## 2. Ce qui a été construit

### 2.1 La boucle d'agent, qui manquait (TASK-038, ajoutée)

Toutes les tâches précédentes renvoyaient au « branchement (TASK-034) » sans qu'aucune tâche
ne le porte. Le chaînon manquant est désormais livré :

- `src/tradingagent/runtime/` : `loop.py` (`AgentLoop`), `pipeline.py` (`SignalPipeline`),
  `portfolio.py`, `ports.py`. Le runtime ne connaît l'exécuteur que par protocol structurel :
  il n'importe jamais `execution`.
- `src/tradingagent/app.py` : racine de composition. Seul module avec `risk` autorisé à
  importer `execution`, conformément à l'exception déjà prévue par le test d'architecture.
  Il construit le terminal MT5, le client de données, le catalogue de stratégies, le broker
  (MT5 ou paper), le notifier Telegram, le gardien, les alertes, les rapports et la boucle ;
  il refuse de démarrer si un symbole configuré n'existe pas sur le compte.
- Commande `uv run tradingagent-run`, avec `--once` et `--cycles N` pour la vérification.
- Commande Telegram `/report daily|weekly|monthly`, absente de TASK-022.

### 2.2 Configuration de production

- `config/agent.yaml` : `XAUUSD` → `witness@1.1.0`, `BTCUSD` → `trend_breakout@1.0.0`
  (EF-003 : une stratégie distincte par marché, avec deux règles réellement différentes).
- `config/strategies/witness@1.1.0.yaml` : remplace le symbole inexistant `frxXAUUSD`.
  `witness@1.0.0` est conservé intact : un manifeste déjà utilisé ne se réécrit jamais.
- `config/strategies/trend_breakout@1.0.0.yaml` : cassure de canal de Donchian filtrée par
  une EMA longue. Les deux manifestes restent plafonnés à `max_mode: SIGNAL` (RM-016).

### 2.3 Exécution (TASK-070, 081 à 085)

`src/tradingagent/execution/` : `mt5_broker.py`, `paper_broker.py`, `journal.py`,
`tracking.py`, `reconciliation.py`, `recovery.py`, `simulator.py`, `ports.py`.

Points décisifs, tous prouvés :

- stop natif envoyé **puis relu sur la position** après exécution, clôture immédiate et alerte
  si absent (EF-015) ;
- RM-017 vérifié avant chaque ordre, un compte incohérent arrêtant le composant ;
- réponse perdue réconciliée par le commentaire d'idempotence, **jamais** de second ordre ;
- phase paper : aucun appel d'exécution, prouvé par un terminal espion qui lève ;
- réconciliation bidirectionnelle, divergence → arrêt global + alerte, **aucune correction
  automatique** (RM-014) ;
- **essai live réel** : `RUN_MT5_LIVE=1 uv run pytest -m mt5_live -q` → 2 passed, dont une
  ouverture au volume minimal XAUUSD sur le compte de démonstration, stop confirmé présent,
  clôture immédiate.

### 2.4 Recherche et backtest (TASK-060 à 065)

`src/tradingagent/backtest/` et `src/tradingagent/research/` : jeux de données immuables et
empreintés, harnais réutilisant `evaluate()` (impossibilité de lire le futur prouvée par un
test tripwire), modélisation des coûts, jeu hors échantillon scellé par jeton, marche
avant, Monte-Carlo, campagne multi-marchés, promotion à seuils figés d'avance.

Campagne réelle exécutée sur 3 999 bougies XAUUSD M15 réelles : la stratégie de référence est
retenue sur la stabilité mais **la promotion est refusée** (14 trades hors échantillon < 30,
score 0,41 < 0,50, facteur de profit net 1,18 < 1,20). Conclusion honnête : aucun avantage
démontré, aucune stratégie promue en production.

### 2.5 Exploitation (TASK-050, 052 à 055)

`scripts/` (installation, dépendances, service Windows, santé, sauvegarde, restauration),
`.github/workflows/ci.yml` (secrets, statique, tests matrice Ubuntu + Windows, build),
`src/tradingagent/observability.py` (journaux JSON, métriques, ressources),
`README.md` et `docs/operations/` (9 documents). Aller-retour sauvegarde → restauration
réellement exécuté sur une base jetable, refus vérifiés par exécution.

### 2.6 PostgreSQL, mode réel, comparaison (TASK-080, 090 à 093)

Migration `0005` (déclencheurs d'immuabilité PostgreSQL et SQLite), `control/live.py`
(double condition RM-000, plafond réduit, montée progressive par paliers), `reporting/
comparison.py` (comparaison backtest/production par stratégie, seuil configurable, séparation
stricte démo/réel), dossier juridique à signer par l'opérateur.

## 3. Décisions prises pendant la session

Les huit décisions d'intégration sont consignées dans
`docs/decisions/2026-10-07-runtime-integration.md`, notamment :

- **un seul écrivain par table** (l'exécuteur possède `orders`, `positions`, `trades`,
  `executions` ; le runtime possède `signals`, `signal_events`, `risk_decisions`,
  `system_events`, `account_snapshots`, `reports`) ;
- **la clé d'API du modèle devient optionnelle** : RM-011 dit que l'indisponibilité de l'IA ne
  doit pas bloquer l'agent, or `Settings` l'exigeait au démarrage ;
- **le filtre IA est lu dans le manifeste de la stratégie**, sans quoi une promotion par
  stratégie (TASK-044) était impossible ;
- **un rejet IA produit une expiration** (`EXPIRED`), seule transition légale depuis
  `CANDIDATE` qui ne pollue pas les statistiques de refus du risque.

## 4. Ce qui reste, et qui n'est pas fait ici

Ces points ne sont pas des oublis : ils exigent une ressource, une durée ou une décision qui
n'appartient pas à un agent.

| Point | Nature | Ce qu'il faut |
|---|---|---|
| TASK-051 — serveur Windows | infrastructure | provisionner un VPS Windows, durcir l'accès, installer le service |
| TASK-071 — campagne de paper trading | durée (Q-15) | 30 jours calendaires et 30 opérations par stratégie |
| TASK-090 — vérification juridique | décision | signer `docs/legal/2026-10-07-verification-operateur.md` |
| Quality gate phase 5 | durée | 7 jours en continu, redémarrage automatique observé |
| Restauration « machine vierge » | manipulation | exécuter `docs/operations/backup-restore.md` sur une machine neuve |
| Validation par un tiers | humain | faire suivre `docs/operations/` à quelqu'un qui n'a pas développé le système |
| Levier crypto / Q-07 | marché | aucun historique crypto réel exploitable ici ; Q-22 (capital réel) non tranchée |
| Phase 9 (exécution réelle) | décision | ne démarre pas avant TASK-090 signée |

## 5. Clés d'environnement

`.env` (jamais dans le dépôt) :

| Clé | État | Rôle |
|---|---|---|
| `MT5_LOGIN`, `MT5_SERVER`, `MT5_PASSWORD` | présentes | compte de démonstration MT5 |
| `MT5_TERMINAL_PATH` | présente | terminal quand plusieurs installations coexistent |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS` | présentes | bot et liste blanche |
| `DATABASE_URL` | présente | PostgreSQL hébergé (Supabase) |
| `TRADING_MODE`, `LIVE_TRADING_ENABLED` | présentes | mode courant, moitié serveur de RM-000 |
| **`ANTHROPIC_API_KEY`** | **absente (vide)** | **optionnelle** : sans elle l'agent démarre et le filtre IA n'est pas câblé (RM-011). À renseigner pour activer l'explication par modèle. |
| **`TEST_DATABASE_URL`** | **absente** | **nécessaire** pour exécuter les tests PostgreSQL (doit finir par `_test`). Sans elle, 29 tests se sautent proprement. |
| **`BACKUP_PASSPHRASE`** | **absente** | **nécessaire** aux sauvegardes chiffrées ; à conserver hors du dépôt. |
| `TA_COMPARISON_*` | optionnelles | seuils d'alerte de TASK-093 (défauts : 25 %, 15 pts, 0,5, 25 %, 0,5, 5 trades) |
| `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE`/`PGSSLMODE` | optionnelles | repli de `scripts/backup.ps1` quand `DATABASE_URL` est absente |

## 6. Verdict

La roadmap est complète dans tout ce qui est exécutable hors ligne et sur ce poste : les
phases 0 à 4 étaient faites, les phases 6 à 9 le sont désormais côté code, la boucle d'agent
manquante est livrée, la chaîne qualité est verte de bout en bout, et un ordre réel a été
exécuté et refermé sur le compte de démonstration avec son stop vérifié.

Le projet n'est **pas** pour autant prêt pour l'argent réel : la campagne de paper trading
n'a pas eu lieu, la conformité n'est pas signée, le serveur n'existe pas, et aucune stratégie
n'a franchi les seuils de promotion. C'est l'état exact attendu à la fin de la V2.
