# Le filtre « cassures seulement » : réfuté par le walk-forward

**Date :** 2026-10-09 · **Famille :** `breakout_only` · **Jeux :** `docs/research/datasets-long`
(BTCUSD et XAUUSD, 60 000 bougies M15 chacun) · **Aucune promotion, aucun seuil touché.**

**En une phrase :** le filtre qui semblait transformer deux stratégies perdantes en stratégies
gagnantes **ne survit pas au walk-forward** — 14 candidats sur 16 rejetés pour surapprentissage,
1 seul pli rentable sur 6 en médiane — et c'est **exactement ce que la porte existait pour
attraper**.

---

## 1. Ce qui a été construit, et pourquoi

La mesure du 2026-10-08 (`2026-10-08-recommandations-consolidees.md`) avait montré que sur les
deux marchés et les deux stratégies de production, les entrées prises en range perdent tout
l'argent et celles prises sur une cassure gagnent : PF **1,652** et **1,656** sur l'or,
**1,719** et **1,658** sur le BTC, contre un seuil de 1,20.

Ce découpage avait été fait **après** avoir vu les résultats, sur les données qui les avaient
produits. Il était donc une **hypothèse**, et le rapport le disait déjà. Pour la traiter comme
telle, deux pièces ont été écrites :

| Pièce | Rôle |
|---|---|
| `indicators/regime.is_breakout` | le verdict de cassure, nommé pour son seul usage : autoriser une entrée |
| `research/discovery.BreakoutOnly` | une garde qui **délègue** à la règle parente et refuse le range |

`BreakoutOnly` ne réécrit jamais le signal du parent : il refuse de le laisser passer. Une garde
qui modifierait le signal serait une autre règle, et il faudrait la mesurer comme telle.

## 2. La mesure

`uv run python scripts/backtest/discover_m1_scalp.py --families breakout_only --datasets docs/research/datasets-long`

16 candidats (8 par marché), chacun passé aux échelons du protocole : apprentissage,
validation, walk-forward à 6 plis, robustesse des paramètres.

| Marché | Cause de rejet | Candidats |
|---|---|---|
| BTCUSD | `overfitting` | 6 |
| BTCUSD | `parameter_dispersion` | 2 |
| XAUUSD | `overfitting` | 8 |

**Détail des 16 candidats :**

| Marché | Label | Cause | Plis rentables /6 | Ops (train) | Net train | Net validation |
|---|---|---|---|---|---|---|
| BTCUSD | 00 | overfitting | 1 | 173 | −149,97 € | −179,96 € |
| BTCUSD | 01 | overfitting | 1 | 169 | −141,87 € | −184,13 € |
| BTCUSD | 02 | overfitting | **0** | 52 | −18,62 € | +29,47 € |
| BTCUSD | 03 | overfitting | **0** | 52 | −42,61 € | −29,67 € |
| BTCUSD | 04 | dispersion | **3** | 179 | **+307,29 €** | −23,25 € |
| BTCUSD | 05 | dispersion | **3** | 177 | −17,26 € | −131,91 € |
| BTCUSD | 06 | overfitting | 1 | 78 | −112,81 € | +65,11 € |
| BTCUSD | 07 | overfitting | 1 | 77 | −125,17 € | +44,17 € |
| XAUUSD | 00 | overfitting | 1 | 173 | −464,81 € | −182,63 € |
| XAUUSD | 01 | overfitting | 1 | 172 | −339,70 € | −143,01 € |
| XAUUSD | 02 | overfitting | 1 | 74 | −182,62 € | −83,72 € |
| XAUUSD | 03 | overfitting | 1 | 74 | −109,38 € | −88,82 € |
| XAUUSD | 04 | overfitting | 1 | 172 | −144,10 € | −211,47 € |
| XAUUSD | 05 | overfitting | 1 | 169 | −42,42 € | −151,40 € |
| XAUUSD | 06 | overfitting | **2** | 75 | −89,85 € | −89,68 € |
| XAUUSD | 07 | overfitting | **0** | 73 | −126,42 € | −119,60 € |

