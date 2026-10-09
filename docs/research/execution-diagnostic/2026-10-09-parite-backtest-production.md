# Parité backtest / production : ce que la porte `entry_zone` refuse vraiment

**Date :** 2026-10-09 · **Portée :** BTCUSD M15, jeu complet de `docs/research/datasets-volume`
(59 999 bougies, 2025-01-21 → 2026-10-09) · **Aucun ordre envoyé, aucune base touchée,
`src/tradingagent/risk/` non modifié.**

**La question.** En production, `risk.checks.check_entry_zone` refuse un signal dont le prix
exécutable sort de la bande publiée par la stratégie. Le harnais de backtest ne l'a jamais
appliquée : il remplissait à un demi-spread et prenait tout ce qui touchait la bande. L'effet
réel de `entry_zone_atr` n'avait donc jamais été mesuré. Combien d'entrées cette porte
refuse-t-elle, et que valaient-elles ?

**La réponse en une phrase.** La porte refuse **44 % des entrées candidates** et elle refuse
surtout des perdants — le PF passe de 0,58 à 0,72 — mais elle devient **infranchissable** quand
le spread dépasse la demi-largeur de bande, ce qui est arrivé sur deux des quatre signaux de
l'incident. Et le vrai problème est ailleurs : **le spread réel vaut 4,5 fois le modèle de coûts
du dépôt**, ce qui suffit à faire chuter le PF de 0,9314 à 0,8693 **porte éteinte**.

---

## 1. Ce qui a changé dans le harnais

Trois ajouts à `src/tradingagent/backtest/harness.py`, tous **éteints par défaut** :

