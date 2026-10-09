# Axe A — le stop et la géométrie des sorties (VWAP pullback, BTCUSD)

**Date :** 2026-10-09 · **Statut :** mesuré sur le jeu complet (59 999 bougies), **aucune
promotion**, `max_mode` reste `SIGNAL` · **Périmètre :** `docs/research/vwap-tuning/`,
`scripts/backtest/tune_stop_*.py`. Aucune ligne de `src/` n'a été écrite par cet axe.

**La réponse, en une phrase : PF > 1,0 — NON.** Une seule configuration sur seize le franchit sur
le jeu complet, de **1,0073**, et elle ne tient ni au découpage (0,9190 sur la première moitié du
jeu, 1,1016 sur la seconde) ni au coût (0,9446 sous le spread observé). Le franchissement est une
moyenne entre une période perdante et une période gagnante, pas une propriété de la règle.

**Et la prémisse de l'axe est réfutée.** « Le stop à 1,5 ATR est probablement trop large » : c'est
l'inverse qui est mesuré. Resserrer le stop dégrade le profit factor, et à 0,5 ATR la règle perd
2 876,58 € — le pire résultat du balayage.

---

## 1. La prémisse était circulaire

L'axe partait de ce raisonnement : « les perdants touchent le stop à **1,192 R** en médiane, donc
le stop est **trop large** ». Ce nombre ne peut pas mesurer la largeur du stop : un trade qui
touche son stop a, **par définition**, une excursion adverse d'au moins 1 R. Mesuré sur le jeu
complet, sur quatre géométries très différentes :

| Configuration (59 999 bougies) | Perdants : MAE méd. | Perdants : MFE méd. | Gagnants : MAE méd. | Gagnants : MFE méd. | Réussite |
|---|---|---|---|---|---|
| TP 0,8/1,5, partiel (spec opérateur) | 1,2114 R | 0,2404 R | 0,3678 R | 1,5265 R | 56,32 % |
| TP 0,8/1,5, sortie unique | 1,2102 R | 0,2438 R | 0,3264 R | 0,9882 R | 56,05 % |
| TP 1,5/3,0, partiel | 1,2195 R | 0,3874 R | 0,4462 R | 3,0199 R | 40,20 % |
| TP 1,5/3,0, sortie unique | 1,2213 R | 0,4024 R | 0,3679 R | 1,7454 R | 40,27 % |

Quatre géométries, quatre fois **le même nombre** à un centième près. La MAE des perdants mesure
« le stop a été touché », jamais « le stop était mal dimensionné ». Ce qui informe, ce sont les
deux autres colonnes :

1. **les perdants ne vont nulle part** — 0,24 R de MFE médiane à la géométrie de l'opérateur,
   0,39 R à TP 1,5/3,0. Ils sont perdants presque immédiatement ;
2. **les gagnants vont jusqu'à leur cible** — 3,02 R de MFE médiane quand TP2 est à 3,0 R,
   1,53 R quand TP2 est à 1,5 R.

Le problème n'est donc pas la largeur du stop. Il est dans l'arbitrage entre ce que les gagnants
laissent sur la table et ce que les perdants coûtent — et la mesure ci-dessous tranche cet
arbitrage dans le sens des **objectifs plus lointains**, pas du stop plus serré.

## 2. Méthode, et les quatre pièges qu'elle écarte

**Deux étages, parce qu'une mesure coûte cher.** Un backtest sur 59 999 bougies prend **5 min 45 à
7 min 20** de temps réel sur cette machine (mesuré : 345 s à 442 s par configuration). L'étage 1
(exploration) tourne donc large sur **4 999 bougies** pour éliminer ; l'étage 2 (décision) tourne
sur le **jeu complet** et c'est le seul qui engage. Chaque ligne porte sa fenêtre : aucune mesure
d'exploration ne peut être lue comme une mesure de décision, et le tableau de l'étage 1 est
explicitement étiqueté « élimination ».

**Le jeu de signaux est invariant, et c'est vérifié sur les mesures.** Toutes les configurations
gardent `pullback_atr=0,4`, `entry_zone_atr=0,1`, les EMA 20/50 et `slope_window=10` : seuls
changent le stop, les objectifs et la gestion de position. `tune_stop_analysis.py` le contrôle sur
les compteurs produits — **149 signaux** sur les cinq stops de l'exploration, **1 973 signaux** sur
le jeu complet, « 0 groupe instable ». Un écart de profit factor est donc attribuable à la
géométrie et non à un changement de détection.

**La source a été gelée, puis l'équivalence vérifiée.** Pendant le premier passage,
`src/tradingagent/strategies/library/vwap_pullback.py` était **en cours d'édition par l'axe B** :
50 mesures d'affilée ont rendu « 0 trade » avec `TypeError: must be real number, not str` sur
chaque barre. Ces mesures ne disaient rien de la règle, elles disaient que le fichier changeait ;
elles ont été jetées, la règle a été gelée au commit `bfa0e65`
(`vwap_pullback_pinned_bfa0e65.py`, sha256 `17d48adf…`), et toutes les mesures de décision ont été
refaites **sur la source vivante** après le commit `fa76d2a`. Les trois références donnent le même
résultat au chiffre près sur les deux sources :

<!-- DEBUT:controles -->
3 mesure(s).

| Configuration | Geometrie | Harnais | Barres | Trades | Reussite | Net EUR | PF | DD EUR | MAE gagnants | MFE gagnants | MAE perdants | MFE perdants |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `stop1.5_tp1.5-3.0_partiel` | stop 1,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 883 | 40,2 % | -392,74 | 0,9314 | 566,80 | 0,45 | 3,02 | 1,22 | 0,39 |
| `ref_stop1.5_tp0.8-1.5_partiel` | stop 1,5 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 59999 | 1092 | 56,3 % | -850,01 | 0,8362 | 948,00 | 0,37 | 1,53 | 1,21 | 0,24 |
| `ref_stop1.5_tp0.8-1.5_sortie-unique` | stop 1,5 ATR, TP 0,8/1,5 | sortie unique | 59999 | 1133 | 56,0 % | -934,23 | 0,8275 | 1041,75 | 0,33 | 0,99 | 1,21 | 0,24 |

<!-- FIN:controles -->

