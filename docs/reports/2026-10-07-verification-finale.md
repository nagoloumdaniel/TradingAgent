# Vérification finale indépendante — TradingAgent

- **Vérificateur :** `verifier` (indépendant de tous les producteurs)
- **Date :** 2026-10-07, poste Windows, `uv run`, dépôt `D:\Projets\TradingAgent`
- **HEAD au moment de la vérification :** `e57c7020bdb683991c79bc452ab6a7797eeb15a0`
  (`feat(TASK-044): shadow-mode AI filter evaluation with robustness check`)
- **Nature de l'arbre :** pas un commit « final » — l'arbre est un working tree non commité
  (23 fichiers suivis modifiés, 53 entrées non suivies). Aucun `git commit` n'a été fait
  par quiconque pendant la vérification (interdit respecté).
- **Périmètre d'écriture du vérificateur :** ce rapport et `tests/verification/` uniquement.
  Aucun fichier de production, ni `ROADMAP.md`, ni test existant n'a été modifié.

---

## 0. Verdict

**Le code livré dans le working tree actuel est livrable**, au sens où : la suite complète est
verte, les contrôles statiques et `pre-commit` sont verts, les garde-fous de sécurité se
déclenchent réellement, et les tests MT5 sur le compte DÉMO passent sans laisser de position
ouverte.

**Mais la passe de vérification a commencé sur un arbre non gelé et rouge.** Le premier run
complet a donné **1 failed / 1006 passed / 38 skipped** ; le test en échec a été édité
**pendant** la vérification (04:54:27), ce qui l'a rendu vert. Le chiffre « 1007 passed,
0 failed » n'est donc vrai que de l'arbre **d'après** cette édition, pas de l'arbre tel
qu'il existait au début de la vérification. Voir section 2 (écart de sévérité haute).

Ce n'est **pas** un défaut de production : le rouge venait d'un test incomplet (transition de
lifecycle manquante), pas du code livré.

Ce qui reste hors de portée d'une vérification locale (PostgreSQL réel, campagne de paper
trading, conformité juridique, VPS) est listé en section 7 et **n'est pas** présenté comme
vérifié.

---

## 1. Objet et méthode

Rejouer, par exécution, l'intégralité de `task-5` (TASK-100/101/102) sans faire confiance à
aucun rapport d'équipier, et tenter de faire échouer les points sensibles au lieu de les
confirmer. Toute sortie citée ci-dessous a été produite sur ce poste.

La vérification s'est déroulée en deux passes :

- **Passe A** : premier passage, sur un arbre encore en cours d'écriture par d'autres agents.
- **Passe B** : passage de référence sur un arbre calme, après une fenêtre de deux minutes
  sans processus de test ni écriture, avec empreinte SHA-256 de tous les fichiers avant/après.

---

## 2. Passe A — preuve de rupture du gel (sévérité HAUTE)

### 2.1 Chronologie établie par horodatage

| Heure (Europe/Paris) | Événement | Preuve |
|---|---|---|
| 04:40:00 → 04:51:39 | L'équipier `execution` écrit encore des fichiers de production et de tests (`runtime/pipeline.py`, `execution/mt5_broker.py`, `app.py`, `journal.py`, …) | `LastWriteTime` des fichiers |
| 04:52:05 | Démarrage de **ma** passe A (`uv run pytest -q`, `uv.exe` PID 28856) | `Win32_Process.CreationDate` |
| 04:53:03 | `ROADMAP.md` écrit par l'équipier `roadmap` | `LastWriteTime` |
| 04:53:04 | Empreinte de référence n° 1 (444 fichiers) | `$TEMP\ta_baseline_hashes.txt` |
| ~04:53:43 | Fin de la passe A : **1 failed, 1006 passed, 38 skipped in 98.11s** | sortie ci-dessous |
| 04:54:02 | Création de `docs/reports/2026-10-07-synthese-finale.md` (par le Lead, reconnu) | `CreationTime` |
| 04:54:27 | **`tests/execution/test_tracking.py` édité par l'équipier `execution`** | `LastWriteTime` |
| 04:54:30 puis 04:55:47 | Nouveaux `uv run pytest -q` lancés par `roadmap` | parents `powershell.exe` PID 30744 / 2840 |
| 04:58:16 | Empreinte de référence n° 2 (445 fichiers), début de la passe B | `$TEMP\ta_baseline_B.txt` |

