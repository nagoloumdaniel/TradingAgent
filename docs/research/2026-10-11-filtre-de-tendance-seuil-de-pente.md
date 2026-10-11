# Un réglage moins sélectif du filtre de tendance : oui, et c'est un oui faible

**Date :** 2026-10-11 · **Nature :** mesure en lecture seule · **Aucune activation, `config/` inchangé.**
**Question :** au seuil de pente par défaut (0,05), le filtre de tendance de `vwap_pullback` ne garde
que 14,6 % et 14,3 % des opérations, ce qui laisse 30 et 33 opérations en validation — exactement
`min_trades`. Existe-t-il un réglage de `trend_slope_atr` **moins sélectif** qui conserve l'effet
sur les deux fenêtres **et** les deux marchés ?

**Réponse courte.** **Oui, selon la règle de décision écrite avant la mesure** : deux réglages,
`trend_slope_atr = 0,01` et `0,02`, gardent 37 % à 56 % des opérations (contre 12 % à 15 % au seuil
par défaut) et leur facteur de profit net dépasse celui de la règle sans filtre sur les quatre
blocs. **Mais le oui est faible, et il doit être dit dans la même phrase** : les huit facteurs de
profit concernés restent **sous 1,00**, l'espérance nette des deux réglages retenus est
**significativement négative** sur les deux fenêtres réunies, et **aucune des 18 hypothèses ne
survit à la correction de tests multiples du dépôt** (0 survivant). L'effet fort du seuil par défaut
— validation du bitcoin à PF 1,5376 — **ne se transporte pas** : il est le prix des 85 % d'opérations
refusées. Si « conserver l'effet » veut dire « retrouver ce facteur de profit », la réponse est
**non**. Les deux lectures sont conservées ci-dessous, la seconde n'annulant pas la première mais la
bornant.

---

## 1. Ce qui a été mesuré, et la vérification qui rend la comparaison possible

Le filtre est basculé **en mémoire** dans un `CandidateSpec` : aucun manifeste n'est réécrit,
`config/` n'est pas touché, et le jeu scellé n'est pas ouvert (`SealedSet.unlock` n'a jamais reçu son
jeton ; seuls l'effectif et la fenêtre du sceau sont lus, pour pouvoir nommer la période qui n'a pas
été regardée). Un seul paramètre bouge : `trend_slope_atr`. `ema_fast`/`ema_slow` restent 20/50 —
ce sont aussi les EMA de la stratégie, les changer serait une autre règle et pas un réglage de
filtre.

| | BTCUSD | XAUUSD |
|---|---|---|
| Version en production | `vwap_pullback@1.2.0` | `vwap_pullback@1.1.0` |
| Jeu gelé | `docs/research/datasets-volume/` | idem |
| Bougies mesurées | 59 999 (M15) | 59 999 (M15) |
| Empreinte | `2f04c3b23c31e407…` | `d847b980c6802d6a…` |
| Entraînement | 2025-01-21 16:30 → 2026-01-31 20:30 | 2024-03-25 13:00 → 2025-10-02 11:45 |
| Validation | 2026-01-31 20:30 → 2026-06-05 23:15 | 2025-10-02 11:45 → 2026-04-08 18:15 |
| **Scellé — non lu** | 2026-06-05 23:15 → 2026-10-09 01:45 (12 001) | 2026-04-08 18:15 → 2026-10-09 03:30 (12 001) |
| Sortie | au **premier** objectif (`targets_at_first_only`), comme `runtime/pipeline.py` | idem |
| Coûts | `campaign_costs` : spread, slippage, 0,50 €/opération, `mode=SIGNAL`, une position à la fois | idem |
| Découpage | `split_dataset(anchor=True)`, 60 / 20 / 20 scellé | idem |

**La mesure publiée est reproduite avant toute conclusion.** La baseline sans filtre **et** le seuil
par défaut sont confrontés champ par champ à `docs/research/measurements/trend-filter-effect.json` :

