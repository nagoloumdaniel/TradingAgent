# Vérification indépendante — horloge serveur, exécution, clôtures (task-4)

**Date** : 2026-10-08 · **Vérificateur** : équipier `verifier` (task-4), indépendant des trois lots
**Arbre vérifié** : `daf368e` + 7 fichiers `src/` et 5 fichiers `tests/` modifiés non commités.
Le diff mesuré au début **et** à la fin de la vérification est identique (`git diff --numstat`) :
`data/market_data.py` 84/8, `execution/mt5_broker.py` 136/19, `execution/paper_broker.py` 9/0,
`execution/ports.py` 2/0, `runtime/loop.py` 107/5, `runtime/pipeline.py` 68/1, `runtime/ports.py` 2/0,
`tests/data/conftest.py` 4/1, `tests/data/test_market_data.py` 99/2, `tests/runtime/fakes.py` 6/1,
`tests/runtime/test_loop.py` 179/9, `tests/verification/test_pipeline_and_idempotency.py` 3/0.
**Aucun fichier de `src/` ni de `tests/` n'a été modifié par cette vérification.** Le seul fichier
écrit dans le dépôt est ce rapport.

## Méthode

Un test qui passe avant **et** après un correctif ne prouve rien. Chaque affirmation est donc
mesurée deux fois, sur le même fichier de test :

* **avant** : `$env:PYTHONPATH="$env:TEMP\ta-verify\wt\src"`, où `wt` est un worktree détaché à
  HEAD (`git worktree add --detach %TEMP%\ta-verify\wt HEAD`), vérifié sans `collect_closures`
  ni `_volume_problem` ;
* **après** : l'arbre de travail, sans `PYTHONPATH`.