### 2.2 Sortie exacte de la passe A (extrait utile)

```
tests\execution\test_tracking.py:118: AssertionError
E       AssertionError: assert <SignalState.ORDER_ACCEPTED: 'order_accepted'> is
        <SignalState.CLOSED: 'closed'>
WARNING  tradingagent.execution.journal:journal.py:206 signal 1 stayed put:
         order_accepted → closed is not allowed (RM-018)
...
FAILED tests/execution/test_tracking.py::test_a_closure_writes_the_trade_reason_and_result
1 failed, 1006 passed, 38 skipped in 98.11s (0:01:38)
```

Le code source lu au moment de l'échec ne contenait **pas** la transition
`SignalState.POSITION_OPEN` ; la version actuelle (lignes 102-103) la contient :

```python
    instance.record_fill(order_id, order, result(), deal_ticket=9001, at=NOW)
    # The caller (agent loop) moves the signal to POSITION_OPEN once the fill is confirmed.
    transition(engine, signal_id, SignalState.POSITION_OPEN, NOW)
```

Le test échoue dans la version vue par la passe A et **passe en isolation** dans la version
d'après 04:54:27. La correction est légitime (le test omettait une transition que le
lifecycle RM-018 exige), mais elle a été faite **pendant** la vérification, après que
l'équipier avait déclaré son travail terminé.

### 2.3 Conséquences

1. Le dépôt **n'était pas gelé** : `tests/execution/test_tracking.py` a changé pendant la
   passe, un rapport a été créé, et deux runs de tests concurrents ont été lancés.
2. Le chiffre « **1007 passed, 38 skipped, 0 failed** » du rapport de synthèse décrit l'arbre
   d'après 04:54:27. Il ne peut pas être rattaché à l'arbre présent au début de la
   vérification, qui valait « 1 failed, 1006 passed ».
3. Aucun défaut de production n'est impliqué : le seul rouge était un test incomplet.
4. Observation mineure, non élucidée : `ruff format --check .` a renvoyé « 231 files already
   formatted » à 04:52 puis « 232 » à 04:57 sans ajout de fichier `.py` identifié entre les
   deux — cohérent avec un arbre encore en écriture, sans impact détecté.

---

## 3. Passe B — arbre de référence gelé

- **Pré-état** capturé à `2026-10-07T04:58:11`, empreinte SHA-256 de **445 fichiers**
  (hors `.git`, `.venv`, caches) à `04:58:16`.
- Aucun processus `pytest`/`uv`/`python` actif avant le départ (vérifié par `Win32_Process`).
- **Post-état** : `CHANGED during Pass B: <none>` ; `NEW during Pass B: <none>`.
- **Verdict passe B : `1007 passed, 38 skipped in 60.86s`.**

Après l'ajout de mes propres tests, une comparaison finale contre l'empreinte B ne montre
que mes 4 fichiers :

```
CHANGED vs Pass B baseline B: <none>
NEW vs Pass B baseline B:
tests\verification\conftest.py
tests\verification\test_adversarial_risk.py
tests\verification\test_pipeline_and_idempotency.py
tests\verification\test_security_invariants.py
```

Aucun autre écrivain n'a touché le dépôt pendant la passe B et les mesures finales.

---

## 4. Commandes exécutées et sorties exactes

### 4.1 Tests

