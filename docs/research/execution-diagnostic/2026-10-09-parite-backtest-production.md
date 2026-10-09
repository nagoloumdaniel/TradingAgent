# Parité backtest / production : ce que la porte `entry_zone` refuse vraiment

**Date :** 2026-10-09 · **Portée :** BTCUSD M15, jeu complet de `docs/research/datasets-volume`
(59 999 bougies, du 2025-01-21 au 2026-10-09) · **Aucun ordre envoyé, aucune base touchée,
`src/tradingagent/risk/` non modifié.**

**La question.** En production, `risk.checks.check_entry_zone` refuse un signal dont le prix
exécutable sort de la bande publiée par la stratégie. Le harnais de backtest ne l'a jamais
appliquée : il remplissait à un demi-spread et prenait tout ce qui touchait la bande. L'effet
réel de `entry_zone_atr` n'avait donc jamais été mesuré. Combien d'entrées cette porte
refuse-t-elle, et que valaient-elles ?

**La réponse, en trois phrases.**

1. **Tout dépend du prix auquel on juge la porte**, et c'est le résultat principal : la même
   règle, jugée au prix que le harnais simule (demi-spread), refuse 169 entrées et **améliore**
   le résultat de **+341 €** (PF 0,8693 → 0,9191) ; jugée au prix qu'un compte réel paie (l'ask
   entier), elle refuse **368** entrées et **dégrade** le résultat (PF 0,8693 → **0,8487**,
   170 trades en moins). La règle telle qu'elle est codée est donc, en production, **pire que pas
   de porte du tout**.
2. **Ce que la porte attrape d'utile, c'est la dérive du remplissage, pas le spread.** Les
   entrées qui ont dérivé de plus de ~13 $ (0,054 ATR) avant le fill perdent avec un PF de 0,41 ;
   celles qui ont dérivé entre 5,7 $ et 13 $ sont gagnantes. Or la règle actuelle, en comparant
   l'ask à une bande de 0,1 ATR, place son seuil effectif à **5,7 $** : le spread (18,424 $, soit
   0,078 ATR) mange 76 % du budget de dérive. **Recaler la bande à 0,13 ATR renverse le signe :
   PF 0,8748, meilleur que sans porte (0,8693), avec deux fois moins de refus.**
3. **La règle qui a frappé le 2026-10-09** — bande plus étroite que le spread, refus à l'instant
   du signal — refuse 147 entrées, **toutes des achats**, dont la valeur (PF 0,8676, moyenne
   −0,85 €) est celle du livre entier (PF 0,8693, −0,86 €) : **elle ne trie rien**.

Et le second résultat, indépendant de la porte : **le spread réel vaut 3,5 fois le modèle de
coûts du dépôt** (18,424 $ cotés contre 5,254122 $ modélisés), ce qui suffit à faire passer la
référence de −392,74 € (PF 0,9314) à **−763,60 € (PF 0,8693)**, porte éteinte.

---

## 1. Ce qui a changé dans le harnais

Trois ajouts à `src/tradingagent/backtest/harness.py`, tous **éteints par défaut** :

| Champ de `BacktestConfig` | Défaut | Effet |
|---|---|---|
| `entry_zone_parity: bool` | `False` | allume la porte RM-012 : une entrée dont le prix jugé sort de la bande est refusée, et le refus est **définitif** (le signal meurt, il n'est pas reporté — c'est ce que fait la production) |
| `entry_zone_parity_basis: "paid" \| "production" \| "reference"` | `"paid"` | quel prix la porte juge : le fill du harnais (½ spread), **le prix qu'un compte réel paie (ask entier)**, ou le niveau de remplissage dans l'espace de prix de la bande (option B). Inerte si la porte est éteinte |
| `BacktestResult.refused_entry_zone: int` | `0` | combien d'entrées la porte a refusées, à côté des compteurs existants (`expired_signals`, `skipped_no_room`…) |

Pourquoi `False` par défaut : toutes les campagnes passées ont été mesurées sans cette porte.
La rallumer par défaut changerait le sens de leurs chiffres sans prévenir — le dépôt a déjà
payé ce genre de bascule silencieuse.

