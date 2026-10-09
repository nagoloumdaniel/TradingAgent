# Les six rejets du 2026-10-09 en DEMO : motif exact et verdict, signal par signal

**Date du diagnostic :** 2026-10-09 · **Portée :** signaux `#11` à `#16`, BTCUSD M15,
`trend_breakout@1.0.0` / `@1.0.1`, compte de démonstration à **5 496,67 €**.
**Contraintes tenues :** base lue en SELECT seulement (aucun `INSERT`/`UPDATE`/`DELETE`),
aucun ordre envoyé, `config/agent.yaml` et `src/tradingagent/strategies/` intacts,
`src/tradingagent/risk/` non modifié (voir § 6 : la preuve d'un bug de *calcul* n'existe pas).

---

## 1. Réponse en une page

| Signal | Heure UTC | Mode | État en base | Motif exact, mot pour mot | Verdict |
|---|---|---|---|---|---|
| #11 | 03:45:08 | SIGNAL | `error` | `OrderRefusedError('order carries mode SIGNAL, this executor runs in DEMO: refused')` | refus **CORRECT** (garde-fou), mais signal en trop → **défaut de conception** |
| #12 | 03:45:14 | DEMO | `order_rejected` | `send failed with an unknown outcome: order_send: -2 Invalid "comment" argument` | **BUG** (commentaire de 31 caractères refusé par le module MT5) |
| #13 | 04:00:10 | SIGNAL | `risk_rejected` | `entry_zone: price 82404.176 outside [82365.4312758585, 82403.1287241415]` | règle appliquée **CORRECTEMENT** ; réglage incohérent |
| #14 | 04:00:13 | DEMO | `risk_rejected` | `entry_zone: price 82404.19 outside [82365.4312758585, 82403.1287241415]` | idem |
| #15 | 04:15:08 | SIGNAL | `risk_rejected` | `entry_zone: price 82491.162 outside [82454.1918490115, 82490.8041509886]` | idem |
| #16 | 04:15:12 | DEMO | `risk_rejected` | `entry_zone: price 82491.164 outside [82454.1918490115, 82490.8041509886]` | idem |

**Trois causes indépendantes, aucune n'est le sizing.**