| | compte |
|---|---|
| champs confrontés | **48** (2 marchés × 2 lignes publiées × 2 fenêtres × 6 grandeurs) |
| écart maximal | **0,000e+00** |
| verdict | **REPRODUITE** |

Le script refuse de publier un balayage qui ne reproduirait pas la mesure qui l'a motivé, et il n'a
rien publié avant de l'avoir vérifié.

**Une vérification de statistique, pas seulement de plomberie.** La loi de Student vient de
`edge_location.py` et est **re-vérifiée ici** contre des valeurs tabulées : `t(0,975 ; 10)`,
`t(0,975 ; 1)`, `t(0,95 ; 29)`, la limite asymptotique, et `P(|T| ≥ 2,228138852 ; 10) = 0,05`
(erreurs 1,4e-11 à 4,7e-10). **Et un septième contrôle, qui a servi** : la p-value unilatérale est
ré-orientée, parce que `edge_location._significance` rend `P(|T| ≥ |t|)/2`, qui n'est la p-value
unilatérale que pour une statistique **positive**. Sur une série à espérance significativement
négative, la valeur brute vaut 0,000422 et se lirait comme le résultat le plus significatif du
balayage ; ré-orientée, elle vaut 0,999578. La version du script qui a produit le premier passage
n'avait pas ce contrôle : la correction de tests multiples y proclamait **11 découvertes**, toutes
sur des réglages à espérance **négative**. Le balayage a été refait avec l'orientation corrigée
(7 contrôles sur 7), et c'est ce passage-là qui est publié.

**Ce que le balayage a coûté, et comment.** Une seule passe sur le jeu complet : 9 seuils × 2
marchés × 2 fenêtres = **36 mesures**, plus les **4** blocs de la baseline, chaque bloc mesuré une
fois et une seule, en 8 processus parallèles (≈ 12 min). Les valeurs sont mises en cache avant
toute analyse (`docs/research/tune-trend-filter/measurements-cache.json`), ce qui permet de refaire
l'analyse sans relancer un seul backtest — c'est ce qui a été fait après la correction de
l'orientation. Le jeu complet n'a pas été relancé dix fois.

---

## 2. La règle de décision, écrite avant la mesure, recopiée ici telle quelle

Un réglage `t` est **RETENU** si et seulement si les quatre conditions tiennent ensemble :

| | condition | fixée avant la mesure |
|---|---|---|
| **(a)** | sélectivité | le filtre garde **plus** d'opérations qu'au seuil par défaut, sur chacun des 4 blocs |
| **(b)** | amélioration | PF net **supérieur** à celui de la même règle **sans filtre**, entraînement **et** validation, sur les **deux** marchés |
| **(c)** | plancher | au moins **30 %** des opérations gardées, sur chacun des 4 blocs |
| **(d)** | marge sur le seuil | au moins **60** opérations en validation par marché (le double de `min_trades`) |

Le plancher (c) porte sur les **quatre** blocs et pas seulement la validation : un réglage qui
tiendrait 30 % en validation et 15 % en entraînement serait un effet de régime, pas une sélectivité.

**Ordre fixé d'avance entre plusieurs réglages retenus :** celui dont le PF net **minimal** sur les
quatre blocs est le plus haut. Aucun autre critère n'a été regardé après coup.

**Hypothèses essayées : 9 seuils × 2 marchés = 18**, annoncées avant les chiffres. La correction
appliquée est celle du dépôt (Benjamini-Hochberg, `research.protocol.price_false_discoveries`,
α = 0,10), sur ces 18 p-values.

**Plage balayée, et pourquoi elle :** `0,001 / 0,002 / 0,005 / 0,01 / 0,02 / 0,03 / **0,05** / 0,08 /
0,12`. `0,05` est le seuil par défaut du module de régime (lu dans `indicators.regime`, pas recopié).
`0,005` est la pente par barre que la règle exige déjà d'elle-même (`min_slope_atr`, mesurée sur 10
barres) : un point de comparaison nommé par le manifeste. Sous `0,001`, la pente d'une barre
d'EMA(50) est indistinguable de son bruit ; au-dessus de `0,12`, le filtre ne garde presque plus
rien. La plage couvre les **deux côtés** du seuil, parce que la direction de la sélectivité est
triviale et devait être vérifiée plutôt que supposée.