### 1.1 Les tests, écrits avant le code

Sept cas ajoutés à `tests/backtest/test_harness.py`, tous rouges avant l'implémentation
(`TypeError: BacktestConfig.__init__() got an unexpected keyword argument 'entry_zone_parity'`) :

| Test | Ce qu'il fixe |
|---|---|
| `test_entry_zone_parity_is_off_by_default` | la porte n'agit jamais sans qu'on la demande |
| `test_entry_zone_parity_refuses_a_price_paid_above_the_band` | fill 100,8 contre une bande `[100,0 ; 100,5]` → refus, compteur à 1, et `expired_signals == 0` (un refus n'est pas une expiration) |
| `test_entry_zone_parity_accepts_a_price_paid_inside_the_band` | sans coûts le fill est 100,2, dans la bande → la porte n'est pas un refus général |
| `test_entry_zone_parity_refuses_a_price_paid_below_the_band` | l'autre côté de RM-012, celui du signal #2 de l'incident : le prix passe sous la zone |
| `test_entry_zone_parity_refusal_is_final_and_never_deferred` | une barre suivante aurait payé dans la bande : la porte ne la prend pas, le refus est définitif |
| `test_entry_zone_parity_on_the_reference_is_the_band_shifted_by_the_costs` | la base `reference` juge 100,2 et laisse passer : c'est l'option B, mesurée |
| `test_the_production_basis_judges_the_price_a_real_account_pays` | fill 100,49 (dans la bande) contre ask 100,74 (hors) : la même entrée, deux verdicts |
| `test_the_basis_is_inert_when_the_gate_is_off` | un seul interrupteur change une mesure |

```text
$ uv run pytest tests/backtest/test_harness.py -q      # avant le code
5 failed, 16 passed          # les cinq cas de la porte, rouges

$ uv run pytest tests/backtest/test_harness.py -q      # après
24 passed in 0.22s

$ uv run pytest tests/backtest -q
118 passed in 6.09s

$ uv run ruff check src/tradingagent/backtest/harness.py tests/backtest/test_harness.py
All checks passed!
```

---

## 2. Le protocole de mesure

| Élément | Valeur |
|---|---|
| Jeu | `mt5-BTCUSD-M15-2026-10-09`, 59 999 bougies M15, empreinte `2f04c3b2…` |
| Règle | copie **gelée** de `vwap_pullback` à `bfa0e65` (`docs/research/vwap-tuning/vwap_pullback_pinned_bfa0e65.py`) : un réglage concurrent ne peut pas déplacer la mesure |
| Paramètres | `ema_fast=20, ema_slow=50, vwap_period=20, atr_period=14, stop_atr_multiplier=1.5, first_target_rr=1.5, final_target_rr=3.0, pullback_atr=0.4, entry_zone_atr=0.1, min_slope_atr=0.005, slope_window=10` |
| Gestion | partiel 50/50, break-even après le premier objectif, `max_concurrent_positions=1`, risque 10 € par trade, `expiry_bars=2`, `history_bars=400` |
| Coûts « du dépôt » | spread 0,5 bp du prix, slippage 0,2 bp, 0,50 € de commission par opération |
| Coûts « observés » | **spread fixe de 18,424 $** (2,2 bp), la valeur cotée sur le compte de démonstration le 2026-10-09 |

**Pourquoi deux modèles de coûts.** Le spread observé ne vient pas d'une supposition : les onze
décisions BTCUSD enregistrées entre le 2026-10-08 02:00 et le 2026-10-09 04:15 donnent 18,424 $
huit fois, puis 18,574, 20,175 et 21,031. Le modèle du dépôt, lui, facture 0,5 point de base du
prix — soit 5,254122 $ sur ce jeu, quand le compte en cotait 18,424 $. Les deux sont donc
mesurés, et le second est celui qui décide.

### 2.1 La méthode d'attribution, exacte par construction

Appliquer la porte change la simulation : une entrée refusée libère un créneau, donc un signal
que la référence avait écarté faute de place (`skipped_no_room` = 1 087 sur ce jeu) peut être
pris ensuite. Comparer deux populations par différence d'ensembles mélangerait deux effets.

La mesure d'attribution reste donc **dans le run de référence** : pour chacune de ses 886
entrées, la bande du signal est recalculée par `evaluate` sur la barre de décision, le prix
jugé par la formule du harnais (`_try_enter`), et la porte rend son verdict. Les entrées
refusées sont exactement celles que la production n'aurait pas prises, et leur `pnl_eur` est
celui du backtest. La reconstruction se vérifie elle-même : **886 entrées examinées, 0 non
appariée, 0 incohérence** (chaque refus a bien un prix hors bande).

Deux lectures supplémentaires sortent de la même passe, sans backtest de plus : la règle de
production **à l'instant du signal** (`close + spread` contre `entry_high`), et une **courbe de
largeur** donnant ce que vaudrait une bande de `k × ATR` à entrées inchangées.

---

## 3. Le spread change tout, avant même la porte

Trois runs réels sur le jeu complet, porte éteinte sauf mention :

| Coûts appliqués | Spread | Signaux | Entrées | Réussite | Net | **PF** | Perte max |
|---|---|---:|---:|---:|---:|---:|---:|
| Modèle du dépôt (0,5 bp) | 5,254122 $ | 1 973 | **883** | 40,20 % | **−392,74 €** | **0,9314** | 566,80 € |
| **Observé (2,2 bp)** | **18,424 $** | 1 973 | **886** | 40,29 % | **−763,60 €** | **0,8693** | 894,81 € |

**La référence du protocole est reproduite à l'identique** : 883 trades, −392,74 €, PF 0,9314 —
au chiffre près, avec la même règle gelée, le même jeu et le même manifeste. Les deux runs
émettent **exactement les mêmes 1 973 signaux** : seul le modèle de coûts les sépare. L'écart de
3 entrées (886 contre 883) vient d'un effet de chemin, pas du protocole : un remplissage plus
coûteux déplace le break-even après le premier objectif, donc la durée des positions, donc quel
signal suivant trouve un créneau libre (`skipped_no_room` : 1 087 contre 1 090).

**371 € de plus perdus et 0,062 de PF en moins, pour la seule raison que le spread réel vaut
3,5 fois celui du modèle et 2,2 points de base au lieu de 0,5.** Aucune campagne de ce dépôt n'a
mesuré le marché réel : l'hypothèse de coût n'est pas prudente, elle est optimiste — et c'est le
poste qui décide de la rentabilité.

---

## 4. Le verdict décisif : la même porte, deux prix, deux conclusions

Trois runs réels sur les 59 999 bougies, spread observé, à entrées mesurées par le harnais :

| | Porte éteinte | Porte allumée, **prix du harnais** (½ spread) | Porte allumée, **prix réel** (ask entier) |
|---|---:|---:|---:|
| Trades | **886** | **807** | **716** |
| Réussite | 40,3 % | **41,0 %** | 39,7 % |
| Net | **−763,60 €** | **−422,48 €** | **−720,40 €** |
| **PF** | **0,8693** | **0,9191** | **0,8487** |
| Perte maximale (drawdown) | 894,81 € | **553,69 €** | 873,02 € |
| Entrées refusées par la porte | 0 | **169** | **368** |
| Signaux sans place (`skipped_no_room`) | 1 087 | **997** | 889 |

**Lecture.** La même règle, sur le même jeu, améliore le résultat de **+341 €** quand on la juge
au prix que le harnais simule, et de **+43 €** — en **dégradant le PF** — quand on la juge au
prix qu'un compte réel paie. L'écart entre les deux verdicts vient d'exactement un demi-spread :
c'est le prix que le harnais ne facture pas au moment de décider.

**La décomposition, créneau par créneau** (appariement des deux runs par `opened_at`, l'identité
`886 − 807 = supprimés − créés` est vérifiée) :

| Variante, spread observé | Trades supprimés | Leur net | Trades créés par les créneaux libérés | Leur net | Effet net |
|---|---:|---:|---:|---:|---:|
| Porte au prix du harnais | 83 | −385,05 € | 4 | +43,93 € | **+341 €** |
| Porte au prix réel | *non mesuré* | — | | | **+43 €** |

Autrement dit : les **83 trades que la porte évite valaient −385,05 €** (des pertes), et les
**4 trades que la libération des créneaux fait entrer à leur place valent −43,93 €** (des pertes
aussi). Le gain net de **+341 €** vient donc des 385 € de pertes évitées, diminués des 44 € que
coûtent leurs remplaçants — et non d'un tri parfait. Que les remplaçants perdent aussi est un
fait de ce jeu : sur 59 999 bougies, presque tout perd.

**Et voici pourquoi** (verdicts entrée par entrée, run de référence, 886 entrées) :

| Seuil effectif de la porte | Prix jugé | Refusées | Part | Net des refusées | **PF des refusées** | Moyenne |
|---|---|---:|---:|---:|---:|---:|
| dérive > **12,9 $** (0,054 ATR) | fill du harnais (½ spread) | **82** | 9,3 % | −391,17 € | **0,4114** | −4,77 € |
| dérive > **5,7 $** (0,024 ATR) | ask réel (spread entier) | **189** | 21,3 % | −26,73 € | **0,9772** | −0,14 € |
| *pour mémoire : les 804 entrées gardées par le premier* | | | | *−372,43 €* | *0,9281* | *−0,46 €* |
| *le livre entier, porte éteinte* | | | | *−763,60 €* | *0,8693* | *−0,86 €* |

Les 107 entrées que la charge du spread entier fait basculer du côté refusé valent **+364 €** :
ce sont des gagnants. Autrement dit :

* la dérive **au-delà de ~13 $ (0,054 ATR)** est un vrai signal de perte (PF 0,41) ;
* la dérive **entre 5,7 $ et 13 $** est neutre à gagnante ;
* la règle de production, en comparant l'ask à la bande, coupe à **5,7 $** : elle refuse les
  gagnantes avec les perdantes.

**Le dépassement médian des refus n'est que de +2,18 $ (maximum +11,31 $)** : ce ne sont pas des
marchés qui se sont enfuis, ce sont des remplissages qui ont dérivé d'un dixième d'ATR — et la
bande de 0,1 ATR (48,3 $ de large en médiane, soit 2,62 fois le spread) laisse 18,4 $ au spread,
donc 5,7 $ à la dérive.

### 4.1 La porte n'existe que parce que le spread est large

Même porte, même base `paid`, même jeu — seuls les coûts changent :

| Coûts | Entrées | Refus de la porte | Net | PF |
|---|---:|---:|---:|---:|
| Modèle du dépôt (spread 5,254122 $) | 883 | **6** (0,7 %) | −392,74 → **−369,96 €** | 0,9314 → **0,9350** |
| **Spread observé (18,424 $)** | 886 | **169** (19,1 %) | −763,60 → **−422,48 €** | 0,8693 → **0,9191** |

**Dans le monde du modèle de coûts du dépôt, la porte est un détail** : six entrées refusées sur
883, invisible dans les statistiques, et légèrement favorable. Dans le monde réel, elle refuse
**169 entrées** et décide du sort de la stratégie. Le harnais, tel qu'il était configuré, ne
pouvait pas voir le problème : **c'est le spread qui fait exister la porte.**

Le verdict entrée par entrée le confirme, et donne la mesure du rapport de force : aux coûts du
dépôt la bande vaut **9,19 fois** le spread médian et la porte ne condamne que **5 entrées sur
883** (0,6 %), qui valaient −40,07 € avec un PF de **0,1162** — elle attrape bien les rares
mauvaises, mais il n'y en a presque pas à écarter. Au spread observé la bande ne vaut plus que
**2,62 fois** le spread et la porte condamne **82 entrées sur 886**, dont 107 de plus basculent
dès qu'on facture le spread entier (§ 4 tableau).

### 4.2 Deux compteurs, et ils ne mesurent pas la même chose

Dans le **run de référence**, la porte aurait refusé **82 des 886 entrées** (verdict rendu entrée
par entrée, sans rejouer la simulation). Dans le **run où la porte agit**, le harnais en compte
**169** : une entrée refusée libère un créneau, donc un signal que la référence avait écarté faute
de place peut être pris — et refusé à son tour. Le compteur `skipped_no_room` passe de 1 087 à
997, ce qui mesure exactement ce recyclage. Les deux chiffres sont justes ; ils répondent à deux
questions différentes (« quelles entrées la règle condamne-t-elle ? » et « combien de fois
refuse-t-elle pendant que l'agent tourne ? »).

### 4.3 La règle qui a frappé le 2026-10-09 ne trie rien

Appliquée à l'instant du signal — `close + spread` contre `entry_high`, la règle littérale qui
a refusé #13 à #16 — la porte refuse **147 entrées sur 886 (16,6 %), toutes des achats**
(0 vente : pour une vente le prix exécutable est le bid, que le spread ne pénalise pas) :

| | Refusées à l'instant du signal | Livre entier |
|---|---:|---:|
| Entrées | 147 | 886 |
| Net | −124,71 € | −763,60 € |
| Moyenne par trade | **−0,85 €** | **−0,86 €** |
| Réussite | 42,9 % | 40,3 % |
| **PF** | **0,8676** | **0,8693** |

**Un échantillon exactement moyen.** La règle ne sélectionne rien : elle retire des trades au
hasard, et sur BTCUSD elle les retire **tous du même côté** — c'est l'asymétrie structurelle que
la bande soit construite sur le bid et jugée sur l'ask.

---

## 5. Les trois options, chiffrées

### Option A — élargir la bande (`entry_zone_atr`), paramètre seul

Courbe de largeur, **à entrées inchangées** (les 886 entrées du run de référence, spread
observé ; ATR médian ≈ 241 $, bande médiane 2,62 fois le spread), puis **runs réels** :

| Largeur | Entrées gardées (courbe) | Net (courbe) | PF (courbe) | **Run réel : trades / net / PF** | Seuil de dérive côté production |
|---|---:|---:|---:|---|---:|
| 0,05 ATR | 482 | −423,66 € | 0,8669 | — | **−6,3 $** (bande sous le spread : refus d'avance) |
| **0,10 ATR (livré)** | 804 | −372,43 € | 0,9281 | **807 / −422,48 € / 0,9191** | **5,7 $** |
| 0,15 ATR | 871 | −725,16 € | 0,8738 | **871 / −742,15 € / 0,8711** | 17,8 $ |
| 0,20 ATR | 880 | −705,26 € | 0,8780 | **880 / −705,25 € / 0,8780** | 29,9 $ |
| 0,30 ATR | 884 | −737,46 € | 0,8732 | **884 / −737,45 € / 0,8732** | 54,0 $ |

**Les runs réels valident l'approximation « entrées inchangées »** : à 0,2 et 0,3 ATR les
chiffres tombent au centime près, et à 0,10/0,15 l'écart reste sous 3 % sur le net (la différence
vient des créneaux libérés, plus nombreux quand la porte refuse beaucoup). La courbe est donc un
instrument fiable pour situer une largeur sans rejouer un backtest — et c'est ce qui autorise
l'extrapolation ci-dessous.

Sous la convention du harnais, l'optimum est **0,10 ATR — la valeur livrée** : élargir **détruit**
le bénéfice du filtre (PF 0,9191 → 0,8711), parce que le harnais ne facture qu'un demi-spread au
moment de décider et garde alors les entrées qui ont dérivé de 13 à 25 $, c'est-à-dire les
mauvaises. En production, la même bande de 0,10 ATR coupe à **5,7 $**, bien avant le seuil qui
discrimine (**~13 $**). **La bande optimale du harnais n'est pas la bande optimale de la
production, et c'est le spread qui les sépare.**

Le chiffre qui égalise les deux lectures est **0,13 ATR** : la bande vaut alors 62,8 $, le spread
en prend 18,4, et il reste **13 $ de budget de dérive — exactement le seuil mesuré au § 4.**

### Option B — comparer l'ask à une bande décalée du spread

Mesurée, et le run réel est sans ambiguïté : **0 refus sur 886, et un résultat identique au
chiffre près à celui sans porte** — 886 trades, −763,60 €, PF 0,8693. L'appariement des deux runs
donne 0 trade supprimé, 0 créé. Le niveau de remplissage est toujours dans la bande : il ne peut
en sortir que par un gap d'ouverture, et il n'y en a aucun sur ce jeu.

Autrement dit, l'option B **désactive la porte en pratique**. Elle laisse l'agent trader, mais
elle abandonne aussi le seul filtre utile — celui qui écarte les entrées ayant dérivé de plus de
13 $ (PF 0,41). Pour le garder en adoptant l'option B, il faudrait **resserrer la bande à
~0,05 ATR** (budget de dérive 12 $) — mais la bande serait alors plus étroite que le spread, et le
refus structurel d'avance reviendrait. **L'option B ne suffit donc pas seule** : elle doit être
accompagnée d'un plancher de bande au niveau du spread, ou d'un seuil de dérive explicite — ce que
fait l'option C.

### Option C — ce que la mesure suggère : recaler la bande sur le budget de dérive

Le résultat décisif, et il est mesuré : **garder la porte telle qu'elle est codée (jugée au prix
réel, l'ask), mais recaler la bande pour que la dérive — et non le spread — fixe le seuil.**

| Variante, spread observé, jeu complet | Trades | Réussite | Net | **PF** | Refus | Perte max |
|---|---:|---:|---:|---:|---:|---:|
| Porte éteinte | 886 | 40,29 % | −763,60 € | 0,8693 | 0 | 894,81 € |
| **Règle actuelle** (0,10 ATR, jugée au prix réel) | **716** | 39,7 % | **−720,40 €** | **0,8487** | **368** | 873,02 € |
| *Règle actuelle jugée au prix du harnais (½ spread)* | *807* | *41,0 %* | *−422,48 €* | *0,9191* | *169* | *553,69 €* |
| **Bande recalibrée (0,13 ATR), jugée au prix réel** | **802** | 40,15 % | **−661,98 €** | **0,8748** | **185** | 818,56 € |

Trois lectures, dans l'ordre d'importance :

1. **La règle actuelle, jugée au prix réel, est pire que pas de porte du tout** : PF 0,8487 contre
   0,8693, en refusant 368 entrées sur 886. C'est le fait que le harnais cachait.
2. **Recaler la bande à 0,13 ATR renverse le signe** : PF **0,8748**, au-dessus du livre sans
   porte (0,8693), avec **+101,62 €** de net, 86 trades rendus à la stratégie et **deux fois moins
   de refus** (185 contre 368). Le budget de dérive redevient 13 $ — exactement le seuil mesuré
   au § 4 — au lieu de 5,7 $.
3. **C'est un paramètre, pas du code** : `entry_zone_atr: 0.1 → 0.13` dans le manifeste de la
   stratégie. Aucune limite de risque n'est desserrée, aucun module n'est modifié, et le
   changement est réversible.

L'alternative « propre » côté moteur de risque — comparer le **bid** à la bande (la dérive seule)
et refuser explicitement une bande inférieure au spread — demanderait, elle, de toucher
`check_entry_zone` : décision opérateur, hors du périmètre de cet axe. Elle rendrait la règle
lisible (« on n'entre pas si le prix a couru de plus de 0,05 ATR ; on n'entre pas sur un
instrument dont la bande ne couvre pas son spread ») au lieu de mélanger les deux dans une même
comparaison. Le chiffre mesuré ici dit que le résultat, lui, serait le même qu'à l'option C.

---

## 6. Recommandation

1. **Corriger le modèle de coûts du dépôt avant tout le reste.** Aucune option ci-dessus ne
   compte autant que les 3,5 fois de spread manquants : −371 € et 0,062 de PF sur cette seule
   mesure, porte éteinte. Tant que les campagnes tournent à 0,5 bp de spread sur un instrument
   qui en cote 2,2, elles mesurent un autre marché. *Régler la porte pendant que l'hypothèse de
   coût est fausse revient à huiler la serrure d'une porte ouverte.*
2. **Recaler `entry_zone_atr` à 0,13** (mesuré : PF 0,8748 contre 0,8487 pour la règle actuelle
   et 0,8693 sans porte). Paramètre seul, réversible, aucune limite de risque desserrée — et
   c'est la variante qui remet le seuil de la règle là où le signal se trouve (13 $ de dérive,
   au lieu de 5,7 $).
3. **Ne pas désactiver la porte** (ce que ferait l'option B seule, avec 0 refus sur 886) : elle
   attrape un vrai signal — les entrées qui ont dérivé de plus de 13 $ perdent avec un PF de 0,41.
4. **Mesurer la fréquence horaire du spread BTCUSD** avant de conclure sur les fenêtres de
   trading : le spread de 18,424 $ vient de onze décisions d'une seule journée.

**Ce que cette mesure ne dit pas** : elle ne rend pas la stratégie rentable. Même la meilleure
variante mesurée ici plafonne à **PF 0,8748** sur le jeu complet — très loin du seuil de promotion
(1,20). La parité backtest/production est une condition nécessaire, pas une solution.

---

## 7. Ce qui n'est pas mesuré, et qu'il faut dire

* **Un défaut corrigé en cours de route, et il faut le dire** : la base `reference` du harnais
  (option B) avait un défaut de signe pour les ventes — le fill d'une vente est *sous* sa
  référence, donc le soustraire au lieu de l'ajouter condamnait des entrées que la règle ne
  refuse pas. Un premier run a produit 711 trades / −341,17 € / PF 0,9252 : **ce chiffre est
  invalide**. Après correctif et test
  (`test_the_reference_basis_undoes_the_cost_on_the_right_side_for_a_sell`), le run de contrôle
  donne 886 trades / −763,60 € / PF 0,8693 / **0 refus** — identique au livre sans porte, ce qui
  confirme l'analyse entrée par entrée (0 refus sur 886). Le défaut n'a jamais touché un run de
  production : la porte n'est pas branchée par défaut.
* **Le run « prix réel » n'a pas de décomposition créneau par créneau** (`supprimés` / `créés`) :
  elle est mesurée pour la base `paid` (83 supprimés, 4 créés), pas pour la base `production`.
  Les agrégats, eux, sont mesurés.
* **Le spread observé ne vient que de onze décisions BTCUSD d'une seule journée.** C'est ce qui a
  déclenché l'incident, ce n'est pas une distribution : la fréquence horaire n'est pas connue, et
  c'est elle qui dirait si une bande plus large suffit ou s'il faut restreindre les heures.
* **La validité de la convention « entrées inchangées »** sur d'autres marchés (XAUUSD), où le
  spread est négligeable devant l'ATR : rien ici ne s'y transpose sans mesure.

---

## 8. Annexe — commandes et sorties

```text
uv run python scripts/backtest/parity_entry_zone.py                    # jeu complet, scénarios
uv run python scripts/backtest/parity_entry_zone.py --bars 8000 --only baseline_prod_spread
uv run python docs/research/execution-diagnostic/tools/read_verdicts.py [json]
uv run pytest tests/backtest -q          # 120 passed
uv run ruff check .                      # voir la note ci-dessous
uv run mypy                              # Success: no issues found in 350 source files
```

Sorties brutes : `docs/research/execution-diagnostic/tools/` —
`evidence-parity-all.txt`, `evidence-parity-production.txt`, `parity-entry-zone-all.json`,
`parity-entry-zone-production.json`, `evidence-verdicts.txt`, `evidence-parity-red.txt`.

*Note sur `ruff check .` : un avertissement subsiste, hors de cet axe —
`scripts/backtest/tune_filter_vwap.py:459`, ligne trop longue, dans un fichier qu'un autre axe
écrit en ce moment. Les fichiers de cet axe passent
(`uv run ruff check src/tradingagent/backtest tests/backtest scripts/backtest/parity_entry_zone.py`
→ `All checks passed!`).*