**Le harnais a changé deux fois pendant la session, et il est désormais nommé aussi.** Un autre
axe a modifié `src/tradingagent/backtest/harness.py` à 16:21:51 (parité de la porte `entry_zone`,
modèle de spread), alors que les deux lots de mesure étaient partis à 16:06:16. Python charge un
module à l'import : les lots restent donc cohérents sur le harnais de `338da61`, et le contrôle
relancé sur l'arbre de travail **reproduit 883 trades et PF 0,9314 au chiffre près**. Le harnais a
ensuite changé une seconde fois, entre les deux moitiés de jeu — et le contrôle interne le plus
utile est là : la même configuration mesurée sous trois révisions du harnais donne **0,9314 ;
0,9314 ; 0,9326 ; 0,9333**. Le chemin par défaut ne bouge pas. Chaque ligne de mesure porte
maintenant l'empreinte du harnais en plus de celle de la règle.

**Un confondant subsiste, et il est réel : les places occupées.** Le harnais tourne avec
`max_concurrent_positions=1` : un signal n'est exécuté que si aucune position n'est ouverte. Or un
stop plus serré libère la place plus vite. À jeu de signaux **strictement constant** (149 signaux,
4 999 bougies), le nombre de trades exécutés va de **48 à 108** selon la configuration ; sur le jeu
complet, de **883 à 1 436**. Deux lignes du tableau ne comparent donc pas les mêmes trades : elles
comparent deux échantillons tirés du même flux de signaux, par une règle de sélection qui dépend
elle-même de la géométrie mesurée. C'est écrit ici pour que personne ne lise le tableau comme une
comparaison contrôlée trade à trade.

**Coûts, les deux modèles.** Modèle du dépôt : `spread = close₀ × 5·10⁻⁵`,
`slippage = close₀ × 2·10⁻⁵`, `commission = 0,50 €` par trade. Le prix de référence étant celui de
la première bougie de la fenêtre, les coûts absolus varient d'une fenêtre à l'autre (sur le jeu
complet : spread **5,254122**, slippage **2,101649**). Les finalistes ont en plus été re-mesurés
sous le **spread réellement observé, 18,424 $**, soit 2,2 points de base contre 0,5 pour le modèle
— un facteur 4,5.

## 3. Étage 1 — exploration sur 4 999 bougies (élimination seulement)

Ces 50 mesures ont servi à éliminer, pas à décider. Le meilleur PF y vaut 1,3396 sur **48 trades** :
à cette taille d'échantillon, un classement n'est pas une information. Deux choses en sont
sorties, et l'une a été démentie par l'étage 2 (voir § 5).

<!-- DEBUT:exploration -->
Source : copie gelee `bfa0e65` — le champ `source` n'existait pas encore. 50 mesure(s).