1. **Les quatre `risk_rejected` ne sont pas des rejets de taille.** Le contrôle `sizing` a
   **passé** sur les quatre (0,07 lot, « limited by risk » sur #13/#14, « limited by margin »
   sur #15/#16) et **dix-sept contrôles sur dix-huit ont passé** ; seul `check_entry_zone` a
   refusé. Le signal jumeau autorisé (#12) chiffre ce que le moteur accordait : 25,18 € de
   risque, soit 0,46 % des fonds propres. Le motif du refus n'est ni une limite de capital, ni
   le nombre de trades, ni le cooldown, ni la marge.
2. **Le sizing BTCUSD et la conversion EUR/USD ne sont pas la cause** (hypothèse du lead) : le
   taux USD→EUR existe et est appliqué (0,891), le contrôle croisé entre `order_calc_profit` et
   la formule indépendante passe, le contrôle `account_currency` passe. Démonstration chiffrée
   au § 3.
3. **Ce que le motif cache :** le prix exécutable comparé à la bande est l'**ask** (le prix payé
   à l'achat), alors que la bande du signal est construite sur le **close de bougie**, un prix
   **bid**. L'écart bid/ask (18,424 $) vaut à peu près toute la demi-largeur de la bande
   (±18,31 à ±18,85 $). Sur #15 et #16, la bande est même **plus étroite que le spread** : le
   marché n'avait pas besoin de bouger pour que l'ordre soit refusé.

**Conséquence opérationnelle :** sur BTCUSD, un signal BUY né d'un breakout est refusé
quasi systématiquement à la seconde qui suit la clôture, quel que soit le signal. C'est
pourquoi l'agent n'a jamais ouvert de position — mais ce n'est pas la seule raison (§ 4 et § 5).

---

## 2. Comment chaque motif a été établi

La table `signals` ne porte pas de motif, et **le journal applicatif ne contient rien sur
l'incident** : `logs/agent-20261008.log` s'arrête sur six lignes
`console: UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-80`,
écrites à `2026-10-09 05:45:18` heure locale, soit exactement `03:45:18` UTC — les
enregistrements du moment ont été **jetés** par l'encodage cp1252 du gestionnaire console, et
laissent six lignes de bruit à la place. Tout ce qui suit vient donc de la base.

```text
$ uv run python docs/research/execution-diagnostic/tools/dump_incident.py
# database: postgresql://postgres:***@db.vheoxevixffljqpwxnop.supabase.co:5432/postgres

=== risk_decisions (toutes) ===
id=13 | signal_id=13 | outcome=refused | decided_at=2026-10-09 04:00:10.296382+00:00 |
  reason=entry_zone: price 82404.176 outside [82365.4312758585, 82403.1287241415]
...
=== signal_events ===
id=56 | signal_id=11 | state=error | detail=order carries mode SIGNAL, this executor runs in DEMO: refused
id=61 | signal_id=12 | state=order_rejected | detail=send failed with an unknown outcome:
                                                            order_send: -2 Invalid "comment" argument
...
=== counts ===
signals=16 | risk_decisions=16 | orders=5 | positions=0 | trades=0 | executions=0
```

Trois instruments, chacun en lecture seule : la table `risk_decisions` (motif + verdict des
18 contrôles), la table `signal_events` / `execution_events` (transitions et cause d'échec
d'ordre), et le `checks` JSON qui contient **les valeurs numériques** vues par le moteur
(prix, spread, distance de stop, marge), ce qui permet de rejouer la décision hors base.

Le rejeu est **exact** : reconstruire la capture de risque à partir de ces seules valeurs
reproduit les décisions enregistrées au caractère près (18 contrôles, tous les motifs) :

```text
$ uv run python docs/research/execution-diagnostic/tools/replay_decisions.py
--- #12 DEMO ---   outcome : authorized   reason : 0.07 lot, 25.18 EUR at risk
                   sizing : 0.07 lot, limited by risk
--- #13 SIGNAL --- outcome : refused  refusals: ['entry_zone']
                   reason : entry_zone: price 82404.176 outside [82365.4312758585, 82403.1287241415]
--- #15 SIGNAL --- outcome : refused  refusals: ['entry_zone']
                   sizing : 0.07 lot, limited by margin
                   spread : spread 18.424, limit 38.47870197709
                   margin : minimum lot needs 367.43 EUR, 2748.34 usable
--- #16 DEMO ---   outcome : refused  refusals: ['entry_zone']   (idem, prix 82491.164)
```

Cette reconstruction est devenue un test : `tests/risk/test_incident_20261009.py` (15 cas,
`142 passed` sur `tests/risk`, sortie complète au § 8).

---

## 3. Le sizing et la conversion EUR/USD : hypothèse réfutée, chiffres à l'appui

Le risque est libellé en EUR, BTCUSD est coté en USD. La crainte était qu'une conversion
absente produise un refus systématique. **C'est faux, et voici la preuve.**

Le signal #12 (même bougie que #13, mais en DEMO) a été **autorisé** :

| Grandeur | Valeur mesurée | Source |
|---|---|---|
| Capital (equity) | 5 496,67 € | `account_snapshots`, et `daily_loss` : « 27.48 EUR at stake, limit 109.93 » |
| Budget de risque (0,5 %) | 27,4834 € | = 5 496,67 × 0,005 |
| Taux USD→EUR appliqué | 0,891 | déduit de `risk_eur / (volume × distance)` ; le contrôle croisé passe |
| Distance de stop depuis le prix de fill | 403,7422122787 $ | ask 82 373,657 − stop 81 969,9147877213 |
| Perte pour 1 lot (`order_calc_profit`) | ≈ 359,72 € | `risk_eur` enregistré = 25,1804 = 0,07 × 359,72 |
| **Taille calculée** | **0,07 lot** | `risk_decisions.volume = 0.07` |
| **Risque réalisé** | **25,1804 € = 0,458 %** | `risk_decisions.risk_eur` |
| Marge pour 1 lot | 36 695,98 € | `margin` : « minimum lot needs 366.96 EUR » (× 0,01) |
| Contrôle croisé broker / formule | écart ≪ 2 % (tolérance) | le contrôle `sizing` passe au lieu de lever `SizingError` |
| Contrôle de devise | `account_currency` : « account in EUR, expected EUR » | `checks.account_currency.passed = true` |

Autrement dit : la conversion existe (`MarketQuote.profit_to_eur`, calculée par
`MT5Broker.quote` comme `order_calc_profit(1 lot) / (contract_size × distance)`), elle est
appliquée au sizing (`risk/sizing.py:53`), et le résultat est un ordre **conforme** au budget
de risque. Sur #15 et #16, le `sizing` indique même « limited by **margin** » : la contrainte
était la marge (2 748,34 € utilisables pour 2 568,72 € nécessaires), pas la devise.

**Le test le prouve** (`tests/risk/test_incident_20261009.py`) :

```text
def test_the_authorized_twin_is_sized_at_007_lot_so_sizing_is_not_the_cause() -> None:
    """Signal #12 proves the EUR/USD conversion works: 0.07 lot, 25.18 EUR at risk, 0.46 %."""
```

---

## 4. #13 à #16 — `entry_zone` : règle appliquée correctement, réglage incohérent

### 4.1 Ce que le code fait, et pourquoi ce n'est pas une erreur de calcul

`check_entry_zone` (`src/tradingagent/risk/checks.py:104-111`) compare `ctx.entry_price`
à la bande du signal :

```python
price = ctx.entry_price  # ask pour un BUY, bid pour un SELL
inside = intent.entry_low <= price <= intent.entry_high
```

`RiskContext.entry_price` vaut `quote.ask` pour un BUY : c'est **le prix réellement payé**,
et le comparer à la bande est exactement l'intention de RM-012 (« le prix proposé s'écarte du
prix attendu au-delà du slippage maximal accepté »). Les bornes comparées sont bien celles du
signal, au chiffre près. **Il n'y a aucune erreur d'arithmétique : le verdict est fidèle.**

### 4.2 Ce que la règle a réellement mesuré

| | #13 / #14 (04:00) | #15 / #16 (04:15) |
|---|---|---|
| Close de la bougie déclenchante (`observed_price`) | 82 384,280 | 82 472,498 |
| ATR14 du signal | 188,4872414151 | 183,0615098855 |
| Demi-largeur de bande (`entry_zone_atr: 0.1`) | **18,8487241415 $** | **18,30615098855 $** |
| Spread ask − bid à la décision | **18,424 $** | **18,424 $** |
| Marge de manœuvre pour l'ask (demi-largeur − spread) | **+0,4247 $** | **−0,1178 $** |
| Bid à la décision (ask − spread) | 82 385,752 / 82 385,766 | 82 472,740 |
| Écart du **bid** au close du signal | **+1,47 $ (+0,0018 %)** | **+0,24 $ (+0,0003 %)** |
| Écart de l'**ask** au close du signal | +19,90 $ (+0,024 %) | +18,67 $ (+0,023 %) |
| Ask comparé à `entry_high` | +1,047 / +1,061 **au-dessus** | +0,358 / +0,360 **au-dessus** |

Lecture : **le marché n'avait pratiquement pas bougé.** En termes de bid — le prix sur lequel
la bande est centrée — il était à +1,5 $ et +0,2 $ du close, donc *dans* la bande, à 17,4 $ et
18,1 $ de la borne haute. Ce qui a fait sortir le prix de la bande, c'est le **spread** :
l'ask vaut bid + 18,424, et la bande n'autorise que +18,31 à +18,85.

**Le cas décisif** : sur #15/#16, même un fill au prix exact du signal (bid = 82 472,498, le
close qui a produit le signal) donne un ask de 82 490,922 > `entry_high` 82 490,8041509886.
Le refus ne dépend d'aucun mouvement de marché : la bande entière est plus petite que le
spread. C'est le test
`test_at_the_signal_price_itself_the_buy_is_already_out_of_its_own_band`.

### 4.3 La règle fonctionne correctement quand le marché « s'échappe » vraiment

Les six refus `entry_zone` de l'historique ne se valent pas — et c'est ce qui permet de
trancher :

| Signal | Date | Ask comparé | Demi-bande | Position du **bid** | Lecture |
|---|---|---|---|---|---|
| #2 | 08/10 02:00 | 83 032,795 | 15,921 | 83 014,371 → **32,0 $ sous** `entry_low` | refus **juste** : le prix est passé sous la zone |
| #7 | 08/10 15:45 | 81 278,969 | 38,624 | 81 260,545 → **22,0 $ au-dessus** de `entry_high` | refus **juste** : le marché a couru |
| #13 | 09/10 04:00 | 82 404,176 | 18,849 | 82 385,752 → **17,4 $ dans** la bande | refus dû au **spread seul** |
| #14 | 09/10 04:00 | 82 404,190 | 18,849 | 82 385,766 → **17,4 $ dans** la bande | idem |
| #15 | 09/10 04:15 | 82 491,162 | 18,306 | 82 472,740 → **18,1 $ dans** la bande | idem |
| #16 | 09/10 04:15 | 82 491,164 | 18,306 | 82 472,740 → **18,1 $ dans** la bande | idem |

Les décisions BTCUSD enregistrées donnent un spread de 18,424 (huit fois), 18,574, 20,175 et
21,031 entre le 2026-10-08 02:00 et le 2026-10-09 04:15 : ce n'est pas un pic isolé, c'est le
spread courant de ce compte sur ce symbole. Le rapport de capacités du 2026-10-03 mesurait un
spread **médian** de 2,424 $ sur les 300 dernières bougies M15 — donc la bande n'est pas
toujours fatale ; elle l'est chaque fois que le spread est large, et il l'était sur les six
signaux de la nuit.

**Pourquoi seulement BTCUSD ?** L'or n'est pas touché : sur XAUUSD la bande mesure ±0,84 $ pour
un spread de 0,31 $, soit 2,7 fois le spread. Le défaut est propre au bitcoin, où
`0.1 × ATR ≈ 18 $` et le spread vaut 18,42 $ : les deux échelles coïncident.

### 4.4 Verdict

> **Verdict par rejet : CORRECT.** `entry_zone` est une règle voulue (RM-012), elle est
> appliquée sans erreur, et les bornes comparées sont bien celles du signal. Le refus n'est ni
> un bug de calcul, ni un contournement de limite, ni une exception.
>
> **Verdict sur le réglage : défaut avéré.** La règle compare un prix **ask** à une bande
> construite sur un prix **bid**, et la demi-largeur (`entry_zone_atr: 0.1` × ATR) est du même
> ordre que le spread du courtier. Le résultat est un refus structurel des BUY BTCUSD : quatre
> sur quatre dans la fenêtre, sans que le marché ait bougé de plus de 0,002 %.

Ce que je ne tranche pas, et pourquoi : **la correction est une décision d'opérateur, pas un
correctif de code**, parce que les deux lectures possibles de la bande sont défendables (voir
§ 6). Je n'ai donc pas touché `src/tradingagent/risk/` : il n'y a pas de bug à y corriger.

### 4.5 Le même décalage existe dans le backtest, et il n'a jamais été mesuré

`BacktestHarness._try_enter` (`src/tradingagent/backtest/harness.py:322-340`) entre dès que la
bougie suivante touche la bande — `bar.low > entry_high` suffit à annuler l'entrée — puis
**fill au prix de référence + un demi-spread** (`backtest/costs.py:57-62` :
`reference + half_spread`) ; le moteur
de risque, lui, exige que l'**ask** (bid + spread entier) soit dans la bande. En termes de bid,
l'écart entre les deux conditions vaut exactement un spread : 18,424 $, soit **0,10 ATR**, soit
4,9 % de la distance de stop — plus que la demi-bande elle-même sur #15/#16. Et `backtest/`
n'importe pas `tradingagent.risk` (vérifié : aucune occurrence), donc **la porte `entry_zone`
n'est jamais appliquée dans les mesures qui ont validé la stratégie**. C'est la raison de fond
pour laquelle une stratégie « validée » peut être intradable en production, et cela doit être
dit avant de toucher au paramètre (§ 6).

---

## 5. #12 — `order_rejected` : **BUG** ; le commentaire d'ordre fait 31 caractères et le module MT5 en refuse 30 ou plus

### 5.1 Le motif

`SignalPipeline._execute` construit le commentaire, puis `MT5Broker.place` le re-hache :

```python
# runtime/pipeline.py:81
def order_comment(idempotency_key: str) -> str:
    return "ta-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]      # 15 caractères

# execution/mt5_broker.py:252
comment=key_comment(request.idempotency_key, request.comment)
# execution/mt5_broker.py:81
def key_comment(idempotency_key: str, prefix: str = "ta") -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:HASH_LENGTH]  # 16
    return f"{prefix}-{digest}"[:COMMENT_LIMIT]        # COMMENT_LIMIT = 31

$ uv run python -c "..."
order_comment  = 'ta-2fd16bf5cebc' (15)
key_comment    = 'ta-2fd16bf5cebc-2fd16bf5cebc210' (31)
```

Deux remarques au passage : le hash est **le même** que celui déjà présent dans le préfixe
(le re-hachage n'ajoute aucune entropie), et la troncature à 31 tombe pile sur la limite
supposée.

### 5.2 La mesure : la limite réelle est 29, pas 31

Sonde sur le module natif `MetaTrader5 5.0.6231` (`_core.cp312-win_amd64.pyd`, qui porte la
table des messages `Invalid "<champ>" argument`). `order_check` est utilisé — jamais
`order_send` — et aucun terminal n'est connecté dans le processus (`terminal_info() is None`),
donc rien ne peut partir :

```text
$ uv run python docs/research/execution-diagnostic/tools/probe_comment_limit.py
terminal_info  : None
last_error     : (-10004, 'No IPC connection')
version        : 5.0.6231
comment runtime: 'ta-2fd16bf5cebc-2fd16bf5cebc210' longueur 31

longueur 28          -> accepte (passe a l'IPC)  last_error=(-10004, 'No IPC connection')
longueur 29          -> accepte (passe a l'IPC)  last_error=(-10004, 'No IPC connection')
longueur 30          -> REFUSE par le module     last_error=(-2, 'Invalid "comment" argument')
longueur 31          -> REFUSE par le module     last_error=(-2, 'Invalid "comment" argument')
longueurs acceptees : [0..29]
longueurs refusees  : [30, 31, 32, 33]
```

**Conclusion : le commentaire de 31 caractères est refusé par le module avant toute IPC.**
`mt5.order_send` lève alors `TerminalError: order_send: -2 Invalid "comment" argument`
(`data/mt5_terminal.py:162-165`), ce que `MT5Broker._after_send_failure` transforme en
`send failed with an unknown outcome` — d'où l'état `order_rejected` du signal #12. L'ordre
n'a **jamais atteint le courtier** : `positions`, `trades` et `executions` sont vides, et
`orders.broker_order_ticket` est `NULL` pour les cinq ordres.

Ce n'est pas propre à #12 : les quatre ordres du 2026-10-08 (#1 à #4 dans `orders`) portaient
le même commentaire, construit par la même ligne de code, et sont morts de la même façon ; mais
`order_send` jetait alors le motif (`answer lost: reconcile by comment…`), donc pour ces quatre
la cause est une **inférence forte, pas une lecture** — le correctif du 2026-10-08
(`mt5_terminal.py`) a conservé le message, ce qui a permis de lire `-2 Invalid "comment"` le
2026-10-09 sur #12. **Aucun ordre n'a abouti depuis le début de la campagne** (`orders` : 5
lignes, 0 ticket ; `positions`, `trades`, `executions` : vides), et pour le cinquième le motif
est formellement identifié.

### 5.3 Bug secondaire, dans la même ligne : la réconciliation cherche un autre commentaire

```python
# execution/mt5_broker.py:252   ce qui part chez le courtier
comment = key_comment(request.idempotency_key, request.comment)
# execution/mt5_broker.py:565   ce que la réconciliation cherche
comment = key_comment(idempotency_key)
```

```text
envoye    : 'ta-2fd16bf5cebc-2fd16bf5cebc210' 31
recherche : 'ta-2fd16bf5cebc210b' 19
identiques: False
```

Même si l'ordre était parti, `_find_by_comment` ne l'aurait **jamais** retrouvé : la clé
d'idempotence annoncée dans le commentaire (§ 10.2 du cahier des charges) est inopérante pour
les ordres d'entrée. Les clôtures, elles, utilisent `key_comment(f"close:{ticket}:{reason}", "tc")`,
soit 19 caractères : sous la limite et cohérentes avec leur propre recherche.

### 5.4 Correctif proposé (hors de mon périmètre d'écriture)

`src/tradingagent/execution/` ne fait pas partie du périmètre qui m'a été assigné
(`docs/research/execution-diagnostic/`, `tests/risk/`, `src/tradingagent/risk/`). Le correctif
est d'une ligne, plus une suppression de doublon :

```python
# execution/mt5_broker.py
COMMENT_LIMIT = 29  # le module MetaTrader5 refuse 30 et plus : (-2, 'Invalid "comment" argument')
...
# place(), ligne 252 : ne pas re-hacher une clé déjà hachée par l'appelant
comment = key_comment(request.idempotency_key)  # 'ta-' + 16 hex = 19 caractères
```

Avec cette correction, le commentaire envoyé est **exactement** celui que `_find_by_comment`
recherche, et il tient dans la limite mesurée. Attention : ces 29 caractères sont la limite du
**module Python** ; le pont EA, lui, tronque à 31 (`docs/ea/protocole-pont.md:100`). Le chemin
qui appelle `mt5.order_send` doit retenir la plus basse des deux.

Deux tests de régression à écrire **d'abord** (les deux sont rouges aujourd'hui, verts après le
correctif ; à ajouter dans `tests/execution/`, par le propriétaire de ce périmètre) :