| Commande | Résultat | Passe |
|---|---|---|
| `uv run pytest -q` | `1 failed, 1006 passed, 38 skipped in 98.11s (0:01:38)` | A (arbre mouvant) |
| `uv run pytest -q` | `1007 passed, 38 skipped in 60.86s (0:01:00)` | B (gelé) |
| `uv run pytest -q` | `1048 passed, 38 skipped in 58.64s` | finale (+ 41 tests vérificateur) |
| `uv run pytest -q tests/test_architecture.py -v` | `44 passed in 1.90s` | B |
| `uv run pytest -q tests/config/test_shipped_config.py -v` | `2 passed in 0.20s` | B |
| `uv run pytest -q tests/verification -v` | `41 passed in 3.69s` | B |
| `RUN_MT5_LIVE=1 uv run pytest -m mt5_live -q` | `2 passed, 1083 deselected in 5.48s` | B |

Les 38 sauts de la passe B sont les mêmes que ceux de la synthèse : 2 `mt5_live` (sans la
variable), 29 PostgreSQL (`TEST_DATABASE_URL` absent), 6 SQLite (TRUNCATE), 1 verrou de
ligne PostgreSQL. **29 tests ne sont donc pas exercés ici** (voir section 7).

### 4.2 Qualité statique (passe B)

```
uv run ruff check .            -> All checks passed!
uv run ruff format --check .   -> 232 files already formatted      (236 après ajout de mes 4 fichiers)
uv run mypy                    -> Success: no issues found in 209 source files
```

### 4.3 `pre-commit run --all-files` (passe B, avec mes tests présents)

```
detect-secrets...........................................................Passed
detect-private-key.......................................................Passed
check-added-large-files..................................................Passed
end-of-file-fixer........................................................Passed
trailing-whitespace......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
mypy.....................................................................Passed
architecture boundaries..................................................Passed
EXIT_PRECOMMIT3=0
```

Aucun hook n'a réécrit de fichier : l'empreinte SHA-256 de l'arbre est identique avant/après
(`CHANGED by pre-commit vs baseline B: <none>`).

### 4.4 Preuve que la détection de secrets est active

Faux secret injecté dans un fichier **hors dépôt** (`$TEMP\ta_secret_probe\probe.env`),
supprimé aussitôt, jamais commité :

```
$ uv run detect-secrets-hook --no-verify \
    --plugin tools/detect_secrets_plugins/project_tokens.py --baseline .secrets.baseline <probe>
ERROR: Potential secrets about to be committed to git repo!

Secret Type: Telegram Bot Token
Location:    ...\probe.env:1
Secret Type: Project Token Assignment
Location:    ...\probe.env:2
Secret Type: Project Token Assignment
Location:    ...\probe.env:3
Secret Type: Secret Keyword
Location:    ...\probe.env:3
EXIT_DETECT_SECRETS=1

$ uv run detect-private-key <probe_key.pem>
Private key found: ...\probe_key.pem
EXIT_PRIVATE_KEY=1
```

Nettoyage effectué, `Test-Path` renvoie `False`, et `git status` n'a pas bougé du fait du
probe. Les détecteurs sont donc réellement armés, pas seulement présents dans la config.

### 4.5 Aucun secret de `.env` recopié dans le dépôt