**Les chiffres qui décident :**

- **1 candidat sur 16** est rentable en apprentissage, **3 sur 16** en validation — mais aucun
  des deux groupes ne se recoupe de façon exploitable ;
- **plis rentables : médiane 1 sur 6**, exigence 3 sur 6 (50 %) ;
- **12 candidats sur 16** sont rejetés pour rétention hors échantillon **0,00**, et deux pour
  dispersion des paramètres (0,72 et 0,96 contre un plafond de 0,50) ;
- **le jeu scellé n'a jamais été ouvert** : 0 ouverture sur les deux marchés. Les 12 000
  bougies scellées de chaque marché restent intactes, donc réutilisables.

## 3. Ce que ça veut dire, et pourquoi c'est une bonne nouvelle

**L'hypothèse est morte, et elle est morte correctement.** Le PF de 1,65 à 1,72 mesuré sur le
jeu complet était un **artefact** : il venait de regarder les résultats et de choisir le
découpage après coup. Le walk-forward, qui réapprend sur chaque pli et juge sur le pli suivant,
ne retrouve rien.

**C'est la démonstration que la porte fonctionne.** Elle a attrapé exactement ce qu'un backtest
naïf aurait présenté comme une découverte. Sans elle, ce filtre aurait pu être promu, et le
dépôt aurait ajouté une règle qui perd en réel en croyant avoir trouvé un edge.

**Et c'est la deuxième fois que le laboratoire rend un zéro, avec deux causes différentes.**
La campagne du 2026-10-08 rejetait 34 candidats sur 34 ; celle-ci en rejette 16 sur 16. Le
laboratoire ne trouve pas « rien » par incapacité : il refuse, et il nomme pourquoi.

## 4. Ce que ça n'autorise pas à conclure

**Le concept de filtre de régime n'est pas réfuté.** Ce qui est réfuté, c'est **ce** filtre,
avec **ces** paramètres et **ce** protocole :

- le canal de cassure est adossé à `ema_slow` (30 ou 60), un choix arbitraire qui n'a pas été
  exploré. Un canal plus court ou plus long n'a pas été testé ;
- le walk-forward à 6 plis de 350/250 barres ne couvre que **3,3 %** de la fenêtre roulante
  (constat déjà fait le 2026-10-08, non résolu). Les 14 rejets « surapprentissage » reposent
  donc sur les semaines les plus anciennes du jeu, pas sur les deux ans et demi ;
- **la structure au moment de l'entrée reste un fait mesuré**, et son croisement avec le
  résultat reste valide comme **description** : les entrées en range perdent, celles en
  cassure gagnent, sur 977 et 1 812 opérations. Ce qui ne tient pas, c'est d'en faire une
  **règle** qui prédit le pli suivant.

Autrement dit : le fait est solide, la règle ne l'est pas. La distinction compte, et c'est
elle qu'il faut retenir.

## 5. Reprise après correction du protocole (2026-10-09)

Les 16 candidats ont été rejoués avec le plan walk-forward corrigé (TASK-104) : **46 plis au
lieu de 6**, couvrant **97,9 % de la fenêtre roulante au lieu de 3,0 %**. Le verdict final ne
change pas — 16 rejetés, 14 pour surapprentissage, 2 pour dispersion — mais **son fondement
change du tout au tout**, et c'est ce qu'il faut lire.

| | Avant (plafond 6) | Après (46 plis) |
|---|---|---|
| Plis joués | 6 | **46** |
| Couverture de la série roulante | **3,0 %** | **97,9 %** |
| Plis rentables, **médiane** | **1 / 6 (16,7 %)** | **18 / 46 (39,1 %)** |
| Plis rentables, amplitude | 0 à 3 | **11 à 24** |
| Plis ignorés (bloc trop court) | 0 | 0 |
| Candidats rentables en apprentissage | 1 / 16 | 1 / 16 |
| Ouvertures du scellé | 0 | 0 |