Les tests sont écrits **hors du dépôt** (`%TEMP%\ta-verify\my_tests\`) et lancés avec
`-c D:\Projets\TradingAgent\pyproject.toml --rootdir D:\Projets\TradingAgent -p no:cacheprovider`,
ce qui fait résoudre `tradingagent` par `PYTHONPATH` (vérifié :
`IMP: C:\Users\Landh\AppData\Local\Temp\ta-verify\wt\src\tradingagent\__init__.py`,
`HAS collect_closures: False`).

Fichiers de test (réutilisables tels quels) :

| Fichier | Objet |
|---|---|
| `t4a_clock_data.py` | horloge, couche données (`MarketDataClient`) |
| `t4b_clock_loop.py` | horloge, boucle réelle + `MarketDataClient` réel |
| `t4c_closures_broker.py` | clôtures et réconciliation, `MT5Broker` |
| `t4d_pipeline_loop.py` | `_execute`, drain, bout-en-bout boucle + `MT5Broker` réel |
| `t4e_lot_b.py` | les cinq correctifs de l'audit, écrits sans les nouveaux paramètres de test |

Convention de nommage des tests : `test_head_repro_*` / `test_pre_fix_*` passent sur HEAD et
échouent après (preuve du comportement d'origine) ; `test_fixed_*` font l'inverse ; les autres
sont des invariants qui doivent passer des deux côtés.

**Pourquoi mes propres fichiers** : les tests ajoutés au dépôt par le lot A importent
`ClockUnverifiableError`, qui n'existe pas sur HEAD — ils ne peuvent donc pas être collectés
contre les sources d'avant et ne peuvent pas servir de détecteurs. Un test qui n'existe pas avant
le correctif ne prouve pas que le défaut existait : d'où les fichiers ci-dessus, écrits pour
tourner des deux côtés.

**Interdits respectés** : aucun `tradingagent-run` lancé (`Get-Process -Id 23420` → présent,
`python`, démarré 05:19:46, non touché), aucun ordre manuel, `RUN_MT5_LIVE` jamais positionné
(les deux tests `mt5_live` restent *skipped*).

---

## A. Lot T1 — gel de l'agent sur un tick de sonde manquant

### A1. « Un tick de sonde absent produit un événement CRITICAL et une sortie de cycle » (état d'origine)

**CONFIRMÉ.**

Sur HEAD, `MarketDataClient.verify_clock` lève `ClockMismatchError` quand la sonde ne rend aucun
tick, et la boucle transforme cela en cycle abandonné :

```text
$ $env:PYTHONPATH="$env:TEMP\ta-verify\wt\src"; uv run pytest -q -c ...\pyproject.toml `
    --rootdir D:\Projets\TradingAgent -p no:cacheprovider ...\t4a_clock_data.py
SKIPPED [1] ...:167: HEAD has no ClockUnverifiableError
SKIPPED [1] ...:190: HEAD has no ClockUnverifiableError
FAILED ::test_detector_2_on_head_the_probe_is_never_selected_so_its_tick_never_arrives
1 failed, 4 passed, 2 skipped in 0.72s
```
```text
C:\Users\Landh\AppData\Local\Temp\ta-verify\wt\src\tradingagent\data\market_data.py:101: ClockMismatchError
    async def verify_clock(self) -> None:
        tick = await self._call(self._terminal.last_tick, self._probe)
        if tick is None:
>           raise ClockMismatchError(f"no tick on {self._probe} to verify the server clock")
E           tradingagent.data.market_data.ClockMismatchError: no tick on BTCUSD to verify the server clock
```

`test_detector_2` établit au passage le mécanisme du gel : sur HEAD, la sonde n'est **jamais**
sélectionnée avant la lecture, donc son tick ne peut pas arriver (le test échoue parce que
`connect()` lève `ClockMismatchError`).

Et sur la boucle réelle (marché = `MarketDataClient` réel, terminal simulé, 5 cycles, sonde muette
après une connexion saine) :

```text
$ $env:PYTHONPATH="$env:TEMP\ta-verify\wt\src"; uv run pytest -q -c ...\pyproject.toml `
    --rootdir D:\Projets\TradingAgent -p no:cacheprovider ...\t4b_clock_loop.py
FAILED ::test_fixed_a_silent_probe_no_longer_storms_the_ledger
FAILED ::test_fixed_the_cycle_still_collects_the_candle_but_asks_no_strategy
FAILED ::test_fixed_the_probe_coming_back_resumes_the_strategies_without_a_restart
3 failed, 2 passed in 3.55s
```
`test_head_repro_a_silent_probe_is_a_critical_mismatch_on_every_cycle` (l'un des 2 passés)
vérifie sur HEAD : 5 événements `clock_mismatch` de gravité CRITICAL, `publications == 0` sur les
5 cycles, aucune bougie stockée (`CandleStore.last_open_time(...) is None`), aucune stratégie
appelée. Le cycle est bien abandonné avant la collecte — c'est-à-dire avant tout ce qui pourrait
faire revenir le tick.

**Échelle du défaut** : 383 événements sur 2 h 08 à 20 s par cycle est cohérent avec le rythme
observé (5 cycles → 5 événements). Non vérifié au-delà : je n'ai pas rejoué les journaux du
2026-10-07.

### A2. « Le correctif n'affaiblit pas la garantie d'horloge : un offset réellement différent arrête toujours l'agent »

**CONFIRMÉ** — pour tout offset **mesurable**. Trois invariants passent des deux côtés :

* `test_guarantee_1_a_fresh_tick_with_another_offset_is_still_a_mismatch` : tick frais à +3 h →
  `ClockMismatchError` (HEAD **et** après).
* `test_guarantee_2_a_reconnect_that_measures_another_offset_is_never_declared_connected` :
  `ensure_connected()` après une déconnexion, tick frais à +1 h → `ClockMismatchError` se propage.
  C'est le point sensible : la nouvelle branche `except ClockUnverifiableError: return True` ne
  capture **pas** `ClockMismatchError`, donc un offset mesuré faux n'est jamais avalé.
* `test_guarantee_3_the_bounded_retry_does_not_mask_a_measurable_offset_change` : première lecture
  muette, seconde lecture = tick frais décalé d'une heure → toujours `ClockMismatchError`.

Et au niveau de la boucle, `test_guarantee_a_measured_offset_change_stops_the_cycle`
(2 cycles, tick frais à +1 h) : 2 événements `clock_mismatch` CRITICAL, **zéro**
`clock_unverified`, `publications == 0` sur les deux cycles, aucune stratégie appelée — identique
avant et après le correctif.

```text
POST-FIX : 1 failed, 4 passed in 4.44s   (seul le test de reproduction HEAD échoue)
PRE-FIX  : 3 failed, 2 passed in 3.55s   (les 3 tests du correctif échouent, la garantie passe)
```

### A3. « Le correctif traite l'absence de mesure comme telle, sans tempête d'événements »

**CONFIRMÉ.** 100 cycles simulés avec une sonde muette, horloge mutable avançant d'une minute par
cycle, seuil de connexion 10 min :

```text
$ uv run pytest -q -c ...\pyproject.toml --rootdir D:\Projets\TradingAgent `
    -p no:cacheprovider ...\t4b_clock_loop.py
1 failed, 4 passed in 4.44s    # l'unique échec est le test de reproduction HEAD
```
`test_fixed_a_silent_probe_no_longer_storms_the_ledger` vérifie :

* `events_of(engine, "clock_unverified")` → exactement `[WARNING, CRITICAL]` (ouverture d'épisode
  puis escalade au seuil), soit **2 événements pour 100 cycles** au lieu de 100 ;
* `events_of(engine, "clock_mismatch") == []` ;
* 1 à 5 messages opérateur contenant « horloge » (lealerter garde son propre cooldown de 30 min).

`test_fixed_the_cycle_still_collects_the_candle_but_asks_no_strategy` vérifie que le cycle
continue de collecter (1 bougie publiée et stockée, `last_open_time == NOW - 14 min`) **et**
qu'aucune stratégie n'est interrogée : la suspension du trading est réelle, et l'alerte
« Aucun signal n'est émis tant que l'horloge n'est pas vérifiée » reste littéralement vraie.

### A4. « Un tick qui revient restaure le comportement normal sans redémarrage »

**CONFIRMÉ.** `test_fixed_the_probe_coming_back_resumes_the_strategies_without_a_restart` :
cycle 1 normal → sonde muette (cycle 2, suspendu) → sonde de retour (cycle 3, sans redémarrage) :
1 bougie publiée, **1 appel stratégie**, un événement `clock_verified` de gravité INFO, et un
message opérateur contenant « rétablie ». Ce test échoue sur HEAD (aucun `clock_verified`).

### A5. Limite déclarée : un offset réellement différent **non mesurable** n'arrête plus le cycle

**CONFIRMÉ (limite du correctif, pas un défaut caché).** `measure_offset` arrondit à la demi-heure
et rejette tout écart > 120 s : un serveur décalé d'une heure dont le seul tick a 10 minutes
(`3600 - 600 = 3000 s`, arrondi à 3600, écart 600 s) est classé **indifférenciable d'un tick
périmé**. Sur le code corrigé c'est `ClockUnverifiableError`, donc : trading suspendu, mais cycle
poursuivi (collecte, réconciliation, heartbeat EA, rapports) et **offset conservé** pour convertir
les bougies.

```text
test_limit_a_real_one_hour_shift_with_only_an_old_tick_is_declared_unverifiable
  → passe après correctif (ClockUnverifiableError), échoue sur HEAD (ClockMismatchError)
```

Conséquences exactes, et rien de plus : (1) aucune décision de trading n'est prise (vérifié
ci-dessus) ; (2) les données de marché continuent d'être converties avec l'offset mesuré à la
connexion, qui peut être faux dans ce cas précis ; (3) l'agent ne s'arrête pas, il attend un tick
mesurable. Le point (2) est une régression *théorique* de pureté des données par rapport à HEAD,
où le cycle était refusé — je la déclare, je ne peux pas la chiffrer sans terminal réel.