| Configuration | Geometrie | Harnais | Barres | Trades | Reussite | Net EUR | PF | DD EUR | MAE gagnants | MFE gagnants | MAE perdants | MFE perdants |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `stop2.0_tp2.0-4.0_partiel` | stop 2,0 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 4999 | 48 | 37,5 % | 107,76 | 1,3396 | 65,86 | 0,58 | 4,13 | 1,26 | 0,55 |
| `stop2.0_tp1.5-3.0_partiel` | stop 2,0 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 4999 | 55 | 43,6 % | 73,52 | 1,2242 | 87,35 | 0,49 | 3,02 | 1,20 | 0,42 |
| `stop1.5_tp3.0-6.0_partiel` | stop 1,5 ATR, TP 3,0/6,0 | partiel 50/50 + break-even | 4999 | 49 | 28,6 % | 64,77 | 1,1744 | 134,87 | 0,54 | 6,13 | 1,34 | 0,87 |
| `stop1.5_tp0.8-1.5_sortie-unique` | stop 1,5 ATR, TP 0,8/1,5 | sortie unique | 4999 | 86 | 62,8 % | 44,95 | 1,1309 | 101,12 | 0,40 | 0,96 | 1,22 | 0,25 |
| `stop1.2_tp2.0-4.0_partiel` | stop 1,2 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 4999 | 62 | 35,5 % | 45,42 | 1,1050 | 111,83 | 0,65 | 4,24 | 1,41 | 0,63 |
| `stop1.2_tp1.0-2.0_sortie-unique` | stop 1,2 ATR, TP 1,0/2,0 | sortie unique | 4999 | 88 | 56,8 % | 42,99 | 1,1044 | 105,98 | 0,41 | 1,20 | 1,32 | 0,31 |
| `stop2.0_tp2.0-4.0_sortie-unique` | stop 2,0 ATR, TP 2,0/4,0 | sortie unique | 4999 | 53 | 37,7 % | 34,56 | 1,0990 | 75,83 | 0,49 | 2,27 | 1,30 | 0,45 |
| `stop1.5_tp2.0-4.0_partiel` | stop 1,5 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 4999 | 58 | 34,5 % | 35,42 | 1,0871 | 127,27 | 0,51 | 4,08 | 1,34 | 0,57 |
| `stop1.2_tp0.8-1.5_sortie-unique` | stop 1,2 ATR, TP 0,8/1,5 | sortie unique | 4999 | 91 | 60,4 % | 1,11 | 1,0028 | 92,09 | 0,37 | 1,07 | 1,32 | 0,30 |
| `stop1.2_tp2.0-4.0_sortie-unique` | stop 1,2 ATR, TP 2,0/4,0 | sortie unique | 4999 | 67 | 35,8 % | -8,03 | 0,9827 | 111,81 | 0,60 | 2,13 | 1,41 | 0,62 |
| `stop1.2_tp1.0-2.0_partiel` | stop 1,2 ATR, TP 1,0/2,0 | partiel 50/50 + break-even | 4999 | 83 | 56,6 % | -8,39 | 0,9785 | 107,43 | 0,54 | 1,54 | 1,34 | 0,30 |
| `stop1.2_tp1.5-3.0_holding16` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even + holding 16 | 4999 | 79 | 41,8 % | -10,84 | 0,9753 | 122,17 | 0,45 | 2,15 | 1,30 | 0,56 |
| `stop1.2_tp1.5-3.0_sortie-unique` | stop 1,2 ATR, TP 1,5/3,0 | sortie unique | 4999 | 73 | 42,5 % | -18,06 | 0,9602 | 128,32 | 0,44 | 1,76 | 1,36 | 0,61 |
| `stop2.0_tp1.0-2.0_sortie-unique` | stop 2,0 ATR, TP 1,0/2,0 | sortie unique | 4999 | 67 | 52,2 % | -14,15 | 0,9582 | 83,25 | 0,39 | 1,17 | 1,21 | 0,38 |
| `stop2.0_tp1.5-3.0_sortie-unique` | stop 2,0 ATR, TP 1,5/3,0 | sortie unique | 4999 | 58 | 41,4 % | -18,43 | 0,9488 | 84,58 | 0,42 | 1,68 | 1,20 | 0,42 |
| `stop2.0_tp0.8-1.5_sortie-unique` | stop 2,0 ATR, TP 0,8/1,5 | sortie unique | 4999 | 73 | 57,5 % | -22,65 | 0,9310 | 118,54 | 0,36 | 0,91 | 1,20 | 0,33 |
| `stop0.8_tp1.5-3.0_sortie-unique` | stop 0,8 ATR, TP 1,5/3,0 | sortie unique | 4999 | 99 | 43,4 % | -47,10 | 0,9264 | 160,96 | 0,50 | 1,77 | 1,45 | 0,45 |
| `stop1.2_tp1.5-3.0_swing2` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even + trailing structure 2 | 4999 | 80 | 37,5 % | -31,87 | 0,9234 | 111,37 | 0,42 | 2,50 | 1,16 | 0,61 |
| `stop0.8_tp1.0-2.0_sortie-unique` | stop 0,8 ATR, TP 1,0/2,0 | sortie unique | 4999 | 103 | 54,4 % | -45,49 | 0,9163 | 106,69 | 0,48 | 1,42 | 1,54 | 0,38 |
| `stop1.2_tp0.8-1.5_partiel` | stop 1,2 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 4999 | 88 | 60,2 % | -35,03 | 0,9077 | 117,40 | 0,40 | 1,44 | 1,33 | 0,31 |
| `stop1.5_tp0.8-1.5_partiel` | stop 1,5 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 4999 | 81 | 61,7 % | -30,89 | 0,9071 | 119,67 | 0,48 | 1,23 | 1,22 | 0,24 |
| `stop1.2_tp1.5-3.0_holding48` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even + holding 48 | 4999 | 73 | 41,1 % | -44,92 | 0,9033 | 140,64 | 0,49 | 2,69 | 1,41 | 0,60 |
| `stop1.5_tp1.0-2.0_sortie-unique` | stop 1,5 ATR, TP 1,0/2,0 | sortie unique | 4999 | 78 | 51,3 % | -40,33 | 0,9009 | 119,61 | 0,35 | 1,15 | 1,25 | 0,45 |
| `stop1.2_tp1.5-3.0_partiel` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 4999 | 69 | 40,6 % | -48,28 | 0,8910 | 150,44 | 0,54 | 2,89 | 1,41 | 0,62 |
| `stop1.0_tp1.0-2.0_sortie-unique` | stop 1,0 ATR, TP 1,0/2,0 | sortie unique | 4999 | 96 | 52,1 % | -58,94 | 0,8842 | 125,41 | 0,41 | 1,28 | 1,34 | 0,34 |
| `stop2.0_tp1.0-2.0_partiel` | stop 2,0 ATR, TP 1,0/2,0 | partiel 50/50 + break-even | 4999 | 62 | 48,4 % | -41,84 | 0,8764 | 106,89 | 0,46 | 2,03 | 1,21 | 0,38 |
| `stop1.5_tp1.5-3.0_partiel` | stop 1,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 4999 | 60 | 36,7 % | -50,27 | 0,8763 | 127,15 | 0,52 | 3,01 | 1,32 | 0,54 |
| `stop0.8_tp0.8-1.5_sortie-unique` | stop 0,8 ATR, TP 0,8/1,5 | sortie unique | 4999 | 108 | 59,3 % | -67,50 | 0,8681 | 100,46 | 0,40 | 1,21 | 1,49 | 0,38 |
| `stop0.8_tp0.8-1.5_partiel` | stop 0,8 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 4999 | 102 | 58,8 % | -74,33 | 0,8484 | 134,97 | 0,53 | 1,59 | 1,54 | 0,36 |
| `stop1.0_tp0.8-1.5_sortie-unique` | stop 1,0 ATR, TP 0,8/1,5 | sortie unique | 4999 | 100 | 57,0 % | -75,73 | 0,8413 | 112,99 | 0,39 | 1,16 | 1,34 | 0,32 |
| `stop1.2_tp1.5-3.0_swing3_partiel_off` | stop 1,2 ATR, TP 1,5/3,0 | sortie unique + trailing structure 3 | 4999 | 82 | 35,4 % | -82,07 | 0,8281 | 132,53 | 0,37 | 1,70 | 1,19 | 0,60 |
| `stop1.5_tp1.5-3.0_swing2` | stop 1,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even + trailing structure 2 | 4999 | 76 | 30,3 % | -64,51 | 0,8260 | 115,73 | 0,33 | 2,48 | 1,07 | 0,56 |
| `stop0.8_tp1.0-2.0_partiel` | stop 0,8 ATR, TP 1,0/2,0 | partiel 50/50 + break-even | 4999 | 98 | 54,1 % | -92,32 | 0,8252 | 144,52 | 0,52 | 1,95 | 1,55 | 0,38 |
| `stop0.8_tp2.0-4.0_sortie-unique` | stop 0,8 ATR, TP 2,0/4,0 | sortie unique | 4999 | 88 | 33,0 % | -128,24 | 0,8087 | 217,93 | 0,52 | 2,25 | 1,40 | 0,65 |
| `stop1.5_tp2.0-4.0_sortie-unique` | stop 1,5 ATR, TP 2,0/4,0 | sortie unique | 4999 | 61 | 31,1 % | -86,05 | 0,8083 | 137,13 | 0,51 | 2,21 | 1,30 | 0,57 |
| `stop1.5_tp1.0-2.0_partiel` | stop 1,5 ATR, TP 1,0/2,0 | partiel 50/50 + break-even | 4999 | 72 | 50,0 % | -74,52 | 0,8067 | 134,25 | 0,43 | 1,78 | 1,25 | 0,45 |
| `stop1.0_tp2.0-4.0_sortie-unique` | stop 1,0 ATR, TP 2,0/4,0 | sortie unique | 4999 | 79 | 31,6 % | -121,47 | 0,7956 | 182,64 | 0,42 | 2,27 | 1,34 | 0,52 |
| `stop0.8_tp1.5-3.0_partiel` | stop 0,8 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 4999 | 93 | 41,9 % | -133,70 | 0,7838 | 181,48 | 0,53 | 2,43 | 1,45 | 0,45 |
| `stop1.2_tp1.5-3.0_swing3` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even + trailing structure 3 | 4999 | 79 | 36,7 % | -100,41 | 0,7831 | 139,67 | 0,41 | 2,42 | 1,22 | 0,58 |
| `stop2.0_tp0.8-1.5_partiel` | stop 2,0 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 4999 | 69 | 53,6 % | -75,88 | 0,7761 | 138,33 | 0,40 | 1,50 | 1,18 | 0,34 |
| `stop1.5_tp1.5-3.0_sortie-unique` | stop 1,5 ATR, TP 1,5/3,0 | sortie unique | 4999 | 65 | 36,9 % | -98,63 | 0,7751 | 127,16 | 0,48 | 1,66 | 1,29 | 0,52 |
| `stop1.0_tp1.5-3.0_sortie-unique` | stop 1,0 ATR, TP 1,5/3,0 | sortie unique | 4999 | 87 | 37,9 % | -135,68 | 0,7719 | 164,37 | 0,44 | 1,72 | 1,34 | 0,52 |
| `stop1.0_tp1.5-3.0_swing2` | stop 1,0 ATR, TP 1,5/3,0 | partiel 50/50 + break-even + trailing structure 2 | 4999 | 87 | 35,6 % | -127,70 | 0,7591 | 165,60 | 0,44 | 2,68 | 1,24 | 0,49 |
| `stop1.0_tp0.8-1.5_partiel` | stop 1,0 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 4999 | 97 | 56,7 % | -115,29 | 0,7543 | 140,35 | 0,43 | 1,49 | 1,34 | 0,31 |
| `stop1.0_tp1.0-2.0_partiel` | stop 1,0 ATR, TP 1,0/2,0 | partiel 50/50 + break-even | 4999 | 93 | 50,5 % | -130,66 | 0,7433 | 160,92 | 0,43 | 1,81 | 1,34 | 0,35 |
| `stop1.0_tp1.5-3.0_swing3` | stop 1,0 ATR, TP 1,5/3,0 | partiel 50/50 + break-even + trailing structure 3 | 4999 | 87 | 35,6 % | -156,45 | 0,7231 | 194,35 | 0,44 | 2,68 | 1,28 | 0,49 |
| `stop0.8_tp2.0-4.0_partiel` | stop 0,8 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 4999 | 82 | 30,5 % | -189,69 | 0,7078 | 239,74 | 0,56 | 3,86 | 1,40 | 0,65 |
| `stop1.5_tp1.5-3.0_swing3` | stop 1,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even + trailing structure 3 | 4999 | 75 | 28,0 % | -125,29 | 0,6997 | 170,47 | 0,33 | 2,86 | 1,11 | 0,54 |
| `stop1.0_tp1.5-3.0_partiel` | stop 1,0 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 4999 | 82 | 36,6 % | -183,47 | 0,6800 | 211,81 | 0,45 | 2,66 | 1,34 | 0,49 |
| `stop1.0_tp2.0-4.0_partiel` | stop 1,0 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 4999 | 73 | 28,8 % | -185,19 | 0,6767 | 238,97 | 0,53 | 3,51 | 1,34 | 0,58 |