Balayage de **tous** les fichiers du dépôt contre les valeurs réelles de `.env`
(`MT5_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `DATABASE_URL` ; `ANTHROPIC_API_KEY` est présente mais
vide) — les valeurs ne sont **jamais** affichées, seules les clés le sont :

```
SECRET values checked: MT5_PASSWORD TELEGRAM_BOT_TOKEN DATABASE_URL
NO LEAK: no secret .env value found in any repo file
```

Point d'information : la valeur **non secrète** `MT5_SERVER` (nom du serveur de démonstration)
apparaît recopiée dans `tests/`, `docs/reports/` et `.vscode/`. Ce n'est pas un secret, mais
c'est la valeur réelle qui a fuité hors de `.env` ; à nettoyer si l'on veut pouvoir publier
le dépôt.

### 4.6 `mt5_live` sur le compte DÉMO + contrôle des positions

```
$ RUN_MT5_LIVE=1 uv run pytest -m mt5_live -q
2 passed, 1083 deselected in 5.48s
```

Relecture indépendante du compte après le run (script lecture seule, aucun ordre) :

```
account_is_demo: True
trade_allowed: True
open_positions: 0
```

**Aucune position laissée ouverte.** Le second test impose lui-même `snapshot.is_demo` et
`snapshot.trade_allowed`, et ne s'exécute que sur DÉMO.

### 4.7 Invariant d'architecture (lecture du code + test)

- Le test `tests/test_architecture.py` (scanner AST maison) passe : `44 passed`. Il couvre
  l'import d'`execution` par paquet, l'interdiction d'importer `backtest`/`research` en
  production, la pureté d'`indicators`/`strategies`, l'accès au SDK `MetaTrader5` et les
  lectures d'horloge.
- Recoupement indépendant par `grep` sur `src/tradingagent/**/*.py` : hors du paquet
  `execution` lui-même, **le seul** import d'`execution` est `src/tradingagent/app.py:45`
  (racine de composition). `risk` n'importe même pas `execution` : il définit ses modèles
  (`TradeIntent`, `OrderRequest`) localement et le runtime ne parle au broker que par
  `runtime/ports.py` (protocoles structurels). L'invariant est donc respecté **strictement**.
- Aucun import de `backtest`/`research` hors de `backtest`/`research` (et de leurs tests) :
  seul `research` importe `backtest`, dans le bon sens.

### 4.8 Sécurité (TASK-102)

| Contrôle | Résultat observé |
|---|---|
| Liste blanche Telegram, identifiant inconnu | `service.handle()` renvoie `None` ; 1 ligne d'audit `command_refused` (`verdict=unauthorized`) ; **aucune** ligne `command` → silence + journalisation, jamais d'exécution |
| Opérateur en chat de groupe | `None`, aucune exécution (`not_private`) |
| `/mode LIVE` depuis Telegram | refusé, réponse contenant `RM-000` ; **aucun** événement `mode_command` persisté |
| `LIVE_TRADING_ENABLED` absent/false + `TRADING_MODE=LIVE` | `Settings` lève une `ValidationError` mentionnant `LIVE_TRADING_ENABLED` (RM-000) |
| Message d'erreur de configuration | `load_settings()` n'expose jamais la valeur fautive (`_describe` avec `include_input=False`) |
| Secrets dans les journaux | `config/redaction.py` masque arguments positionnels, exceptions ; vérifié sur un logger tiers via le *log record factory*. Preuve de terrain : le run de l'agent journalise `https://api.telegram.org/bot***/getMe` — jeton masqué |
| RM-017 avant chaque ordre | lu dans `execution/mt5_broker.py` : `_check_mode` → `_ensure_trading` → `await self._reverify()` avant toute recherche d'idempotence ou envoi ; testé : terminal non-DÉMO ⇒ `AccountModeMismatchError` et `order_send` jamais appelé |
| Réponse de modèle malveillante | champs surnuméraires (`volume`, `stop_loss`, `entry_price`, `signal`) → tuple d'`overrun_attempts`, événement `ai_overrun` enregistré, verdict inchangé ; `ReviewOutcome` ne porte **aucun** champ de niveau/volume ; réponse non conforme ⇒ `unavailable`, jamais une approbation |
| Filtre IA ne voit ni compte ni capital | prompt utilisateur vérifié : ni `equity`, ni `capital`, ni `solde`, ni `balance`, ni marge |

### 4.9 Configuration livrée et démarrage

- `tests/config/test_shipped_config.py` : `witness@1.1.0` et `trend_breakout@1.0.0` se
  chargent depuis `config/strategies/`, `config/agent.yaml` se valide en mode SIGNAL, et
  **les deux marchés ont deux stratégies distinctes** (`len(set(refs)) == 2`, EF-003).
- `uv run tradingagent-run --once` : **démarre et sort en 0**, avec `DATABASE_URL` surchargée
  vers une base **SQLite jetable** (`sqlite:///$TEMP/ta_run_once.db`) — la base Supabase de
  production n'a **pas** été écrite.

```
Running upgrade  -> 0001, initial schema
...
Running upgrade 0004 -> 0005, re-assert the append-only immutability guards for every dialect
connected to Deriv-Demo (demo)
no ANTHROPIC_API_KEY: the AI filter is disabled (RM-011, shadow only)
XAUUSD M15 history: 5472 requested, 5471 received, 5471 new
BTCUSD M15 history: 5472 requested, 5471 received, 5471 new
BTCUSD calendar learned: 672 open slots
XAUUSD calendar learned: 459 open slots
agent started in SIGNAL mode on BTCUSD, XAUUSD
cycle: 0 publication(s), 0 signal(s), 0 close(s), 0 divergence(s)
EXIT_RUN_ONCE=0
```

Le mode courant est `TRADING_MODE=SIGNAL` et `LIVE_TRADING_ENABLED=false` : en SIGNAL,
`execution_enabled` est faux, donc aucun ordre n'est possible dans ce run.

### 4.10 Tests adversariaux ajoutés — `tests/verification/` (41 tests)

Écrits par le vérificateur, avec leurs propres fixtures (aucune réutilisation des helpers des
équipiers), tous verts :

- `test_adversarial_risk.py` (15) : trade de référence autorisé (témoin) ; stop absent ;
  stop du mauvais côté ; volume sous le lot minimal ; arrêt actif ; plafond de positions ;
  spread trop large ; marché non prouvé ouvert ; compte non EUR ; cooldown ; une position par
  marché ; RM-017 (mauvais login, compte réel en DÉMO, compte DÉMO en LIVE) ; RM-019 en LIVE.
- `test_pipeline_and_idempotency.py` (12) : un agent arrêté n'atteint jamais le broker et le
  signal finit `RISK_REJECTED` ; marché incertain ⇒ refus ; IA bloquante ⇒ `EXPIRED` sans
  broker ; le verdict IA ne porte aucun champ créateur ; deux enregistrements de la même
  bougie ⇒ un seul signal ; même clé d'idempotence ⇒ **un seul** `order_send` et une seule
  ligne `orders` ; RM-017 avant tout envoi ; l'arrêt d'urgence global **survit à un
  redémarrage** ; un `/resume` opérateur le lève ; un agent automatique ne peut pas lever un
  arrêt global (`OperatorRequiredError`).
- `test_security_invariants.py` (14) : liste blanche, groupe, `/mode LIVE`, drapeau serveur,
  non-fuite des secrets, masquage des journaux, détection de secrets, prompt borné, réponse
  hostile, modes shadow/advisory.

Aucun de ces tests n'expose de défaut de production : les 41 passent sur l'arbre livré.

---

## 5. Points conformes (synthèse)

1. Suite complète verte sur l'arbre actuel : **1048 passed, 38 skipped, 0 failed**.
2. `ruff check`, `ruff format --check`, `mypy` verts ; `pre-commit run --all-files` : 9/9 hooks.
3. Détection de secrets **prouvée active** (jeton bot, affectation de token projet, clé privée).
4. Aucune valeur secrète de `.env` recopiée dans le dépôt.
5. `mt5_live` : 2/2 sur le compte DÉMO, **zéro position ouverte** après.
6. Invariant d'architecture respecté strictement (seul `app.py` importe `execution` ; `risk`
   ne l'importe même pas ; aucun module de production n'importe `backtest`/`research`).