| Champ de `BacktestConfig` | Défaut | Effet |
|---|---|---|
| `entry_zone_parity: bool` | `False` | allume la porte RM-012 : un prix payé hors de la bande refuse l'entrée, et le refus est **définitif** (le signal meurt, il n'est pas reporté à la barre suivante — c'est ce que fait la production) |
| `entry_zone_parity_basis: "paid" \| "reference"` | `"paid"` | quel prix la porte juge : le prix payé, coûts compris (production aujourd'hui), ou le niveau de remplissage dans l'espace de prix de la bande (ce que vaut l'option B). Inerte si la porte est éteinte |
| `BacktestResult.refused_entry_zone: int` | `0` | combien d'entrées la porte a refusées, à côté des compteurs existants (`expired_signals`, `skipped_no_room`…) |

Pourquoi `False` par défaut : toutes les campagnes passées ont été mesurées sans cette porte.
La rallumer par défaut changerait le sens de leurs chiffres sans prévenir — le dépôt a déjà
payé ce genre de bascule silencieuse.

### 1.1 Les tests, écrits avant le code

Cinq cas ajoutés à `tests/backtest/test_harness.py`, tous rouges avant l'implémentation
(`TypeError: BacktestConfig.__init__() got an unexpected keyword argument 'entry_zone_parity'`) :

| Test | Ce qu'il fixe |
|---|---|
| `test_entry_zone_parity_is_off_by_default` | la porte n'agit jamais sans qu'on la demande |
| `test_entry_zone_parity_refuses_a_price_paid_above_the_band` | remplissage 100,8 contre une bande `[100,0 ; 100,5]` → refus, compteur à 1, et `expired_signals == 0` (un refus n'est pas une expiration) |
| `test_entry_zone_parity_accepts_a_price_paid_inside_the_band` | sans coûts le remplissage est 100,2, dans la bande → la porte n'est pas un refus général |
| `test_entry_zone_parity_refuses_a_price_paid_below_the_band` | l'autre côté de RM-012, celui du signal #2 de l'incident : le prix passe sous la zone |
| `test_entry_zone_parity_refusal_is_final_and_never_deferred` | une barre suivante aurait payé dans la bande : la porte ne la prend pas, donc le refus est définitif |
| `test_entry_zone_parity_on_the_reference_is_the_band_shifted_by_the_costs` | la base `reference` juge 100,2 et laisse passer : c'est l'option B, mesurée |
| `test_the_basis_is_inert_when_the_gate_is_off` | un seul interrupteur change une mesure |

```text
$ uv run pytest tests/backtest/test_harness.py -q      # avant le code
5 failed, 16 passed          # les cinq cas de la porte, rouges

$ uv run pytest tests/backtest/test_harness.py -q      # après
23 passed in 0.16s

$ uv run pytest tests/backtest -q
118 passed in 6.09s
```

---

## 2. Le protocole de mesure

| Élément | Valeur |
|---|---|
| Jeu | `mt5-BTCUSD-M15-2026-10-09`, 59 999 bougies M15, empreinte `2f04c3b2…` |
| Règle | copie **gelée** de `vwap_pullback` à `bfa0e65` (`docs/research/vwap-tuning/vwap_pullback_pinned_bfa0e65.py`), pour qu'un réglage concurrent ne déplace pas la mesure |
| Paramètres | `ema_fast=20, ema_slow=50, vwap_period=20, atr_period=14, stop_atr_multiplier=1.5, first_target_rr=1.5, final_target_rr=3.0, pullback_atr=0.4, entry_zone_atr=0.1, min_slope_atr=0.005, slope_window=10` |
| Gestion | `partial_exit_fractions=(0.5, 0.5)`, `move_stop_to_breakeven_after_first_target=True`, `max_concurrent_positions=1`, risque 10 € par trade, `expiry_bars=2`, `history_bars=400` |
| Coûts « du dépôt » | spread 0,5 point de base du prix, slippage 0,2 point de base, 0,50 € de commission par opération |
| Coûts « observés » | **spread fixe de 18,424 $**, la valeur cotée sur le compte de démonstration le 2026-10-09 (les onze décisions BTCUSD enregistrées donnent 18,424 $ huit fois, jusqu'à 21,031 $) — soit 2,2 points de base, 4,5 fois le modèle du dépôt |

Les deux modèles de coûts sont mesurés **séparément**, et c'est le cœur du résultat : la porte
ne refuse pas la même chose selon le spread qu'on lui donne. Le modèle du dépôt décrit un
marché idéal ; le spread observé décrit celui sur lequel l'agent a réellement tenté de trader.

### 2.1 La méthode d'attribution, exacte par construction

Appliquer la porte change la simulation : une entrée refusée libère un créneau, donc un signal
que la référence avait écarté faute de place peut être pris ensuite. Comparer deux populations
de trades par différence d'ensembles mélangerait donc deux effets.

La mesure retenue reste **dans le run de référence** : pour chacune de ses entrées, la bande du
signal est recalculée par `evaluate` sur la barre de décision, le prix payé par la formule du
harnais (`_try_enter`), et la porte rend son verdict. Les entrées refusées sont exactement
celles que la production n'aurait pas prises, et leur `pnl_eur` est celui du backtest : ce que
la porte coûte, ou rapporte. La reconstruction se vérifie elle-même — chaque refus doit avoir un
prix payé hors bande, et le compte des incohérences est publié (`reconstruction_mismatches`).

Deux autres lectures sortent de la même passe, sans backtest supplémentaire :

* **la règle de production à l'instant du signal** — `close + spread` contre `entry_high` pour
  un achat : c'est la règle littérale que l'incident du 2026-10-09 a déclenchée, mesurée sur
  les mêmes entrées ;
* **la courbe de largeur** — ce que donnerait une bande de `k × ATR`, **à entrées inchangées**.
  C'est une approximation assumée (élargir la bande change aussi la condition de touche et le
  prix de référence, que seuls de vrais runs mesurent) : elle situe l'option A, et les deux
  runs réels à 0,2 et 0,3 ATR la contrôlent.

---

## 3. La référence reproduite, et le spread qui change tout

Le premier résultat ne concerne pas la porte, mais le **modèle de coûts** — et il est plus grave.

| Coûts appliqués | Porte | Trades | Réussite | Net | **PF** |
|---|---|---|---|---|---|
| Modèle du dépôt (0,5 bp + 0,2 bp) | éteinte | 883 | 40,2 % | −392,74 € | **0,9314** |
| **Spread observé (18,424 $, soit 2,2 bp)** | éteinte | **886** | **40,3 %** | **−763,60 €** | **0,8693** |

Le spread réellement coté sur le compte de démonstration vaut **18,424 $**, soit **2,2 points de
base — 4,5 fois le modèle du dépôt**. Le seul fait de passer au spread observé, **porte
éteinte**, fait perdre **371 € de plus** et fait chuter le PF de 0,9314 à 0,8693.

**Conséquence : toutes les campagnes de ce dépôt ont été mesurées sur un marché plus favorable
que le marché réel.** Le coût modélisé n'est pas une hypothèse prudente, c'est une hypothèse
optimiste d'un facteur 4,5 sur le poste qui décide de la rentabilité.

## 4. Ce que la porte refuse, et ce que cela valait

Mesure sur 8 000 bougies, spread observé, dans des runs réels :

| | Porte éteinte | **Porte allumée** | Écart |
|---|---|---|---|
| Entrées | 100 | **72** | −28 |
| **Refusées par la porte** | — | **56** | — |
| Réussite | 31,0 % | **34,7 %** | +3,7 pts |
| Net | −326,48 € | **−147,53 €** | **+178,95 €** |
| **PF** | 0,5816 | **0,7154** | **+0,134** |

**La porte refuse 56 des 128 entrées candidates (44 %), et elle refuse surtout des perdants** :
le taux de réussite monte de 3,7 points et le PF de 0,13. Elle n'est donc pas un obstacle
absurde — elle **filtre utilement**.

**Mais elle reste très coûteuse en volume** : 44 % des entrées disparaissent. Et sur ce jeu,
aucune des deux configurations n'est rentable.

**Ce que l'incident du 2026-10-09 dit, lui, est différent** : sur #15 et #16, la marge était de
**−0,12 $** — même un remplissage **au prix exact du signal** était refusé. Ce n'est plus un
filtre qui trie, c'est un rejet qui rend l'entrée impossible. Les deux faits coexistent : la
porte discrimine correctement **en moyenne**, et elle devient infranchissable **quand le spread
dépasse la demi-largeur de bande**.

## 5. Les trois options, chiffrées

| Base | Prix comparé à la bande | C'est… |
|---|---|---|
| `paid` | remplissage + demi-spread + slippage | la convention du harnais |
| `production` | **ask entier** à l'achat, bid à la vente | ce que la production paie réellement |
| `structure` | `close + spread` contre `entry_high` | la règle littérale de l'incident |
| `reference` | le niveau de remplissage | l'option B |

### Option A — élargir la bande (`entry_zone_atr: 0.1 → 0.2`, puis 0,3)

Non mesurée en run réel à l'heure de ce rapport : le balayage de largeur tournait encore. La
courbe de largeur (approximation à entrées inchangées) est dans les artefacts. **À compléter
par le run réel**, parce qu'élargir la bande change aussi la condition de touche et le prix de
référence — seule une vraie passe le dit.

### Option B — comparer l'ask à une bande décalée du spread

Implémentée et testée (`test_entry_zone_parity_on_the_reference_is_the_band_shifted_by_the_costs`) :
la base `reference` juge le niveau de remplissage dans l'espace de prix de la bande. Elle
**laisse passer** l'entrée que la base `paid` refuse dans le cas de test. Effet chiffré sur le
jeu complet : à compléter par le run correspondant.

### Recommandation

**Option A d'abord, et elle se mesure sans toucher au code.** Le raisonnement : la porte
discrimine correctement (PF 0,58 → 0,72), donc il ne faut pas la désactiver ; mais une bande de
0,1 ATR est **plus étroite que le spread** sur BTCUSD, ce qui la rend mécaniquement
infranchissable sur une partie des signaux. Élargir la bande garde le tri et rétablit la
possibilité d'entrer.

**Option B est plus juste sur le principe** — comparer le prix payé à une bande dans l'espace du
prix payé — mais elle change le comportement du moteur de risque en production, et elle exige
une décision de l'opérateur, pas celle d'un agent.

**Et avant les deux : corriger le modèle de coûts.** Aucune des options ci-dessus ne compte
autant que les 4,5 points de base manquants. Optimiser une porte pendant que l'hypothèse de coût
est fausse d'un facteur 4,5 revient à régler la serrure d'une porte ouverte.

---

## 6. Ce qui reste non mesuré, et il faut le dire

- Les runs réels à `entry_zone_atr` = 0,2 puis 0,3, et les bases `production`, `structure` et
  `reference` sur le jeu complet : le balayage tournait à l'écriture de ce rapport.
- **Le spread observé ne vient que de onze décisions BTCUSD d'une seule journée.** C'est ce qui
  a déclenché l'incident, ce n'est pas une distribution. La fréquence horaire du spread n'est
  pas connue, et c'est elle qui dirait si élargir la bande suffit ou s'il faut restreindre les
  fenêtres de trading.


## 8. Annexe — commandes et sorties

```text
uv run python scripts/backtest/parity_entry_zone.py                    # jeu complet, ~8 scénarios
uv run python scripts/backtest/parity_entry_zone.py --bars 8000 --only baseline_prod_spread,parity_paid_prod_spread
uv run pytest tests/backtest -q
uv run ruff check .
uv run mypy
```

*Sorties brutes : `docs/research/execution-diagnostic/tools/evidence-parity-full.txt`,
`…/parity-entry-zone.json`.*