**Ce que la correction révèle, et qui n'était pas visible :**

1. **Le filtre n'échoue pas « partout » — il échoue de justesse.** Médiane passée de **17 % à
   39 %** de plis rentables, pour une exigence de **50 %** (23 plis sur 46). L'écart n'est plus
   « le filtre ne marche pas », c'est « il manque une dizaine de plis sur 46 ». Conclusion
   **plus utile et plus fragile à la fois** : un candidat à 39 % n'est pas à jeter comme un
   candidat à 17 %, il est à retravailler.

2. **La variante 04 du BTC est le meilleur candidat jamais mesuré par ce dépôt** :
   **24 plis rentables sur 46 (52,2 %)**, au-dessus de l'exigence, avec +307,29 € en
   apprentissage. Elle tombe malgré tout, sur la **rétention hors échantillon (−0,08)** et sur
   la **dispersion des paramètres**. La porte qui la refuse n'est donc plus le walk-forward,
   c'est la robustesse — deux diagnostics qui appellent deux remèdes différents.

3. **Deux candidats franchissent l'exigence de plis sans être retenus** (04 et 05 du BTC, 24 et
   23 plis rentables) : le walk-forward n'est pas la barrière qui les arrête, c'est la
   dispersion pour l'un et la rétention pour l'autre. **La hiérarchie des portes compte autant
   que leur verdict.**

4. **L'ancien rapport était donc trop sévère.** « 1 pli rentable sur 6 » laissait croire à un
   filtre sans mérite. En vérité il approche du seuil sur 46 plis, et sa variante 04 le
   dépasse. **L'artefact de protocole ne créait pas un faux positif : il créait un faux
   négatif**, ce qui est tout aussi coûteux — on jetait une piste qui méritait d'être
   retravaillée.

**Ce qui ne change pas, et qui reste la conclusion :** aucun candidat n'est retenu, le jeu
scellé n'a **jamais** été ouvert (0 sur les deux marchés), et rien n'est promu. Le filtre
« cassures seulement » **n'est pas validé**. Il est simplement **moins réfuté qu'on ne le
croyait**, et la piste à rouvrir est maintenant précise : la variante 04 du BTC et sa
dispersion, plutôt que le filtre en général.

---

## 6. Correction d'une conclusion trop rapide : la variante 04 n'est pas la piste

Le paragraphe 5 présentait la variante 04 du BTC (24 plis rentables sur 46) comme « le meilleur
candidat jamais mesuré par ce dépôt », en notant qu'elle tombait sur la dispersion. **C'était
une lecture incomplète, et elle désignait la mauvaise piste.** Le détail des portes le montre
sans ambiguïté :

| Candidat | Plis rentables | Dispersion | Rétention | Stabilité | Périodes | Portes échouées |
|---|---|---|---|---|---|---|
| **BTC 06** | 39,1 % | **0,19** | **1,00** | **0,84** | **0,75** | **plis — une seule** |
| BTC 04 | **52,2 %** | **6,50** | −0,08 | 0,05 | 0,25 | dispersion, rétention, stabilité, périodes |
| BTC 02 | 37,0 % | 0,72 | 1,00 | 0,65 | 0,75 | plis, dispersion |
| BTC 05 | 50,0 % | 0,82 | 0,00 | 0,00 | 0,00 | dispersion, rétention, stabilité, périodes |
| BTC 07 | 34,8 % | 0,96 | 1,00 | 0,65 | 0,75 | plis, dispersion |
| BTC 00, 01, 03 | 41,3 / 41,3 / 28,3 % | 0,17 à 0,96 | 0,00 | 0,05 à 0,25 | 0,25 | 4 à 5 portes |
| **XAUUSD (les 8)** | 23,9 à 43,5 % | 0,14 à 0,38 | **toutes 0,00** | 0,16 à 0,28 | 0,14 à 0,43 | **4 à 5 portes chacune** |

**Ce que ce tableau corrige :**

