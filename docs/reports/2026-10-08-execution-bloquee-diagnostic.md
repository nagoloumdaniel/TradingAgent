# L'agent émet des signaux et n'exécute aucun ordre — diagnostic et correctif

**Date :** 2026-10-08 · **Portée :** chemin d'exécution démo, de `strategies` au terminal, sur
`XAUUSD` et `BTCUSD` · **Aucun seuil de risque, aucune stratégie, aucun manifeste modifié.**

**En une phrase :** l'agent a produit 10 signaux, ouvert 0 position et clôturé 0 trade ; deux
causes distinctes l'expliquent, la première est déjà corrigée par le redémarrage de 18:13:52,
la seconde est une lacune de diagnostic qui a maintenant un test et un correctif — et il reste
**une action opérateur** pour que le compte démo accepte à nouveau des ordres.

---

## 1. Ce qui est mesuré, pas supposé

Lecture directe de la base de production (`signals`, `orders`, `positions`, `trades`,
`risk_decisions`, `execution_events`, `system_events`) et des fichiers du pont EA.

| Fait | Valeur | Source |
|---|---|---|
| Signaux générés (tout l'historique) | **10** | table `signals` |
| Fenêtre couverte | 2026-10-08 01:45 → 17:30 UTC | `signals.generated_at` |
| Décisions de risque | 10 : **8 autorisées**, 2 refusées | `risk_decisions` |
| Causes de refus | `entry_zone` ×2 (BTCUSD) | `risk_decisions.checks` |
| Ordres créés | **4**, tous en état `error` | table `orders` |
| Positions ouvertes | **0** | table `positions` |
| Trades clôturés | **0** | table `trades` |
| Événements d'exécution depuis le redémarrage 18:13:52 | **0** | `execution_events` |

**Conséquence :** le plafond `max_trades_per_day: 4` n'a **jamais** été atteint, et aucun
chiffre de performance n'existe. Le projet n'a pas un problème de stratégie non rentable sur
le compte démo : il n'a **aucune opération** à mesurer.

---

## 2. Les deux causes, nommées

### Cause 1 — le mode de l'ordre ne correspondait pas au mode de l'exécuteur (corrigée)

Trois des quatre ordres portaient `mode: SIGNAL` alors que l'exécuteur tournait en `DEMO` :

```
15:30:10  BTCUSD  order_sent     mode=SIGNAL
15:30:16  BTCUSD  order_rejected OrderRefusedError('order carries mode SIGNAL,
                                 this executor runs in DEMO: refused')
```

Le refus vient de `execution/mt5_broker.py:711` (`_check_mode`). `.env` porte désormais
`TRADING_MODE=DEMO`, et le redémarrage du 2026-10-08 à 18:13:52 a rendu les deux modes
cohérents. Depuis, **aucun ordre n'a été tenté** (0 événement d'exécution en 40 minutes).

### Cause 2 — une réponse perdue sans motif conservé (corrigée dans ce dépôt)

Les quatre ordres ont fini avec le même message :

```
answer lost: reconcile by comment before any retry, never resend
```

Ce message vient de `execution/mt5_broker.py:443` (`_after_lost_answer`), atteint quand
`mt5.order_send()` a renvoyé `None` — un terminal muet. Le motif réel vivait dans
`mt5.last_error()`, et **l'adaptateur le jetait** :

```python
# av                    : l'information est perdue
raw = mt5.order_send(...)
if raw is None:
    return None
```

Aucune des quatre lignes ne permet donc de dire si le courtier a refusé l'ordre, si le
marché était fermé, si le prix avait bougé ou si l'agent n'a jamais été entendu. Sur un
compte réel, cette différence vaut de l'argent.

**Asymétrie relevée dans le même fichier :** `order_check`, deux fonctions plus haut,
conservait déjà son erreur (`mt5_terminal.py:145`). Seul `order_send` la perdait.

---

## 3. L'action opérateur qui reste : l'arrêt local persistant de l'EA

Ce n'est pas un bug, c'est un filet de sécurité qui a fonctionné, puis est resté armé.