Second point de fragilité, non un défaut démontré : `_clock_failed` (mismatch) ne remet pas
`self._clock_verified` à `False` ; la suspension du trading sur mismatch ne tient qu'au `return`
anticipé du cycle, avant `_poll`. Une réorganisation future de `run_once` qui mettrait `_poll`
avant la vérification ferait trader la boucle avec un offset refusé. Aucun chemin actuel ne le fait.

### A6. Modification de l'infrastructure de test partagée

`tests/data/conftest.py` rend `FakeTerminal.select` idempotent (il empilait avant). Le changement
était nécessaire : le correctif appelle `select(sonde)` dans `connect()`, et deux tests existants
assertent `terminal.selected == ["BTCUSD"]` (`tests/data/test_market_data.py:285` et `:455`).
**Sémantiquement défendable** — `selected` décrit les symboles surveillés, comme `symbol_select`
—, mais cela retire au champ la capacité d'observer des appels répétés ; ceux-ci restent
observables par `terminal.calls` (chaque appel y est journalisé). Aucun autre test ne dépend de
`selected`.

---

## B. Lot T2 — audit du chemin d'exécution (5 correctifs dans `mt5_broker.py`)

### B0. « 10 tests échouaient avant correctif, 17 passent après »

**CONFIRMÉ avec une réserve de comptage.** Sur le fichier **actuel**
`tests/execution/test_execution_audit.py`, contre les sources d'avant :

```text
$ $env:PYTHONPATH="$env:TEMP\ta-verify\wt\src"; uv run pytest -q -c ...\pyproject.toml `
    --rootdir D:\Projets\TradingAgent -p no:cacheprovider tests/execution/test_execution_audit.py