```python
def test_the_order_comment_fits_the_terminal_limit() -> None:
    # 29 est mesuré sur MetaTrader5 5.0.6231 : 30 et plus donnent (-2, 'Invalid "comment" argument').
    key = "trend_breakout@1.0.1:BTCUSD:M15:2026-10-09T03:45Z"
    assert len(key_comment(key, order_comment(key))) <= 29  # aujourd'hui : 31 → rouge


def test_the_comment_sent_is_the_one_reconciliation_looks_up() -> None:
    key = "trend_breakout@1.0.1:BTCUSD:M15:2026-10-09T03:45Z"
    assert key_comment(key, order_comment(key)) == key_comment(key)  # aujourd'hui : rouge
```

Baisser `COMMENT_LIMIT` à 29 suffit à verdir le premier ; le second impose en plus de
supprimer le re-hachage, faute de quoi la réconciliation par commentaire reste inopérante.

**C'est le premier correctif à appliquer** : tant qu'il n'est pas fait, aucun ordre ne peut
partir, quelle que soit la correction apportée au § 4.

---

## 6. Ce qu'il faut faire de `entry_zone`, et ce qu'il manque pour trancher

La règle actuelle est **plus stricte que ce que la stratégie a voulu, d'exactement un spread** :
la bande exprime une tolérance de 0,1 ATR *autour du close*, mais la porte la teste sur un prix
qui vaut déjà bid + spread. Autrement dit, en espace bid, la tolérance effective est
`0,1 × ATR − spread` : +0,42 $ sur #13/#14, **−0,12 $ sur #15/#16**.