---

## 3. Le balayage

`conserve` = opérations gardées rapportées à la règle **sans filtre**, du même bloc. `PF net` = le
facteur de profit net du même bloc. La baseline vaut, en entraînement / validation :
**BTC 709 op. · PF 0,7148 · −999,06 €** puis **205 op. · PF 0,6818 · −327,71 €** ;
**or 656 op. · PF 0,7229 · −863,80 €** puis **231 op. · PF 0,5454 · −621,91 €**.

| seuil | conserve BTC tr / va | conserve or tr / va | PF net BTC tr / va | PF net or tr / va | a | b | c | d | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 0,001 | 66,6 % / 65,4 % | 63,9 % / 66,2 % | 0,7474 / 0,7466 | 0,6768 / 0,6614 | oui | **non** | oui | oui | refusé |
| 0,002 | 64,9 % / 63,9 % | 61,0 % / 64,5 % | 0,7649 / 0,7396 | 0,6802 / 0,6798 | oui | **non** | oui | oui | refusé |
| 0,005 | 61,2 % / 59,0 % | 56,6 % / 58,4 % | 0,7775 / 0,7552 | 0,6949 / 0,6852 | oui | **non** | oui | oui | refusé |
| **0,01** | 54,3 % / 56,1 % | 49,5 % / 51,9 % | 0,8312 / 0,7391 | 0,7457 / 0,6870 | oui | oui | oui | oui | **RETENU** |
| **0,02** | 41,3 % / 43,4 % | 37,3 % / 39,0 % | 0,8214 / 0,7818 | 0,7509 / 0,6422 | oui | oui | oui | oui | **RETENU** |
| 0,03 | 27,8 % / 29,3 % | 27,4 % / 25,5 % | 0,8268 / 0,8947 | 0,6794 / 0,6195 | oui | **non** | **non** | **non** | refusé |
| 0,05 *(défaut)* | 12,7 % / 14,6 % | 12,2 % / 14,3 % | 0,9643 / **1,5376** | 0,6782 / 0,9630 | **non** | **non** | **non** | **non** | refusé |
| 0,08 | 3,5 % / 5,4 % | 3,2 % / 7,8 % | 1,0801 / 2,4787 | 0,8839 / 0,8129 | **non** | oui | **non** | **non** | refusé |
| 0,12 | 0,3 % / 2,4 % | 0,5 % / 3,0 % | 0,0000 *(2 op.)* / 2,1178 | — *(3 op.)* / 0,8678 | **non** | **non** | **non** | **non** | refusé |

**La direction triviale est bien celle qu'on croyait** : la colonne `conserve` monte d'un facteur 20
à 200 selon le bloc quand le seuil passe de 0,12 à 0,001. Elle n'est pas le résultat. Le résultat est
la colonne PF, et la ligne `verdict`.

**Ce que les refus disent, et qui compte autant que les retenus :**

- `0,001`, `0,002`, `0,005` gardent 56 % à 67 % des opérations et **échouent sur (b) à cause de
  l'or en entraînement** : 0,6768 / 0,6802 / 0,6949 contre 0,7229. Le filtre y dégrade la fenêtre
  qui sert à sélectionner, exactement comme le seuil par défaut (0,6782). Descendre le seuil ne
  répare pas l'or.
- `0,05`, le seuil par défaut, **échoue sur les quatre conditions** — y compris (a) et (c), qui sont
  la contrainte pratique. Son PF de validation du bitcoin (1,5376) est le meilleur nombre du
  tableau, et il ne suffit pas à le retenir.
- `0,08` améliore le PF sur les quatre blocs (1,0801 / 2,4787 / 0,8839 / 0,8129) mais en gardant
  **3,2 % à 7,8 %** des opérations : il est plus sélectif, pas moins, et il échoue sur (a), (c) et
  (d). C'est la même famille d'effet, à l'autre bout.