1. **La variante 04 échoue à quatre portes, pas une.** Sa dispersion vaut **6,50**, soit
   **treize fois le plafond de 0,50** : c'est un point aberrant, pas un candidat. Une règle
   dont le résultat s'effondre quand on bouge un paramètre de 10 % n'a pas d'edge, elle a une
   coïncidence. **Je l'ai mise en avant parce que j'ai lu le chiffre qui m'arrangeait** — les
   plis rentables — sans regarder les trois autres portes qu'elle échouait.

2. **La vraie piste est la variante 06** : `ema_fast=20, ema_slow=60, tp=1.5`. Elle franchit
   **toutes les portes sauf une**, celle des plis rentables (**39,1 % contre 50 %**), avec la
   dispersion la plus basse de la campagne (**0,19**), une rétention hors échantillon parfaite
   (**1,00**), le meilleur score de stabilité (**0,84**) et 75 % de périodes profitables. Elle
   échoue **de cinq plis sur 46.**

3. **Sur l'or, aucun candidat n'a de rétention hors échantillon : les huit valent 0,00.** Le
   filtre de cassure ne se comporte donc pas de la même façon sur les deux marchés, et il faut
   le dire : la piste est **une piste BTC**, pas une propriété générale de la règle.

**Ce qui reste vrai de tout ce rapport :** aucun candidat n'est retenu, le scellé n'a jamais
été ouvert, rien n'est promu. Mais la conclusion utile n'est plus « le filtre est réfuté de
justesse » — c'est **« un réglage précis, sur le BTC, franchit huit portes sur neuf »**, et
c'est cette variante-là qui mérite d'être travaillée.

---

## 7. Le voisinage de la variante 06 : un candidat franchit les 9 portes, la sélection multiple le refuse

La variante 06 ne tombait que sur les plis rentables (39,1 % contre 50 %). Son voisinage serré
a donc été exploré — **27 combinaisons par marché, annoncées d'avance** (`ema_fast` ∈ {15, 20,
25}, `ema_slow` ∈ {50, 60, 70}, `tp` ∈ {1,3 ; 1,5 ; 1,7}), soit 54 hypothèses.

**Pour la première fois dans l'histoire de ce dépôt, un candidat a franchi les neuf portes.**

| `BTCUSD breakout_only:20` | |
|---|---|
| Paramètres | `ema_fast=25, ema_slow=50, tp=1.7` — **les trois seuls degrés de liberté libérés** |
| Plis rentables | 24 / 46 (52,2 %) |
| Dispersion des paramètres | 0,31 (plafond 0,50) |
| Rétention hors échantillon | 1,71 |
| Score de stabilité | 0,72 |
| Net apprentissage / validation | +47,42 € / +80,93 € |
| **Net sur le jeu scellé** | **+24,62 €** |
| **p-value** | **0,3467** |

**Il est refusé, et c'est la bonne décision.** Le seuil de Benjamini-Hochberg vaut **0,0019**
pour 54 hypothèses à α = 0,10, et sa p-value de **0,3467** est cent quatre-vingts fois
au-dessus. Autrement dit : sur 54 réglages essayés, obtenir un survivant par hasard est
**parfaitement banal**. Ce n'est pas un edge, c'est le résultat attendu d'une recherche.

**Ce que la campagne montre par ailleurs, et qui est encourageant :**

| Candidat | Plis | Dispersion | Rétention | Stabilité | Cause |
|---|---|---|---|---|---|
| 20 | **24/46 (52,2 %)** | 0,31 | 1,71 | 0,72 | `false_discovery` (p = 0,347) |
| 18 | 28/46 (60,9 %) | 0,32 | 1,00 | 0,76 | `out_of_sample_negative` (−18,28 €) |
| 19 | 26/46 (56,5 %) | 0,41 | 3,36 | 0,65 | `out_of_sample_negative` (net positif, rétention instable) |
| 06 | 22/46 (47,8 %) | 0,35 | 1,69 | **0,79** | `overfitting` |