FAILED tests/execution/test_execution_audit.py::test_a_recovered_fill_carries_a_real_deal_ticket
FAILED tests/execution/test_execution_audit.py::test_a_lagging_position_cache_does_not_close_a_protected_position
FAILED tests/execution/test_execution_audit.py::test_a_stop_that_really_is_absent_still_closes_the_position
FAILED tests/execution/test_execution_audit.py::test_a_broker_stop_out_is_journalled_once_and_the_operator_is_told
FAILED tests/execution/test_execution_audit.py::test_three_positions_closed_at_three_different_seconds_are_all_collected
FAILED tests/execution/test_execution_audit.py::test_a_broker_stop_out_is_not_a_state_divergence
FAILED tests/execution/test_execution_audit.py::test_an_illegal_volume_never_reaches_the_terminal[volume0-lot step]
FAILED tests/execution/test_execution_audit.py::test_an_illegal_volume_never_reaches_the_terminal[volume1-broker limits]
FAILED tests/execution/test_execution_audit.py::test_an_illegal_volume_never_reaches_the_terminal[volume2-broker limits]
9 failed, 8 passed in 3.51s

$ uv run pytest -q ... tests/execution/test_execution_audit.py     # après
17 passed in 1.94s
```

Donc **9 échecs avant**, et non 10 : le dixième du rapport est
`test_the_loss_cross_check_cannot_fail_because_the_rate_comes_from_the_loss`, que le rapport
déclare lui-même avoir corrigé (valeur attendue fausse de sa part). Il passe aujourd'hui des deux
côtés : c'est un test de caractérisation, pas un détecteur.

Classement des 9 échecs, vérifié un par un :

| Test | Échec avant correctif | Détecteur de défaut ? |
|---|---|---|
| `test_a_recovered_fill_carries_a_real_deal_ticket` | `1001 is not a deal ticket` | **oui** |
| `test_a_lagging_position_cache_does_not_close_a_protected_position` | `TypeError: unexpected keyword argument 'stop_readback_pause'` | **non** (signature) |
| `test_a_stop_that_really_is_absent_still_closes_the_position` | même `TypeError` | **non**, et le rapport le dit (contre-épreuve) |
| `test_a_broker_stop_out_is_journalled_once_and_the_operator_is_told` | `assert len(rows) == 1 → 0` | **oui** |
| `test_three_positions_closed_at_three_different_seconds_are_all_collected` | `{1001, 1003} == {1001, 1002, 1003}` | **oui** |
| `test_a_broker_stop_out_is_not_a_state_divergence` | `('position 1001 … absent at the broker',) == ()` | **oui** |
| `test_an_illegal_volume_never_reaches_the_terminal` ×3 | l'ordre est accepté et exécuté | **oui** |

Deux des trois « détecteurs » du correctif de relecture du stop ne peuvent donc pas démontrer le
défaut tel qu'écrits : ils échouent sur un paramètre qui n'existait pas. J'ai écrit l'équivalent
sans ce paramètre (`%TEMP%\ta-verify\my_tests\t4e_lot_b.py`) — voir B1.

### B1. Relecture bornée du stop après exécution

**CONFIRMÉ** (défaut réel, correctif efficace), par un test qui tourne des deux côtés :

```text
$ $env:PYTHONPATH="$env:TEMP\ta-verify\wt\src"; uv run pytest -q ... t4e_lot_b.py
FAILED ::test_b1_one_stale_read_does_not_close_a_protected_position - Asserti...
FAILED ::test_b3_a_recovered_fill_carries_a_real_deal_ticket - AssertionError...
FAILED ::test_b4_an_illegal_volume_never_reaches_the_terminal[volume0-lot step]
FAILED ::test_b4_an_illegal_volume_never_reaches_the_terminal[volume1-broker limits]
FAILED ::test_b4_an_illegal_volume_never_reaches_the_terminal[volume2-broker limits]
5 failed, 3 passed in 2.21s

$ uv run pytest -q ... t4e_lot_b.py          # après
8 passed in 2.74s
```

`test_b1_one_stale_read_does_not_close_a_protected_position` : une seule lecture de `positions()`
périmée (le terminal répond le fill avant de montrer la position). Avant : la position protégée est
fermée au marché, `log.closure_count == 0` faux, une alerte critique levée. Après : position
ouverte, aucun ordre de clôture, aucune alerte.

Contre-épreuve `test_b2_a_position_that_really_has_no_stop_is_still_closed` (`drop_protection=True`)
passe **des deux côtés** : les relances bornées ne transforment pas une position non protégée en
position conservée. La nuance du rapport (le délai de propagation est une hypothèse injectée, aucun
retard observé sur le compte réel) est exacte et je n'ai pas pu la lever — voir « non vérifié ».

### B2. Une seule lecture du lot de deals (clôture perdue)

**CONFIRMÉ.** Reproduit indépendamment, sans réutiliser le test de l'audit :

`test_c3_three_stop_outs_on_three_seconds_are_all_collected` (trois positions, trois secondes
serveur différentes) :

```text
$ $env:PYTHONPATH="$env:TEMP\ta-verify\wt\src"; uv run pytest -q ... `
    "t4c_closures_broker.py::test_c3_three_stop_outs_on_three_seconds_are_all_collected"
>       assert {item.ticket for item in closed} == set(tickets)
E       assert {1001, 1003} == {1001, 1002, 1003}
E         Extra items in the right set:
E         1002
```
Après correctif : les trois clôtures, un seul relevé, aucune au deuxième appel, `pnl` total
−30,6 € (`t4c` : 7 passés, 1 échec intentionnel — le miroir « pre-fix »).
`test_c2_three_stop_outs_in_the_same_second_are_each_collected_once` passe des deux côtés : le cas
« même seconde » n'était pas cassé, il ne l'est pas devenu.