7. RM-000 vérifié sur ses deux moitiés (liste blanche, `/mode LIVE`, drapeau serveur).
8. RM-017 vérifié avant chaque ordre, par lecture et par test.
9. Aucune écriture sur la base `DATABASE_URL` de production : le seul run de l'agent a utilisé
   une SQLite jetable ; aucun ordre réel n'a été envoyé.
10. `witness@1.1.0`, `trend_breakout@1.0.0` et `config/agent.yaml` se chargent ; un marché =
    une stratégie pour la configuration livrée ; `tradingagent-run --once` démarre en SIGNAL.

---

## 6. Écarts constatés

| # | Sévérité | Écart | Preuve | Impact |
|---|---|---|---|---|
| 1 | **HAUTE** | Le dépôt n'était **pas gelé** : `tests/execution/test_tracking.py` modifié à 04:54:27 pendant la vérification, après que l'équipier `execution` avait déclaré son travail terminé. Le test passait de rouge à vert. | `LastWriteTime=04:54:27` ; trace de la passe A sans `POSITION_OPEN` vs lignes 102-103 actuelles | La validité du « 0 failed » annoncé pour l'arbre antérieur est nulle ; seul l'arbre post-édition est vert |
| 2 | **HAUTE** | Deux runs `uv run pytest -q` concurrents au mien (04:53:07 puis 04:54:30/04:55:47, équipier `roadmap`) sur un dépôt déclaré gelé | `Win32_Process` : `uv.exe` PID 8712 puis 1880/7852, parents `powershell.exe` distincts de mon job | Flakiness possible (bases SQLite temporaires, contention MT5) ; mes mesures ont été refaites en passe B sur arbre calme |
| 3 | **HAUTE** | `docs/reports/2026-10-07-synthese-finale.md` créé à 04:54:02 (absent de mon empreinte de 04:53:04), et son tableau annonce « 1007 passed / 0 failed » pour l'arbre d'après 04:54:27 | `CreationTime=04:54:02` | Chiffre juste pour l'arbre final, trompeur s'il est lu comme le résultat de la vérification d'origine |
| 4 | **MOYENNE** | L'équipier `execution` écrivait encore des fichiers jusqu'à 04:51:39 et un test à 04:54:27, alors que la tâche affirmait « les autres équipiers ont terminé ; personne d'autre n'écrit » | 41 fichiers `.py` modifiés entre 04:40 et 04:54:27 | Le protocole de gel n'a pas été respecté ; toute vérification « à confiance » aurait été faussée |
| 5 | **FAIBLE** | `load_agent_config` n'impose pas l'unicité des stratégies entre marchés : rien n'empêche deux marchés de pointer le même manifeste. EF-003 n'est garanti que par la configuration livrée et son test | `src/tradingagent/config/agent.py` `_reference_problems` ne vérifie que l'unicité des **symboles** | Un futur `agent.yaml` pourrait violer EF-003 sans être rejeté |
| 6 | **FAIBLE** | La valeur réelle non secrète `MT5_SERVER` est recopiée dans `tests/`, `docs/reports/` et `.vscode/` | balayage `.env` → valeurs | Fuite d'information d'environnement (pas un secret) ; à nettoyer avant publication |
| 7 | **INFO** | `ruff format --check .` a renvoyé 231 puis 232 fichiers sans ajout de `.py` identifié entre les deux mesures | deux sorties horodatées | Non élucidé, sans impact détecté ; cohérent avec l'arbre mouvant |