**Quatre candidats sur 54 franchissent le seuil des plis**, contre **un seul sur 16** dans la
campagne précédente. Les dispersions sont **saines** (0,31 à 0,41, contre 6,50 pour la variante
04) et les scores de stabilité élevés (0,65 à 0,79). Le voisinage de la variante 06 est donc
**un plateau réel**, pas une coïncidence — mais un plateau qui **ne survit pas à la correction
de sélection multiple**.

**Le jeu scellé du BTC a été ouvert 3 fois** (contre 0 pour l'or) : c'est la contrepartie
normale du fait qu'un candidat soit allé au bout de l'échelle. **Le budget du scellé du BTC est
donc entamé**, et il faudra en tenir compte avant toute nouvelle campagne sur ce marché — un
scellé lu trois fois n'est plus tout à fait scellé.

**Ce que ça change dans la lecture du projet :** la barrière n'est plus « aucune stratégie ne
passe jamais rien ». C'est **« une piste existe, elle est réelle, et elle n'est pas encore
distinguable du hasard compte tenu du nombre d'essais »**. Ce sont deux situations très
différentes, et la seconde est celle où on continue.

---

## 8. Le test de confirmation : la piste BTC survit à un changement de découpage

**La règle du test, et elle est posée avant de le lancer.** Après un survivant à p = 0,3467
pour un seuil de 0,0019, la seule suite honnête n'est **pas** de relancer une recherche sur un
autre jeu — cela créerait 54 nouvelles hypothèses et aggraverait le problème qu'on vient de
mesurer. C'est de **geler** les paramètres, de les appliquer **une fois**, et de regarder.
Aucune grille, aucun ajustement : si le résultat déçoit, on ne réessaie pas un autre jeu. Le
script est [confirm_breakout_h1.py](../../scripts/backtest/confirm_breakout_h1.py).

**Ce que le nouveau découpage teste.** Les jeux H1 sont ré-agrégés depuis les mêmes bougies
M15 : ce ne sont **pas des données neuves**, et il ne faut pas le prétendre. Mais
`ema_slow=50` couvre 12,5 heures en M15 et **50 heures** en H1. Un edge qui ne survit pas à ce
changement était un artefact de la résolution choisie, pas une propriété du marché.

**Paramètres gelés :** `ema_fast=25, ema_slow=50, atr_period=14, stop=1,5 ATR, tp=1,7`.

| Découpage | Marché | Ops | Réussite | Net | PF | MAE moy. |
|---|---|---|---|---|---|---|
| M15 (référence, où il a été trouvé) | BTCUSD | 143 | 44,1 % | +143,30 € | 1,166 | 1,04 |
| M15 (référence) | XAUUSD | 167 | 28,7 % | −511,73 € | 0,600 | 1,06 |
| **H1 (nouveau découpage)** | **BTCUSD** | 42 | **47,6 %** | **+90,52 €** | **1,390** | 0,80 |
| **H1 (nouveau découpage)** | XAUUSD | 28 | 28,6 % | −120,33 € | 0,524 | 1,20 |
| H4 (découpage plus grossier) | BTCUSD | 4 | 50,0 % | +11,70 € | 1,556 | 1,00 |
| H4 | XAUUSD | 3 | 33,3 % | −6,31 € | 0,700 | 0,91 |

**Ce que ça montre :**

1. **Sur le BTC, le candidat survit au changement de découpage.** PF de 1,166 en M15 puis
   **1,390 en H1**, avec 47,6 % de réussite et **+90,52 € sur 42 opérations**. Le signe et
   l'ordre de grandeur tiennent quand la fenêtre des moyennes passe de 12,5 à 50 heures. Ce
   n'était pas acquis : c'était tout l'enjeu du test.

2. **Sur l'or, il échoue aux trois découpages** (PF 0,600 en M15, 0,524 en H1, 0,700 en H4 sur
   3 opérations). Ce n'est donc pas un problème de résolution : **la piste est spécifique au
   BTC**, et l'or est un marché où cette règle ne marche pas.