### B3. Ticket de deal réel dans `executions`

**CONFIRMÉ** : `test_b3_a_recovered_fill_carries_a_real_deal_ticket` échoue avant (le ticket de
position est écrit), passe après (le ticket de deal d'entrée est retrouvé).

### B4. Volume validé avant envoi

**CONFIRMÉ** : `test_b4_an_illegal_volume_never_reaches_the_terminal[0.015 | 0.001 | 500]` échoue
avant (l'ordre est accepté : `accepted=True, ticket=1001`, `order_send` appelé, position
journalisée), passe après (`accepted=False`, message nommant le volume et la limite, `order_send`
jamais appelé, aucune ligne `orders`). `test_b5_a_legal_volume_is_still_sent` passe des deux côtés.

### B5. Collecte des clôtures avant comparaison (`reconcile`)

**CONFIRMÉ**, paire de tests miroirs :

```text
$ $env:PYTHONPATH=... ; uv run pytest -q ... t4c_closures_broker.py
FAILED ::test_c3_three_stop_outs_on_three_seconds_are_all_collected
FAILED ::test_c4_a_closure_from_before_a_restart_is_collected_by_the_first_drain
FAILED ::test_fixed_reconcile_collects_the_stop_out_before_any_comparison
3 failed, 5 passed in 3.93s
```
Sur HEAD, `reconcile()` rend `('position 1001 XAUUSD is open locally but absent at the broker',)`
et `test_pre_fix_reconcile_called_the_stop_out_a_divergence` passe ; après correctif, `reconcile()`
rend `()` et c'est le test miroir qui échoue (échec intentionnel, un seul dans `t4c` après
correctif). Au niveau de la boucle, l'effet est visible dans `t4d` (voir C3).

Chaîne de conséquence vérifiée sur la boucle réelle, avant correctif :

```text
CRITICAL tradingagent.control.guardian:guardian.py:76 halting global: local state diverges from
the account (RM-014): position 1001 XAUUSD is open locally but absent at the broker
```
→ arrêt **global**, non levé automatiquement (l'audit le dit, `control/guardian.py` le confirme ;
couvert par `tests/runtime/test_loop.py::test_a_divergence_halts_globally_and_alerts`, qui passe).

### B6. « Aucun chemin testé n'a produit de double envoi »

**PARTIELLEMENT VÉRIFIÉ — déclaré NON VÉRIFIÉ de façon exhaustive.** J'ai vérifié le chemin
réponse perdue : `test_b6_the_same_key_never_sends_twice_even_with_a_lost_answer` (1 `order_send`,
même ticket aux deux appels) passe des deux côtés, et la piste 1 du rapport est cohérente avec le
code lu (`find_order` avant tout envoi, `_find_by_comment` avant tout renvoi). Je n'ai pas
recherché de façon exhaustive un chemin de double envoi hors de ces scénarios.

---

## C. Lot C — clôtures drainées, refus notifié, exception d'exécution

### C1. `collect_closures` peut-il perdre une clôture ?

**NON, sur tous les scénarios que j'ai construits** — et un cas où le défaut d'origine est reproduit.

* `test_c2_…same_second…` : trois clôtures à la même seconde serveur → trois collectées, zéro au
  second appel. Passe des deux côtés.
* `test_c3_…three_seconds…` : trois clôtures à trois secondes → trois collectées (avant correctif :
  `{1001, 1003}`, la 1002 perdue).
* `test_c3b_a_later_entry_deal_never_hides_a_closure` : le curseur est avancé par le deal d'entrée
  d'une position ouverte **après** la clôture d'une autre ; la clôture antérieure est quand même
  trouvée. Passe des deux côtés.
* `test_c4_a_closure_from_before_a_restart_is_collected_by_the_first_drain` : la position a été
  clôturée pendant que l'agent était arrêté ; un exécuteur neuf (curseur à 0, `_known` vide) se
  ré-amorce depuis le registre, retrouve le deal de sortie, journalise le trade et ne signale
  **aucune** divergence. Avant correctif : `assert set() == {1001}` — aucune clôture, et
  `reconcile()` déclare la divergence (arrêt global au redémarrage).

