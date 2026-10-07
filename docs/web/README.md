# Tableau de bord de monitoring (`tradingagent.web`)

Monitoring **en lecture seule** de l'agent : une application FastAPI + Jinja2 + Server-Sent
Events, écrite en Python uniquement. Pas de Next.js, pas de TypeScript, pas de chaîne de
build Node : une seule pile, un seul `pytest`, déployable sur le même hôte Windows que
l'agent. C'est la raison du choix (§23).

## Lancement

```bash
uv sync                                  # installer (fastapi, uvicorn et jinja2 sont déjà déclarés)
uv run tradingagent-web                  # http://127.0.0.1:8787
uv run tradingagent-web --port 9000
uv run tradingagent-web --interval 5     # intervalle du flux SSE, en secondes
uv run tradingagent-web --ea-reports-dir runtime/ea/reports   # statut des Gardiens EA
uv run tradingagent-web --log-level debug
```

L'entrée de commande est déclarée dans `pyproject.toml` :

```toml
[project.scripts]
tradingagent-web = "tradingagent.web.app:main"
```

Elle lit **uniquement** `DATABASE_URL` depuis `.env` (via `DatabaseSettings`) : le tableau de
bord n'a besoin ni des identifiants MT5, ni du jeton Telegram, ni de la clé Anthropic. Les
montants sont en EUR et toutes les dates affichées sont en UTC.

Le répertoire des rapports EA est optionnel et sans secret : soit `--ea-reports-dir`, soit la
variable d'environnement `TRADINGAGENT_EA_REPORTS_DIR`. Sans lui, les sections EA affichent
« aucun rapport EA : le pont n'est pas configuré » plutôt qu'un tableau vide qui ressemblerait
à un statut.

## Pages

| Chemin | Contenu |
| --- | --- |
| `/` | Vue d'ensemble : solde, équité, P&L jour/semaine/mois, drawdown courant et maximal, positions ouvertes, exposition, taux de réussite, facteur de profit, statut des stratégies, statut des Gardiens EA, fraîcheur des données de marché, agrégats journaliers — séparé par marché (BTCUSD, XAUUSD) puis TOTAL |
| `/positions` | Positions ouvertes, rafraîchies par le flux SSE |
| `/trades` | Historique des trades clôturés, filtres marché / stratégie / mode, détail d'un trade (signal, indicateurs enregistrés, décision de risque, exécutions) |
| `/strategies` | Registre `strategy_registry`, performance réalisée par référence, portails de validation, derniers backtests |
| `/ai-lab` | Analyses, hypothèses, propositions et validations issues de l'IA |
| `/risk` | Expositions, limites configurées, événements système, arrêt en cours, kill switch des EA, historique des arrêts et reprises |
| `/system` | Santé : base de données, Gardiens EA (battement de cœur, connexion, révision, kill switch, journal), fraîcheur des données de marché, latences d'exécution mesurées, dernières erreurs, télémétrie |
| `/reports` | Rapports enregistrés et exports CSV / JSON / SVG |
| `/events` | Flux SSE (`positions`, `alerts`). `?cycles=N` borne le flux à N cycles puis ferme — pratique pour `curl` et pour les tests |
| `/healthz` | Sonde JSON : base joignable, mode lecture seule, arrêt en cours |
| `/export/trades.csv`, `/export/trades.json`, `/export/performance.json`, `/export/equity.svg`, `/export/reports/{id}.txt` | Exports, produits par le paquet `reporting` |

Les pages sont responsives (piles de cartes et tableaux à défilement horizontal) et pensées
d'abord pour un écran de téléphone.

## La règle §34 : aucun chiffre recalculé

Le tableau de bord n'invente aucun indicateur :

- toute statistique de performance (taux de réussite, facteur de profit, espérance, drawdown
  maximal, Sharpe, Sortino, séries, glissement…) vient de
  `tradingagent.analytics.compute_performance` — le même code que les rapports et les
  backtests (C-001) ;
- le découpage par marché, par stratégie, par mode utilise `tradingagent.analytics.group` ;
- la courbe d'équité et le drawdown SVG sont produits par `reporting.exports` ;
- les métriques de backtest, les constats de l'IA et les verdicts de validation sont
  **affichés tels qu'enregistrés**, jamais relancés ;
- les agrégats du tableau « Par jour » sortent de `daily_performance` via
  `DailyPerformanceStore`, avec les ratios que la couche de stockage en dérive
  (`win_rate`, `r_multiple`) : le tableau de bord ne re-tranche pas les jours lui-même ;
- le statut des Gardiens EA est exactement ce que l'EA a écrit dans son rapport JSON
  (`tradingagent.ea.bridge`), battement de cœur et journal compris ; un rapport illisible
  vaut **hors ligne**, jamais une exception (`tradingagent.ea.health`) ;