3. **Le H4 ne dit rien** : 4 et 3 opérations. Le présenter comme un PF de 1,556 serait une
   faute d'interprétation ; il est là pour être vu comme vide.

**Ce qui limite la portée de ce test, et qu'il faut écrire noir sur blanc :**

- **42 opérations, c'est peu.** L'intervalle de confiance d'un PF de 1,39 sur 42 trades est
  large, et il contient 1,0.
- **Ce ne sont pas des données indépendantes.** H1 est ré-agrégé des mêmes M15. Le test
  démontre une **robustesse au découpage**, pas une réplication hors échantillon.
- **Un facteur confondant existe : le coût des frais.** La commission est fixe (0,50 €) et le
  risque aussi (10 €), mais le **stop vaut quatre fois plus de points en H1** pour la même
  fraction d'ATR. Le coût des frais rapporté au prix payé est donc environ **quatre fois plus
  faible en H1**. Une partie de l'amélioration du PF vient de là, pas du signal, et personne ne
  peut chiffrer exactement cette part sans une mesure dédiée.
- **Le M15 de référence contient sa part d'échantillon d'apprentissage** : 143 opérations sur
  l'ensemble du jeu, alors que le candidat a été **sélectionné** dessus. Le H1, lui, n'a jamais
  servi à choisir ce réglage — c'est la seule partie du tableau qui soit honnêtement hors
  échantillon.

**Conclusion en une phrase :** la piste BTC passe d'« indiscernable du hasard sur les données
qui l'ont produite » à **« robuste à un changement de découpage, sur 42 opérations »** — un
progrès réel, et **toujours pas une validation**. Il faudrait du BTCUSD H1 natif depuis 2011,
jamais lu, pour trancher.

---

## 9. Le test décisif : H1 **natif** depuis 2011, jamais lu

Le rapport demandait « du BTCUSD H1 natif depuis 2011, jamais lu ». Il a été récupéré du
terminal le 2026-10-09 (87 694 bougies, 2011-03-23 → 2026-10-09) et **les paramètres gelés y
ont été appliqués une seule fois**, sans ajustement. C'est la seule mesure du projet faite sur
des données qu'aucune campagne n'avait touchées.

| Bloc | Ops | Réussite | Net | PF |
|---|---|---|---|---|
| **Total 2011→2026** | **179** | 45,8 % | **+300,64 €** | **1,291** |
| **dont 2011-2024 — JAMAIS VU** | **136** | **44,9 %** | **+186,73 €** | **1,233** |
| dont 2025-2026 — période déjà vue | 43 | 48,8 % | +113,91 € | 1,493 |

**Le chiffre qui compte : PF 1,233 sur 136 opérations à 44,9 % de réussite, sur une période que
le candidat n'a jamais vue.** Le seuil du dépôt est de 1,20. C'est la première fois qu'une
règle de ce projet franchit un seuil **hors des données qui l'ont produite**.

**Décomposition par période, et elle est instructive :**

| Période | Ops | Réussite | Net | PF |
|---|---|---|---|---|
| 2011-2014 | **0** | — | — | — |
| 2015-2018 | 20 | 55,0 % | +60,56 € | 1,581 |
| 2019-2022 | 70 | 44,3 % | +98,81 € | 1,241 |
| 2023-2026 | 89 | 44,9 % | +141,28 € | 1,273 |

**Pourquoi 2011-2014 ne produit rien — et ce n'est pas un bug.** Le relevé des bougies le dit :

| Année | Bougies | Premier prix | Dernier prix |
|---|---|---|---|
| 2011 | **278** | 0,87 $ | 5,27 $ |
| 2012 | **366** | 5,22 $ | 13,30 $ |
| 2013 | **365** | 13,28 $ | 741,32 $ |
| 2015 | **365** | 312,72 $ | 433,01 $ |
| 2016 | **3 692** | 431,58 $ | 979,36 $ |
| 2019 | 8 739 | 3 827,67 $ | 7 193,76 $ |
| 2024 | 8 784 | 42 502,34 $ | 93 547,54 $ |