| Élément | Valeur constatée |
|---|---|
| `control\XAUUSD_halt.txt` | `backend silencieux depuis 33 s (limite 30)`, écrit **2026-10-08 19:30:28** |
| `control\BTCUSD_halt.txt` | même motif, même horodatage |
| Rapport EA `XAUUSD_report.json` | `local_halt: true`, `kill_switch: false`, `connected: true`, `trade_allowed: true`, `counters.orders_sent: 0` |
| Pont EA | **fonctionnel** : `applied_revision` 2296, une révision appliquée toutes les ~20 s |
| Arrêt côté agent | **aucun** : dernière commande `resume` = `market:XAUUSD` le 2026-10-08 13:31 |

**Pourquoi il ne se lève pas tout seul.** Le code MQL5 le dit lui-même
(`mt5/Experts/TradingAgent/TradingAgentGuardian.mqh:1937`) : *« l'arret est persiste sur
disque. Il survit au redemarrage et ne peut etre leve que par une action de l'operateur
(suppression du fichier), jamais par un redemarrage. »* Et `LoadHalt()` n'est appelé qu'à
`OnInit` (ligne 791) : l'arrêt vit en mémoire, donc **supprimer le fichier ne suffit pas, il
faut redémarrer l'EA.**

La cause de l'arrêt est historique et déjà réparée : l'incident `clock_mismatch` de 2 h 08
documenté dans `docs/reports/2026-10-08-etat-agent-demo.md` §1.1 a laissé le backend muet
plus de 30 s. Le backend publie de nouveau normalement.

**Procédure (celle de `docs/ea/README.md:200`) :**

1. supprimer `control\XAUUSD_halt.txt` et `control\BTCUSD_halt.txt` ;
2. redémarrer l'EA sur chacun des deux graphiques (le retirer du graphique et le remettre) ;
3. vérifier `local_halt: false` dans les deux rapports.

`trade_allowed: true` prouve déjà que l'AutoTrading est actif : le rapport agrège
`MQL_TRADE_ALLOWED` **et** le compte **et** l'EA **et** le symbole
(`TradingAgentGuardian.mqh:1252`). Ce n'est donc pas le verrou.

---

## 4. Le correctif, en TDD

**Test d'abord** — `tests/data/test_mt5_terminal_orders.py`, trois cas, rouges avant le
correctif :

| Cas | Avant | Après |
|---|---|---|
| terminal muet → le motif doit être conservé | `AttributeError` (le code lisait `.retcode` sur `None`) | ✅ `TerminalError: order_send: 10004 Requote` |
| terminal muet sans code → reste une erreur | idem | ✅ |
| réponse malformée → refusée, pas `AttributeError` | ✅ reproduit : `'object' object has no attribute 'retcode'` | ✅ `TerminalError` |

**Puis le correctif** — `data/mt5_terminal.py::order_send` ne renvoie plus `None`, il lève :

```python
raw = mt5.order_send(self._order_fields(request))
if raw is None:
    code, description = self.last_error()
    raise TerminalError(f"order_send: {code} {description}".strip())
```

`TradingTerminal.order_send` (`data/terminal.py:186`) déclare désormais `TradeResult`, plus
`TradeResult | None` : le contrat n'autorise plus l'implémentation qui perd le motif. Le
simulateur de test a été aligné sur le même contrat (`SimulatedLostAnswer`), pour qu'un
double ne puisse pas valider ce que la production refuse.

**Un ordre perdu reste un ordre perdu :** la réconciliation par commentaire et l'interdiction
de renvoyer sont inchangées, et leurs tests passent toujours.

---

## 5. Vérification

| Contrôle | Résultat |
|---|---|
| `uv run pytest -q` | **2246 passed, 9 skipped** (base avant correctif : 2243 passed) |
| `uv run mypy` | **Success: no issues found in 319 source files** |
| `uv run ruff check .` | **All checks passed!** |
| `uv run ruff format --check src/tradingagent/data tests/data` | 2 files already formatted |
| Tests du chemin visé (`tests/execution`, `tests/data`) | **173 passed, 2 skipped** |

Le seul fichier non formaté du dépôt, `docs/research/datasets-h1/build_h1_h4.py`, est un
artefact préexistant d'un autre intervenant, hors de cette portée et laissé intact.

**Ce qui n'est pas encore vérifié :** qu'un ordre réel aboutisse. Cela demande l'action
opérateur du §3, puis un signal sur bougie close. Tant que ce n'est pas fait, le correctif
est prouvé par tests unitaires, pas par une exécution réelle — et cette phrase doit rester
lisible par le prochain lecteur.