- la seule arithmétique locale est l'agrégation de colonnes stockées en un total
  d'affichage (notionnel = volume × prix d'entrée, compteurs). Les montants sont des
  `Decimal` et toutes les sommes se font en Python : sous SQLite ils sont stockés en texte,
  aucun `SUM` SQL ne les touche (TASK-005).

Le JavaScript des pages ne calcule rien : les lignes poussées par le SSE arrivent déjà
mises en forme par Python et le navigateur ne fait qu'insérer des nœuds texte.

## Sécurité

- **Écoute locale par défaut** : `127.0.0.1:8787`. Passer `--host 0.0.0.0` est un choix
  explicite de l'opérateur et doit s'accompagner d'un reverse proxy TLS et d'une
  authentification.
- **Pas d'authentification en v1** : l'application est supposée n'être joignable que depuis
  la machine de l'agent. Ne l'exposez pas telle quelle sur Internet.
- **Aucune écriture possible, aucune authentification d'écriture à voler** : toutes les
  routes sont `GET`, il n'existe aucun endpoint de trading, et `web/queries.py` n'exécute que
  des `SELECT`. Trois preuves dans `tests/web/test_read_only.py` :
  1. le tableau de routage ne contient que des méthodes `GET`/`HEAD` ;
  2. les verbes `POST`, `PUT`, `PATCH`, `DELETE` répondent `405` ;
  3. parcourir toute la surface (pages, exports, flux SSE) laisse le nombre de lignes de
     **chaque** table strictement identique.
  S'y ajoute un scan AST du paquet : aucune fonction d'écriture SQLAlchemy n'y apparaît.
- **Surface minimale** : `/docs`, `/redoc` et `/openapi.json` sont désactivés.
- Les secrets ne sont jamais rendus : la page Risque affiche les *limites* de `agent.yaml`
  (pourcentages, plafonds), jamais les identifiants. Le contenu des rapports est celui qui a
  déjà été généré pour l'opérateur.

## Architecture du paquet

| Module | Rôle |
| --- | --- |
| `web/app.py` | `create_app(engine, …)` et `main()` : routes, gabarits, exports, écoute uvicorn |
| `web/queries.py` | toutes les lectures (SQL et rapports EA), en fonctions testables ; `Decimal` et agrégations Python |
| `web/sse.py` | `EventStream` : poll de la base, trames `positions` / `alerts`, intervalle injectable |
| `web/views.py` | lignes d'affichage partagées entre le rendu initial et le flux SSE |
| `web/format.py` | mise en forme pure (montants, pourcentages, durées, libellés français) |
| `web/templates/` | gabarits Jinja2 (`base.html` + une page par route) |

`create_app` accepte une horloge injectée (`now=`) : chaque page est reproductible dans un
test, et aucun module ne lit l'heure de sa propre initiative. Une erreur de base de données
n'affiche pas une trace de pile mais une page 503 explicite (`templates/unavailable.html`) :
un tableau de bord de monitoring qui disparaît est pire que pas de tableau de bord.

## Tests

```bash
uv run pytest -q tests/web
```

La suite monte une base SQLite jetable **construite par les migrations** (triggers et
contraintes compris), y insère un jeu de données représentatif dont chaque chiffre attendu
est écrit explicitement dans `tests/web/seed.py`, puis interroge l'application avec
`fastapi.testclient.TestClient`. Les montants attendus (P&L jour/semaine/mois/total, split
par marché, drawdown, notionnel) sont donc vérifiés de bout en bout, du `INSERT` au HTML.

Les rapports EA sont de vrais fichiers JSON écrits dans un `tmp_path`, ce qui couvre les
trois cas que l'opérateur doit distinguer : garde en ligne, garde silencieux (battement trop
vieux), et rapport illisible — ce dernier étant listé **hors ligne** et non oublié.

`TEST_DATABASE_URL` est honoré comme dans `tests/storage` (base dont le nom finit par
`_test`), pour rejouer la suite sur PostgreSQL.

## Limites connues

- **Historique complet en mémoire** : `/` et `/trades` lisent toute la table `trades`. C'est
  sans conséquence à la cadence actuelle (quelques trades par jour) ; le tableau « Par jour »
  s'appuie déjà sur `daily_performance`, la voie rapide prévue par le cahier v3.
- **Pas de vue par marché sur les agrégats journaliers** : la page affiche les lignes
  `daily_performance` telles qu'écrites (jour × mode × marché × stratégie) ; un regroupement
  se ferait en SQL et violerait la règle des montants stockés en texte.
- **Notionnel** : `volume × prix d'entrée` est un ordre de grandeur ; la taille de contrat
  n'est pas stockée en base, l'exposition réelle du compte n'est donc pas affichée.
- **Battement de cœur EA** : lu depuis des fichiers JSON partagés, donc tributaire du montage
  du répertoire. Si le tableau de bord et le terminal MT5 ne voient pas le même dossier, tous
  les Gardiens apparaissent hors ligne — c'est un problème de déploiement, pas un faux statut.