<!-- FIN:exploration -->

## 4. Étage 2 — décision sur le jeu complet (59 999 bougies)

Seize configurations, toutes à `pullback_atr=0,4` et `entry_zone_atr=0,1`, donc **1 973 signaux
identiques** d'une ligne à l'autre.

<!-- DEBUT:decision -->
Source : source vivante `fa76d2a`, harnais `338da61`, couts du depot. 16 mesure(s) sur 59 999 bougies.

| Configuration | Geometrie | Harnais | Barres | Trades | Reussite | Net EUR | PF | DD EUR | MAE gagnants | MFE gagnants | MAE perdants | MFE perdants |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `stop1.5_tp3.0-6.0_partiel` | stop 1,5 ATR, TP 3,0/6,0 | partiel 50/50 + break-even | 59999 | 694 | 26,8 % | 39,85 | 1,0073 | 409,66 | 0,51 | 5,95 | 1,23 | 0,60 |
| `stop2.0_tp2.0-4.0_partiel` | stop 2,0 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 59999 | 629 | 34,0 % | -164,40 | 0,9629 | 508,37 | 0,45 | 3,99 | 1,17 | 0,52 |
| `stop1.2_tp1.5-3.0_partiel` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 1011 | 41,9 % | -306,59 | 0,9527 | 694,05 | 0,45 | 3,00 | 1,27 | 0,39 |
| `stop1.5_tp2.0-4.0_partiel` | stop 1,5 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 59999 | 794 | 34,1 % | -279,80 | 0,9506 | 655,65 | 0,49 | 3,98 | 1,22 | 0,47 |
| `stop2.0_tp1.5-3.0_partiel` | stop 2,0 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 699 | 40,3 % | -268,29 | 0,9397 | 532,12 | 0,45 | 2,99 | 1,18 | 0,42 |
| `stop1.0_tp2.0-4.0_partiel` | stop 1,0 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 59999 | 1033 | 34,6 % | -484,01 | 0,9363 | 662,89 | 0,49 | 3,99 | 1,32 | 0,50 |
| `stop1.5_tp1.5-3.0_partiel` | stop 1,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 883 | 40,2 % | -392,74 | 0,9314 | 566,80 | 0,45 | 3,02 | 1,22 | 0,39 |
| `stop2.0_tp1.0-2.0_partiel` | stop 2,0 ATR, TP 1,0/2,0 | partiel 50/50 + break-even | 59999 | 834 | 50,1 % | -366,40 | 0,9177 | 476,23 | 0,39 | 2,02 | 1,19 | 0,33 |
| `stop0.8_tp1.5-3.0_partiel` | stop 0,8 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 1239 | 43,1 % | -891,28 | 0,8926 | 996,76 | 0,51 | 2,97 | 1,47 | 0,43 |
| `stop1.0_tp1.5-3.0_partiel` | stop 1,0 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 1125 | 41,6 % | -843,61 | 0,8861 | 969,48 | 0,48 | 2,96 | 1,33 | 0,39 |
| `stop1.5_tp1.5-3.0_sortie-unique` | stop 1,5 ATR, TP 1,5/3,0 | sortie unique | 59999 | 951 | 40,3 % | -812,74 | 0,8680 | 928,44 | 0,37 | 1,75 | 1,22 | 0,40 |
| `stop1.5_tp0.8-1.5_swing2` | stop 1,5 ATR, TP 0,8/1,5 | partiel 50/50 + break-even + trailing structure 2 | 59999 | 1193 | 49,9 % | -705,58 | 0,8553 | 806,38 | 0,33 | 1,52 | 1,05 | 0,27 |
| `stop1.5_tp0.8-1.5_partiel` | stop 1,5 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 59999 | 1092 | 56,3 % | -850,01 | 0,8362 | 948,00 | 0,37 | 1,53 | 1,21 | 0,24 |
| `stop1.5_tp0.8-1.5_sortie-unique` | stop 1,5 ATR, TP 0,8/1,5 | sortie unique | 59999 | 1133 | 56,0 % | -934,23 | 0,8275 | 1041,75 | 0,33 | 0,99 | 1,21 | 0,24 |
| `stop1.5_tp0.8-1.5_swing3` | stop 1,5 ATR, TP 0,8/1,5 | partiel 50/50 + break-even + trailing structure 3 | 59999 | 1166 | 51,1 % | -887,45 | 0,8247 | 997,32 | 0,34 | 1,52 | 1,10 | 0,26 |
| `stop0.5_tp1.5-3.0_partiel` | stop 0,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 1436 | 43,2 % | -2876,58 | 0,7422 | 2979,51 | 0,63 | 3,03 | 1,91 | 0,57 |