**De 2011 à 2015, il n'y a que 278 à 366 bougies H1 par an, contre 3 692 en 2016 et 8 700
ensuite.** Le BTC a très peu traité pendant ses premières années : les heures sans échange
n'existent pas dans les données, donc la série est clairsemée. Une règle qui exige la cassure
d'un canal de 50 heures **ne trouve presque rien à faire dans un marché qui n'ouvre que quand
quelqu'un échange**. Le silence de 2011-2014 est un fait de marché, pas une absence de
données — et il est cohérent avec les 20 opérations de 2015-2018, quand la liquidité arrive.

**⚠️ Les deux limites qui interdisent de crier victoire, et elles sont sérieuses :**

1. **Le modèle de coûts est invraisemblable pour la première décennie.** Il applique
   `0,00007 × prix` de spread et de slippage, plus 0,50 € fixes. À 0,87 $ le BTC en 2011, cela
   suppose un spread de **0,006 centime** — quand les spreads réels de l'époque se comptaient en
   **pour cent**. Sur les premières années, le backtest est donc **optimiste d'un ordre de
   grandeur**, et le PF de 1,581 en 2015-2018 n'est pas fiable. **La seule fenêtre où ce modèle
   tient à peu près est 2019-2026 : PF 1,259 sur 159 opérations.** C'est le chiffre à retenir,
   et il reste au-dessus du seuil.

2. **L'instrument n'est pas le même sur quinze ans.** Le BTC de 2011 à 1 $ sur un marché
   naissant et celui de 2024 à 90 000 $ sur un marché institutionnel ne sont pas le même actif.
   Qu'un résultat tienne sur les deux est encourageant ; **cela ne prouve pas qu'une seule
   stratégie les couvre.**

**Ce que ça vaut, en une phrase :** une règle de suivi de tendance avec filtre de cassure, sur
BTCUSD H1, gagne **hors des données qui l'ont produite** (PF 1,233 sur 136 opérations) et sur
la période où les coûts modélisés sont crédibles (PF 1,259 sur 159 opérations). Ce n'est
**toujours pas une promotion** — échantillon petit, un seul marché, quinze ans d'un actif qui a
changé de nature — mais c'est **la première piste du projet qui franchit un seuil hors
échantillon**, et elle mérite un vrai protocole de validation.

**Ce que ce test ne fait pas :** le scellé M15 du BTC a été lu 3 fois et reste entamé. Le H1
natif est un jeu **neuf**, donc ce test ne consomme rien — mais il ne valide pas non plus le
réglage sur M15, qui est un autre marché en pratique.

---

## 10. Le protocole complet en H1 natif : les 9 portes rejettent aussi

Le test de confirmation du point 9 est une **mesure unique sur paramètres gelés**. Le protocole
complet — walk-forward à **68 plis**, robustesse des paramètres, correction de sélection
multiple — a ensuite été lancé sur le **même jeu H1 natif**, avec un **scellé neuf** de
17 540 bougies jamais lu.

| Candidat | Plis rentables | Dispersion | Rétention | Stabilité | Périodes | Ops train | Net train | Cause |
|---|---|---|---|---|---|---|---|---|
| 01 | **52,9 %** | 0,45 | 0,58 | 0,42 | 0,48 | 190 | +144,38 € | `unstable` |
| 05 | 48,5 % | 0,51 | −2,01 | 0,08 | 0,41 | 197 | +68,08 € | `overfitting` |
| 00 | 44,1 % | 1,69 | −3,05 | 0,10 | 0,48 | 196 | +5,42 € | `overfitting` |
| 02 | 42,6 % | 5,14 | −0,09 | 0,10 | 0,50 | 62 | +133,18 € | `overfitting` |
| 04 | 35,3 % | 0,24 | −1,60 | 0,25 | 0,45 | 197 | +73,15 € | `overfitting` |
| 06 | 36,8 % | 0,73 | −0,25 | 0,09 | 0,47 | 60 | +103,86 € | `overfitting` |
| 03 | 33,8 % | 0,40 | −0,77 | 0,12 | 0,28 | 62 | +164,82 € | `overfitting` |
| 07 | 30,9 % | 0,52 | −0,65 | 0,06 | 0,32 | 60 | +124,48 € | `overfitting` |