Aucun écart de sévérité haute ne porte sur le **code de production** : ils portent sur
l'intégrité du processus de vérification et sur la traçabilité des chiffres annoncés.

---

## 7. Ce qui n'a PAS pu être vérifié ici

1. **PostgreSQL réel** : `TEST_DATABASE_URL` absent ⇒ **29 tests sautés**, dont l'immuabilité
   PostgreSQL (`0005_postgres_immutability`), la migration `0005`, le verrou de ligne qui
   sérialise deux transitions concurrentes, et `TRUNCATE`. Le comportement de la migration
   `0005` sur Supabase n'a donc **pas** été observé, seulement son exécution sur SQLite.
2. **Restauration / sauvegarde chiffrée** : `BACKUP_PASSPHRASE` absent ; l'aller-retour
   sauvegarde → restauration annoncé par les équipiers n'a pas été rejoué ici.
3. **Campagne de paper trading** (TASK-071) : 30 jours / 30 opérations par stratégie — hors
   de portée d'une session.
4. **Vérification juridique** (TASK-090) et **phase 9** : dépendent d'une signature humaine.
5. **Serveur Windows** (TASK-051) et installation « machine vierge » : pas d'infrastructure.
6. **Stratégie promue** : la synthèse indique elle-même qu'aucune stratégie n'a franchi les
   seuils de promotion ; je n'ai pas rejoué la campagne de backtest complète (elle dépend de
   jeux de données MT5 et de durées longues).