Deux lectures défendables, à trancher par l'opérateur :

| Option | Changement | Effet mesuré sur #15/#16 | Coût |
|---|---|---|---|
| **A. Élargir la bande** | `entry_zone_atr: 0.1 → 0.2` dans `config/strategies/trend_breakout@1.0.1.yaml` | demi-largeur 18,31 → **36,61 $**, marge pour l'ask **+18,19 $** | la tolérance d'entrée passe de 5 % à 10 % de la distance de stop ; **invalide les paramètres validés en backtest** |
| **B. Comparer à l'identique** | dans `check_entry_zone`, exiger l'ask dans la bande **décalée du spread** (BUY : `ask ≤ entry_high + spread`, ce qui équivaut à `bid ≤ entry_high` ; SELL : inchangé, le bid est déjà le prix exécutable et la bande est en espace bid) | l'ask au prix du signal (82 490,922) repasse **dans** la bande | modifie une règle de protection du capital : **mandat explicite de l'opérateur requis** (esprit C-002) |

Ce que je recommande : **A comme correctif immédiat de campagne démo** (c'est du paramétrage
de stratégie, réversible, et cela ne desserre aucune limite de risque), **B comme correction de
fond** si l'opérateur confirme que la bande est une tolérance en espace bid — et dans les deux
cas, **refaire tourner le backtest** : la porte `entry_zone` n'y est pas appliquée, donc l'effet
réel du paramètre n'a jamais été mesuré (§ 4.5).