**DEUX LIMITES DÉCLARÉES, non couvertes par le correctif :**

1. **Filtre de magic du terminal réel** (`test_limit_a_closure_without_the_agent_magic_is_invisible_and_halts`).
   `data/mt5_terminal.py:204` ne remonte que les deals portant `MAGIC` ; le simulateur, lui, ne
   filtre pas. En émulant ce filtre (sous-classe qui enregistre le magic par deal), une position
   fermée par un acteur étranger (fermeture manuelle dans MT5, autre EA — le EA Gardien du projet
   en est un) ne produit **aucun** deal visible : `collect_closures()` rend `()`, la position reste
   `OPEN` en base, et `reconcile()` rend 1 divergence → arrêt global. **Comportement identique
   avant le correctif** (donc pas une régression), mais la docstring de `collect_closures`
   (« Whatever the broker closed ») est plus large que le code.
2. **Perte en cas d'échec du drain** — voir C3/issue 2.

### C2. `collect_closures` peut-il compter une clôture deux fois ?

**NON, sur les scénarios testés.** Les deux chemins de la boucle (`on_candle` pendant `_poll`, puis
`_drain_closures`) partagent le même curseur et le même `_known` : après une collecte, le second
appel rend `()`, `reconcile()` n'ajoute aucune ligne `trades`, et une seule alerte est levée
(`test_c1_one_stop_out_is_collected_once_whichever_path_asks_first`).

Au niveau de la boucle, avec le faux courtier dont la file est partagée par `on_candle` et
`collect_closures` : `test_l5_a_candle_and_the_drain_never_count_the_same_closure_twice` (une bougie
publiée **et** une clôture en file dans le même cycle) → 1 publication, `closed_positions == 1`,
**1 seul message**. Passe des deux côtés (sur HEAD la clôture passe par le chemin bougie, sur le
code corrigé par le drain — sans doublon dans les deux cas).

### C3. Le drain placé avant `_reconcile` empêche-t-il toujours une fausse divergence ?

**OUI, et j'ai mesuré les deux moitiés de la garantie.**

* `test_l1_the_drain_runs_before_the_comparison` : l'ordre d'appel observé est exactement
  `["collect_closures", "reconcile"]` ; clôture drainée, `closed_positions == 1`,
  `divergences == 0`, aucun arrêt global, opérateur prévenu.
* `test_l2_control_without_a_drain_the_comparison_halts` : le même courtier, la même divergence,
  mais rien à drainer → `divergences == 1` et **arrêt global** (`HaltStore.status().halted is
  True`). C'est exactement ce que produit l'arbre d'avant correctif de bout en bout
  (`test_l3` avant : `CRITICAL … halting global … RM-014`).
* `test_l3_a_stop_out_reaches_operator_telemetry_and_daily_bucket` (bout-en-bout, boucle réelle +
  `MT5Broker` réel + `OrderJournal` réel) : `closed_positions == 1`, `divergences == 0`, pas
  d'arrêt, message opérateur contenant `XAUUSD`, télémétrie `POSITION_CLOSED == 1`, bucket
  journalier `trades == 1` / `pnl ≈ −10,20 €`, signal `CLOSED`, registre vidé de la position.

**DEUX FENÊTRES RÉSIDUELLES, démontrées :**

1. `test_l4_a_failing_drain_leaves_the_closure_to_reconciliation` : si `collect_closures()` lève,
   `_drain_closures` l'attrape, journalise un `log.warning` et rend la main. La comparaison suivante
   sauve l'agent (le vrai `reconcile()` collecte lui-même) : `divergences == 0`, pas d'arrêt,
   registre à jour. Mais `closed_positions == 0`, **aucun message opérateur**, **aucune télémétrie
   `POSITION_CLOSED`**, **aucun bucket journalier**.
2. Même chose pour une clôture qui tombe **entre** le drain et `reconcile()` : elle est collectée
   par le courtier (alerte `log.critical`), mais ni l'opérateur ni la télémétrie ne la voient.
   L'audit signale cette réserve dans son rapport ; le lot C la réduit sans la fermer.
   Confirmé par lecture : `app.py:891` câble l'alarme de l'exécuteur sur
   `alert=lambda message: log.critical("broker: %s", message)` — **le canal Telegram n'est pas
   branché** sur cette alarme. L'affirmation de l'audit est donc **CONFIRMÉE**.