7. **Git** : rien n'est commité ; le « livrable » est un working tree. La traçabilité par
   commit est donc nulle tant que l'opérateur n'a pas commité.
8. **`uv run tradingagent-run --once` contre la vraie `DATABASE_URL`** : volontairement non
   exécuté (la racine de composition appelle `upgrade(database_url)` et écrirait les
   migrations sur Supabase). Le démarrage a été validé sur SQLite jetable uniquement.

---

## 8. Clés d'environnement nécessaires

`Fichier .env` (jamais dans le dépôt) — état constaté sur ce poste, sans afficher les valeurs :

| Clé | État | Rôle |
|---|---|---|
| `MT5_LOGIN`, `MT5_SERVER`, `MT5_PASSWORD` | présentes | compte MT5 de démonstration |
| `MT5_TERMINAL_PATH` | présente | terminal quand plusieurs installations coexistent |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS` | présentes | bot et liste blanche (au moins 1 identifiant requis) |
| `DATABASE_URL` | présente | PostgreSQL hébergé (Supabase) — **ne pas écrire dessus en vérification** |
| `TRADING_MODE`, `LIVE_TRADING_ENABLED` | présentes (`SIGNAL`, `false`) | mode courant et moitié serveur de RM-000 |
| `ANTHROPIC_API_KEY` | **présente mais vide** | optionnelle (RM-011) : sans elle l'agent démarre et le filtre IA n'est pas câblé |
| `TEST_DATABASE_URL` | **absente** | **requise** pour les 29 tests PostgreSQL ; doit finir par `_test` |
| `BACKUP_PASSPHRASE` | **absente** | **requise** pour les sauvegardes chiffrées |
| `TA_COMPARISON_*` | optionnelles | seuils d'alerte de TASK-093 |
| `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE`/`PGSSLMODE` | optionnelles | repli de `scripts/backup.ps1` |

Aucune valeur de ces clés n'apparaît dans ce rapport ni dans le dépôt.

---

## 9. Reproductibilité

```bash
uv run pytest -q                                    # 1048 passed, 38 skipped
uv run pytest -q tests/test_architecture.py -v      # 44 passed
uv run ruff check .                                 # All checks passed!
uv run ruff format --check .                        # 236 files already formatted
uv run mypy                                         # Success: no issues found in 209 source files
uv run pre-commit run --all-files                   # 9 hooks Passed
uv run pytest -q tests/verification                 # 41 passed
RUN_MT5_LIVE=1 uv run pytest -m mt5_live -q         # 2 passed (compte DÉMO)
DATABASE_URL="sqlite:///<jetable>" uv run tradingagent-run --once   # exit 0, mode SIGNAL
```

Pour un run `--once` sûr, la variable d'environnement `DATABASE_URL` prend le pas sur le
`.env` : c'est ce qui a été utilisé, et **aucune** écriture n'a touché la base de production.