- `0,12` ne laisse que 2, 5, 3 et 7 opérations : ce n'est plus un filtre, c'est une suppression, et
  un PF n'y est pas défini.

---

## 4. La règle appliquée : deux réglages retenus

**`trend_slope_atr = 0,01` et `0,02` satisfont les quatre conditions.** L'ordre fixé d'avance
désigne **0,01** (PF net minimal sur les quatre blocs : 0,6870 contre 0,6422 pour 0,02).

| marché | fenêtre | sans filtre | **0,01** | **0,02** | 0,05 *(défaut)* |
|---|---|---|---|---|---|
| BTCUSD | entraînement | 709 · 0,7148 · −999,06 € | **385 · 54,3 % · 0,8312 · −293,61 €** | 293 · 41,3 % · 0,8214 · −238,08 € | 90 · 12,7 % · 0,9643 · −13,50 € |
| BTCUSD | validation | 205 · 0,6818 · −327,71 € | **115 · 56,1 % · 0,7391 · −142,53 €** | 89 · 43,4 % · 0,7818 · −88,93 € | 30 · 14,6 % · 1,5376 · **+47,34 €** |
| XAUUSD | entraînement | 656 · 0,7229 · −863,80 € | **325 · 49,5 % · 0,7457 · −382,00 €** | 245 · 37,3 % · 0,7509 · −281,44 € | 80 · 12,2 % · 0,6782 · −125,09 € |
| XAUUSD | validation | 231 · 0,5454 · −621,91 € | **120 · 51,9 % · 0,6870 · −195,53 €** | 90 · 39,0 % · 0,6422 · −173,56 € | 33 · 14,3 % · 0,9630 · −5,27 € |

**Les opérations conservées, en validation : 115 (bitcoin) et 120 (or) à 0,01, 89 et 90 à 0,02.**
Le seuil du projet (`min_trades = 30`) est dépassé d'un facteur 3 à 4, et la contrainte pratique du
rapport précédent — 30 et 33 opérations, « une opération tous les trois jours » — disparaît : à 0,01
le filtre laisse passer une opération sur deux.

**Le gain sur la règle sans filtre, réglage par réglage :**

| | PF sans filtre | PF à 0,01 | écart | PF à 0,02 | écart |
|---|---|---|---|---|---|
| BTC entraînement | 0,7148 | 0,8312 | **+0,116** | 0,8214 | +0,107 |
| BTC validation | 0,6818 | 0,7391 | **+0,057** | 0,7818 | +0,100 |
| Or entraînement | 0,7229 | 0,7457 | **+0,023** | 0,7509 | +0,028 |
| Or validation | 0,5454 | 0,6870 | **+0,142** | 0,6422 | +0,097 |

---

## 5. Ce que le oui ne dit pas

**1. Aucun des huit facteurs de profit ne franchit 1,00.** Le gain est un **moins-pire**, pas une
rentabilité : à 0,01 le bitcoin perd encore 142,53 € en validation et l'or 195,53 €, et **les huit
blocs des deux réglages retenus sont négatifs**. Les seuls blocs positifs du balayage sont la
validation du bitcoin au seuil par défaut (+47,34 € sur 30 op.) et des blocs de très petits
échantillons : 0,08 BTC entraînement (+7,87 € sur 25 op.) et validation (+32,45 € sur 11 op.),
0,12 BTC validation (+12,25 € sur 5 op.) et or entraînement (+17,94 € sur 3 op.). Le seuil du projet
(PF ≥ 1,20) reste hors d'atteinte, et le meilleur nombre du tableau est la validation du bitcoin au
seuil par défaut — celui qui ne garde que 30 opérations.

**2. L'espérance nette des deux réglages retenus est significativement négative.** T de Student
unilatéral sur le R net par opération, **deux fenêtres réunies** :