**Observation supplémentaire (bruit, pas un défaut)** : sur une clôture collectée par le courtier,
`OrderJournal.record_closures` a déjà fait passer le signal en `CLOSED` (`journal.py:195`) ; la
boucle tente la même transition et journalise
`WARNING … signal 1 not moved to CLOSED: closed → closed is not allowed (RM-018)` à chaque
clôture. L'état final est correct, la télémétrie et le bucket aussi ; la transition de la boucle
est simplement morte sur ce chemin.

### C4. `_execute` : l'exception transforme-t-elle un ordre en ERROR, et le message est-il honnête ?

**CONFIRMÉ pour les trois volets.**

* `test_p1_a_raising_executor_never_leaves_the_signal_in_order_sent` : le courtier lève
  `OrderRefusedError` → l'état du signal est `ERROR` (plus jamais `ORDER_SENT`), l'issue est
  `order_refused`, télémétrie `ORDER_REJECTED` + événement `order_refused`, message opérateur
  « ⚠️ Ordre en erreur ». Avant correctif, l'exception remonte hors de `process()` (les tests p1,
  p2, p5 échouent par propagation).
* `test_p2_the_error_message_never_claims_an_order_was_refused` : ordre **déjà parti** puis
  exception. Le message dit « Aucun ordre confirmé. La réconciliation tranchera au prochain
  cycle. » et **ne contient pas** « refusé ». L'honnêteté de la formulation est vérifiée, pas
  supposée.
* `test_p5_a_notification_failure_never_changes_the_outcome` : un notifieur qui lève après le
  premier envoi ne change ni l'issue ni l'état du signal.

Réserve déclarée : état `ERROR` **ne remplace pas** une interdiction de trader le même marché. Un
nouveau signal sur une bougie ultérieure porte une nouvelle clé d'idempotence et peut partir. Les
garde-fous sont ailleurs (RM-014 compare les positions, `max_positions_per_market = 1`), et je n'ai
pas prouvé de bout en bout qu'aucune position en double n'est possible dans cette fenêtre.

### C5. `_notify_failure` prévient-il l'opérateur ?

**CONFIRMÉ.**

* `test_p3_a_broker_refusal_reaches_the_operator` : refus courtier (`retcode 10006`) → issue
  `order_rejected`, état `ORDER_REJECTED`, dernier message contenant « ⛔ Ordre refusé par le
  courtier » **et** `10006`. Avant correctif : le seul message est celui du signal, l'échec
  n'atteint pas l'opérateur.
* `test_p4_a_position_without_its_stop_is_announced` : `stop_present=False` → dernier message
  contenant « 🚨 Position sans stop-loss », le ticket, et « fermée immédiatement » (le message
  distingue bien le cas où la fermeture aurait échoué — branche lue dans `pipeline.py:422-431`).

### C6. Chaîne qualité après les trois lots

```text
$ uv run pytest -q tests/data tests/runtime tests/execution tests/verification tests/test_architecture.py
327 passed, 2 skipped in 38.32s
SKIPPED [1] tests\data\test_mt5_live.py:38: set RUN_MT5_LIVE=1
SKIPPED [1] tests\data\test_mt5_live.py:76: set RUN_MT5_LIVE=1

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
366 files already formatted

$ uv run mypy
Success: no issues found in 313 source files

$ uv run pytest -q                      # suite complète, arbre de travail
2181 passed, 9 skipped in 312.38s (0:05:12)
```

Les 9 sauts sont ceux attendus (2 `mt5_live` sans `RUN_MT5_LIVE`, 1 verrou PostgreSQL, 5 TRUNCATE
SQLite, 1 migration PostgreSQL). `ruff check .` est propre : l'E501 de
`scripts/analysis/scalping_truth.py` signalé par le rapport d'audit n'existe plus.

---

## Synthèse des verdicts