**8 rejetés sur 8.** Le scellé neuf n'a **pas été ouvert** (0 ouverture sur 17 540 bougies) : il
reste disponible pour une prochaine tentative.

**Ce que ça change dans la lecture du point 9, et il faut être direct.** Ce test mesurait **un
réglage gelé appliqué en continu sur quinze ans**, et donnait PF 1,233 hors échantillon. J'ai
écrit que c'était « la première piste du projet qui franchit un seuil hors échantillon ». **C'est
exact au sens strict, et trompeur en pratique.** Le walk-forward, lui, **réapprend sur chaque
pli et juge sur le suivant** : il montre que la règle ne tient pas d'une fenêtre à l'autre.
**Sept candidats sur huit ont une rétention hors échantillon négative** (−0,09 à −3,05) : ils
gagnent sur l'ensemble et perdent dès qu'on découpe.

**La différence entre les deux mesures est toute la leçon.** Un paramètre gelé appliqué à une
série longue capitalise sur des **régimes favorables traversés en continu**. Un walk-forward
exige que la règle **retrouve** son avantage à chaque fenêtre. La première mesure dit « cette
règle a gagné sur cette histoire » ; la seconde dit « cette règle ne se reproduit pas ». **La
seconde est la seule qui compte pour trader demain**, et c'est elle qui rejette.

**Mon point 9 était donc trop enthousiaste.** « Premier franchissement de seuil hors
échantillon » décrit correctement une mesure, et **ne doit pas se lire comme « piste validée »**.
Les 9 portes ont tranché dans l'autre sens dès qu'on les a fait tourner.

**Une nuance qui reste, et qui n'est pas rien :** le candidat **01 franchit le seuil des plis
(52,9 %, une première)** avec une dispersion saine (0,45) et +144,38 € en apprentissage. Il
tombe sur le **score de stabilité, 0,42 contre 0,50 requis — de 0,08.** C'est le deuxième
candidat du projet à échouer d'aussi peu, et comme le premier, il mérite mieux qu'un rejet
définitif : il mérite d'être compris.

---

## 11. Ce qu'il faudrait pour rouvrir la question

| Piste | Pourquoi |
|---|---|
| **Comprendre le candidat 01 du H1 natif** | il franchit le seuil des plis (52,9 %, une première) et tombe sur la stabilité de **0,08** : c'est la piste la plus proche du but |
| **Chercher pourquoi la rétention est négative partout** | 7 candidats sur 8 en H1 natif, 8 sur 8 sur l'or : la règle gagne sur l'ensemble et perd dès qu'on découpe, ce qui est un symptôme à diagnostiquer avant tout nouveau réglage |
| **Corriger le modèle de coûts pour l'ère 2011-2018** | il suppose 0,006 centime de spread en 2011, quand le réel se comptait en pour cent : les PF de cette période ne veulent rien dire |
| Tester le canal de cassure comme **paramètre libre** | il vaut aujourd'hui `ema_slow`, ce qui lie deux choix qui n'ont pas de raison de l'être |
| Mesurer le filtre sur une **autre règle parente** | ici il n'a été testé qu'autour du croisement EMA |
| **Ne pas retravailler la variante 04** | dispersion de 6,50 : la poursuivre serait chercher un edge dans du bruit |
| **Ne pas retravailler l'or avec cette règle** | elle échoue aux quatre découpages (PF 0,600 / 0,524 / 0,700, et le H1 natif or n'a pas été récupéré) |

**Rien n'a été promu.** `config/strategies/` n'a pas été touché, `strategies/registry.py` non
plus, et `BreakoutOnly` n'est pas au registre de production — un test le vérifie.