| réglage | marché | op. | R moyen / op. | IC 95 % | p unilatérale | survit à BH |
|---|---|---|---|---|---|---|
| 0,01 | BTCUSD | 500 | −0,0872 | [−0,1627 ; −0,0118] | 0,988 | non |
| 0,01 | XAUUSD | 445 | −0,1298 | [−0,2097 ; −0,0498] | 0,999 | non |
| 0,02 | BTCUSD | 382 | −0,0856 | [−0,1718 ; +0,0006] | 0,974 | non |
| 0,02 | XAUUSD | 335 | −0,1358 | [−0,2282 ; −0,0434] | 0,998 | non |
| 0,05 *(défaut)* | BTCUSD | 120 | +0,0282 | [−0,1239 ; +0,1803] | 0,357 | non |
| 0,05 *(défaut)* | XAUUSD | 113 | −0,1154 | [−0,2754 ; +0,0447] | 0,922 | non |

Trois des quatre intervalles des réglages retenus **excluent zéro par le bas** : le filtre améliore
le PF par rapport à la règle nue, et la règle reste perdante de façon mesurable. La p unilatérale
publiée est celle de H0 « espérance ≤ 0 » : une valeur proche de 1 signifie « rien ici ne soutient
une espérance positive », et l'intervalle qui l'accompagne dit en plus, quand il est entièrement
sous zéro, que l'espérance est négative de façon mesurable.

**3. Aucune hypothèse ne survit à la correction de tests multiples.** Sur **18** hypothèses
(marché × seuil), **14** sont concluantes (≥ `min_trades` en validation) :

| | compte |
|---|---|
| hypothèses essayées | **18** |
| dont concluantes | 14 |
| ligne de Bonferroni (α = 0,10) | 5,556e-03 |
| fausses découvertes attendues par hasard | 1,8 |
| **survivantes après Benjamini-Hochberg** | **0** |

**La p-value la plus petite du balayage vaut 0,207**, et elle appartient à 0,08 (bitcoin), qui n'est
pas concluant : 11 opérations en validation, et un PF de 2,4787 sur 11 opérations n'est pas une
mesure. Aucune hypothèse n'approche donc la ligne de Bonferroni (5,556e-03), ni même le seuil
nominal de 0,05.

**Les deux lectures ne se contredisent pas, elles répondent à deux questions différentes.** (b)
demande « le filtre améliore-t-il le PF **par rapport à la règle nue**, sur ces quatre blocs ? » —
oui, à 0,01 et 0,02, et c'est le critère qui a été écrit d'avance. La correction de tests multiples
demande « l'un de ces réglages a-t-il une espérance **positive** que le hasard n'explique pas ? » —
non, aucun, pas même le seuil par défaut. **Le oui de la règle est donc un oui de signe, pas un oui
de rentabilité**, et un Lead qui lirait « retenu » comme « à activer » lirait plus que ce que le
chiffre porte.

---

## 6. Le mécanisme : l'effet fort et les 85 % de refus sont inséparables

C'est le résultat qui compte pour la suite, et il est monotone **sur le bitcoin** : le PF net monte
quand la part gardée descend.

| seuil | part gardée (BTC validation) | PF net (BTC validation) |
|---|---|---|
| 0,001 | 65,4 % | 0,7466 |
| 0,005 | 59,0 % | 0,7552 |
| 0,01 | 56,1 % | 0,7391 |
| 0,02 | 43,4 % | 0,7818 |
| 0,03 | 29,3 % | 0,8947 |
| 0,05 | 14,6 % | **1,5376** |
| 0,08 | 5,4 % | **2,4787** |

Dans cette famille à un paramètre, **il n'existe pas de réglage qui garde beaucoup d'opérations et
retrouve le PF du seuil par défaut** : chaque opération rendue au filtre ramène le PF vers la
baseline. Le PF de 1,5376 sur la validation du bitcoin est acheté par le refus de 85 % des
entrées — c'est un fait de la mesure, pas une opinion, et il borne ce qu'un réglage de seuil peut
donner.