| # | Affirmation | Verdict |
|---|---|---|
| A1 | Un tick de sonde absent produisait CRITICAL + sortie de cycle | **CONFIRMÉ** |
| A2 | Le correctif ne laisse plus confondre absence de mesure et offset décalé | **CONFIRMÉ** |
| A2b | Un offset réellement différent arrête toujours l'agent | **CONFIRMÉ** si l'offset est mesurable ; **limite déclarée** sinon (A5) |
| A3 | Aucune tempête : 2 événements pour 100 cycles, pas de trading | **CONFIRMÉ** |
| A4 | Un tick qui revient restaure le comportement sans redémarrage | **CONFIRMÉ** |
| A5 | Un offset non mesurable laisse le cycle tourner avec l'ancien offset | **CONFIRMÉ** (limite, pas un défaut caché) |
| B0 | 10 tests échouaient avant l'audit | **RÉFUTÉ sur le chiffre** : 9 avec le fichier actuel ; le 10e était une assertion à corriger, le rapport le dit |
| B1 | Relecture bornée du stop | **CONFIRMÉ** (le test-témoin de l'audit, lui, ne démontre rien : `TypeError`) |
| B2 | Lecture unique du lot de deals | **CONFIRMÉ** (reproduit indépendamment) |
| B3 | Ticket de deal réel dans `executions` | **CONFIRMÉ** |
| B4 | Volume invalide refusé avant tout envoi | **CONFIRMÉ** |
| B5 | `reconcile()` collecte avant de comparer | **CONFIRMÉ** |
| B6 | Aucun chemin ne double-envoie | **NON VÉRIFIÉ** de façon exhaustive ; chemin réponse perdue vérifié |
| C1 | `collect_closures` ne perd ni ne double une clôture | **CONFIRMÉ** sur 5 scénarios ; **2 limites déclarées** (magic étranger, échec du drain) |
| C2 | Le drain atteint `notify_close`, la télémétrie et le bucket | **CONFIRMÉ** de bout en bout |
| C3 | Le drain avant `_reconcile` empêche la fausse divergence | **CONFIRMÉ** (avec la fenêtre résiduelle C3-issue 1/2) |
| C4 | L'exception d'exécution met le signal en ERROR, message honnête | **CONFIRMÉ** |
| C5 | Refus courtier et position sans stop notifiés | **CONFIRMÉ** |
| C6 | Chaîne qualité verte | **CONFIRMÉ** (327 ciblés, 2181 en entier, ruff, format, mypy) |

## Note d'exploitation

Le processus qui tourne (PID 23420, `python`, démarré à 05:19:46) exécute le code **d'avant** les
correctifs. Tout ce qui précède concerne l'arbre de travail : rien n'est actif tant que l'agent
n'est pas redémarré, et je ne l'ai ni arrêté ni redémarré.

## Ce qui n'a PAS été vérifié

1. **Le compte démo réel.** `RUN_MT5_LIVE=1 uv run pytest -m mt5_live -v` n'a pas été relancé : la
   preuve du chemin nominal était déjà acquise, et je n'ai pas voulu passer un ordre sur le compte
   pendant que l'instance tourne. Les deux tests restent *skipped*. Rien dans ce rapport ne prouve
   le comportement du terminal MetaTrader réel.
2. **La prémisse du correctif d'horloge** : que `symbol_select(sonde)` rende un tick disponible
   en moins de 3 lectures / 1 s sur un terminal qui vient de redémarrer. Le terminal simulé le fait
   immédiatement ; sur un terminal réel en cours de téléchargement de symbole, l'agent resterait en
   trading suspendu. Non mesurable sans toucher au terminal.
3. **Le filtre de magic réel** : je l'ai émulé parce que `SimulatedTerminal.deals_since` ne filtre
   pas. Comportement du vrai `history_deals_get` sur une clôture serveur (stop-out) vs une
   fermeture manuelle : non observé.
4. **Un décalage d'offset réel pendant que le marché est fermé** (changement d'heure serveur) : la
   limite A5 est démontrée par construction, pas par un cas réel.
5. **La robustesse de la cadence** : mes 100 cycles sont simulés (horloge mutable). Le
   comportement réel à 20 s par cycle sur plusieurs heures, y compris le cooldown de 30 min de
   l'alerter et l'escalade, n'a pas été rejoué en conditions réelles.
6. **L'invariant « jamais deux positions sur le même marché »** après un `ERROR` d'exécution : le
   raisonnement (nouvelle clé d'idempotence, RM-014, `max_positions_per_market`) n'a pas été
   vérifié de bout en bout.
7. **Les soupçons non corrigés de l'audit** : `record_closures` sans filtre de mode (collision de
   tickets PAPER/DEMO) et `open_price`/`take_profit` absents de `reconciliation.py` n'ont **aucun
   test** — je ne les ai pas instruits. Pour le contrôle croisé tautologique du taux de change et
   l'exposition rabattue sur un taux de 1, j'ai seulement constaté que leurs tests passent des deux
   côtés : ce sont des tests de caractérisation, ils ne détectent rien.
8. **Une régression exhaustive de la suite complète contre HEAD** : seule la suite ciblée et les
   fichiers de l'audit ont été rejoués contre les sources d'avant.
9. **La latence et le coût** des nouveaux appels (un `positions()` + un `deals_since()` de plus par
   cycle pour le drain, 0,5 s de pause dans le seul cas où le stop manque) : non mesurés en
   conditions réelles.

Aucun de ces neuf points n'est silencieux : chacun est soit hors de portée sans le terminal réel,
soit un angle que je n'ai pas instruit, et il est dit ici plutôt que supposé résolu.