<!-- FIN:decision -->

### 4.1 Le stop : la mesure répond « non », et dans l'autre sens

À TP 1,5/3,0 et sortie partielle, en ne faisant varier **que** le stop :

| Stop (ATR) | Trades | Réussite | Net | **PF** | Drawdown | MAE gagnants | MFE gagnants |
|---|---|---|---|---|---|---|---|
| **0,5** | 1 436 | 43,18 % | **−2 876,58 €** | **0,7422** | 2 979,51 € | 0,6257 R | 3,0335 R |
| 0,8 | 1 239 | 43,10 % | −891,28 € | 0,8926 | 996,76 € | 0,5141 R | 2,9652 R |
| 1,0 | 1 125 | 41,60 % | −843,61 € | 0,8861 | 969,48 € | 0,4775 R | 2,9556 R |
| **1,2** | 1 011 | 41,94 % | **−306,59 €** | **0,9527** | 694,05 € | 0,4485 R | 2,9957 R |
| 1,5 (référence) | 883 | 40,20 % | −392,74 € | 0,9314 | 566,80 € | 0,4462 R | 3,0199 R |
| 2,0 | 699 | 40,34 % | −268,29 € | 0,9397 | 532,12 € | 0,4466 R | 2,9943 R |

**Le profit factor monte avec le stop jusqu'à 1,2 ATR, puis reste plat.** Serrer sous 1,2 dégrade
les deux chiffres qui comptent, et pas marginalement : à 0,5 ATR la règle exécute 1 436 trades
(contre 883) et perd 2 876,58 €, avec un drawdown de 2 979,51 €. Deux mécanismes se lisent dans le
tableau :

- les gagnants prennent **plus de chaleur en R** quand le stop se resserre : leur MAE médiane
  passe de 0,4466 R (stop 2,0) à 0,6257 R (stop 0,5). Un stop serré ne coupe pas les perdants, il
  coupe les gagnants ;
- les perdants dépassent leur stop de plus en plus, en R : MAE médiane 1,1824 R au stop 2,0 contre
  **1,9114 R** au stop 0,5. Le dépassement d'une bougie est une distance en points à peu près
  constante ; divisée par un R plus petit, elle gonfle.

Le meilleur compromis du balayage est **stop 1,2 ATR**, et le plateau entre 1,2 et 2,0 ATR est
large : la règle n'est pas sensible au stop dans cette zone, ce qui est une bonne propriété — mais
aucun point du plateau ne franchit 1,0.

### 4.2 Les objectifs : un gradient monotone, et c'est là que tout se joue

À stop 1,5 ATR et sortie partielle, en ne faisant varier **que** les objectifs :

| TP1 / TP2 | Trades | Réussite | Net | **PF** | MFE médiane des gagnants |
|---|---|---|---|---|---|
| 0,8 / 1,5 (spec opérateur) | 1 092 | 56,32 % | −850,01 € | 0,8362 | 1,5265 R |
| 1,5 / 3,0 | 883 | 40,20 % | −392,74 € | 0,9314 | 3,0199 R |
| 2,0 / 4,0 | 794 | 34,13 % | −279,80 € | 0,9506 | 3,9765 R |
| **3,0 / 6,0** | 694 | 26,80 % | **+39,85 €** | **1,0073** | 5,9459 R |

**Le profit factor monte de façon monotone à mesure que les objectifs s'éloignent** — 0,8362 ;
0,9314 ; 0,9506 ; 1,0073 — et le taux de réussite s'effondre dans le même mouvement, de 56,32 % à
26,80 %, sans que cela suffise à compenser. C'est la signature d'une règle qui a raison sur la
direction mais qui encaisse trop tôt : ses gagnants vont en médiane à **5,95 R** quand on lui en
demande 6, et à **1,53 R** quand on lui en demande 1,5.

Dit autrement : la spec de l'opérateur (TP1 0,8 R) coupe ses gagnants à 1,5 R de MFE médiane, et
c'est ce qui la maintient à 0,8362 malgré 56 % de réussite.

### 4.3 Le mode de sortie : le partiel 50/50 + break-even paie sur le jeu complet

| Géométrie | Partiel 50/50 + BE | Sortie unique | Écart |
|---|---|---|---|
| TP 0,8/1,5, stop 1,5 | 0,8362 | 0,8275 | **+0,0087** |
| TP 1,5/3,0, stop 1,5 | 0,9314 | 0,8680 | **+0,0634** |

Sur le jeu complet, le partiel gagne les deux paires. C'est l'inverse de ce que l'exploration
disait (§ 5), et c'est la réponse à la question « avec et sans gestion de position » : **avec**.