**Et l'or ne suit pas la même courbe.** Son PF d'entraînement dessine une **bosse étroite** :
0,6768 / 0,6802 / 0,6949 aux seuils 0,001–0,005, puis **0,7457** et **0,7509** à 0,01 et 0,02, puis
0,6794 et 0,6782 à 0,03 et 0,05. Les deux réglages retenus sont exactement sur cette bosse, et
aucun de leurs voisins immédiats ne passe la condition (b) pour l'or. C'est le motif qu'un balayage
produit par hasard aussi souvent qu'autrement, et il doit être lu comme tel : **la moitié « or » du
oui repose sur une anomalie locale de 0,02 de large**, pas sur un plateau. Le bitcoin, lui, est
monotone et ne dépend pas de ce détail.

---

## 7. La robustesse du verdict au plancher choisi

Le plancher de 30 % et la marge de 60 opérations étaient **suggérés**, pas imposés ; l'ordre entre
plusieurs retenus, lui, est entièrement le mien. Le verdict ne dépend d'aucun des trois :

| plancher (c) | réglages retenus |
|---|---|
| 0 % à 37,3 % | **0,01 et 0,02** |
| 37,4 % à 49,5 % | **0,01 seul** |
| au-delà de 49,5 % | aucun |

Et le cas limite mérite d'être écrit : **0,03** garde 25,5 % à 29,3 % des opérations, et **59**
opérations en validation pour l'or — il rate le plancher de 4,5 points et la marge (d) d'**une**
opération (le bitcoin en a 60). Il a par ailleurs le meilleur PF de validation du bitcoin de toute
la zone intermédiaire (0,8947). **Il ne serait pas retenu même avec un plancher à 25 % et une marge
à 50**, parce qu'il échoue sur (b) : l'or en entraînement vaut 0,6794 contre 0,7229 sans filtre. La
condition qui tranche n'est donc pas le plancher, c'est l'amélioration de l'entraînement de l'or —
et c'est la même condition qui refuse 0,001, 0,002, 0,005 et 0,05.

---

## 8. Ce qui n'a pas pu être établi, et les limites

1. **Rien de ce document ne sort des fenêtres d'entraînement et de validation.** Le holdout de l'or
   — la dernière fenêtre intacte du projet — reste fermé. Un réglage « retenu » ici n'est pas un
   réglage validé : il n'a franchi aucune des neuf portes.
2. **Les opérations ne sont pas indépendantes** (elles se groupent par régime et se chevauchent dans
   le temps) : les p-values publiées sont optimistes, et la correction de tests multiples ne répare
   pas cela — elle est appliquée parce que le dépôt l'exige, et son verdict (0 survivant) est donc
   à lire comme un plafond, pas comme un plancher.
3. **Le harnais n'ouvre qu'une position à la fois** : `skipped_no_room` est non nul, donc la part
   conservée en **opérations** n'est pas la part de signaux acceptés (elle est publiée aussi en
   signaux dans le JSON ; l'écart va de 0 à 5 points selon le bloc). Un filtre mesuré ici ne dit
   rien de ce qu'il ferait avec plusieurs positions simultanées.
