# Où se trouve réellement l'avantage : le PnL découpé par le contexte d'entrée

**Date :** 2026-10-11 00:55 UTC · **Nature :** mesure en lecture seule · **Aucune activation, aucune promotion, `config/` inchangé.**
**Question :** les deux règles en production perdent, et le projet a éliminé par la mesure le filtre de séance/volatilité (`docs/research/session-volatility/`) et la géométrie de sortie (`docs/research/2026-10-10-validation-vwap-pullback-1.3.0.md`). Où se trouve réellement l'avantage, s'il existe ?

**Réponse courte.** L'avantage existe, il est **localisé, nommable et mesuré**, et ce n'est pas un paramètre de sortie : c'est **le sens de l'entrée par rapport à la tendance nommée du marché**. Une opération qui va dans le sens de la tendance au moment de l'entrée rend +0.2975 R sur 157 opérations sur le bitcoin et +0.2198 R sur 133 sur l'or, positives dans les deux fenêtres ; celles qui vont contre la tendance rendent -0.5045 R et -0.6707 R. La détection n'est pas le problème : l'**acceptation** l'est, et le remède est un **refus**, pas un réglage. Le jeu scellé n'a pas été ouvert.

> **ERRATUM DU 2026-10-11 — un défaut trouvé dans l'outil de ce rapport, et il change un chiffre.**
> Un équipier travaillant sur le filtre de tendance a trouvé que
> `edge_location._significance` publiait `student_two_sided_p(t) / 2`, qui n'est une p-value
> unilatérale **que pour `t > 0`** : la fonction est symétrique en signe, une p-value unilatérale
> ne l'est pas. Pour une case qui **perd**, elle rendait une p-value minuscule. Corrigé dans
> `tradingagent.research.statistics.student_one_sided_p`, verrouillé par
> `tests/backtest/test_one_sided_p_value.py`.
>
> **Le chiffre qui change, mesuré en rejouant l'analyse** (aucun backtest relancé : le jeu complet
> n'a tourné qu'une fois, et l'outil se contente de relire son cache) :
>
> | | avant | après correction |
> |---|---|---|
> | slices à espérance strictement positive | 12 | **12** |
> | … et dont l'IC 95 % exclut zéro | 10 | **10** |
> | … et qui survivent à Benjamini-Hochberg | 10 | **8** |
>
> **Deux slices ne survivent plus.** Les p-values des cases perdantes étaient fausses, donc elles
> entraient dans le classement de Benjamini-Hochberg et **déplaçaient son seuil** ; corrigées, le
> classement change et deux découvertes tombent. Le seuil de Bonferroni ne bouge pas (6,098e-04),
> et les 12 slices positifs comme les 10 à IC concluant restent identiques.
>
> **Ce qui reste vrai, et c'est le résultat de fond :** des slices à espérance positive survivent
> toujours, sous leurs quatre noms, et le sens de l'effet — entrer **avec** la tendance nommée —
> est inchangé. La conclusion du rapport tient ; **c'est son décompte qui était optimiste**, de
> deux unités.
>
> **Et une correction de mon propre raisonnement, parce qu'elle vaut d'être notée.** J'avais
> d'abord écrit ici que la correction ne changeait « rien », en raisonnant que les dix slices
> survivants ayant une espérance positive, leurs p-values étaient déjà justes et donc leur
> classement aussi. Le raisonnement était bon sur les p-values et **faux sur le classement** :
> Benjamini-Hochberg lit **toutes** les hypothèses, pas seulement celles qui survivent au bout.
> Une correction de tests multiples dépend de l'ensemble des valeurs qu'on lui donne, y compris
> celles qui ne concluent pas.

---

## 1. Ce qui a été mesuré, et sur quoi

**La pièce existante, pas une pièce nouvelle.** `indicators/features.py::entry_features` (TASK-069) produit un instantané du contexte à l'entrée — tendance, volatilité, structure de marché, structure de swing, séance — et `backtest/harness.py::_entry_features` l'attache à chaque `Trade`. Le harnais enregistre aussi `mae_r` et `mfe_r`. Ce script ne mesure rien de neuf : il **découpe** ce qui était déjà enregistré, et personne ne l'avait fait.

| | BTCUSD | XAUUSD |
|---|---|---|
| Version en production | `vwap_pullback@1.2.0` | `vwap_pullback@1.1.0` |
| Jeu gelé | `docs/research/datasets-volume/` | idem |
| Bougies | 59 999 (M15) | 59 999 (M15) |
| Empreinte | `2f04c3b23c31…` | `d847b980c680…` |
| Sortie | au **premier** objectif (`targets_at_first_only`), comme `runtime/pipeline.py` l'exécute | idem |
| Coûts | `campaign_costs` du dépôt : spread 2,24 bp, slippage 0,2 bp, commission 0,50 €/opération, `mode=SIGNAL`, une position à la fois | idem |
| Découpage | `split_dataset(anchor=True)`, 60 / 20 / 20 scellé | idem |

### Les fenêtres, et celle qui n'a pas été ouverte

| Fenêtre | BTCUSD | XAUUSD | Bougies |
|---|---|---|---|
| entraînement | 2025-01-21 16:30 → 2026-01-31 20:30 | 2024-03-25 13:00 → 2025-10-02 11:45 | 35 999 |
| validation | 2026-01-31 20:30 → 2026-06-05 23:15 | 2025-10-02 11:45 → 2026-04-08 18:15 | 11 999 |
| **scellé** | 2026-06-05 23:15 → 2026-10-09 01:45 | 2026-04-08 18:15 → 2026-10-09 03:30 | 12 001 |

**Le jeu scellé n'a pas été ouvert.** `SealedSet.unlock` n'a jamais reçu son jeton : seule la **fenêtre** du sceau est lue, pour nommer la période réservée. Aucune conclusion de ce document ne vient des 12 001 dernières bougies de chaque marché.

### Comment l'entrée est reconstituée, et pourquoi ce n'est pas une reconstruction

`distance_atr`, le ratio de volume et le VWAP vivent dans le `SignalCandidate`, que le harnais ne conserve pas jusqu'à la clôture. Une **enveloppe** de la stratégie enregistre donc ce qu'elle vient de rendre, indexé par l'instant de la décision ; `Trade.opened_at` est la clôture de la barre de remplissage, soit la décision plus une barre. Le harnais n'est pas modifié et ne voit pas la différence : l'enveloppe rend exactement le candidat reçu. Appariement : **1 799 opérations sur 1 801** portent leurs indicateurs ; les 2 restantes sont des opérations d'or sans VWAP mesurable, comptées comme `non_measure` et jamais comme zéro.

**Un point de lecture qui compte.** `entry_features` mesure la tendance avec **ses** paramètres (EMA 10/30, pente sur une barre, seuil 0,05 ATR), pas avec ceux de la règle (EMA 20/50, pente sur 10 barres, seuil 0,005). Le verdict « tendance » de ce rapport est donc une **lecture indépendante** du marché à l'instant de l'entrée : c'est précisément ce qui la rend informative, et ce qui la distingue du filtre `trend_of` déjà présent dans la stratégie.

### Vérifications avant de conclure

**La baseline est confrontée à la mesure publiée**, pas supposée. Le script relit `docs/research/measurements/<marché>-<ref>.json` et compare effectifs et facteurs de profit :