Ce qui manque pour trancher définitivement, et que je ne peux pas produire d'ici :
une mesure du spread BTCUSD **par heure** sur plusieurs jours (le rapport de capacités ne
donne qu'une médiane sur 300 bougies, et le spread courant du compte est 7,6 fois cette
médiane). Sans elle, on ne sait pas si la bande à 0,1 ATR est fatale en permanence ou seulement
aux heures où le spread s'élargit — et donc si le bon levier est le paramètre de stratégie
(A), ou la fenêtre de trading.

---

## 7. #11 — `error` : refus correct d'un signal qui n'aurait pas dû être exécuté

**Motif exact** (`signal_events.id=56`, `system_events.id=471`) :
`OrderRefusedError('order carries mode SIGNAL, this executor runs in DEMO: refused')`, levée par
`MT5Broker._check_mode` (`execution/mt5_broker.py:714-718`) parce que le signal porte
`mode = SIGNAL` alors que l'exécuteur tourne en DEMO.

**Le refus lui-même est CORRECT** : un exécuteur qui reçoit un ordre d'un autre mode ne doit
rien envoyer, et la garde fonctionne. Mais ce signal **n'aurait jamais dû être exécuté** :

1. `SignalGenerator.on_candle_closed` (`signals/generator.py:120-146`) évalue **toutes** les
   stratégies du catalogue dont `allowed_symbols` contient le marché — pas seulement celle que
   `config/agent.yaml` assigne au marché. Pour BTCUSD, deux manifestes coexistent :
   `trend_breakout@1.0.0` (`max_mode: SIGNAL`) et `@1.0.1` (`max_mode: DEMO`), tous deux `paper`
   dans `strategy_registry`. Chaque bougie produit donc **deux signaux**, dont l'un est
   inexécutable par construction (`#11`/`#12`, `#13`/`#14`, `#15`/`#16` — même bougie, deux clés
   d'idempotence).
2. `_capped_mode` (`signals/generator.py:218-220`) pose `mode = min(mode_agent, max_mode)`,
   donc le signal de `@1.0.0` porte SIGNAL ; mais `SignalPipeline.process` décide d'exécuter
   d'après le mode **global** de l'agent (`execution_enabled`, `runtime/pipeline.py:122-124`),
   pas d'après le mode du signal.

Conséquence à chaque signal de la veille : le signal est marqué `sent`, `accepted`,
`order_sent`, puis bascule en `error`, un événement `order_refused` de sévérité **CRITICAL**
est écrit, et une alerte Telegram « ⚠️ Ordre en erreur » part vers l'opérateur — pour un signal
que l'agent n'avait pas à exécuter. Quatre occurrences dans `system_events` (ids 453, 455, 459,
471), une par signal SIGNAL jumeau (#3, #5, #9, #11).

**Ce n'est pas la cause de l'absence de trades** (le jumeau DEMO, lui, a bien été exécuté et a
échoué sur le commentaire), mais c'est un défaut réel : du bruit critique, un état `error` qui
n'en est pas un, et une stratégie plafonnée SIGNAL que l'agent tente de trader. Correctif :
faire porter la décision d'exécution au **mode du signal** (`execution_enabled and detail.mode
is self._mode`), et/ou n'évaluer que la stratégie assignée au marché.

---

## 8. Les limites configurées sont-elles raisonnables pour 5 496,67 € ?

Relevé pour les six signaux : **aucune** de ces limites n'a été touchée, et toutes ont été
contrôlées par le moteur (verdict `true`, valeurs ci-dessous).

| Limite (`config/agent.yaml`, profil `simulated`) | Valeur | Constaté | Avis |
|---|---|---|---|
| `risk_per_trade_pct: 0.5` | 27,48 € | 25,18 € (0,458 %) | **raisonnable** ; l'arrondi au pas de lot est conservateur |
| `daily_loss_pct: 2` | 109,93 € | 0,00 € consommé (+27,48 € engagés) | cohérent : `max_trades_per_day` (4) × 0,5 % = 2 %, soit exactement le plafond du jour. Serré mais volontaire, et le contrôle prospectif refuse le trade qui ferait dépasser |
| `weekly_loss_pct: 6` | 329,80 € | 0,00 € | sans objet ici |
| `max_drawdown_pct: 10` | 549,70 € | 0,29 € | sans objet ici |
| `max_trades_per_day: 4` | 4 | **0** | jamais atteint : la limite n'a rien bloqué |
| `cooldown_after_losses: 3` / `cooldown_hours: 4` | — | 0 perte consécutive | jamais déclenché ; raisonnable |
| `max_open_positions: 2` / `max_positions_per_market: 1` | 2 / 1 | 0 position | sans objet ici |
| `max_spread_stop_pct: 10` | 38,48 $ (10 % de 384,79 $) | **18,424 $ accepté** | voir § 4 : cette échelle-ci laisse passer un spread **deux fois plus large** que la bande d'entrée qui, elle, refuse. C'est l'incohérence à corriger |
| `margin_usage_pct: 50` | 2 748,34 € utilisables | min lot 366,96 € ; 0,07 lot = 2 568,72 € | **c'est la vraie contrainte de capacité.** Une position BTCUSD de 0,07 lot consomme 46,7 % des fonds propres (2 568,72 € sur 5 496,67 €) ; après elle, il reste ≈ 1 464 € de marge utilisable, donc une seconde position BTCUSD de même taille est impossible et une petite position or (183,93 €) reste possible |
| `max_total_exposure_pct: 200` (défaut) | 10 993,34 € | 5 138,18 € (0,07 lot) | raisonnable |
| `account_currency` EUR | — | EUR | correct |
| RM-019 (éligibilité live) | — | « not applicable in DEMO » | sans objet en démo |

Rien de déraisonnable dans ces seuils pour un compte de démonstration de 5 496,67 € : 0,5 % par
trade et 2 % par jour sont des valeurs prudentes, et le seul point à connaître est la capacité
réelle (une position bitcoin à la fois, à cause de l'effet de levier 2:1 du symbole).

---

## 9. Actions recommandées, par ordre de priorité

1. **Corriger la limite de commentaire** (§ 5.4) : sans cela, aucun ordre ne quittera jamais
   l'agent. `COMMENT_LIMIT = 29` et suppression du double hachage. Fichier :
   `src/tradingagent/execution/mt5_broker.py`, périmètre d'un autre intervenant.
2. **Trancher le réglage de `entry_zone`** (§ 6) : option A (`entry_zone_atr: 0.2`) pour
   débloquer la campagne démo, puis re-mesurer la stratégie avec la porte réellement appliquée ;
   option B si l'opérateur confirme que la bande est une tolérance en espace bid.
3. **N'exécuter que les signaux du mode de l'exécuteur** (§ 7) : supprime un événement CRITICAL
   et une alerte Telegram par signal, et évite de tenter des ordres pour une stratégie plafonnée
   SIGNAL.
4. **Rendre le journal lisible** : la nuit de l'incident n'a laissé aucune trace applicative
   (six lignes `UnicodeEncodeError` et rien d'autre). Un gestionnaire de fichier en UTF-8, ou un
   `errors="replace"` sur la console, suffirait à ce que le prochain incident soit diagnosti-
   quable sans lire la base.
5. **Documenter la divergence backtest/production** (§ 4.5) : la porte `entry_zone` n'est pas
   appliquée par `backtest/`, et le fill y est à un demi-spread alors que la production exige un
   spread entier. Toute campagne de validation future devrait le dire.

---

## 10. Annexe — commandes et sorties

Toutes les commandes sont rejouables ; les sorties complètes sont dans
`docs/research/execution-diagnostic/tools/`.

```text
uv run python docs/research/execution-diagnostic/tools/dump_incident.py      # base, SELECT seuls
uv run python docs/research/execution-diagnostic/tools/replay_decisions.py   # rejeu hors base
uv run python docs/research/execution-diagnostic/tools/probe_comment_limit.py # limite du module MT5
uv run pytest tests/risk/test_incident_20261009.py -q                         # reproduction
uv run pytest tests/risk -q                                                   # suite complète
```

```text
$ uv run pytest tests/risk -q --ignore=tests/risk/test_incident_20261009.py
122 passed in 0.36s        # la suite existante, avant l'ajout du fichier de reproduction

$ uv run pytest tests/risk -q
........................................................................ [ 50%]
......................................................................   [100%]
142 passed in 0.38s        # +20 cas dans tests/risk/test_incident_20261009.py

$ uv run pytest -q         # suite complète du dépôt
SKIPPED [1] tests\data\test_mt5_live.py:38: set RUN_MT5_LIVE=1
... (9 ignorés : MT5 live, PostgreSQL)
2506 passed, 9 skipped in 410.75s (0:06:50)

$ uv run ruff check tests/risk docs/research/execution-diagnostic
All checks passed!

$ uv run mypy
Success: no issues found in 348 source files
```

Aucun fichier de production n'a été modifié par ce diagnostic :
`git status --porcelain` montre exactement deux entrées pour cet axe —
`?? docs/research/execution-diagnostic/` et `?? tests/risk/test_incident_20261009.py`
(les autres entrées du dépôt appartiennent aux axes voisins). `src/tradingagent/risk/` est
intact — la preuve d'un bug de calcul n'existe pas, et un correctif de réglage n'appartient pas
au moteur de risque.