### 4.4 Les briques de gestion : le trailing sur structure coûte, sauf à force 2

| Configuration (géométrie de l'opérateur) | Trades | Réussite | Net | **PF** |
|---|---|---|---|---|
| Référence, sans trailing | 1 092 | 56,32 % | −850,01 € | 0,8362 |
| + trailing sur structure, force **2** | 1 193 | 49,87 % | **−705,58 €** | **0,8553** |
| + trailing sur structure, force **3** | 1 166 | 51,11 % | −887,45 € | 0,8247 |

C'est la brique que le lead voulait voir testée, et elle n'avait jamais été utilisée sur cette
règle. Résultat : **la force 2 améliore** (+0,0191 de PF, +144,43 € de net), **la force 3 dégrade**
(−0,0115). L'amélioration est réelle mais faible, elle porte sur une seule géométrie, et deux
forces testées ne font pas une courbe. À retenir comme piste, pas comme résultat.

## 5. Le seul franchissement de PF 1,0, et pourquoi il ne tient pas

`stop1.5_tp3.0-6.0_partiel` : stop 1,5 ATR, TP1 3 R, TP2 6 R, partiel 50/50 + break-even.
**694 trades, 26,80 % de réussite, net +39,85 €, PF 1,0073, drawdown 409,66 €.**

Le chiffre est au-dessus de 1,0. Il ne vaut rien, et la validation par moitiés de jeu le montre :

| Configuration | Première moitié (0:30 000) | Seconde moitié (30 000:59 999) | Jeu complet |
|---|---|---|---|
| **TP 3,0/6,0, stop 1,5** | 332 trades, **PF 0,9190**, −218,73 € | 356 trades, **PF 1,1016**, +277,54 € | 694 trades, PF 1,0073, +39,85 € |
| TP 1,5/3,0, stop 1,2 | 518 trades, **PF 1,0277**, +87,60 € | 486 trades, **PF 0,8903**, −358,42 € | 1 011 trades, PF 0,9527, −306,59 € |
| TP 1,5/3,0, stop 1,5 (contrôle) | 448 trades, PF 0,9326, −194,56 € | 428 trades, PF 0,9333, −186,31 € | 883 trades, PF 0,9314, −392,74 € |

<!-- DEBUT:fenetres -->
6 mesure(s), deux moities disjointes, deux revisions du harnais.

| Configuration | Geometrie | Harnais | Barres | Trades | Reussite | Net EUR | PF | DD EUR | MAE gagnants | MFE gagnants | MAE perdants | MFE perdants |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `stop1.5_tp3.0-6.0_partiel` | stop 1,5 ATR, TP 3,0/6,0 | partiel 50/50 + break-even | 29999 | 356 | 28,9 % | 277,54 | 1,1016 | 228,00 | 0,54 | 5,93 | 1,24 | 0,61 |
| `stop1.2_tp1.5-3.0_partiel` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 30000 | 518 | 44,6 % | 87,60 | 1,0277 | 278,53 | 0,48 | 2,95 | 1,27 | 0,42 |
| `stop1.5_tp1.5-3.0_partiel` | stop 1,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 29999 | 428 | 40,0 % | -186,31 | 0,9333 | 345,04 | 0,46 | 3,03 | 1,24 | 0,41 |
| `stop1.5_tp1.5-3.0_partiel` | stop 1,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 30000 | 448 | 40,4 % | -194,56 | 0,9326 | 353,36 | 0,44 | 3,02 | 1,21 | 0,37 |
| `stop1.5_tp3.0-6.0_partiel` | stop 1,5 ATR, TP 3,0/6,0 | partiel 50/50 + break-even | 30000 | 332 | 24,7 % | -218,73 | 0,9190 | 350,23 | 0,50 | 5,96 | 1,21 | 0,60 |
| `stop1.2_tp1.5-3.0_partiel` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 29999 | 486 | 39,1 % | -358,42 | 0,8903 | 467,18 | 0,43 | 3,07 | 1,27 | 0,38 |

<!-- FIN:fenetres -->

Le seul franchissement est donc **une moyenne** : 1,0073 sur le jeu complet, entre 0,9190 et 1,1016
sur ses deux moitiés — dont la moyenne simple vaut 1,0103. La configuration gagne 277,54 € sur la
seconde moitié et perd 218,73 € sur la première. À l'inverse, le contrôle TP 1,5/3,0 donne
**0,9326 et 0,9333** — 0,0007 d'écart entre les deux moitiés, ce qui est remarquablement stable, et
une bien meilleure base que n'importe quel réglage à 0,95.

**Ce que cela dit de la méthode de l'axe.** Avec seize configurations mesurées sur une seule
fenêtre, la meilleure est retenue par un biais de sélection, et ce biais suffit à produire ici un
franchissement qui n'existe pas. Le contrôle par moitiés de jeu coûte deux mesures par candidat,
et il est la seule raison pour laquelle ce rapport peut dire « non » au lieu d'annoncer 1,0073.

## 6. Sous le spread réellement observé

Toutes les mesures ci-dessus tournent sous le modèle de coûts du dépôt, **0,5 point de base**,
alors que le spread observé sur BTCUSD vaut **18,424 $, soit 2,2 points de base — 4,5 fois le
modèle**. Les finalistes ont donc été re-mesurés sous le spread observé, toutes choses égales.

<!-- DEBUT:spread -->
Source : spread impose a 18,424 $, reste du modele du depot. 7 mesure(s).

| Configuration | Geometrie | Harnais | Barres | Trades | Reussite | Net EUR | PF | DD EUR | MAE gagnants | MFE gagnants | MAE perdants | MFE perdants |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `stop1.5_tp3.0-6.0_partiel` | stop 1,5 ATR, TP 3,0/6,0 | partiel 50/50 + break-even | 59999 | 695 | 26,8 % | -310,49 | 0,9446 | 521,02 | 0,52 | 5,77 | 1,22 | 0,57 |
| `stop2.0_tp2.0-4.0_partiel` | stop 2,0 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 59999 | 627 | 34,0 % | -384,28 | 0,9142 | 585,61 | 0,46 | 3,92 | 1,18 | 0,50 |
| `stop1.5_tp1.5-3.0_partiel` | stop 1,5 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 886 | 40,3 % | -763,60 | 0,8693 | 894,81 | 0,46 | 2,95 | 1,21 | 0,35 |
| `stop1.2_tp1.5-3.0_partiel` | stop 1,2 ATR, TP 1,5/3,0 | partiel 50/50 + break-even | 59999 | 1014 | 42,0 % | -868,06 | 0,8691 | 1123,70 | 0,46 | 2,88 | 1,27 | 0,36 |
| `stop2.0_tp1.0-2.0_partiel` | stop 2,0 ATR, TP 1,0/2,0 | partiel 50/50 + break-even | 59999 | 837 | 50,1 % | -665,25 | 0,8534 | 762,84 | 0,39 | 1,97 | 1,19 | 0,31 |
| `stop1.0_tp2.0-4.0_partiel` | stop 1,0 ATR, TP 2,0/4,0 | partiel 50/50 + break-even | 59999 | 1037 | 34,5 % | -1187,32 | 0,8482 | 1353,35 | 0,51 | 3,87 | 1,31 | 0,46 |
| `stop1.5_tp0.8-1.5_partiel` | stop 1,5 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 59999 | 1093 | 56,3 % | -1313,06 | 0,7523 | 1403,14 | 0,38 | 1,47 | 1,21 | 0,21 |

<!-- FIN:spread -->

**Le classement tient, les valeurs non.** Toutes les configurations perdent entre 0,049 et 0,088 de
profit factor. La spec opérateur passe de 0,8362 à **0,7523**, le contrôle de 0,9314 à **0,8693**,
et le meilleur du balayage (stop 1,2, TP 1,5/3,0) de 0,9527 à **0,8691** — les deux premiers du
classement se rejoignent à deux dix-millièmes près, ce qui veut dire que le classement **ne tient
que sur la moitié haute**, et qu'à coûts réalistes il n'y a plus de « meilleur » stop à choisir
entre 1,2 et 1,5.

Le candidat à 1,0073 a été re-mesuré dans les mêmes conditions : **PF 0,9446**, net −310,49 € sur
695 trades. **Le franchissement disparaît donc deux fois plutôt qu'une** — une fois par le
découpage en moitiés, une fois par le coût.

Ce qui compte n'est pas le nombre de trades mais la **taille relative du coût dans l'unité de
risque R** : un R large (stop 1,5 ATR, objectifs à 6 R) ne protège pas de l'aller-retour payé à
chaque entrée. La conclusion ne change pas — sous coûts réalistes, la règle est à **0,7523** dans
sa spécification d'origine et à **0,9446** dans sa meilleure géométrie mesurée sur le jeu complet.

## 7. Ce que l'exploration sur 4 999 bougies a fait rater

Le petit échantillon a donné un verdict **inversé** sur la seule question que l'étage 2 devait
trancher, et il vaut la peine de l'écrire pour les prochains balayages :

| Question | Réponse sur 4 999 bougies | Réponse sur 59 999 bougies |
|---|---|---|
| Sortie partielle ou sortie unique ? | sortie unique gagne **15 paires sur 20** | partiel gagne **2 paires sur 2** (0,8362 > 0,8275 ; 0,9314 > 0,8680) |
| Où est le meilleur stop ? | le plus large possible (2,0 ATR en tête) | plateau 1,2 → 2,0 ATR, optimum à **1,2** |
| Meilleur PF | 1,3396 sur **48 trades** | 1,0073 sur **694 trades**, et il ne tient pas |

Le compte apparié est produit par `scripts/backtest/tune_stop_modes.py`. Le détail qui explique
l'erreur : sur les 5 paires où le partiel gagnait dans l'exploration, **toutes** avaient des
objectifs à 1,5/3,0 ou 2,0/4,0 — c'est-à-dire exactement la famille où la question se décide. La
majorité des 15 autres paires portait sur des objectifs proches, un régime que le jeu complet
n'utilise pas.

## 8. Réponse

**PF > 1,0 franchi : NON.**

Sur seize configurations mesurées sur le jeu complet (59 999 bougies), avec le jeu de signaux rendu
invariant et le harnais nommé :

- **une seule** affiche un profit factor supérieur à 1,0 — `stop1.5_tp3.0-6.0_partiel`, PF 1,0073,
  net +39,85 € sur 694 trades. Sur les moitiés du jeu, elle fait **0,9190** puis **1,1016** : le
  franchissement est un artefact de moyenne, pas une propriété. Sous le spread observé elle tombe
  à **0,9446**, net −310,49 € : il disparaît une seconde fois, par le coût ;
- la configuration **stable** est TP 1,5/3,0 à stop 1,5 ATR, partiel 50/50 + break-even :
  **0,9326 et 0,9333** sur les deux moitiés, **0,9314** sur le jeu complet, **0,8693** sous le
  spread observé ;
- **la prémisse de l'axe est réfutée** : resserrer le stop dégrade. À 0,5 ATR, PF 0,7422 et
  −2 876,58 €, le pire résultat du balayage ;
- **la direction utile est l'inverse** : éloigner les objectifs fait monter le PF de 0,8362
  (spec opérateur) à 0,9506 (2,0/4,0) sur le jeu complet, et 1,0073 (3,0/6,0) qui ne tient pas.
  C'est le seul gradient propre et monotone de tout le balayage ;
- **la spec de l'opérateur est la pire géométrie mesurée** des quatre testées à stop 1,5
  (0,8362), et elle tombe à 0,7523 sous le spread observé.

## 9. Limites, et ce que je n'ai pas mesuré

- **Seize configurations sur une seule fenêtre, c'est un test multiple.** Le meilleur d'entre eux
  est biaisé vers le haut par construction. Les moitiés de jeu corrigent ce biais pour les trois
  candidats qui les ont passées ; les autres lignes du tableau ne sont pas validées.
- **Le confondant des places occupées** (§ 2) n'est pas neutralisé : `max_concurrent_positions=1`
  fait que le nombre de trades exécutés dépend de la géométrie. Une comparaison rigoureuse
  exigerait de rejouer les mêmes trades sous chaque géométrie, ce que le harnais ne permet pas.
- **Le jeu de données est un seul marché, un seul timeframe, une seule période**
  (BTCUSD M15, 2025-01-21 → 2026-10-09). Rien ici ne dit ce que la règle fait sur l'or, ni sur une
  autre année.
- **Six configurations du croisement n'ont pas été mesurées** sur le jeu complet : les variantes
  `sortie-unique` à stops 0,5 / 0,8 / 1,0 / 1,2 / 2,0 (cinq) et `stop1.2_tp2.0-4.0_partiel` (une).
  Elles ne changeraient pas la conclusion — le mode `sortie-unique` perd les deux paires
  appariées, et l'étage 2 a déjà balayé le gradient de stop et celui des objectifs — mais elles
  manquent, et c'est écrit ici plutôt que tu.
- **Le holding maximal et le partiel asymétrique** n'ont pas été mesurés sur le jeu complet. Sur
  4 999 bougies, `holding16` valait 0,9753 contre 0,8910 pour la même configuration sans lui —
  piste non confirmée, à ne pas retenir en l'état.
- **Aucun changement de code n'est proposé.** Rien de ce qui a été mesuré n'autorise à toucher la
  règle, et un axe de mesure ne modifie pas `src/`.

## 10. Reproductibilité et sorties brutes

```bash
# Étage 1 — exploration, 4 999 bougies (élimination)
uv run python scripts/backtest/tune_stop_sweep.py --group stage1 --bars 4999 \
    --out docs/research/vwap-tuning/axe-A-exploration-5k.jsonl

# Étage 2 — décision, jeu complet, source vivante, deux lots disjoints
uv run python scripts/backtest/tune_stop_sweep.py --group decision_core60 --range 3:18 \
    --bars 59999 --live --out docs/research/vwap-tuning/axe-A-decision-60k.jsonl
uv run python scripts/backtest/tune_stop_sweep.py --group decision_core60 --range 19:34 \
    --bars 59999 --live --out docs/research/vwap-tuning/axe-A-decision-60k-b.jsonl
uv run python scripts/backtest/tune_stop_sweep.py --group decision_finish \
    --bars 59999 --live --out docs/research/vwap-tuning/axe-A-decision-60k.jsonl

# Contrôle du harnais après la modification de harness.py (attendu : 883 trades, PF 0,9314)
uv run python scripts/backtest/tune_stop_sweep.py --group decision_core60 \
    --label stop1.5_tp1.5-3.0_partiel --bars 59999 --live \
    --out docs/research/vwap-tuning/axe-A-harness-check.jsonl

# Validation par moitiés de jeu
uv run python scripts/backtest/tune_stop_sweep.py --group decision_core60 \
    --label stop1.2_tp1.5-3.0_partiel --label stop1.5_tp1.5-3.0_partiel \
    --slice 0:30000 --live --out docs/research/vwap-tuning/axe-A-windows-60k.jsonl
uv run python scripts/backtest/tune_stop_sweep.py --group decision_core60 \
    --label stop1.2_tp1.5-3.0_partiel --label stop1.5_tp1.5-3.0_partiel \
    --slice 30000:59999 --live --out docs/research/vwap-tuning/axe-A-windows-60k.jsonl

# Finalistes sous le spread réellement observé
uv run python scripts/backtest/tune_stop_sweep.py --group decision_core60 \
    --label stop1.5_tp1.5-3.0_partiel --bars 59999 --live --spread 18.424 \
    --out docs/research/vwap-tuning/axe-A-spread-observe.jsonl

# Contrôles de méthode
uv run python scripts/backtest/tune_stop_analysis.py      # invariance des signaux, couts, excursions
uv run python scripts/backtest/tune_stop_modes.py         # partiel contre sortie unique, appaire
uv run python scripts/backtest/tune_stop_report.py        # tableaux, injectes dans ce rapport
```

Sortie brute du contrôle de harnais — c'est la ligne qui rend les mesures ci-dessus attribuables à
une révision précise du code :

```text
== regle lue depuis src/ : vwap_pullback.py sha256 6a0da2b30cdeb17d3b059976db91bc7633c9a582260765882ba707cdf9a6ec6a ==
== harnais : harness.py sha256 a5d83403ba9b9720989b15ff1fed05160c9eed68e27a6f3240e6961772d4ebf6 ==
   couts : spread 5.254122 ; slippage 2.101649
== BTCUSD M15 : 59999 barres, 59999 dernieres barres sur 59999 ==
   2025-01-21T16:30:00+00:00 -> 2026-10-09T01:30:00+00:00
   1 configuration(s) ; groupe decision_core60
   [1/1] stop1.5_tp1.5-3.0_partiel                  trades 883 reussite 0.402 net -392.74 PF 0.9314 (436.2s) ok
```

Sortie brute de la validation par moitiés — noter que les deux moitiés ont tourné sous **deux
révisions différentes** du harnais (`a5d83403` puis `4a4dcb3b`) : le contrôle TP 1,5/3,0 y donne
0,9326 et 0,9333, ce qui est la preuve interne que la révision du harnais ne déplace pas le chemin
par défaut.

```text
== BTCUSD M15 : 30000 barres, indices 0:30000 sur 59999 ==
   [1/2] stop1.5_tp1.5-3.0_partiel                  trades 448 reussite 0.404 net -194.56 PF 0.9326 (142.5s) ok
   [2/2] stop1.2_tp1.5-3.0_partiel                  trades 518 reussite 0.4459 net 87.6 PF 1.0277 (150.5s) ok
== BTCUSD M15 : 29999 barres, indices 30000:59999 sur 59999 ==
   [1/2] stop1.5_tp1.5-3.0_partiel                  trades 428 reussite 0.3995 net -186.31 PF 0.9333 (134.6s) ok
   [2/2] stop1.2_tp1.5-3.0_partiel                  trades 486 reussite 0.3909 net -358.42 PF 0.8903 (123.2s) ok
```

Les mesures brutes, ligne par ligne, sont dans les JSONL du dossier : `axe-A-exploration-5k.jsonl`
(50 lignes), `axe-A-decision-60k.jsonl` et `axe-A-decision-60k-b.jsonl` (16 lignes de décision),
`axe-A-verification-source.jsonl`, `axe-A-harness-check.jsonl`, `axe-A-spread-observe.jsonl`,
`axe-A-windows-60k.jsonl`. Chaque ligne porte ses paramètres, son harnais, ses fractions de sortie,
sa fenêtre, ses coûts, ses compteurs et ses excursions médianes. Le tableau complet, engendré
depuis ces fichiers, est dans `axe-A-tableaux.md`.