| marché | fenêtre | op. mesurées | op. publiées | PF mesuré | PF publié | écart |
|---|---|---|---|---|---|---|
| BTCUSD | train | 709 | 709 | 0.714838 | 0.714838 | +0.0e+00 |
| BTCUSD | validation | 205 | 205 | 0.681801 | 0.681801 | +0.0e+00 |
| XAUUSD | train | 656 | 656 | 0.722939 | 0.722939 | +0.0e+00 |
| XAUUSD | validation | 231 | 231 | 0.545443 | 0.545443 | +0.0e+00 |

Verdict de la confrontation : **la baseline reproduit vwap_pullback@1.1.0 sur le meme jeu de donnees (etiquette de version differente, cf. l'errata des bandeaux de config/)** pour le bitcoin (`docs/research/measurements/BTCUSD-vwap_pullback@1.1.0.json`) ; **la baseline reproduit la mesure publiee, aux chiffres publies pres** pour l'or (`docs/research/measurements/XAUUSD-vwap_pullback@1.1.0.json`), écart de facteur de profit maximal 0.0e+00. Les quatre blocs de la baseline sont donc reproduits au chiffre près, et les nombres de ce document sont comparables à ceux déjà publiés.

**Les statistiques sont vérifiées, pas supposées.** Le dépôt n'a aucune fonction de Student : `session_volatility.py` utilise Z95 = 1,96, ce qui suppose un grand échantillon et des R normaux. Les R d'un système à stop ne le sont pas, et certaines cases tombent à 30 opérations. La loi de Student est donc implémentée dans le script et **vérifiée contre des valeurs tablées** (`--self-check`, 11 contrôles : `I_0.5(1,1) = 0,5`, `t(0,975,10) = 2,228138852`, `t(0,95,29) = 1,699127027`, la frontière asymptotique à 1e-4). Un `SystemExit` refuse de publier si un contrôle échoue.

**Le test de Student est confronté au test de randomisation** du protocole (`research.protocol.monte_carlo_p_value`) sur 20 slices (1000 tirages, germe 20261011) : écart maximal de p-value **0.0060**, soit l'ordre de la résolution du test de randomisation (1/1001). Les deux lectures s'accordent.

---

## 2. La baseline des deux marchés, avec la géométrie en production

Espérance en **R net** : le PnL réalisé divisé par le risque de l'opération. Le R brut (avant commission) est plus haut de 0,05 R par opération — la commission de 0,50 € sur un risque de 10 €. C'est le R **net** qui est publié partout dans ce document, parce que c'est celui que le compte encaisse.

| marché | fenêtre | n | réussite | PF net | profit net | esp/op (€) | esp/op (R) | IC 95 % (R) |
|---|---|---|---|---|---|---|---|---|
| BTCUSD | train | 709 | 55.3 % | 0.7148 | -999.06 € | -1.4091 | **-0.1409** | [-0.2052 ; -0.0766] |
| BTCUSD | validation | 205 | 55.1 % | 0.6818 | -327.71 € | -1.5986 | **-0.1599** | [-0.2801 ; -0.0396] |
| BTCUSD | **entraînement + validation** | 914 | 55.3 % | 0.7073 | -1 326.77 € | -1.4516 | **-0.1452** | [-0.2017 ; -0.0886] |
| XAUUSD | train | 656 | 57.6 % | 0.7229 | -863.80 € | -1.3168 | **-0.1317** | [-0.1971 ; -0.0662] |
| XAUUSD | validation | 231 | 46.8 % | 0.5454 | -621.91 € | -2.6923 | **-0.2692** | [-0.3879 ; -0.1506] |
| XAUUSD | **entraînement + validation** | 887 | 54.8 % | 0.6688 | -1 485.71 € | -1.6750 | **-0.1675** | [-0.2249 ; -0.1101] |

**Les deux marchés perdent, et les deux intervalles de confiance excluent zéro par le bas.** Ce n'est pas « pas encore prouvé » : c'est une espérance négative mesurée sur 914 et 887 opérations. Le seuil du projet est PF ≥ 1,20 ; le meilleur nombre de ce tableau vaut 0.7229.

---

## 3. Le tableau complet : le PnL par dimension d'entrée

Chaque dimension est lue **marché par marché**, sur les deux fenêtres réunies (les colonnes `esp. train` et `esp. validation` disent si le signe tient dans chacune). Les grandeurs continues sont coupées en **quartiles dérivés de l'entraînement des deux marchés réunis**, puis appliqués tels quels à la validation : recalculer la coupure par fenêtre en ferait une fonction du futur. Aucun seuil de `docs/research/thresholds.json` n'est touché.

| grandeur | quartiles (q1 / q2 / q3) |
|---|---|
| `atr_ratio` | 0.9292 / 0.9800 / 1.0430 |
| `slope_atr` | -0.0382 / 0.0009 / 0.0405 |
| `vwap_distance_atr` | 0.0796 / 0.1653 / 0.2629 |
| `vwap_gap_atr` | 0.0795 / 0.1649 / 0.2635 |
| `volume_ratio` | 0.8179 / 1.0428 / 1.3816 |
| `duration_bars` | 1.0000 / 3.0000 / 7.0000 |

Une case sous 30 opérations est **observée, pas concluante** : c'est le seuil du dépôt (`min_trades`). Les cases marquées `non_measure` sont des mesures absentes, jamais des zéros.

### verdict de tendance (EMA 10/30 et pente en ATR) — entry_features

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `neutral` | 629 | 52.8 % | 0.65 | -0.1825 | [-0.251 ; -0.114] | -0.167 | -0.239 | oui |
| `up` | 148 | 57.4 % | 0.74 | -0.1281 | [-0.273 ; +0.017] | -0.170 | -0.006 | oui |
| `down` | 137 | 64.2 % | 1.02 | +0.0079 | [-0.135 ; +0.151] | +0.006 | +0.016 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `neutral` | 638 | 54.9 % | 0.68 | -0.1567 | [-0.223 ; -0.090] | -0.128 | -0.241 | oui |
| `up` | 133 | 60.2 % | 0.82 | -0.0823 | [-0.232 ; +0.067] | -0.073 | -0.106 | oui |
| `down` | 116 | 48.3 % | 0.47 | -0.3243 | [-0.496 ; -0.153] | -0.217 | -0.631 | oui |

### verdict de volatilite nomme (ATR courant / moyenne) — entry_features

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `normal` | 913 | 55.2 % | 0.71 | -0.1461 | [-0.203 ; -0.090] | -0.142 | -0.160 | oui |
| `volatile` | 1 | 100.0 % | — | +0.7207 | [— ; —] | +0.721 | — | non |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `normal` | 887 | 54.8 % | 0.67 | -0.1675 | [-0.225 ; -0.110] | -0.132 | -0.269 | oui |

### structure de marche (cloture contre le canal des 20 precedentes) — entry_features

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `range` | 837 | 56.8 % | 0.76 | -0.1126 | [-0.171 ; -0.054] | -0.107 | -0.132 | oui |
| `breakout_up` | 42 | 35.7 % | 0.27 | -0.5833 | [-0.875 ; -0.292] | -0.508 | -0.751 | oui |
| `breakout_down` | 35 | 42.9 % | 0.40 | -0.3973 | [-0.709 ; -0.086] | -0.494 | +0.635 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `range` | 804 | 56.1 % | 0.72 | -0.1379 | [-0.197 ; -0.079] | -0.122 | -0.185 | oui |
| `breakout_up` | 42 | 45.2 % | 0.45 | -0.3461 | [-0.628 ; -0.064] | -0.184 | -0.585 | oui |
| `breakout_down` | 41 | 39.0 % | 0.29 | -0.5652 | [-0.884 ; -0.247] | -0.285 | -1.330 | oui |

### seance UTC de la barre de decision — entry_features

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `tokyo` | 328 | 52.4 % | 0.63 | -0.1976 | [-0.293 ; -0.102] | -0.195 | -0.205 | oui |
| `london` | 231 | 55.0 % | 0.68 | -0.1599 | [-0.273 ; -0.047] | -0.148 | -0.201 | oui |
| `overlap` | 135 | 60.0 % | 0.87 | -0.0593 | [-0.208 ; +0.089] | -0.094 | +0.069 | oui |
| `new_york` | 181 | 57.5 % | 0.80 | -0.0914 | [-0.218 ; +0.035] | -0.077 | -0.147 | oui |
| `off` | 39 | 53.8 % | 0.67 | -0.1640 | [-0.445 ; +0.117] | -0.122 | -0.357 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `tokyo` | 310 | 52.3 % | 0.58 | -0.2247 | [-0.322 ; -0.127] | -0.202 | -0.297 | oui |
| `london` | 252 | 59.5 % | 0.81 | -0.0866 | [-0.192 ; +0.019] | -0.076 | -0.124 | oui |
| `overlap` | 176 | 52.8 % | 0.65 | -0.1829 | [-0.313 ; -0.052] | -0.095 | -0.398 | oui |
| `new_york` | 121 | 54.5 % | 0.69 | -0.1564 | [-0.318 ; +0.006] | -0.171 | -0.128 | oui |
| `off` | 28 | 53.6 % | 0.59 | -0.2137 | [-0.554 ; +0.126] | +0.101 | -0.877 | non |

### sens de l'operation

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `BUY` | 471 | 57.7 % | 0.79 | -0.0991 | [-0.177 ; -0.021] | -0.095 | -0.115 | oui |
| `SELL` | 443 | 52.6 % | 0.63 | -0.1942 | [-0.277 ; -0.112] | -0.193 | -0.197 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `BUY` | 523 | 57.0 % | 0.73 | -0.1302 | [-0.205 ; -0.055] | -0.094 | -0.231 | oui |
| `SELL` | 364 | 51.6 % | 0.59 | -0.2211 | [-0.311 ; -0.132] | -0.185 | -0.324 | oui |

### structure de swing (HH/HL, LH/LL, mixte) — entry_features, non nommee par la demande

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `hh_hl` | 263 | 54.8 % | 0.70 | -0.1499 | [-0.255 ; -0.045] | -0.160 | -0.112 | oui |
| `lh_ll` | 256 | 57.4 % | 0.77 | -0.1086 | [-0.216 ; -0.001] | -0.064 | -0.236 | oui |
| `mixed` | 395 | 54.2 % | 0.67 | -0.1657 | [-0.252 ; -0.079] | -0.175 | -0.131 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `hh_hl` | 217 | 57.1 % | 0.71 | -0.1437 | [-0.263 ; -0.024] | -0.125 | -0.199 | oui |
| `lh_ll` | 251 | 58.2 % | 0.77 | -0.1060 | [-0.212 ; -0.000] | -0.079 | -0.180 | oui |
| `mixed` | 419 | 51.6 % | 0.60 | -0.2167 | [-0.300 ; -0.133] | -0.167 | -0.359 | oui |

### cote du VWAP a l'entree — derive de l'indicateur `vwap` du signal

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `au_dessus` | 471 | 57.7 % | 0.79 | -0.0991 | [-0.177 ; -0.021] | -0.095 | -0.115 | oui |
| `en_dessous` | 443 | 52.6 % | 0.63 | -0.1942 | [-0.277 ; -0.112] | -0.193 | -0.197 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `au_dessus` | 522 | 57.1 % | 0.73 | -0.1284 | [-0.203 ; -0.053] | -0.094 | -0.225 | oui |
| `en_dessous` | 363 | 51.5 % | 0.59 | -0.2230 | [-0.313 ; -0.133] | -0.187 | -0.324 | oui |
| `non_measure` | 2 | 50.0 % | 0.42 | -0.3132 | [-9.942 ; +9.315] | +0.445 | -1.071 | non |

### l'operation va-t-elle DANS le sens de la tendance nommee ? — pre-enregistre

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `dans_la_tendance` | 157 | 80.9 % | 2.42 | +0.2975 | [+0.190 ; +0.405] | +0.289 | +0.322 | oui |
| `contre_la_tendance` | 128 | 35.9 % | 0.31 | -0.5045 | [-0.657 ; -0.352] | -0.506 | -0.499 | oui |
| `sans_tendance` | 629 | 52.8 % | 0.65 | -0.1825 | [-0.251 ; -0.114] | -0.167 | -0.239 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `dans_la_tendance` | 133 | 76.7 % | 1.87 | +0.2198 | [+0.095 ; +0.344] | +0.235 | +0.182 | oui |
| `contre_la_tendance` | 116 | 29.3 % | 0.21 | -0.6707 | [-0.829 ; -0.512] | -0.557 | -0.996 | oui |
| `sans_tendance` | 638 | 54.9 % | 0.68 | -0.1567 | [-0.223 ; -0.090] | -0.128 | -0.241 | oui |

### rapport ATR courant / moyenne, en quartiles — entry_features

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 266 | 54.5 % | 0.61 | -0.2013 | [-0.306 ; -0.097] | -0.196 | -0.226 | oui |
| `q2` | 176 | 58.0 % | 0.80 | -0.0916 | [-0.221 ; +0.037] | -0.019 | -0.274 | oui |
| `q3` | 209 | 56.5 % | 0.78 | -0.1055 | [-0.223 ; +0.012] | -0.133 | -0.013 | oui |
| `q4` | 263 | 53.2 % | 0.70 | -0.1556 | [-0.263 ; -0.048] | -0.163 | -0.132 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 179 | 50.8 % | 0.54 | -0.2507 | [-0.377 ; -0.124] | -0.223 | -0.310 | oui |
| `q2` | 267 | 59.2 % | 0.78 | -0.1015 | [-0.204 ; +0.001] | -0.102 | -0.098 | oui |
| `q3` | 253 | 56.1 % | 0.72 | -0.1373 | [-0.247 ; -0.028] | -0.091 | -0.251 | oui |
| `q4` | 188 | 50.5 % | 0.60 | -0.2227 | [-0.351 ; -0.094] | -0.149 | -0.430 | oui |

### pente de la moyenne lente en ATR par barre, en quartiles — entry_features

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 229 | 56.3 % | 0.74 | -0.1269 | [-0.240 ; -0.013] | -0.130 | -0.117 | oui |
| `q2` | 218 | 52.8 % | 0.66 | -0.1755 | [-0.291 ; -0.059] | -0.153 | -0.284 | oui |
| `q3` | 227 | 59.9 % | 0.86 | -0.0601 | [-0.171 ; +0.051] | -0.036 | -0.131 | oui |
| `q4` | 240 | 52.1 % | 0.60 | -0.2156 | [-0.329 ; -0.102] | -0.240 | -0.144 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 221 | 50.2 % | 0.54 | -0.2662 | [-0.386 ; -0.146] | -0.204 | -0.437 | oui |
| `q2` | 207 | 54.1 % | 0.66 | -0.1694 | [-0.287 ; -0.052] | -0.154 | -0.222 | oui |
| `q3` | 240 | 57.5 % | 0.77 | -0.1049 | [-0.213 ; +0.003] | -0.065 | -0.203 | oui |
| `q4` | 219 | 57.1 % | 0.72 | -0.1348 | [-0.251 ; -0.019] | -0.107 | -0.214 | oui |

### distance au VWAP a l'entree en ATR, en quartiles — indicateur `distance_atr`

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 237 | 57.4 % | 0.79 | -0.0977 | [-0.207 ; +0.012] | -0.122 | +0.023 | oui |
| `q2` | 220 | 53.2 % | 0.63 | -0.1941 | [-0.312 ; -0.077] | -0.198 | -0.182 | oui |
| `q3` | 231 | 54.1 % | 0.68 | -0.1595 | [-0.272 ; -0.047] | -0.104 | -0.308 | oui |
| `q4` | 226 | 56.2 % | 0.73 | -0.1326 | [-0.248 ; -0.017] | -0.143 | -0.095 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 210 | 53.3 % | 0.64 | -0.1861 | [-0.303 ; -0.069] | -0.080 | -0.418 | oui |
| `q2` | 223 | 55.2 % | 0.67 | -0.1663 | [-0.282 ; -0.050] | -0.195 | -0.063 | oui |
| `q3` | 230 | 57.0 % | 0.74 | -0.1232 | [-0.235 ; -0.012] | -0.095 | -0.208 | oui |
| `q4` | 222 | 53.6 % | 0.63 | -0.1957 | [-0.314 ; -0.077] | -0.152 | -0.320 | oui |
| `non_measure` | 2 | 50.0 % | 0.42 | -0.3132 | [-9.942 ; +9.315] | +0.445 | -1.071 | non |

### ecart signe au VWAP en ATR dans le sens de l'operation, en quartiles — derive

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 241 | 56.8 % | 0.76 | -0.1128 | [-0.223 ; -0.003] | -0.133 | -0.016 | oui |
| `q2` | 216 | 53.7 % | 0.65 | -0.1798 | [-0.297 ; -0.062] | -0.177 | -0.188 | oui |
| `q3` | 231 | 54.5 % | 0.69 | -0.1550 | [-0.268 ; -0.042] | -0.105 | -0.298 | oui |
| `q4` | 226 | 55.8 % | 0.72 | -0.1366 | [-0.252 ; -0.022] | -0.152 | -0.086 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 206 | 53.9 % | 0.65 | -0.1766 | [-0.295 ; -0.058] | -0.069 | -0.409 | oui |
| `q2` | 227 | 54.2 % | 0.65 | -0.1832 | [-0.298 ; -0.068] | -0.191 | -0.157 | oui |
| `q3` | 227 | 56.4 % | 0.72 | -0.1379 | [-0.252 ; -0.024] | -0.117 | -0.200 | oui |
| `q4` | 225 | 54.7 % | 0.66 | -0.1719 | [-0.288 ; -0.056] | -0.140 | -0.264 | oui |
| `non_measure` | 2 | 50.0 % | 0.42 | -0.3132 | [-9.942 ; +9.315] | +0.445 | -1.071 | non |

### volume de la barre de contact / moyenne des 20 precedentes, en quartiles — indicateur

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 199 | 58.8 % | 0.82 | -0.0794 | [-0.199 ; +0.041] | -0.051 | -0.333 | oui |
| `q2` | 319 | 51.7 % | 0.62 | -0.2044 | [-0.300 ; -0.108] | -0.248 | -0.109 | oui |
| `q3` | 259 | 52.9 % | 0.64 | -0.1921 | [-0.300 ; -0.084] | -0.182 | -0.216 | oui |
| `q4` | 137 | 62.8 % | 0.97 | -0.0139 | [-0.159 ; +0.131] | -0.023 | +0.096 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 207 | 54.6 % | 0.66 | -0.1737 | [-0.294 ; -0.054] | -0.153 | -0.249 | oui |
| `q2` | 175 | 58.3 % | 0.78 | -0.1000 | [-0.227 ; +0.027] | -0.094 | -0.114 | oui |
| `q3` | 239 | 49.0 % | 0.54 | -0.2594 | [-0.372 ; -0.147] | -0.262 | -0.255 | oui |
| `q4` | 264 | 58.0 % | 0.74 | -0.1231 | [-0.229 ; -0.018] | -0.045 | -0.459 | oui |
| `non_measure` | 2 | 50.0 % | 0.42 | -0.3132 | [-9.942 ; +9.315] | +0.445 | -1.071 | non |

### duree de l'operation en barres, en quartiles — derive des horodatages

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 230 | 58.7 % | 0.77 | -0.1108 | [-0.227 ; +0.006] | -0.086 | -0.209 | oui |
| `q2` | 233 | 61.4 % | 0.92 | -0.0320 | [-0.141 ; +0.077] | -0.036 | -0.020 | oui |
| `q3` | 237 | 50.6 % | 0.59 | -0.2200 | [-0.331 ; -0.109] | -0.216 | -0.231 | oui |
| `q4` | 214 | 50.0 % | 0.59 | -0.2224 | [-0.340 ; -0.105] | -0.233 | -0.180 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `q1` | 244 | 61.1 % | 0.81 | -0.0879 | [-0.201 ; +0.025] | -0.013 | -0.278 | oui |
| `q2` | 240 | 52.5 % | 0.62 | -0.1990 | [-0.308 ; -0.090] | -0.163 | -0.316 | oui |
| `q3` | 212 | 55.7 % | 0.71 | -0.1410 | [-0.257 ; -0.025] | -0.131 | -0.171 | oui |
| `q4` | 191 | 48.7 % | 0.54 | -0.2590 | [-0.384 ; -0.134] | -0.242 | -0.302 | oui |

### heure UTC de la decision (groupes, plus un seau « autres ») — derive

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `00-03` | 177 | 49.7 % | 0.57 | -0.2381 | [-0.368 ; -0.108] | -0.255 | -0.183 | oui |
| `04-07` | 199 | 56.3 % | 0.72 | -0.1368 | [-0.259 ; -0.014] | -0.100 | -0.237 | oui |
| `08-11` | 152 | 54.6 % | 0.67 | -0.1686 | [-0.309 ; -0.028] | -0.149 | -0.243 | oui |
| `12-15` | 169 | 59.2 % | 0.84 | -0.0745 | [-0.206 ; +0.057] | -0.110 | +0.061 | oui |
| `16-19` | 137 | 56.2 % | 0.77 | -0.1087 | [-0.255 ; +0.038] | -0.105 | -0.123 | oui |
| `20-23` | 80 | 56.2 % | 0.73 | -0.1274 | [-0.319 ; +0.064] | -0.104 | -0.235 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `00-03` | 173 | 53.8 % | 0.61 | -0.2052 | [-0.339 ; -0.072] | -0.156 | -0.342 | oui |
| `04-07` | 197 | 53.3 % | 0.62 | -0.1980 | [-0.318 ; -0.078] | -0.177 | -0.272 | oui |
| `08-11` | 151 | 58.9 % | 0.79 | -0.0969 | [-0.234 ; +0.040] | -0.099 | -0.089 | oui |
| `12-15` | 224 | 54.5 % | 0.69 | -0.1556 | [-0.271 ; -0.041] | -0.103 | -0.286 | oui |
| `16-19` | 91 | 56.0 % | 0.76 | -0.1158 | [-0.297 ; +0.066] | -0.111 | -0.126 | oui |
| `20-23` | 51 | 51.0 % | 0.53 | -0.2752 | [-0.537 ; -0.013] | -0.127 | -0.525 | oui |

### jour de la semaine de la decision (groupes, plus un seau « autres ») — derive

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `lun-mar` | 251 | 52.6 % | 0.66 | -0.1776 | [-0.287 ; -0.068] | -0.187 | -0.152 | oui |
| `mer-jeu` | 278 | 55.4 % | 0.74 | -0.1257 | [-0.229 ; -0.023] | -0.101 | -0.210 | oui |
| `ven` | 120 | 59.2 % | 0.86 | -0.0624 | [-0.220 ; +0.095] | -0.071 | -0.023 | oui |
| `autres` | 265 | 55.8 % | 0.66 | -0.1724 | [-0.276 ; -0.068] | -0.175 | -0.164 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `lun-mar` | 356 | 56.7 % | 0.73 | -0.1312 | [-0.221 ; -0.041] | -0.088 | -0.249 | oui |
| `mer-jeu` | 361 | 52.9 % | 0.62 | -0.1994 | [-0.290 ; -0.109] | -0.165 | -0.307 | oui |
| `ven` | 161 | 54.7 % | 0.66 | -0.1771 | [-0.315 ; -0.039] | -0.158 | -0.224 | oui |
| `autres` | 9 | 55.6 % | 0.69 | -0.1536 | [-0.845 ; +0.537] | -0.040 | -1.060 | non |

### croisement tendance x volatilite : le filtre de tendance est le seul qui ait tenu

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `down|normal` | 136 | 64.0 % | 1.01 | +0.0026 | [-0.141 ; +0.146] | -0.001 | +0.016 | oui |
| `down|volatile` | 1 | 100.0 % | — | +0.7207 | [— ; —] | +0.721 | — | non |
| `neutral|normal` | 629 | 52.8 % | 0.65 | -0.1825 | [-0.251 ; -0.114] | -0.167 | -0.239 | oui |
| `up|normal` | 148 | 57.4 % | 0.74 | -0.1281 | [-0.273 ; +0.017] | -0.170 | -0.006 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `down|normal` | 116 | 48.3 % | 0.47 | -0.3243 | [-0.496 ; -0.153] | -0.217 | -0.631 | oui |
| `neutral|normal` | 638 | 54.9 % | 0.68 | -0.1567 | [-0.223 ; -0.090] | -0.128 | -0.241 | oui |
| `up|normal` | 133 | 60.2 % | 0.82 | -0.0823 | [-0.232 ; +0.067] | -0.073 | -0.106 | oui |

### croisement tendance x sens : entrer avec la tendance nommee, ou contre elle

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `down|BUY` | 60 | 40.0 % | 0.38 | -0.4186 | [-0.644 ; -0.193] | -0.418 | -0.421 | oui |
| `down|SELL` | 77 | 83.1 % | 2.82 | +0.3402 | [+0.191 ; +0.490] | +0.371 | +0.246 | oui |
| `neutral|BUY` | 331 | 55.9 % | 0.74 | -0.1271 | [-0.220 ; -0.034] | -0.100 | -0.246 | oui |
| `neutral|SELL` | 298 | 49.3 % | 0.56 | -0.2441 | [-0.344 ; -0.144] | -0.248 | -0.234 | oui |
| `up|BUY` | 80 | 78.8 % | 2.10 | +0.2563 | [+0.098 ; +0.414] | +0.206 | +0.388 | oui |
| `up|SELL` | 68 | 32.4 % | 0.26 | -0.5804 | [-0.792 ; -0.369] | -0.590 | -0.548 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `down|BUY` | 70 | 28.6 % | 0.20 | -0.6952 | [-0.903 ; -0.487] | -0.572 | -1.026 | oui |
| `down|SELL` | 46 | 78.3 % | 2.01 | +0.2399 | [+0.029 ; +0.451] | +0.299 | +0.051 | oui |
| `neutral|BUY` | 366 | 57.9 % | 0.78 | -0.1028 | [-0.190 ; -0.015] | -0.069 | -0.204 | oui |
| `neutral|SELL` | 272 | 50.7 % | 0.58 | -0.2294 | [-0.332 ; -0.127] | -0.209 | -0.287 | oui |
| `up|BUY` | 87 | 75.9 % | 1.80 | +0.2091 | [+0.052 ; +0.366] | +0.197 | +0.236 | oui |
| `up|SELL` | 46 | 30.4 % | 0.22 | -0.6334 | [-0.885 ; -0.381] | -0.536 | -0.944 | oui |

### croisement structure de marche x sens

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `breakout_down|BUY` | 27 | 29.6 % | 0.23 | -0.6339 | [-0.970 ; -0.298] | -0.734 | +0.619 | non |
| `breakout_down|SELL` | 8 | 87.5 % | 3.96 | +0.4012 | [-0.102 ; +0.905] | +0.363 | +0.667 | non |
| `breakout_up|BUY` | 8 | 87.5 % | 3.65 | +0.3716 | [-0.138 ; +0.881] | +0.350 | +0.523 | non |
| `breakout_up|SELL` | 34 | 23.5 % | 0.15 | -0.8080 | [-1.107 ; -0.509] | -0.781 | -0.857 | oui |
| `range|BUY` | 436 | 58.9 % | 0.83 | -0.0746 | [-0.155 ; +0.006] | -0.058 | -0.138 | oui |
| `range|SELL` | 401 | 54.4 % | 0.69 | -0.1540 | [-0.239 ; -0.069] | -0.163 | -0.126 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `breakout_down|BUY` | 32 | 21.9 % | 0.13 | -0.8961 | [-1.219 ; -0.574] | -0.605 | -1.537 | oui |
| `breakout_down|SELL` | 9 | 100.0 % | — | +0.6113 | [+0.561 ; +0.661] | +0.595 | +0.739 | non |
| `breakout_up|BUY` | 12 | 100.0 % | — | +0.6409 | [+0.603 ; +0.679] | +0.615 | +0.692 | non |
| `breakout_up|SELL` | 30 | 23.3 % | 0.16 | -0.7410 | [-1.028 ; -0.454] | -0.560 | -0.978 | oui |
| `range|BUY` | 479 | 58.2 % | 0.79 | -0.0983 | [-0.175 ; -0.022] | -0.079 | -0.155 | oui |
| `range|SELL` | 325 | 52.9 % | 0.62 | -0.1962 | [-0.291 ; -0.102] | -0.185 | -0.232 | oui |

### croisement cote du VWAP x tendance

**BTCUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `au_dessus|down` | 60 | 40.0 % | 0.38 | -0.4186 | [-0.644 ; -0.193] | -0.418 | -0.421 | oui |
| `au_dessus|neutral` | 331 | 55.9 % | 0.74 | -0.1271 | [-0.220 ; -0.034] | -0.100 | -0.246 | oui |
| `au_dessus|up` | 80 | 78.8 % | 2.10 | +0.2563 | [+0.098 ; +0.414] | +0.206 | +0.388 | oui |
| `en_dessous|down` | 77 | 83.1 % | 2.82 | +0.3402 | [+0.191 ; +0.490] | +0.371 | +0.246 | oui |
| `en_dessous|neutral` | 298 | 49.3 % | 0.56 | -0.2441 | [-0.344 ; -0.144] | -0.248 | -0.234 | oui |
| `en_dessous|up` | 68 | 32.4 % | 0.26 | -0.5804 | [-0.792 ; -0.369] | -0.590 | -0.548 | oui |

**XAUUSD**

| valeur | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | concluant |
|---|---|---|---|---|---|---|---|---|
| `au_dessus|down` | 69 | 29.0 % | 0.20 | -0.6897 | [-0.901 ; -0.479] | -0.572 | -1.024 | oui |
| `au_dessus|neutral` | 366 | 57.9 % | 0.78 | -0.1028 | [-0.190 ; -0.015] | -0.069 | -0.204 | oui |
| `au_dessus|up` | 87 | 75.9 % | 1.80 | +0.2091 | [+0.052 ; +0.366] | +0.197 | +0.236 | oui |
| `en_dessous|down` | 46 | 78.3 % | 2.01 | +0.2399 | [+0.029 ; +0.451] | +0.299 | +0.051 | oui |
| `en_dessous|neutral` | 271 | 50.6 % | 0.57 | -0.2319 | [-0.335 ; -0.129] | -0.212 | -0.287 | oui |
| `en_dessous|up` | 46 | 30.4 % | 0.22 | -0.6334 | [-0.885 ; -0.381] | -0.536 | -0.944 | oui |
| `non_measure|down` | 1 | 0.0 % | 0.00 | -1.0710 | [— ; —] | — | -1.071 | non |
| `non_measure|neutral` | 1 | 100.0 % | — | +0.4446 | [— ; —] | +0.445 | — | non |

---

## 4. Les slices à espérance positive, et ceux qui survivent à la correction

### Le compte des tests, publié avant les résultats

Regarder beaucoup de slices et ne rapporter que le meilleur est la façon la plus fiable de trouver un avantage qui n'existe pas. Le compte est donc publié : **164 hypothèses** (marché × dimension × valeur), dont **147 concluantes** (≥ 30 opérations). À un taux de fausses découvertes de 0.10, le hasard seul en produirait **16.4** ; la ligne de Bonferroni vaut **6.098e-04**.

La correction appliquée est celle du dépôt, Benjamini-Hochberg (`research.protocol.price_false_discoveries`), sur **toutes** les hypothèses. Deux dimensions sont **mécaniquement redondantes** avec un croisement déjà compté, et c'est dit plutôt que soustrait en silence :

- `vwap_side_x_trend` ≡ `trend_x_direction` — la strategie n'ouvre un achat que sous le VWAP et une vente au-dessus : les deux croisements avec la tendance donnent donc des cases identiques au nom pres, parce que le cote du VWAP et le sens portent la meme information.
- `trend_agreement` ≡ `trend_x_direction` — memes operations, regroupees autrement : l'agregation est publiee parce que c'est la forme testable de l'hypothese, pas parce qu'elle ajoute un test.

**Conséquence de lecture :** les survivants ci-dessous ne sont donc pas huit découvertes indépendantes, mais **deux effets** (l'accord avec la tendance, à l'achat et à la vente) vus sous quatre noms. Le compte honnête est **deux**, et le classement par p-value ne le dit pas de lui-même.

### Le résultat

| | compte |
|---|---|
| hypothèses essayées | 164 |
| hypothèses concluantes (≥ 30 op.) | 147 |
| slices à espérance strictement positive | 12 |
| … et dont l'IC 95 % exclut zéro par le haut | **10** |
| … et qui survivent à Benjamini-Hochberg | **8** *(10 avant la correction de l'erratum ci-dessus)* |

**Les slices qui survivent à la correction, sous leur nom complet :**

| slice | n | réussite | PF net | esp/op (R) | IC 95 % (R) | p | train (n) | validation (n) | IC exclut 0 |
|---|---|---|---|---|---|---|---|---|---|
| `BTCUSD` · `trend_agreement` · `dans_la_tendance` | 157 | 80.9 % | 2.42 | +0.2975 | [+0.190 ; +0.405] | 9.61e-08 | +0.289 (116) | +0.322 (41) | **oui** |
| `BTCUSD` · `trend_x_direction` · `down|SELL` | 77 | 83.1 % | 2.82 | +0.3402 | [+0.191 ; +0.490] | 1.04e-05 | +0.371 (58) | +0.246 (19) | **oui** |
| `BTCUSD` · `vwap_side_x_trend` · `en_dessous|down` | 77 | 83.1 % | 2.82 | +0.3402 | [+0.191 ; +0.490] | 1.04e-05 | +0.371 (58) | +0.246 (19) | **oui** |
| `XAUUSD` · `trend_agreement` · `dans_la_tendance` | 133 | 76.7 % | 1.87 | +0.2198 | [+0.095 ; +0.344] | 0.000329 | +0.235 (95) | +0.182 (38) | **oui** |
| `BTCUSD` · `trend_x_direction` · `up|BUY` | 80 | 78.8 % | 2.10 | +0.2563 | [+0.098 ; +0.414] | 0.0009 | +0.206 (58) | +0.388 (22) | **oui** |
| `BTCUSD` · `vwap_side_x_trend` · `au_dessus|up` | 80 | 78.8 % | 2.10 | +0.2563 | [+0.098 ; +0.414] | 0.0009 | +0.206 (58) | +0.388 (22) | **oui** |
| `XAUUSD` · `trend_x_direction` · `up|BUY` | 87 | 75.9 % | 1.80 | +0.2091 | [+0.052 ; +0.366] | 0.00487 | +0.197 (60) | +0.236 (27) | **oui** |
| `XAUUSD` · `vwap_side_x_trend` · `au_dessus|up` | 87 | 75.9 % | 1.80 | +0.2091 | [+0.052 ; +0.366] | 0.00487 | +0.197 (60) | +0.236 (27) | **oui** |

**Les deux slices que la correction a fait tomber** — ils gardent leur espérance positive, mais ne
survivent plus à la correction de tests multiples une fois les p-values réparées :

| slice | n | esp/op (R) | IC 95 % (R) | esp. validation | pourquoi il tombe |
|---|---|---|---|---|---|
| `XAUUSD` · `trend_x_direction` · `down|SELL` | 46 | +0.2399 | [+0.029 ; +0.451] | +0.051 | p = 0.0135, le plus faible des quatre noms |
| `XAUUSD` · `vwap_side_x_trend` · `en_dessous|down` | 46 | +0.2399 | [+0.029 ; +0.451] | +0.051 | même slice sous son autre nom |

C'est le `down|SELL` de l'or qui tombe, et sous ses **deux** noms — donc l'effet « vente dans une
tendance baissière sur l'or » n'est plus établi par cette mesure, alors que celui du bitcoin et
les deux effets d'achat le restent. La validation de ce slice valait d'ailleurs **+0.051 R**, à
la limite de zéro : c'était le plus fragile des quatre, et la correction l'a dit avant moi.

**Les slices positifs qui ne survivent pas** (2) sont publiés pour que le tableau ne s'arrête pas à ce qui arrange :

| slice | n | esp/op (R) | IC 95 % (R) | p | survit |
|---|---|---|---|---|---|
| `BTCUSD` · `trend` · `down` | 137 | +0.0079 | [-0.135 ; +0.151] | 0.457 | non |
| `BTCUSD` · `trend_x_volatility` · `down|normal` | 136 | +0.0026 | [-0.141 ; +0.146] | 0.486 | non |

---

## 5. L'effet, nommé : l'entrée va-t-elle dans le sens de la tendance ?

**C'est la seule dimension pré-enregistrée de ce document, et il faut le dire avant de donner le chiffre.** `strategies/library/vwap_pullback.py` porte déjà l'hypothèse, écrite avant cette mesure : « le filtre de tendance est le seul qui ait tenu à la mesure », PF 1,555 sur 117 opérations, statistique t de 2,18 sur 16 mesures — un **candidat**, jamais activé, et non confirmé hors échantillon. Le `trend_filter` de la stratégie n'est pas la même mesure que celle-ci (il lit `trend_of`, une pente sur une barre), mais l'idée est la même : ce résultat ne sort pas de nulle part, et il ne doit pas être présenté comme une découverte née du balayage.

La lecture testée ici est volontairement la plus simple possible : la tendance de `entry_features` **nomme-t-elle** une direction (`up` ou `down`) et l'opération va-t-elle dans ce sens ? `neutral` n'entre dans aucun accord — une opération n'est ni dans la tendance ni contre elle quand il n'y a pas de tendance.

| marché | accord | n | réussite | PF net | esp/op (R) | IC 95 % (R) | esp. train | esp. validation | profit net |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSD | dans la tendance | 157 | 80.9 % | **2.415** | **+0.2975** | [+0.190 ; +0.405] | +0.289 (116) | +0.322 (41) | +467.01 € |
| BTCUSD | contre la tendance | 128 | 35.9 % | **0.313** | **-0.5045** | [-0.657 ; -0.352] | -0.506 (102) | -0.499 (26) | -645.81 € |
| BTCUSD | sans tendance nommée | 629 | 52.8 % | **0.648** | **-0.1825** | [-0.251 ; -0.114] | -0.167 (491) | -0.239 (138) | -1 147.97 € |
| XAUUSD | dans la tendance | 133 | 76.7 % | **1.866** | **+0.2198** | [+0.095 ; +0.344] | +0.235 (95) | +0.182 (38) | +292.27 € |
| XAUUSD | contre la tendance | 116 | 29.3 % | **0.207** | **-0.6707** | [-0.829 ; -0.512] | -0.557 (86) | -0.996 (30) | -777.97 € |
| XAUUSD | sans tendance nommée | 638 | 54.9 % | **0.684** | **-0.1567** | [-0.223 ; -0.090] | -0.128 (475) | -0.241 (163) | -1 000.01 € |

**Ce que ce tableau dit, en une phrase :** sur 290 opérations prises dans le sens d'une tendance nommée, les deux marchés gagnent (+0.2975 R et +0.2198 R) ; sur 244 opérations prises contre elle, les deux perdent presque le double (-0.5045 R et -0.6707 R). L'écart vaut **0.802 R** par opération sur le bitcoin et **0.890 R** sur l'or.

Et le résultat tient **dans les deux fenêtres**, ce qui est la seule chose qui le distingue d'une observation de régime :

| marché | accord | esp. entraînement | esp. validation |
|---|---|---|---|
| BTCUSD | dans la tendance | +0.2887 | +0.3222 |
| BTCUSD | contre la tendance | -0.5060 | -0.4989 |
| XAUUSD | dans la tendance | +0.2348 | +0.1822 |
| XAUUSD | contre la tendance | -0.5572 | -0.9960 |

Le signe ne s'inverse nulle part : dans la tendance, les quatre nombres sont positifs (+0.289 / +0.322 / +0.235 / +0.182), et contre elle, les quatre sont négatifs (-0.506 / -0.499 / -0.557 / -0.996).

### Ce que cet effet n'est pas, et ce qui le sépare d'une règle

1. **Ce n'est pas un candidat au seuil du projet.** Le PF de la case « dans la tendance » vaut 2.415, au-dessus de 1,20 — mais il est mesuré sur les fenêtres qui ont servi à le trouver. Il doit passer le protocole complet, les neuf portes, sur une fenêtre qu'il n'a pas touchée.
2. **Le gain du sous-ensemble est réel mais petit, et il ne finance pas les autres.** +759.28 € gagnés sur les opérations qui suivent la tendance, contre -1 423.78 € perdus sur celles qui la contredisent et -2 147.98 € perdus sur celles qui n'ont pas de tendance nommée. Filtre appliqué, le total resterait négatif sur ces deux fenêtres : il faut donc lire ce chiffre comme un **avantage localisé**, pas comme une rentabilité.
3. **Ce n'est pas un filtre qui ne refuse rien.** Il refuserait 244 opérations sur 1 801 mesurées, soit 13.5 %. Un filtre qui coupe 14 % des entrées et double le PF mérite le protocole ; il ne le remplace pas.
4. **Le filtre refuserait moins que ce tableau ne le suggère.** Le harnais n'ouvre qu'une position à la fois : des signaux contre-tendance ont déjà été écartés faute de place (`skipped_no_room` = 1 296 sur l'ensemble des fenêtres). Le gain d'un refus explicite serait donc plus petit que la colonne « contre la tendance » ne le laisse croire.
5. **Aucune case de volatilité ne le porte.** Le croisement tendance × volatilité est dégénéré sur ces données : 913 des 914 opérations du bitcoin et **toutes** celles de l'or portent le verdict `normal`. Le verdict de volatilité nommé ne sépare rien ici, exactement comme `session_volatility.py` l'avait déjà constaté sur les règles précédentes.

---

## 6. `mae_r` et `mfe_r` : deux diagnostics, deux réponses

Ces deux nombres ne se lisent pas dans le PnL. Conventions du harnais, et elles comptent : la barre d'entrée compte dans l'excursion, et `mae_r ≥ 1` signifie que **le stop a été touché**, puisque le stop vaut 1 R par construction.

**BTCUSD**

| fenêtre | n | MAE méd. | stop touché | MFE méd. | gagnants : n / MFE méd. | perdants : n / MAE méd. |
|---|---|---|---|---|---|---|
| tout | 914 | 0.816 | 45.3 % | 0.765 | 505 / 0.940 | 409 / 1.196 |
| entrainement | 709 | 0.789 | 45.3 % | 0.766 | 392 / 0.937 | 317 / 1.204 |
| validation | 205 | 0.865 | 45.4 % | 0.758 | 113 / 0.952 | 92 / 1.180 |

**XAUUSD**

| fenêtre | n | MAE méd. | stop touché | MFE méd. | gagnants : n / MFE méd. | perdants : n / MAE méd. |
|---|---|---|---|---|---|---|
| tout | 887 | 0.858 | 46.0 % | 0.750 | 486 / 0.946 | 401 / 1.211 |
| entrainement | 656 | 0.794 | 43.4 % | 0.755 | 378 / 0.956 | 278 / 1.212 |
| validation | 231 | 1.015 | 53.2 % | 0.725 | 108 / 0.933 | 123 / 1.208 |

**Histogramme de `mae_r`, BTCUSD, 914 opérations :** `0-0.25` 188 (21 %) · `0.25-0.5` 154 (17 %) · `0.5-0.75` 94 (10 %) · `0.75-1` 64 (7 %) · `1-1.25` 246 (27 %) · `1.25-1.5` 81 (9 %) · `1.5-2` 53 (6 %) · `2-3` 30 (3 %) · `3+` 4 (0 %)

**Histogramme de `mfe_r`, BTCUSD, 914 opérations :** `0-0.25` 176 (19 %) · `0.25-0.5` 101 (11 %) · `0.5-0.75` 104 (11 %) · `0.75-1` 256 (28 %) · `1-1.25` 116 (13 %) · `1.25-1.5` 46 (5 %) · `1.5-2` 42 (5 %) · `2-3` 21 (2 %) · `3+` 3 (0 %)

**Histogramme de `mae_r`, XAUUSD, 887 opérations :** `0-0.25` 181 (20 %) · `0.25-0.5` 136 (15 %) · `0.5-0.75` 93 (10 %) · `0.75-1` 69 (8 %) · `1-1.25` 230 (26 %) · `1.25-1.5` 83 (9 %) · `1.5-2` 72 (8 %) · `2-3` 18 (2 %) · `3+` 5 (1 %)

**Histogramme de `mfe_r`, XAUUSD, 887 opérations :** `0-0.25` 161 (18 %) · `0.25-0.5` 116 (13 %) · `0.5-0.75` 126 (14 %) · `0.75-1` 233 (26 %) · `1-1.25` 114 (13 %) · `1.25-1.5` 48 (5 %) · `1.5-2` 31 (3 %) · `2-3` 11 (1 %) · `3+` 7 (1 %)

### Le stop est-il trop large ? Non : il est touché

La question posée était : « un perdant dont `mae_r` médiane vaut 0,4 R dit que le stop est trop large ». La mesure dit l'inverse. La médiane des perdants vaut **1.196 R** sur le bitcoin et **1.211 R** sur l'or, et le nombre de perdants dont l'excursion adverse est restée sous 0,5 R est de **0** et **0**. Le stop est touché dans 45.3 % et 46.0 % des opérations : il n'est pas trop large, il est **atteint**. Resserrer ou élargir le stop ne peut pas corriger une perte qui vient du **choix de l'entrée**, et c'est exactement ce que le tableau du §5 montre.

### La sortie coupe-t-elle trop tôt ? Non : le marché ne va pas plus loin

Le `mfe_r` médian des **gagnants** vaut 0.940 R sur le bitcoin et 0.946 R sur l'or, pour une sortie à 0,8 R : les gagnants dépassent leur objectif de +0.140 R et +0.146 R **en médiane**. Le nombre de gagnants qui atteignent réellement le second objectif (1,5 R) vaut **66 / 505** (13.1 %) et **49 / 486** (10.1 %).

**C'est le résultat qui explique le balayage de géométrie, et il le complète.** La sortie n'est pas trop serrée : elle est **au bon endroit**. Le bitume est arithmétique — l'espérance d'un système qui gagne `w` fois 0,8 R et perd `(1 − w)` fois 1 R vaut `1,8 w − 1`, donc le seuil de rentabilité est `w = 55,6 %`. Les taux de réussite mesurés sont 55.3 % et 54.8 % : juste au-dessous. Élargir les objectifs ne peut pas réparer une réussite insuffisante, et c'est pourquoi la seule alternative élargie perd 370 € hors échantillon.

**Et le `mfe_r` des perdants tranche le diagnostic de l'entrée :** seuls 6 perdants sur 409 (bitcoin) et 6 sur 401 (or) ont approché le premier objectif avant de perdre. Un perdant qui n'a jamais approché son objectif n'est pas un gagnant manqué : c'est une entrée qui n'avait pas de marge.

---

## 7. Réponse explicite à la question posée

> **Existe-t-il une slice avec un avantage mesurable, ou l'espérance est-elle négative partout ?**

**Il existe une slice avec un avantage mesurable, et elle est unique dans sa forme : l'entrée qui va dans le sens de la tendance nommée à l'instant de l'entrée.** 8 slices survivent à la correction de tests multiples sur 164 hypothèses *(10 avant la correction de l'erratum ci-dessus)*, mais elles ne portent que deux effets (achat dans une tendance haussière, vente dans une tendance baissière), vus sous quatre noms dont deux dimensions mécaniquement équivalentes. L'espérance n'est donc **pas** négative partout : elle est négative pour les entrées contre la tendance (-0.5045 R et -0.6707 R), quasi nulle pour les entrées sans tendance nommée (-0.1825 R et -0.1567 R), et positive pour celles qui la suivent (+0.2975 R et +0.2198 R).

**Ce que cela vaut, et ce que cela ne vaut pas.** C'est une **hypothèse mesurée sur les fenêtres d'entraînement et de validation**, pas une découverte validée : elle n'a franchi aucune des neuf portes, elle n'est pas activée, et `config/` n'est pas touché. Elle a plusieurs raisons d'être fragile, et elles sont écrites ici plutôt que découvertes plus tard :

- **les opérations ne sont pas indépendantes** — elles se groupent par régime et se chevauchent dans le temps, donc les p-values publiées sont optimistes, quelle que soit la correction appliquée ;
- **le mécanisme est déjà connu du code** (§5) : le `trend_filter` existe, mesure 12 % des signaux et attend un test hors échantillon depuis le 2026-10-10. Ce document ne fait que donner le chiffre qui manquait à cette attente ;
- **le gain net reste petit et ne suffit pas** : +759.28 € sur 290 opérations, quand les opérations sans tendance nommée en perdent -2 147.98 € ; et le plancher de PF à 1,20 n'est atteint par aucune règle du projet ;
- **l'or est le marché le plus fragile** : sa validation ne porte que 38 opérations dans la tendance, et sa vente contre-tendance y perd -0.996 R ;
- **le jeu scellé reste fermé.** Le tester là-dessus dépenserait la seule fenêtre vraiment hors échantillon du projet, et la décision d'ouvrir appartient à l'opérateur, pas à cette mesure.

**Ce qu'il faudrait pour conclure autrement, par ordre de valeur :** (1) écrire la règle de refus et la soumettre aux neuf portes du protocole sur l'entraînement et la validation seuls ; (2) mesurer séparément le coût du refus — combien d'opérations perdues pour combien de pertes évitées, `skipped_no_room` inclus ; (3) ne décider de l'ouverture du jeu scellé qu'après, et une seule fois.

---

## 8. Reproduire

```bash
# la mesure complète (deux marchés, 59 999 bougies chacun, ~4 min)
uv run python scripts/backtest/edge_location.py --self-check --monte-carlo-check

# vérification de plomberie, jeu tronqué (ce n'est PAS une mesure)
uv run python scripts/backtest/edge_location.py --bars 9000 --no_write
```

**Sorties brutes de cette mesure :**

- `docs/research/edge-location/edge-location.json` — la mesure complète : baseline et sa confrontation aux chiffres publiés, découpage des 20 dimensions pour les deux marchés, coupures, diagnostic des excursions, correction de tests multiples, confrontation Student / randomisation.
- `docs/research/edge-location/run-datasets-volume.txt` — la sortie standard intégrale, telle quelle.

**Lecture seule, et ce qui n'a pas été touché :** le jeu scellé n'a pas été ouvert (`confirm_holdout` reste faux, `SealedSet.unlock` n'a jamais reçu son jeton), aucun jeu gelé n'a été réécrit, `docs/research/thresholds.json` n'a pas été touché, `config/` n'a pas été touché, aucun ordre n'a été envoyé, aucun module de production n'a été modifié (`scripts/backtest/` et `docs/research/` seulement).

---

**Méthode.** `scripts/backtest/edge_location.py`. Les statistiques de Student sont implémentées dans le script et vérifiées contre des valeurs tabulées avant toute mesure. Les indicateurs du signal sont capturés par une enveloppe de la stratégie dans le processus de mesure ; `backtest/harness.py` n'est pas modifié.
