# Axe A — tableaux de mesure (engendres)

Ce fichier est produit par `scripts/backtest/tune_stop_report.py` depuis les JSONL de mesure. Il ne se recopie pas a la main.

### Exploration, 4 999 bougies (elimination)

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


### Decision, 59 999 bougies (jeu complet) — lot 1

Source : source vivante `src/`. 2 mesure(s).

| Configuration | Geometrie | Harnais | Barres | Trades | Reussite | Net EUR | PF | DD EUR | MAE gagnants | MFE gagnants | MAE perdants | MFE perdants |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `stop1.5_tp0.8-1.5_partiel` | stop 1,5 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 59999 | 1092 | 56,3 % | -850,01 | 0,8362 | 948,00 | 0,37 | 1,53 | 1,21 | 0,24 |
| `stop1.5_tp0.8-1.5_sortie-unique` | stop 1,5 ATR, TP 0,8/1,5 | sortie unique | 59999 | 1133 | 56,0 % | -934,23 | 0,8275 | 1041,75 | 0,33 | 0,99 | 1,21 | 0,24 |


### Controle d'equivalence des sources (59 999 bougies)

Source : copie gelee `bfa0e65`, a rapprocher du lot 1. 2 mesure(s).

Fenetre : `59999`

| Configuration | Geometrie | Harnais | Barres | Trades | Reussite | Net EUR | PF | DD EUR | MAE gagnants | MFE gagnants | MAE perdants | MFE perdants |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ref_stop1.5_tp0.8-1.5_sortie-unique` | stop 1,5 ATR, TP 0,8/1,5 | sortie unique | 59999 | 1133 | 56,0 % | -934,23 | 0,8275 | 1041,75 | 0,33 | 0,99 | 1,21 | 0,24 |


Fenetre : `59999 dernieres barres sur 59999`

| Configuration | Geometrie | Harnais | Barres | Trades | Reussite | Net EUR | PF | DD EUR | MAE gagnants | MFE gagnants | MAE perdants | MFE perdants |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ref_stop1.5_tp0.8-1.5_partiel` | stop 1,5 ATR, TP 0,8/1,5 | partiel 50/50 + break-even | 59999 | 1092 | 56,3 % | -850,01 | 0,8362 | 948,00 | 0,37 | 1,53 | 1,21 | 0,24 |
