# Audit adversarial du chemin d'exécution vers le compte démo — 2026-10-08

**Périmètre** : `src/tradingagent/execution/**` (mt5_broker, tracking, reconciliation, recovery,
paper_broker, journal, ports, simulator) et son interaction avec `runtime/pipeline.py` et
`runtime/loop.py` (lecture seule).
**Méthode** : TDD. Chaque piste a d'abord un test témoin dans
`tests/execution/test_execution_audit.py` ; seuls les défauts reproduits par un test qui
échoue ont été corrigés. Les soupçons non prouvés sont consignés en fin de rapport, non corrigés.
**Correctifs** : cinq, tous dans `src/tradingagent/execution/mt5_broker.py`, plus leurs tests.
**Ce qui n'a pas été refait** : le chemin nominal sur le compte démo réel
(`RUN_MT5_LIVE=1 uv run pytest -m mt5_live -v`), déjà prouvé avant cet audit.

## Synthèse

| # | Piste | Verdict | Correctif |
|---|-------|---------|-----------|
| 1 | Idempotence : réponse perdue | **sain** (jamais de renvoi) — une limite consignée | non |
| 2 | `stop_present` : délai de propagation | **défaut** de robustesse (démontré par injection) | relecture bornée |
| 3 | Clôtures faites par le courtier | **défaut** (clôture perdue définitivement) | lecture unique du lot de deals |
| 4 | Réconciliation / RM-014 | **défaut** (le premier stop touché arrête l'agent) | collecte des clôtures avant comparaison |
| 5 | Conversion EUR/USD | **mixte** : taux absent refusé ; 2 défauts hors périmètre | non (recommandations) |
| 6 | `volume_step` / `volume_min` / arrondi | **défaut** de défense en profondeur | validation avant envoi |
| 7 | `retcode` : refus du courtier | **journalisation saine, notification absente** | non (recommandation) |

Aucun chemin testé n'a produit de **double envoi** : l'invariant R-05 tient sur les sept pistes.

---

## Piste 1 — Idempotence : un ordre dont la réponse est perdue est-il retrouvé par sa clé, sans renvoi ?

**Verdict : sain.** Le renvoi est impossible ; une limite est consignée (non corrigée).

**Preuve — tests qui passent :**

- `test_a_lost_answer_is_recovered_by_comment_and_journalled` : réponse perdue → la position est
  retrouvée par le commentaire (`key_comment` = SHA-256 tronqué de la clé), le ticket adopté,
  `orders.state = FILLED`, **un seul** `order_send`.
- `test_a_lost_answer_whose_position_already_closed_is_never_resent` : réponse perdue **et** stop
  touché par le courtier avant toute réconciliation → refus explicite
  (`accepted=False`, `retcode=None`), 2 envois au total (l'ordre, puis la clôture faite par le
  courtier), **jamais un troisième**.
- Existant, non refait : `tests/execution/test_mt5_broker.py::test_lost_answer_is_recovered_by_comment_without_any_second_order`,
  scénario `arrêt brutal pendant l'envoi` de `execution/recovery.py` (crash entre l'envoi et la
  réponse, puis redémarrage : 1 seul envoi).

**Extrait de code** (numéros d'origine, avant correctif) : la clé est cherchée avant tout envoi
(`mt5_broker.py:195-213`) ; après une perte de réponse ou une erreur d'envoi, `_find_by_comment`
retrouve la position par le commentaire, sinon la réponse est un refus explicite
« reconcile by comment before any retry, never resend » (`mt5_broker.py:405-422`).

**Limite consignée, non corrigée** : si la position a *déjà* été clôturée par le courtier au
moment de la recherche, il ne reste aucune trace locale (ni `positions`, ni `executions`, ni
`trades`) et aucune divergence — le courtier n'a plus rien à comparer. Le P&L du trade est perdu.
Le seul correctif honnête serait de rechercher le deal d'entrée par commentaire dans l'historique
et de le journaliser *avant* sa clôture : plus large que le correctif minimal, et le scénario
exige une réponse perdue **puis** une clôture avant réconciliation. Consigné, non corrigé.

**Corrigé au passage (traçabilité)** : dans ce même chemin de récupération, la ligne `executions`
recevait le **ticket de position** comme `broker_deal_ticket` (`mt5_broker.py:441` avant
correctif), ce qui n'est pas un ticket de deal (MT5 : `POSITION_TICKET` ≠ `DEAL_TICKET`).
`test_a_recovered_fill_carries_a_real_deal_ticket` échouait avant le correctif, il passe après
(`_entry_deal_ticket`, `mt5_broker.py:616-628`).

---

## Piste 2 — `stop_present` : la relecture du stop est-elle robuste au délai de propagation ?

**Verdict : défaut de robustesse, démontré, corrigé.**

**Test témoin (échouait avant, passe après)** :
`test_a_lagging_position_cache_does_not_close_a_protected_position` — un terminal dont la liste
de positions ne montre pas encore le fill à la première lecture (injection d'une lecture périmée) :
l'exécuteur lisait **une seule fois**, concluait « pas de stop », fermait au marché une position
**protégée** et levait une alerte critique.

**Extrait de code corrigé** (avant) :

```python
# mt5_broker.py:473-483 (HEAD)
    async def _stop_present(self, ticket: int, request: OrderRequest) -> bool:
        positions = await self._call(self._terminal.positions, request.symbol)
        for position in positions:
            ...
        return False  # gone or unreadable: not confirmed is not confirmed
```

**Correctif** (`mt5_broker.py:505-524`) : relecture bornée (3 tentatives, 0,25 s entre deux,
paramétrable pour les tests). Une position trouvée avec un stop **à un autre prix** est une
vraie différence et répond immédiatement (pas de nouvelle tentative) ; une position absente ou
sans stop est relue avant de conclure.

**Contre-épreuve** : `test_a_stop_that_really_is_absent_still_closes_the_position` (terminal
`drop_protection=True`) : la position est bien clôturée et l'alerte « without its stop-loss »
est levée — la temporisation ne transforme pas une position non protégée en position conservée.
Le scénario de recouvrement `message Telegram non remis` (qui repose sur cette clôture) passe
toujours.

**Nuance honnête** : le délai de propagation lui-même est une hypothèse (le test l'injecte
artificiellement) ; aucun retard n'a été observé sur le compte démo réel, où la lecture unique
suffit. Ce qui est prouvé, c'est le comportement en présence d'une lecture périmée : clôture
immédiate d'une position protégée. Le coût du correctif est borné à 0,5 s dans le seul cas où le
stop manque vraiment.

---

## Piste 3 — Positions fermées par le courtier : détectées, enregistrées, notifiées, sans doublon ?

**Verdict : défaut — une clôture sur trois était perdue définitivement.** Corrigé.

**Test témoin (échouait avant, passe après)** :
`test_three_positions_closed_at_three_different_seconds_are_all_collected`. Sortie avant
correctif :

```
>       assert {item.ticket for item in closed} == set(tickets)
E       assert {1001, 1003} == {1001, 1002, 1003}
E         Extra items in the right set:
E         1002
```

**Cause, ligne à l'appui** : `_collect_closures` appelait `_closure_for` **une fois par ticket**
(`mt5_broker.py:493-508` avant), et `_closure_for` avançait le curseur de deals au **maximum du
lot reçu** (`mt5_broker.py:511-513` avant). Or `deals_since` renvoie les deals dont l'époque est
`>=` au curseur (`execution/simulator.py:224`, même sémantique que
`data/mt5_terminal.py:184-205`) : le deal du ticket du milieu, plus ancien que le maximum du lot,
n'était plus jamais relu. La position restait `OPEN` en base → divergence au cycle suivant →
arrêt global (voir piste 4).

**Correctif** (`mt5_broker.py:559-614`) : **une seule lecture** du lot de deals par collecte,
extraction pure (`_closed_from`), puis avancement du curseur une fois le lot entier examiné
(`_consume`, `mt5_broker.py:585-589`). Le chemin `close()` (`_closure_for`) ne consomme plus le
curseur : il ne peut plus sauter par-dessus les deals d'un autre ticket.

**Preuves après correctif :**

- `test_three_positions_closed_at_three_different_seconds_are_all_collected` : 3 clôtures
  collectées, 3 lignes `trades`, puis **0** clôture au cycle suivant (toujours 3 lignes).
- `test_a_broker_stop_out_is_journalled_once_and_the_operator_is_told` : 1 ligne `trades`,
  `exit_reason = "stop_loss"`, P&L ≈ −10,20, `local_positions() == ()`, une seule alerte, et
  toujours 1 ligne + 1 alerte après un second `reconcile()`.

**Réserve (résidu runtime, hors de mon périmètre d'écriture)** : le message structuré de clôture
et la télémétrie `POSITION_CLOSED` / bucket journalier sont produits par la boucle à partir des
clôtures rendues par `on_candle` (`runtime/loop.py:285-292`). Depuis le correctif de la piste 4,
la collecte a souvent lieu pendant `reconcile()` : la clôture est enregistrée et alertée
(log critique), mais ces deux écritures de la boucle ne la voient plus. À traiter côté
`runtime/` (le Lead en est propriétaire).

---

## Piste 4 — Réconciliation : un écart local/courtier suspend-il le trading au lieu de le réparer en silence (RM-014) ?

**Verdict : défaut — RM-014 était appliqué à un stop qui avait simplement fait son travail.**
Corrigé.

**Test témoin (échouait avant, passe après)** : `test_a_broker_stop_out_is_not_a_state_divergence`
— position ouverte au courtier puis stop touché ; `MT5Broker.reconcile()` renvoyait
`("position 1001 XAUUSD is open locally but absent at the broker",)`.

**Chaîne de conséquence, prouvée ligne à ligne :**

1. `runtime/loop.py:415-432` (`_reconcile`) : toute divergence non vide →
   `self._guardian.on_divergence(detail)` ;
2. `control/guardian.py:42-44` : `_halt(GLOBAL, …)` ;
3. `control/guardian.py:4-6` et `61-73` : le gardien **ne lève jamais** un arrêt global ; seul
   l'arrêt `CONNECTION` est levé automatiquement. L'agent reste arrêté jusqu'à intervention de
   l'opérateur ;
4. la détection des clôtures ne passait qu'à la bougie **close** (`loop.py:281-292`), et les deux
   stratégies actives sont en M15 (`config/strategies/witness@1.1.1.yaml:27`,
   `config/strategies/trend_breakout@1.0.1.yaml:27`) : jusqu'à 15 minutes de fenêtre pendant
   laquelle chaque cycle (20 s) déclarait la divergence.

Conséquence : **le premier stop touché sur le compte démo arrêtait l'agent** et l'opérateur
recevait une alerte « divergence » pour un événement normal. (L'arrêt global sur divergence est,
lui, correctement testé : `tests/runtime/test_loop.py::test_a_divergence_halts_globally_and_alerts`.)

**Correctif** (`mt5_broker.py:654-676`) : `reconcile()` collecte d'abord les clôtures faites par
le courtier (piste 3), alerte pour chacune, **puis** compare les états. Un écart qui reste
inexpliqué est toujours une divergence : rien n'est réparé en silence.

**Contre-épreuve** : `test_a_broker_position_the_ledger_never_saw_is_still_a_divergence` (passe) —
une position présente au courtier et absente du registre produit bien 1 divergence
(« exists at the broker but not locally »). Le scénario de recouvrement
`redémarrage avec position ouverte` (qui exige `divergences == ()`) passe toujours.

---

## Piste 5 — Conversion EUR/USD du P&L et de la marge : signe inversé ou taux absent

**Verdict : mixte.** Taux absent → **refusé** (sain). Deux défauts **démontrés** mais hors de mon
périmètre d'écriture : le contrôle croisé est tautologique, et l'exposition se rabat en silence
sur un taux de 1.

**Preuve 1 — taux absent refusé (sain)** : `test_an_absent_conversion_rate_is_refused_by_the_sizer`
— un terminal dont `calc_profit` renvoie `None` donne `loss_one_lot = None` et
`profit_to_eur = None` (`mt5_broker.py:163-176`), et `size_position` refuse
(`risk/sizing.py:49-51`, `SizingError: … unavailable`). Aucune taille n'est inventée.

**Preuve 2 — défaut : le contrôle croisé ne peut pas échouer.**
`test_the_loss_cross_check_cannot_fail_because_the_rate_comes_from_the_loss` : avec un
`calc_profit` multiplié par 10, `quote.profit_to_eur` vaut ≈ 10 (au lieu de 1) et
`size_position` **ne déclenche pas** le contrôle de divergence (`risk/sizing.py:53-59`) ; elle
refuse seulement sur le lot minimum (`below the minimum lot`). En cause :
`mt5_broker.py:169-173` dérive le taux **du chiffre même** auquel il sera comparé
(`rate = loss_one_lot / (contract_size × distance)`), donc l'écart
`abs(broker_loss - independent_loss) / independent_loss` est nul par construction. Le contrôle
existe, il ne peut pas détecter une erreur de change. **Non corrigé** : un contrôle non
tautologique exige une source de taux indépendante (EURUSD) et sort du correctif minimal et de
mon périmètre.

**Preuve 3 — défaut : exposition calculée avec un taux de 1, en silence.**
`test_a_quote_without_a_stop_carries_no_rate_at_all` : `quote(symbol, direction, None)` — l'appel
que fait le pipeline pour mesurer l'exposition — ne calcule **jamais** de taux
(`mt5_broker.py:163-168`), donc `runtime/pipeline.py:432` retombe toujours sur
`Decimal(1)`. Pour XAUUSD/BTCUSD (cotés en USD) sur un compte en EUR, l'exposition engagée est
majorée d'environ 8 % : l'erreur est du côté prudent, mais elle est silencieuse et fausserait le
plafond d'exposition d'un symbole coté en JPY. **Non corrigé** (fichier en lecture seule pour
cette tâche) → recommandation.

**Signe** : aucun signe inversé n'est possible dans `quote()` (`abs()` + garde `independent > 0`,
`mt5_broker.py:169-173`) ; en revanche cette valeur absolue masque un signe faux venant du
courtier. Non concluant : invérifiable sans provoquer une réponse fausse du terminal.

---

## Piste 6 — `volume_step` / `volume_min` / arrondi : un volume au-dessus du pas peut-il être envoyé ?

**Verdict : défaut de défense en profondeur, démontré, corrigé.** Le chemin nominal, lui, est sain.

**Preuve — test témoin (échouait avant, passe après)** :
`test_an_illegal_volume_never_reaches_the_terminal[0.015 | 0.001 | 500]` — avant correctif, le
terminal recevait **et exécutait** `0.015` (pas un multiple de 0,01), `0.001` (< minimum) et
`500` (> maximum) : `order_send` était appelé, la position ouverte et journalisée. Sur le compte
réel, MT5 répond `10014` (volume invalide).

**Le chemin nominal est sain** : `risk/sizing.py:71` arrondit **vers le bas** au pas
(`_round_down`, `sizing.py:89-90`) et `sizing.py:74-78` refuse un volume sous le minimum. Le
moteur de risque ne peut donc pas produire un volume hors pas. Le défaut est l'absence de garde
dans l'exécuteur, qui est la dernière porte avant le terminal et sert aussi des chemins qui ne
passent pas par le dimensionnement (harnais, rejeu, appel direct).

**Correctif** (`mt5_broker.py:526-556`, appelé par `place` en `mt5_broker.py:228-241`) : avant
toute écriture au journal et avant tout envoi, le volume est confronté à la spécification du
courtier (minimum, maximum, multiple du pas) ; sinon refus explicite
(`accepted=False`, message nommant le volume, la limite et le symbole), `order_send` jamais
appelé, aucune ligne `orders`. La décision est persistée par le pipeline (transition
`ORDER_REJECTED`, télémétrie, événement — `runtime/pipeline.py:320-340`).

**Contre-épreuve** : `test_a_legal_volume_is_still_sent` (0,01 → envoyé), et toute la suite
`tests/execution` (le dimensionnement réel produit des multiples exacts du pas, même source de
spécification).

---

## Piste 7 — Erreurs de terminal (`retcode`) : un refus du courtier est-il journalisé avec son code et notifié ?

**Verdict : journalisation saine ; notification absente (défaut hors de mon périmètre d'écriture).**

**Journalisation — prouvée** : `test_a_server_refusal_is_journalled_with_its_retcode` (passe) —
refus `10006` (« blocked in France ») : `orders.state = REJECTED`, `orders.retcode = 10006`,
commentaire contenant le code, aucun ticket, aucune position ouverte. Code :
`mt5_broker.py:243-255` (branche de refus) puis `runtime/pipeline.py:321-340` (transition
`ORDER_REJECTED` + télémétrie `ORDER_REJECTED` + événement système `order_rejected` de gravité
WARNING).

**Notification — absente.** Deux lignes à l'appui :

- `runtime/pipeline.py:66-68` et `155-158` : le pipeline ne notifie que les refus **risque**
  « notables » ; dans la branche de refus courtier (`321-340`) il n'appelle `self._notifier.send`
  nulle part ;
- `app.py:891` : le rappel `alert` de l'exécuteur est câblé sur
  `lambda message: log.critical("broker: %s", message)` — une ligne de journal, pas Telegram.
  L'alerte « position ouverte sans son stop-loss » de EF-015 (`mt5_broker.py:454-457` avant
  correctif) et la nouvelle alerte de collecte de clôture n'atteignent donc pas l'opérateur.

**Correctif recommandé (non appliqué)** : notifier dans la branche de refus du pipeline (comme
les refus risque) et, si l'alerte critique doit atteindre l'opérateur, câbler `alert` sur le
canal Telegram dans `app.py:891`. Les deux fichiers sont hors de mon périmètre d'écriture pour
cette tâche.

---

## Correctifs apportés (récapitulatif)

Tous dans `src/tradingagent/execution/mt5_broker.py` ; tests dans
`tests/execution/test_execution_audit.py`.

| Correctif | Lignes (après) | Test qui l'exige |
|-----------|----------------|------------------|
| Relecture bornée du stop après exécution | 505-524 | `test_a_lagging_position_cache_does_not_close_a_protected_position` |
| `reconcile()` collecte les clôtures du courtier avant de comparer | 654-676 | `test_a_broker_stop_out_is_not_a_state_divergence` |
| Une seule lecture du lot de deals (`_unconsumed_deals` / `_consume` / `_closed_from`) | 559-614 | `test_three_positions_closed_at_three_different_seconds_are_all_collected` |
| Ticket de deal réel dans `executions` au lieu du ticket de position | 616-628 | `test_a_recovered_fill_carries_a_real_deal_ticket` |
| Volume validé (pas, minimum, maximum) avant tout envoi | 228-241, 526-556 | `test_an_illegal_volume_never_reaches_the_terminal` |

Signal continu : la position du stop et la pause sont injectables (`stop_readback_attempts` et
`stop_readback_pause`, `mt5_broker.py:110-111` pour les paramètres, `125-126` pour les champs,
`76-77` pour les constantes), ce qui permet une pause nulle en test ; les valeurs de production
sont 3 tentatives et 0,25 s.

## Soupçons consignés, non corrigés (non concluants ou hors périmètre)

1. **`record_closures` sans filtre de mode** — `journal.py:186` retrouve la position par ticket
   seul, tous modes confondus. Une collision de ticket entre PAPER et DEMO fermerait la mauvaise
   ligne et ferait passer le signal de l'autre mode à `CLOSED`. Non concluant : dépend des plages
   de tickets réelles, non observables ici ; le correctif exige de faire porter le mode par
   `ClosedPosition` / `record_closures` (port `TradeLog`).
2. **Refus pré-envoi qui lèvent au lieu de refuser** — `_ensure_trading` et `_check_mode`
   (`mt5_broker.py`, comportement inchangé et testé) lèvent `OrderRefusedError` ; le pipeline ne
   l'attrape pas dans `_execute`, donc le signal reste en `ORDER_SENT` avec un événement CRITICAL
   `pipeline_failed` (`loop.py:307-316`) au lieu d'un `ORDER_REJECTED` propre. Non testé ici
   (pipeline en lecture seule) : à traiter côté `runtime/`.
3. **Aucun message pour une réponse perdue** — la réponse perdue devient `ORDER_REJECTED` côté
   pipeline, sans notification ; le filet est la réconciliation (position au courtier inconnue du
   registre → divergence → alerte). Comportement jugé acceptable tel quel.
4. **Réconciliation partielle** — `reconciliation.py:87-106` compare symbole, sens, volume et stop,
   mais pas `open_price` ni `take_profit` : un take-profit modifié côté courtier ne serait pas
   détecté. Non testé.
5. **Devise** — `pnl_eur`, `risk_eur`, `profit_to_eur` nomment l'euro mais portent la devise du
   compte ; si le compte démo est en USD, tous les noms mentent (les valeurs restent cohérentes
   entre elles). Non vérifiable sans le terminal.

## Commandes exécutées et résultats exacts

```text
$ uv run pytest tests/execution/test_execution_audit.py -q        # avant correctifs
FAILED tests/execution/test_execution_audit.py::test_a_recovered_fill_carries_a_real_deal_ticket
FAILED tests/execution/test_execution_audit.py::test_a_lagging_position_cache_does_not_close_a_protected_position
FAILED tests/execution/test_execution_audit.py::test_a_stop_that_really_is_absent_still_closes_the_position
FAILED tests/execution/test_execution_audit.py::test_a_broker_stop_out_is_journalled_once_and_the_operator_is_told
FAILED tests/execution/test_execution_audit.py::test_three_positions_closed_at_three_different_seconds_are_all_collected
FAILED tests/execution/test_execution_audit.py::test_a_broker_stop_out_is_not_a_state_divergence
FAILED tests/execution/test_execution_audit.py::test_the_loss_cross_check_cannot_fail_because_the_rate_comes_from_the_loss
FAILED tests/execution/test_execution_audit.py::test_an_illegal_volume_never_reaches_the_terminal[volume0-lot step]
FAILED tests/execution/test_execution_audit.py::test_an_illegal_volume_never_reaches_the_terminal[volume1-broker limits]
FAILED tests/execution/test_execution_audit.py::test_an_illegal_volume_never_reaches_the_terminal[volume2-broker limits]
10 failed, 7 passed in 1.89s

$ uv run pytest tests/execution/test_execution_audit.py -q        # après correctifs
17 passed in 1.56s

$ uv run pytest tests/execution tests/verification tests/runtime tests/control -q
222 passed in 39.53s

$ uv run ruff check src/tradingagent/execution tests/execution
All checks passed!

$ uv run ruff format --check src/tradingagent/execution tests/execution
17 files already formatted

$ uv run mypy src/tradingagent/execution/mt5_broker.py
Success: no issues found in 1 source file

$ uv run mypy
Success: no issues found in 313 source files

$ uv run pytest tests/execution -q
76 passed in 18.45s

$ uv run pytest -q                                                # suite complète, après correctifs
SKIPPED [1] tests\data\test_mt5_live.py:38: set RUN_MT5_LIVE=1
SKIPPED [1] tests\data\test_mt5_live.py:76: set RUN_MT5_LIVE=1
SKIPPED [1] tests\signals\test_lifecycle.py:165: the row lock that serializes racers is a PostgreSQL guarantee
SKIPPED [5] tests\storage\test_constraints.py:276: SQLite has no TRUNCATE
SKIPPED [1] tests\storage\test_migrations.py:81: PostgreSQL only
2180 passed, 9 skipped in 332.10s (0:05:32)

$ uv run ruff format --check .
365 files already formatted
```

La suite complète compte 2180 tests (2157 au début de la tâche + les 17 de cet audit + les tests
ajoutés en parallèle par le Lead et l'autre équipier sur `data/` et `runtime/`) : aucun échec.

Une seule erreur `ruff check .` subsiste à l'échelle du dépôt, et elle n'est pas de cet audit :
`scripts/analysis/scalping_truth.py:782` (E501, 103 > 100), un script non suivi créé par l'autre
équipier. `ruff check src/tradingagent/execution tests/execution` est propre.

Deux des dix échecs initiaux ne dénonçaient pas un défaut du code, et le rapport ne les compte
pas comme tels : `test_a_stop_that_really_is_absent_still_closes_the_position` échouait parce que
le paramètre `stop_readback_pause` n'existait pas encore (c'est la contre-épreuve du correctif de
relecture, pas un détecteur), et
`test_the_loss_cross_check_cannot_fail_because_the_rate_comes_from_the_loss` échouait sur une
valeur attendue mal calculée de ma part (9,999… et non 1) ; une fois l'assertion corrigée, il
passe et documente la tautologie du contrôle croisé (piste 5).
