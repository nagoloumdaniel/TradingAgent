# Vérification adversariale indépendante — commit `89e3e8e`

**Date :** 2026-10-10 · **Vérificateur :** `adversarial-verifier` (n'a écrit aucun des artefacts vérifiés)
**Tâche :** `task-5` · **Portée :** les affirmations publiées par le Lead pour le commit `89e3e8e`

## 1. Empreinte de l'arbre vérifié

| Élément | Valeur |
|---|---|
| `git rev-parse HEAD` | `89e3e8e9ac491758d6128e4b25711b932739c0e8` |
| `git rev-parse HEAD^{tree}` | `4233d4ab76431560b6d9b6dd5ff4b5941b461491` |
| `git status --short` au début | *(vide)* |
| `git status --short` à la fin | *(vide, hors le présent rapport)* |
| Fichiers suivis | 673 |
| Commit précédent | `5174a72` |

Aucun processus n'a été tué, redémarré ni arrêté. Aucun ordre n'a été envoyé. Aucune écriture
en base de production : les seules bases touchées sont celles que la suite de tests du dépôt
utilise par construction (SQLite temporaire, plus le PostgreSQL de `TEST_DATABASE_URL`, voir
§5.2). Toutes les mutations de source ont été annulées : `git status --short` est vide.

## 2. Chaîne complète, rejouée sans faire confiance aux totaux annoncés

| Commande | Total annoncé | Total observé | Verdict |
|---|---|---|---|
| `uv run pytest -q` | 2789 passed, 9 skipped | **2789 passed, 9 skipped in 333.17s** | ✅ identique |
| `uv run ruff check .` | *All checks passed* | **`All checks passed!`** | ✅ |
| `uv run mypy` | *Success: no issues found in 361 source files* | **`Success: no issues found in 361 source files`** | ✅ |
| `uv run ruff format --check .` | 2 fichiers non conformes | **`2 files would be reformatted, 456 files already formatted`** | ✅ |
| `uv run pre-commit run --all-files` | *(non annoncé)* | **exit 1 : 4 fichiers**, voir défaut **D1** | ⚠️ |

Sortie brute (pytest) :

```
SKIPPED [1] tests\data\test_mt5_live.py:38: set RUN_MT5_LIVE=1
SKIPPED [1] tests\data\test_mt5_live.py:76: set RUN_MT5_LIVE=1
SKIPPED [1] tests\signals\test_lifecycle.py:165: the row lock that serializes racers is a PostgreSQL guarantee
SKIPPED [5] tests\storage\test_constraints.py:276: SQLite has no TRUNCATE
SKIPPED [1] tests\storage\test_migrations.py:81: PostgreSQL only
2789 passed, 9 skipped in 333.17s (0:05:33)
```

Le fichier d'évidence commité `docs/research/session-volatility/pytest-full.txt` porte la même
sortie (`2789 passed, 9 skipped in 357.79s`) et **le même motif de points position par position** :
l'évidence commitée est authentique et reproductible.

### 2.1 Les 2 fichiers non conformes étaient bien non conformes avant `HEAD`

Les deux fichiers sont **identiques octet pour octet** entre `HEAD~1` et `HEAD` (dernier commit
qui les touche : `e616f4e`, antérieur) :

```
$ git diff --stat HEAD~1 HEAD -- scripts/backtest/tune_stop_modes.py scripts/backtest/tune_stop_report.py
(rien)

$ git show HEAD~1:scripts/backtest/tune_stop_modes.py  > %TEMP%\...\tune_stop_modes.py
$ git show HEAD~1:scripts/backtest/tune_stop_report.py > %TEMP%\...\tune_stop_report.py
$ uv run ruff format --check %TEMP%\dsh-format-preexisting
2 files would be reformatted
```

Verdict : la non-conformité **pré-existe** au commit ; l'affirmation du Lead est exacte.

## 3. Attaques par mutation — chaque affirmation attaquée, chaque détection nommée

Chaque mutation a été appliquée seule, puis annulée. « Détectée par » nomme le test qui a échoué.

| # | Mutation appliquée | Détectée par (tests qui échouent) |
|---|---|---|
| **M1a** | `app._build_command_service` : ajout de `router.register("zzz_mutation", …)`, rien déclaré | `test_bot_menu.py::test_the_production_menu_lists_exactly_the_public_commands`, `::test_the_hook_publishes_the_production_menu_when_it_runs` (2 failed) |
| **M1b** | `bot_menu.publish_menu` : suppression de la portée `BotCommandScopeAllPrivateChats` | `::test_publishing_asks_for_both_scopes_and_the_commands_button`, `::test_publishing_uses_the_sendable_list_not_the_strict_one`, `::test_the_hook_publishes_the_production_menu_when_it_runs` (3 failed) |
| **M1c** | `bot_menu.NOT_TYPED = frozenset()` (donc `aide` proposée à la frappe) | 9 tests, dont `::test_aide_is_not_a_command_the_operator_types`, `::test_help_is_first_because_it_is_what_opens_the_palette` (9 failed) |
| **M7** | `app.run` : `if application.post_init is not None and False:` — le hook ne tourne jamais | `::test_the_agent_runs_the_hook_between_initialize_and_start`, `::test_the_agent_survives_a_hook_that_fails` (2 failed) |
| **M2** | `manifest.entry_policy()` rend un filtre **actif** alors que le manifeste n'a pas la clé | `test_entry_filter_evaluation.py::test_a_manifest_without_the_key_is_exactly_what_it_was`, `::test_the_filter_is_invisible_without_the_key`, `::test_the_filter_can_only_remove_signals_never_add_one`, `test_entry_filter_parity.py::test_a_manifest_without_a_filter_changes_nothing_in_the_harness`, `::test_a_volatility_filter_that_cannot_measure_lets_everything_through`, `::test_a_manifest_without_the_filter_traces_nothing_new` (6 failed) |
| **M3** | `entry_filter._FAIL_OPEN` : `PASS` → `REFUSED` (le filtre bloque quand la mesure manque) | `test_entry_filter.py::test_an_unmeasurable_volatility_lets_the_entry_through`, `test_entry_filter_evaluation.py::test_an_unmeasurable_volatility_lets_the_signal_through`, `test_entry_filter_parity.py::test_a_volatility_filter_that_cannot_measure_lets_everything_through` (3 failed, dont `assert 429 == 0` signaux filtrés) |
| **M4** | `evaluation.py` : `if not decision.passed and False:` — un refus devient un SIGNAL | 7 tests, dont `test_entry_filter_evaluation.py::test_a_signal_in_an_excluded_session_is_refused`, `::test_the_filter_can_only_remove_signals_never_add_one`, `test_entry_filter_parity.py::test_a_refused_signal_leaves_a_readable_event_and_records_nothing` |
| **M5a** | `ai/layer.py` : méthode publique `AiFilterLayer.create_signal` ajoutée | `test_ai_veto.py::test_the_layer_has_no_second_entry_point` (+3 autres) |
| **M5b** | `ai/layer.py` : champ `stop_loss` ajouté à `ReviewOutcome` | `::test_the_outcome_carries_no_field_that_could_move_a_level`, `::test_the_guard_would_catch_a_level_bearing_field` |
| **M5c** | `ai/layer.py` : `blocks_signal=False` — l'IA ne peut plus refuser | `::test_a_rejection_can_only_ever_block` |
| **M5d** | `ai/layer.py` : `applied=True` même en `shadow` | `tests/verification/test_security_invariants.py::test_a_shadow_rejection_never_blocks` |
| **M6** | `app.py` : `notify.bot_menu` importe `tradingagent.execution.ports` | `tests/test_architecture.py::test_real_tree_respects_boundaries` → `tradingagent.notify.bot_menu imports tradingagent.execution.ports: only risk may reach execution` |
| **M6b** | `notify.bot_menu` importe `tradingagent.backtest.harness` | `::test_real_tree_respects_boundaries` → `… imports tradingagent.backtest.harness: only the composition root tradingagent.app may import research or backtest…` |
| **M8** | `templates/scalping.html` : la phrase qui définit « net » est retirée | `test_profit_labels.py::test_the_net_sentence_says_what_net_includes` |

Deux faux positifs de ma part, corrigés et **non** retenus comme défauts : ma première attaque de
M5d utilisait `-k "veto or ai_"` et ratait `tests/verification/test_security_invariants.py`
(re-testé avec la bonne sélection : détecté) ; mon premier garde-fou réseau bloquait aussi le
`socketpair()` loopback du proactor Windows (garde-fou réécrit, voir §5.2).

## 4. Affirmations vérifiées, avec la preuve

### 4.1 Menu Telegram — dérivé du routeur, et de rien d'autre

Calcul indépendant (script hors dépôt, service de commandes de production sur base jetable) :

```
router knows 21 commands: ['aide', 'close_all', …, 'status']
published menu (20): ['help', 'close_all', 'disable', 'emergency_stop', 'enable', 'marche',
  'markets', 'mode', 'pause', 'performance', 'portes', 'positions', 'propositions', 'report',
  'restart', 'restart_all', 'resume', 'shutdown', 'signals', 'status']
count == 20                 : True
first is 'help'             : True
'aide' excluded             : True
order = help + alphabetical : True
all names match Telegram    : True
calls        : [('set_my_commands','BotCommandScopeDefault'),
                ('set_my_commands','BotCommandScopeAllPrivateChats'),
                ('set_chat_menu_button','MenuButtonCommands')]
menu button  : MenuButtonCommands
public entry point installs hook: True
outside readers of router._handlers: ['src\\tradingagent\\notify\\bot_menu.py']
```

Le dernier point confirme le commentaire du module : `bot_menu` est bien le seul lecteur externe
de `router._handlers`. Les deux portées et le bouton sont ceux annoncés.

### 4.2 Le hook est nécessaire — vérifié dans la source de PTB installée, pas sur parole

Dans `.venv\Lib\site-packages\telegram\ext\_application.py` (version mesurée : **22.8**) :

* `initialize()` (ligne 470) documente explicitement, lignes **479-480** :
  `Does *not* call :attr:`post_init` - that is only done by :meth:`run_polling` and :meth:`run_webhook`.`
  et son corps (lignes 485-511) ne l'appelle jamais ;
* `start()` (ligne 586), `stop()` (639), `shutdown()` (518) : aucun appel ;
* le seul appel est **ligne 1055-1056**, dans `def __run(` (ligne **1021**), le lanceur privé
  partagé par `run_polling` (742) et `run_webhook` (853).

`post_init` n'est donc jamais exécuté par `initialize`/`start` en 22.8 : l'appel explicite de
`app.run` (lignes 1062-1072) est **nécessaire**, et il est bien placé entre `initialize()`
(1063) et `start()` (1072). `telegram_app.build_application` installe le même hook (ligne 135)
pour le bot autonome, où `run_polling` s'en charge.

### 4.3 Le filtre d'entrée est inerte

Sur les 6 manifestes de `config/strategies/` :

```
trend_breakout@1.0.0.yaml      entry_filter=no  active=False max_mode=SIGNAL
trend_breakout@1.0.1.yaml      entry_filter=no  active=False max_mode=DEMO
vwap_pullback@1.0.0.yaml       entry_filter=no  active=False max_mode=SIGNAL
witness@1.0.0.yaml             entry_filter=no  active=False max_mode=SIGNAL
witness@1.1.0.yaml             entry_filter=no  active=False max_mode=SIGNAL
witness@1.1.1.yaml             entry_filter=no  active=False max_mode=DEMO
active filters in production: 0
```

`config/agent.yaml` est inchangé (`git diff --stat HEAD~1 HEAD -- config/agent.yaml` : vide),
`docs/research/datasets*` et `docs/research/thresholds.json` sont inchangés, et les mutations
M2/M3 (ci-dessus) prouvent que les tests détectent une activation sans clé comme un refus
transformé en signal.

### 4.4 Fail-open — vérifié par une batterie indépendante, avec témoins

```
--- must fail open (measurement impossible) ---
FAIL-OPEN      no candles at all (no ATR possible)    -> pass     reason=unmeasured
FAIL-OPEN      2 candles (far too few for ATR(100))   -> pass     reason=unmeasured
FAIL-OPEN      20 candles (still too few)             -> pass     reason=unmeasured
--- volume is not an input: the three variants must agree ---
   120 candles, volume=None     -> refused / volatility
   120 candles, volume=0.0      -> refused / volatility
   120 candles, volume=1000     -> refused / volatility
   identical verdict+reason across all three: True
--- controls: a measurable measurement is honoured ---
   regime NORMAL allowed -> pass / measured
   regime NORMAL refused -> refused / volatility
```

**Précision importante :** le volume n'est **jamais lu** par `indicators/entry_filter.py`
(seuls `high`, `low`, `close` entrent dans `atr_ratio`). L'affirmation « sans volume, le filtre
laisse passer » est donc vraie mais **vide** : l'absence de volume ne peut ni bloquer ni
favoriser quoi que ce soit. Les trois variantes de volume donnent une décision identique, ce qui
le prouve.

### 4.5 C-002 — structurellement, le filtre et l'IA ne peuvent pas créer ni desserrer

```
EntryDecision dataclass fields: ['verdict', 'reason', 'detail']
no level-bearing field on the decision: True
```

`strategies/evaluation.py` ne place le filtre **qu'après** la production d'un `SignalCandidate`
(lignes 114-128) : un refus rend `OutcomeKind.FILTERED` sans candidat, un accord rend le
candidat **inchangé**. Parité backtest/production : les deux chemins appellent la même fonction
(`backtest/harness.py:352` et `signals/generator.py:180` appellent tous deux `evaluate(...)`),
la parité est donc structurelle et non testée après coup.

### 4.6 Exposition du dashboard — 38 cas, aucun écart

`web/auth.py::exposure_problem`, attaqué par un script indépendant (38 cas, `RESULT: ALL OK`) :
acceptés `127.0.0.1`, `127.0.0.2`, `127.255.255.255`, `::1`, `0:0:0:0:0:0:0:1`, `localhost`,
`LOCALHOST`, `LocalHost`, `localhost.`, `::ffff:127.0.0.1`, `::1%0` ; refusés `0.0.0.0`, `::`,
`""`, `"   "`, `"\t\n"`, `192.168.1.10`, `10.0.0.5`, `dashboard.lan`, un hôte non résoluble,
`2130706433`, `0x7f000001`, `127.1`, `127.0.0.1.nip.io`, `[::1]`, `::ffff:0.0.0.0`,
`::ffff:192.168.1.10`, `１２７.０.０.１`, `localhost.evil`, `127.0.0.1.evil` ; un jeton
d'espaces (`"   "`, `"\t"`, `""`) compte comme absent ; le message de refus ne contient jamais
le jeton.

Point de contrat vérifié au passage : `::ffff:127.0.0.1` et `::1%0` **sont** de la boucle locale
(`ipaddress…is_loopback=True`), leur acceptation est donc correcte et non un trou.

Chemin d'entrée réel (`web/app.py::main`), sur `.env` temporaire, `uvicorn.run` et
`create_database_engine` remplacés par des fonctions qui échouent :

```
[entry] --host '0.0.0.0' -> exit 2; stderr starts 'Refus de démarrer : --host « 0.0.0.0 » écoute au-delà de la '
[entry] --host '::' -> exit 2; …
[entry] --host '' -> exit 2; … « <vide> » …
[entry] --host '192.168.1.10' -> exit 2; …
[entry] --host 'dashboard.lan' -> exit 2; …
[entry] uvicorn and the database were never reached on any refused bind
```

Refus **explicite**, code **2**, aucune base ouverte, aucun repli silencieux. Le bind par défaut
est `DEFAULT_HOST = "127.0.0.1"` (`web/app.py:126`) — donc le repli n'existe pas.

### 4.7 Chiffres des bandeaux — exacts, et paramètres intacts

| Bandeau | JSON source | Valeur lue | Verdict |
|---|---|---|---|
| or, PF net validation **0,8841** | `XAUUSD-witness@1.1.1.json` → `result.cost_net.profit_factor` | `0.884075` | ✅ arrondi exact |
| or, **38** opérations | idem `.trades` | `38` | ✅ |
| or, **-32,15 EUR** | idem `.net_profit` | `-32.14992761975` | ✅ |
| or, entraînement **0,6255** / 106 | `.train` | `0.625541` / `106` | ✅ |
| BTC, PF net validation **0,8391** | `BTCUSD-trend_breakout@1.0.1.json` → `result.cost_net.profit_factor` | `0.839102` | ✅ |
| BTC, **47** opérations | idem `.trades` | `47` | ✅ |
| BTC, **-53,82 EUR** | idem `.net_profit` | `-53.82184594092` | ✅ |
| BTC, entraînement **0,8124** / 209 | `.train` | `0.812384` / `209` | ✅ |
| `max_mode: DEMO` | les deux manifestes | `DEMO` | ✅ |

`git diff HEAD~1 HEAD` sur les deux manifestes ne touche **que des commentaires** ; vérifié
aussi sur le YAML **analysé** :

```
config/strategies/witness@1.1.1.yaml PARSED-IDENTICAL
   max_mode = DEMO | parameters = {"ema_fast":20,"ema_slow":50,"atr_period":14,
     "stop_atr_multiplier":1.5,"take_profit_rr":2.0,"entry_zone_atr":0.1}
config/strategies/trend_breakout@1.0.1.yaml PARSED-IDENTICAL
   max_mode = DEMO | parameters = {"channel_period":20,"trend_period":100,"atr_period":14,
     "stop_atr_multiplier":2.0,"take_profit_rr":2.0,"entry_zone_atr":0.13}
```

Aucun paramètre n'a bougé, et les `parameters` des JSON sont identiques à ceux des manifestes.

### 4.8 Aucun secret dans le diff

`git show 89e3e8e` filtré sur 9 motifs (clés `sk-`, `ghp_`, `AKIA`, `xox*`, `AIza`, clé privée
PEM, affectation `password|secret|api_key|token = <12+>`, `Bearer <20+>`, forme
`<9-10 chiffres>:<35>`) : aucune correspondance réelle.

```
+        token=HOLDOUT_TOKEN,                         # constante, pas un littéral
         access_token = declared.get_secret_value()… # ligne de contexte, lecture de settings
+        mt5_password="investor-secret",  # pragma: allowlist secret
+TOKEN = "un-jeton-de-test-suffisamment-long"  # pragma: allowlist secret
```

`.env` n'est pas suivi (`git ls-files` ne rend que `.env.example`) et `.gitignore` l'exclut. Le
jeton `123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawE` ajouté par `tests/notify/test_bot_menu.py`
est le faux jeton de la documentation PTB, déjà présent dans 4 autres fichiers de tests
(`test_production_entrypoint.py`, `test_command_palette.py`, `test_security_invariants.py`,
`test_secret_detector.py`).

### 4.9 Aucun test n'ouvre de socket vers Telegram

Garde-fou pytest injecté depuis l'extérieur du dépôt (aucun fichier du dépôt créé), qui refuse
toute connexion **hors boucle locale** (`connect`, `connect_ex`, `create_connection`,
`getaddrinfo`) :

```
$ PYTHONPATH=%TEMP% uv run --no-sync pytest -q -p dsh_no_socket
2760 passed, 9 skipped, 29 errors in 330.07s
```

Les 29 erreurs sont **toutes** dans `tests/storage/test_postgres_immutability.py`, et la cause est
unique :

```
[dsh_no_socket] non-loopback attempts blocked: 1
[dsh_no_socket] BLOCKED 'db.sggdlbtwcdeyfgzjezdl.supabase.co'
```

`2789 = 2760 + 29` : **aucun autre test de la suite n'a tenté une connexion hors boucle locale**.
En particulier, tous les tests `tests/notify/` (dont les 561 lignes de `test_bot_menu.py`) et tous
les tests `tests/web/` sont couverts par cette passe : aucun n'appelle `api.telegram.org`, et
rien ne dépend du réseau Telegram ni du jeton. Le `_SpyBot` de `test_bot_menu.py` et les
`monkeypatch.setattr(Bot, …)` font bien leur travail.

## 5. Défauts trouvés, classés par sévérité

### D1 — SÉVÉRITÉ BASSE · `pre-commit run --all-files` échoue sur l'arbre gelé (2 fichiers sans newline final)

**Fichiers :** `docs/research/execution-diagnostic/tools/evidence-parity-backtest-tests.txt`
(368 octets, dernier octet ≠ LF) et
`docs/research/execution-diagnostic/tools/evidence-verdicts.txt` (4 270 octets, dernier octet ≠ LF).

**Commande exacte :**

```
uv run --no-sync pre-commit run --all-files
```

**Sortie brute :**

```
end-of-file-fixer...............................................................Failed
- hook id: end-of-file-fixer
- exit code: 1
- files were modified by this hook
Fixing docs/research/vwap-tuning/axe-A-tableaux.md
Fixing docs/research/execution-diagnostic/tools/evidence-parity-backtest-tests.txt
Fixing docs/research/execution-diagnostic/tools/evidence-verdicts.txt

ruff-format.....................................................................Failed
- hook id: ruff-format
- exit code: 1
- files were modified by this hook
1 file reformatted, 24 files left unchanged   (×n lots)
mypy............................................................................Passed
architecture boundaries.........................................................Passed
=== EXIT 1 ===
```

Mesure octet par octet (blob `HEAD` vs copie corrigée par le hook) :

```
docs/research/execution-diagnostic/tools/evidence-parity-backtest-tests.txt
   HEAD blob: 368 bytes, ends with LF: False | after pre-commit: 369 bytes, ends with LF: True
docs/research/execution-diagnostic/tools/evidence-verdicts.txt
   HEAD blob: 4270 bytes, ends with LF: False | after pre-commit: 4271 bytes, ends with LF: True
```

**Portée :** les deux fichiers sont **inchangés par `89e3e8e`** (donc pré-existants, comme les
deux fichiers de format), et `axe-A-tableaux.md` n'est qu'un artefact CRLF de `end-of-file-fixer`
sous Windows (contenu identique, `git` le renormalise : `CRLF will be replaced by LF`) — il ne
compte **pas** comme un défaut. Il reste donc **4 fichiers** qui font échouer le propre portail
du dépôt, là où l'affirmation annoncée n'en comptait que 2 (mesure juste, mais pour
`ruff format --check` seulement).

**Correction minimale :** ajouter un newline final aux deux `.txt` (une ligne, aucune sémantique
touchée).

*Aucun impact d'exécution : ce sont des fichiers d'évidence, lus par des humains.*

### D2 — SÉVÉRITÉ BASSE · le bandeau attribue la p-value et les survivants FDR à `docs/research/stats/`, qui ne les contient pas

**Fichiers :** `config/strategies/witness@1.1.1.yaml:22-26`,
`config/strategies/trend_breakout@1.0.1.yaml:22-26` (texte **inchangé** depuis `HEAD~1` : les
deux puces concernées sont hors du diff, seule la première a été réécrite).

**Commande exacte :**

```
uv run --no-sync python -c "import json,pathlib; [print(n, json.loads(pathlib.Path('docs/research/stats/'+n+'.json').read_text())['result']['gates']['p_value']) for n in ['XAUUSD-witness@1.1.1','BTCUSD-trend_breakout@1.0.1']]"
```

**Sortie brute :**

```
XAUUSD-witness@1.1.1 0.63037
BTCUSD-trend_breakout@1.0.1 0.734266
```

Le bandeau annonce « **p-value minimale 0,3497**, contre la ligne de Bonferroni 0,016667 » sous
l'en-tête « Chiffres MESURES, **lus dans `docs/research/stats/`** ». Or `0,3497` n'existe dans
aucun des deux JSON de ce dossier (qui portent `0.63037` et `0.734266`, une p-value de
Monte-Carlo par retournement de signe, une autre mesure). La valeur `0,3497` est en revanche
**correctement sourcée ailleurs** :

```
docs/research/2026-10-08-anchored-split-and-folds-to-the-end.md:78: Minimum p = **0.3497**, i.e. **×21 above** the Bonferroni line
docs/research/campaign-M15/stdout.txt:70:     XAUUSD:slow-2.5R             p=0.3497
docs/research/cross-timeframe-extract.txt:15: multiple testing: benjamini_hochberg alpha=0.1 hyp=6 before=6 after=0 bonferroni=0.016667 pmin=0.3497
```

Le chiffre n'est donc **pas faux** ; c'est la phrase d'attribution qui couvre trois puces
hétérogènes. La même remarque vaut pour « 0 survivant sur 6 » (`after=0`, `hyp=6` ci-dessus).

**Correction minimale :** sortir les puces 2 et 3 de sous l'en-tête `docs/research/stats/`, ou y
ajouter la référence `docs/research/campaign-M15/` / `2026-10-08-anchored-split-and-folds-to-the-end.md`.

*À noter : « la campagne du 2026-10-08 a refusé 6 portes sur 9 » (ligne 5 des deux bandeaux) est,
lui, correctement sourcé (`docs/decisions/2026-10-08-mode-ceiling-derogation.md:64`) et désigne la
campagne du 2026-10-08, antérieure au correctif de spread — ce n'est pas un défaut, même si les
JSON régénérés le 2026-10-10 comptent d'autres verdicts par porte.*

## 6. Ce que je n'ai PAS pu vérifier, et pourquoi

1. **L'état réel du menu sur l'API Telegram** (les « 20 entrées pour les deux portées » et
   `get_chat_menu_button()` répondant `MenuButtonCommands`, « mesurés sur l'API le
   2026-10-10 »). Le vérifier exigerait d'appeler `api.telegram.org` avec le jeton de production
   et de **réécrire le menu du bot en service** : hors de mes contraintes. J'ai vérifié tout ce
   qui précède le fil : les trois appels émis, leurs arguments exacts, les deux types de portée,
   le type de bouton, et les points d'appel de `post_init` dans PTB 22.8.
2. **Que les processus en service exécutent bien `89e3e8e`.** Je n'ai interrogé, ni touché,
   l'agent, le dashboard, MT5 ou le superviseur. Les preuves ci-dessus sont statiques et
   unitaires.
3. **Le comportement du filtre d'entrée sur les 59 999 bougies de la campagne** et les chiffres
   de session/volatilité (`docs/research/session-volatility/session-volatility.json`). Je n'ai
   vérifié que le **code** (inertie, fail-open, C-002, parité par construction) et les
   **manifestes** (0 filtre actif). Je n'ai pas rejoué la campagne : elle lit des jeux gelés de
   plusieurs mégaoctets et n'a aucun effet sur l'arbre déployé.
4. **`tests/storage/test_postgres_immutability.py` sous garde-fou réseau.** Ces 29 tests
   écrivent dans un PostgreSQL distant (`TEST_DATABASE_URL`). Je ne les ai donc pas relancés une
   troisième fois sous garde-fou. En revanche, l'identité de la base a été vérifiée **sans
   connexion** : `TEST_DATABASE_URL` vise `db.sggdlbtwcdeyfgzjezdl.supabase.co/postgres`, la
   production vise `db.vheoxevixffljqpwxnop.supabase.co/postgres` — projets **distincts**
   (`same_database = False`), et le garde du dépôt (`tests/_database_guard.py`) refuse le cas
   dangereux. Aucun risque de donnée de production corrompue, mais la suite complète n'est pas
   hermétique au réseau : 29 de ses 2789 tests dépendent d'un service tiers joignable.
   *(Note de méthode : la suite complète a donc été lancée deux fois, et ces tests font
   `downgrade`/`upgrade` de leur base à chaque lancement — comportement voulu de ce module, sur
   la base de test.)*
5. **L'exactitude des mesures de recherche** (PF, p-values, portes) : je les ai **relues** dans
   les JSON et confrontées aux bandeaux, mais je n'ai pas rejoué les backtests qui les
   produisent ; je n'ai donc pas vérifié que les JSON eux-mêmes sont reproductibles à partir des
   jeux gelés.

## 7. Verdict

Aucun défaut **bloquant** : aucun ordre possible, aucun secret exposé, aucune donnée de
production corrompue, aucun invariant de risque violé. Les deux défauts trouvés sont de
sévérité **basse** (un portail `pre-commit` et une phrase d'attribution) et aucun n'affecte
l'agent en service.

Les affirmations publiées sur le menu Telegram, l'inertie du filtre d'entrée, le fail-open,
C-002, le correctif d'exposition du dashboard, les chiffres des bandeaux (`max_mode: DEMO` et
paramètres inclus), l'absence de secret et les quatre commandes de la chaîne complète sont
**vérifiées**, chacune par une commande et une sortie brute, et chacune des mutations
annoncées comme détectée l'a été par un test nommé.

`git status --short` est vide ; `HEAD` est resté `89e3e8e`, arbre `4233d4ab…`.