4. **Le compte des hypothèses est celui du balayage, pas celui de la décision complète.** 18 = 9
   seuils × 2 marchés. Les mesures antérieures (le seuil par défaut, les périodes d'EMA) et les
   croisements refusés ne sont pas dans ce compte ; s'ils y étaient, la ligne de Bonferroni
   descendrait encore.
5. **Le vrai levier n'était pas dans la plage balayée, et il n'est pas paramétrable.** La règle
   refuse `NEUTRAL` autant que la tendance contraire (`trend is not side`). Or la mesure du
   2026-10-11 dit que ces deux refus ne valent pas la même chose : +0,2975 R pour les entrées dans
   la tendance, −0,1825 R pour celles sans tendance nommée, −0,5045 R pour celles qui la
   contredisent (bitcoin). En gardant « dans la tendance **ou** sans tendance » — c'est-à-dire en ne
   refusant que le contre-tendance — on garderait ≈ 86 % des opérations ; l'arithmétique sur les
   slices déjà publiés donne alors
   `(157 × 0,2975 + 629 × −0,1825) / 786 = −0,0866 R` par opération sur le bitcoin et
   `(133 × 0,2198 + 638 × −0,1567) / 771 = −0,0918 R` sur l'or. **Toujours négatif.** Cette
   variante n'est pas mesurée ici, et elle ne peut pas l'être par un paramètre : c'est une
   **modification de code** dans `vwap_pullback.evaluate`, donc une nouvelle version de manifeste et
   une nouvelle mesure. Ce paragraphe ne conclut rien ; il dit où est la marge, et qu'elle est
   mince.
6. **Le point 5 explique aussi l'écart avec la mesure d'origine.** Le filtre de la règle garde
   12,7 % à 14,6 % au seuil par défaut, là où la lecture `entry_features` (EMA 10/30) nommait une
   direction sur (157+128)/914 = **31,2 %** des opérations du bitcoin et (133+116)/887 = **28,1 %**
   de celles de l'or. Le plancher de 30 % suggéré par la mission est donc exactement la part que la
   mesure d'origine conservait, et le balayage **la retrouve** : 0,01 et 0,02 gardent 37 % à 56 %.
   Ce qu'il ne retrouve pas, c'est l'effet qui allait avec : sur ces deux réglages, l'amélioration
   des quatre blocs vaut de +0,02 à +0,14 de PF, et l'espérance nette y reste négative de façon
   mesurable. Descendre le seuil de `trend_of` rend bien les opérations, pas l'avantage.

---

## 9. Reproduire

```bash
# la mesure complète : 9 seuils x 2 marchés x 2 fenêtres + baseline (≈ 12 min, 8 processus)
uv run python scripts/backtest/tune_trend_filter.py --jobs 8

# l'analyse seule, depuis le cache de mesure : aucun backtest relancé (quelques secondes)
uv run python scripts/backtest/tune_trend_filter.py --from-cache

# la plomberie, jeu tronqué : ce n'est PAS une mesure
uv run python scripts/backtest/tune_trend_filter.py --bars 9000 --jobs 4 --no-write

# les 7 contrôles de la loi de Student, dont l'orientation de la p-value unilatérale
uv run python scripts/backtest/tune_trend_filter.py --self-check
```

**Sorties brutes :**

- `docs/research/tune-trend-filter/tune-trend-filter.json` — le balayage complet : les quatre blocs
  de chaque seuil, les quatre conditions, la significativité, la correction de tests multiples, la
  confrontation à la mesure publiée, le verdict.
- `docs/research/tune-trend-filter/measurements-cache.json` — la mesure brute (R par opération
  inclus), écrite **avant** toute analyse, ce qui rend l'analyse rejouable sans backtest.
- `docs/research/tune-trend-filter/run-datasets-volume.txt` — la sortie standard intégrale du
  passage d'analyse (elle indique elle-même qu'elle relit le cache et n'a relancé aucun backtest).

**Lecture seule, et ce qui n'a pas été touché :** le jeu scellé n'a pas été ouvert
(`confirm_holdout` reste faux, `SealedSet.unlock` n'a jamais reçu son jeton), aucun manifeste n'a
été réécrit, `config/` n'a pas été touché, `docs/research/thresholds.json` n'a été ni lu ni écrit,
aucun ordre n'a été envoyé, aucune écriture n'a eu lieu dans la base de production, et aucun module
de production n'a été modifié : `scripts/backtest/` et `docs/research/` seulement.

---

**Méthode.** `scripts/backtest/tune_trend_filter.py`. Le filtre est basculé dans un `CandidateSpec`
en mémoire (`dataclasses.replace`), donc aucune version publiée n'est réécrite. La loi de Student
vient de `edge_location.py` et est re-vérifiée ici, orientation de la p-value unilatérale comprise.
La mesure complète est mise en cache avant analyse ; l'analyse publiée est le second passage.
