# « Des petits gains très répétitifs » : ce que la mesure du dépôt décide

**Date** : 2026-10-08 · **Demande traitée** : « je veux qu'il fasse du scalping avec le moindre gain,
peu importe la stratégie, je veux juste les petits gains très répétitifs »
**Nature de ce document** : mesure. Il ne modifie aucun seuil, aucune stratégie, aucun manifeste.
Il ne fait passer aucun candidat.

## Réponse en trois chiffres

| Question | Réponse mesurée | Chiffre qui décide |
|---|---|---|
| **1. Quelle espérance brute faudrait-il pour être net-positif ?** | En **M1 sur l'or**, il faudrait multiplier l'espérance brute par **×4,84** (0,0432 → 0,2091 R/op) avec le risque nominal du backtest ; **×4,10** au dimensionnement réel du compte démo. En **points de prix** : 0,506 pt/op requis contre 0,105 pt/op mesuré. Sur BTCUSD M1 : **×1,89** (×1,64 au réel). | Le coût par opération vaut **484 %** du gain brut en M1 or, 229 % en M5, 112 % en M15 |
| **2. Que fait une suite d'opérations à espérance négative, et que fait une martingale ?** | Sur 1000 opérations en M1 or, **P(compte encore positif) = 0,000** ; en M1 BTC au dimensionnement réel, 0,034. En martingale, **7 pertes consécutives** bloquent la série (la mise suivante, 3 517,87 €, ne tient plus dans les 2 006,28 € restants) et **8 l'effacent** (7 008,25 €). **P(une suite de 7) = 1,0000 sur 1000 opérations** ; ruine simulée **97,1 % à 99,9 %** selon la cellule. | Une mise constante ruine dans **0,0 % à 12,0 %** des cas ; la doubler après perte porte la ruine à **97,1 % – 99,9 %** |
| **3. Au-delà de combien d'opérations par jour la fréquence est-elle auto-destructrice ?** | La perte est **linéaire en fréquence avec un coefficient négatif** : le seul seuil non destructeur en M1 est **0 opération/jour**. Au rythme mesuré (34,5 op/j en M1 or, 76,5 en M1 BTC) : **−127,03 €/jour** et **−168,41 €/jour**, soit **−2,31 %** et **−3,06 %** du solde **par jour**. Le frein de perte quotidienne de 2 % (109,93 €) est atteint en **0,87 jour de marché** sur l'or M1. | **3,68 € perdus par opération** en M1 or ; 1 €/jour de perte tolérée plafonne à **0,3 opération/jour** |

Le reste de ce document donne la provenance de chaque chiffre, les hypothèses, et ce que la mesure
**ne** dit pas.

---

## 0. Ce qui a été mesuré, avec quoi, et sur quoi

### 0.1 Les données

Les six jeux gelés du dépôt, déjà produits et empreintés par la campagne précédente
(`docs/research/2026-10-08-scalping-m5-m1.md`, §1) : 11 999 bougies chacun, XAUUSD et BTCUSD en
M15, M5 et M1. Les fenêtres de **validation** — celles que lisent les portes — font 2 399 bougies
et couvrent 36,1 j (M15 or) à 1,7 j (M1 bitcoin).

Ce document **ne refait pas** cette mesure et ne la contredit pas : il la reprend, la vérifie à
l'identique, et l'utilise. Le script `scripts/analysis/scalping_truth.py` reproduit exactement les
totaux de `docs/research/cost-attribution.txt` (mêmes opérations, même brut, même net, même coût,
même décomposition spread / slippage / commission, au centime).

### 0.2 Le modèle de coûts — lu dans le code, pas supposé

`scripts/backtest/run_campaign.py`, fonction `config_for` (lignes 171-183) :

| Poste | Valeur | Source |
|---|---|---|
| spread | `prix × 0,00005` = **0,5 point de base du prix** | `run_campaign.py` L174 |
| slippage | `prix × 0,00002` = **0,2 point de base du prix** | `run_campaign.py` L175 |
| commission | **0,50 € par opération** (forfait) | `run_campaign.py` L176 |
| risque nominal du backtest | **10 € par opération** | `backtest/harness.py` L36, `DEFAULT_RISK_EUR` |

Le harness facture `demi-spread + slippage` à l'entrée **et** à la sortie
(`backtest/harness.py` L313, L318, L393). Le coût en prix d'un aller-retour vaut donc, quel que
soit le marché et quel que soit l'horizon :

> **0,5 bp (une fois) + 2 × 0,2 bp (deux fois) = 0,9 point de base du prix**, plus 0,50 € de
> commission.

Le R est défini par la géométrie de la stratégie : `distance de stop = stop_atr_multiplier × ATR`,
soit **1,5 × ATR(14)** avec les paramètres que la campagne a utilisés
(`run_campaign.py` L116-117, `strategies/library/witness.py` L72-78, `harness.py` L415). Comme le
spread et le slippage sont des **distances de prix fixes** et que le stop est un **multiple d'ATR**,
le coût en R varie en `1 / ATR`. C'est le mécanisme entier du problème.

### 0.3 Le compte démo réel

Lu en lecture seule dans la table `account_snapshots` (`scripts/analysis/account-snapshot.json`) :

| | Valeur |
|---|---|
| solde **et** equity | **5 496,67 €** |
| capturé le (UTC) | 2026-10-08T03:50:24Z |
| lignes dans `account_snapshots` | 39, depuis 2026-10-08T00:20:35Z |
| opérations clôturées | **0** |
| risque par opération | **0,5 %** = **27,48 €** |

Le risque par opération n'est pas recopié d'une consigne : il vient de `config/agent.yaml`
(`risk.simulated.risk_per_trade_pct: 0.5`), et c'est bien le profil `simulated` qui s'applique en
mode DEMO (`risk/model.py` L218-222 : LIVE prend `live`, **tout le reste** prend `simulated`).

**Deux repères sont donc donnés partout** : le risque nominal du backtest (**10 €/op**), qui est
celui des chiffres publiés dans `cost-attribution.txt`, et le risque réel du compte
(**27,48 €/op**). Le second n'est pas une consolation : la commission forfaitaire de 0,50 € y pèse
**1,8 %** du R au lieu de **5,0 %**, mais la part spread+slippage ne bouge pas d'un centime — elle
ne dépend que de l'ATR et du prix.

### 0.4 Ce que la mesure a produit, cellule par cellule

Fenêtre de validation, 3 candidats confondus (c'est le panier déployé), coûts chargés à **1×** :

| UT | Marché | Op. | Jours | Op./jour | Brut € | Net € | Coût € | Coût/op. | Brut/op. | Net/op. |
|---|---|---|---|---|---|---|---|---|---|---|
| M15 | XAUUSD | 124 | 36,11 | 3,43 | +95,01 | −11,73 | 106,74 | **0,0861 R** | **0,0766 R** | −0,0095 R |
| M5 | XAUUSD | 130 | 10,75 | 12,10 | +61,89 | −80,06 | 141,95 | **0,1092 R** | **0,0476 R** | −0,0616 R |
| M1 | XAUUSD | 130 | 3,77 | 34,48 | +56,16 | −215,61 | 271,78 | **0,2091 R** | **0,0432 R** | **−0,1659 R** |
| M15 | BTCUSD | 106 | 25,06 | 4,23 | −35,24 | −109,71 | 74,48 | 0,0703 R | −0,0332 R | −0,1035 R |
| M5 | BTCUSD | 128 | 8,36 | 15,30 | −104,71 | −230,28 | 125,56 | 0,0981 R | −0,0818 R | −0,1799 R |
| M1 | BTCUSD | 128 | 1,67 | 76,54 | +160,66 | −143,18 | 303,84 | 0,2374 R | +0,1255 R | −0,1119 R |

Ces six lignes reproduisent `cost-attribution.txt` au centime. La conclusion déjà acquise tient :
**le coût par opération monte quand l'unité de temps descend (0,086 → 0,109 → 0,209 R sur l'or)
pendant que l'espérance brute par opération baisse (0,077 → 0,048 → 0,043 R).**

Détail de la décomposition, et ATR médian de la fenêtre :

| UT | Marché | Spread € | Slippage € | Commission € | ATR médian | Distance de stop effective | 1,5 × ATR médian |
|---|---|---|---|---|---|---|---|
| M15 | XAUUSD | 25,06 | 19,68 | 62,00 | 8,0635 | 11,62 | 12,10 |
| M5 | XAUUSD | 43,36 | 33,60 | 65,00 | 4,3998 | 6,47 | 6,60 |
| M1 | XAUUSD | 119,45 | 87,33 | 65,00 | 1,6519 | 2,42 | 2,48 |
| M15 | BTCUSD | 12,00 | 9,48 | 53,00 | 213,71 | 282,66 | 320,57 |
| M5 | BTCUSD | 34,72 | 26,85 | 64,00 | 116,08 | 147,53 | 174,12 |
| M1 | BTCUSD | 114,35 | 125,48 | 64,00 | 38,11 | 39,93 | 57,16 |

La « distance de stop effective » n'est pas postulée : elle est déduite du coût spread+slippage
réellement payé (`(spread + 2 × slippage) / (coût spread+slippage en R)`, moyenne harmonique
pondérée par opération). Elle vaut 2,42 points sur l'or M1 pour un ATR médian de 1,65 — l'écart
avec `1,5 × ATR` vient de la zone d'entrée (`entry_zone_atr = 0,1`) et du décalage du stop par les
coûts.

---

## 1. Question 1 — quelle espérance brute faudrait-il pour être net-positif ?

L'espérance nette est nulle quand l'espérance brute égale exactement le coût par opération :

```
brut requis (R/op) = (spread + 2 × slippage) / (1,5 × ATR)  +  commission / risque
```

Les trois colonnes à lire sont : le brut requis en R, le même en **points de prix**
(`brut requis × distance de stop`), et le **multiple** à atteindre par rapport à ce qui a été
mesuré.

### Repère A — risque nominal du backtest, 10 €/opération (les chiffres publiés)

| UT | Marché | Brut mesuré R/op | Coût R/op | **Brut requis R/op** | Requis €/op | **Requis pts/op** | Mesuré pts/op | **Multiple** |
|---|---|---|---|---|---|---|---|---|
| M15 | XAUUSD | 0,0766 | 0,0861 | 0,0861 | 0,86 | 1,0006 | 0,8906 | **×1,12** |
| M5 | XAUUSD | 0,0476 | 0,1092 | 0,1092 | 1,09 | 0,7070 | 0,3083 | **×2,29** |
| M1 | XAUUSD | 0,0432 | 0,2091 | **0,2091** | **2,09** | **0,5061** | **0,1046** | **×4,84** |
| M15 | BTCUSD | −0,0332 | 0,0703 | 0,0703 | 0,70 | 19,8603 | −9,3959 | signe à inverser |
| M5 | BTCUSD | −0,0818 | 0,0981 | 0,0981 | 0,98 | 14,4728 | −12,0694 | signe à inverser |
| M1 | BTCUSD | 0,1255 | 0,2374 | 0,2374 | 2,37 | 9,4775 | 5,0114 | **×1,89** |

### Repère B — compte démo réel, 27,48 €/opération

| UT | Marché | Brut mesuré R/op | Coût R/op | **Brut requis R/op** | Requis €/op | **Requis pts/op** | Mesuré pts/op | **Multiple** |
|---|---|---|---|---|---|---|---|---|
| M15 | XAUUSD | 0,0766 | 0,0543 | 0,0543 | 1,49 | 0,6309 | 0,8906 | **×0,71** (déjà au-dessus) |
| M5 | XAUUSD | 0,0476 | 0,0774 | 0,0774 | 2,13 | 0,5011 | 0,3083 | **×1,63** |
| M1 | XAUUSD | 0,0432 | 0,1773 | **0,1773** | **4,87** | **0,4291** | **0,1046** | **×4,10** |
| M15 | BTCUSD | −0,0332 | 0,0385 | 0,0385 | 1,06 | 10,8697 | −9,3959 | signe à inverser |
| M5 | BTCUSD | −0,0818 | 0,0663 | 0,0663 | 1,82 | 9,7801 | −12,0694 | signe à inverser |
| M1 | BTCUSD | 0,1255 | 0,2056 | 0,2056 | 5,65 | 8,2076 | 5,0114 | **×1,64** |

**La phrase qui répond à l'opérateur :** en M1 sur l'or, il faudrait **multiplier par 4,84**
l'espérance brute par opération (4,10 au dimensionnement réel du compte) pour seulement atteindre
le seuil de rentabilité — c'est-à-dire demander au marché 0,506 point de prix par opération là où
la stratégie en capte 0,105.

### Le chiffre qui rend le M1 impossible autrement qu'en le multipliant

Sur l'or M1 au risque nominal de 10 €, **la commission seule (0,050 R) dépasse l'espérance brute
mesurée (0,0432 R)**. Même avec un spread **et** un slippage ramenés à zéro, la cellule perd de
l'argent. En M5 or, la commission seule (0,050 R) dépasse aussi le brut mesuré (0,0476 R). Le
scalping M1/M5 sur l'or n'est donc pas seulement « cher » : il est négatif **avant** que le premier
centime de spread soit payé, dès lors que le risque par opération n'est que de 10 €.

Au dimensionnement réel (27,48 €/op), la commission retombe à 0,0182 R et le seuil redevient
fini — mais il exige un ATR de **10,263 points** sur l'or M1, soit **6,21 fois** l'ATR médian
mesuré (1,652). Autrement dit : il faudrait que l'or bouge six fois plus vite, à coût de
transaction identique en points de base, pour que la cellule M1 devienne rentable à espérance
brute inchangée.

### Ce que la mesure dit aussi, et qui n'est pas à la gloire du verdict

**1 cellule candidate sur 18** franchit la porte `costs` (PF net ≥ 1,20) **à 1× coûts** :
`M5 XAUUSD slow-2.5R`, PF **1,208** (22 opérations). Les 17 autres sont entre 0,518 et 1,187.
Cette unique cellule est **rejetée** par la correction de sélection multiple
(p-value 0,3756, Benjamini-Hochberg α = 0,10 : **0 survivant sur 6**) et par la porte
`out_of_sample` (**rétention 0,00**). Un franchissement isolé sur 18 essais ne suffit donc pas à
en faire une piste : c'est la correction de sélection multiple qui tranche ce point, et elle
tranche à 0 sur 6 dans les trois unités de temps.

**Et une cellule est nette-positive au dimensionnement réel** : `M15 XAUUSD`, +0,0223 R/op, soit
**+0,614 €/op** et **+2,11 €/jour** à 3,43 op/jour (+0,038 % du solde par jour). Elle est, elle
aussi, rejetée : rétention hors échantillon **0,00** et 0 survivant sur 6 après correction. Mais
elle doit être dite : le problème n'est pas « les coûts tuent tout », c'est « les coûts tuent tout
ce qui est assez fréquent pour être du scalping ».

---

## 2. Question 2 — ce qu'une série d'opérations fait réellement

### 2.1 Le protocole, sans loi postulée

Pour chaque cellule, on rejoue une marche aléatoire de **1 000 pas** (4 000 chemins, graine
20261008) où **chaque pas tire une issue réellement observée** dans le run sans coûts de la
campagne (bootstrap sur les multiples R mesurés) **puis paie le coût mesuré**. Aucune distribution
n'est supposée : la forme (queues, asymétrie, taux de perte) est celle que le backtest a produite.
Les nombres sont donnés aux deux repères de risque, comme en §1.

### 2.2 Probabilité d'être encore positif

| UT | Marché | Esp. nette R/op | σ R | **P(>0) après 100** | **P(>0) après 500** | **P(>0) après 1000** | E[1000] |
|---|---|---|---|---|---|---|---|
| M15 | XAUUSD | −0,0095 | 1,382 | 0,473 | 0,463 | **0,418** | −9,5 R |
| M5 | XAUUSD | −0,0616 | 1,449 | 0,336 | 0,172 | **0,085** | −61,6 R |
| M1 | XAUUSD | −0,1659 | 1,361 | 0,108 | 0,003 | **0,000** | −165,9 R |
| M15 | BTCUSD | −0,1035 | 1,310 | 0,217 | 0,044 | **0,008** | −103,5 R |
| M5 | BTCUSD | −0,1799 | 1,370 | 0,094 | 0,002 | **0,000** | −179,9 R |
| M1 | BTCUSD | −0,1119 | 1,383 | 0,210 | 0,037 | **0,005** | −111,9 R |

*Repère risque nominal 10 €/op. Au repère démo 27,48 €/op : M1 XAU 0,164 / 0,018 / **0,000** ;
M5 XAU 0,418 / 0,322 / **0,254** ; M15 XAU 0,553 / 0,636 / **0,682** ; M1 BTC 0,277 / 0,103 /
**0,034** ; M5 BTC 0,134 / 0,009 / **0,000** ; M15 BTC 0,293 / 0,110 / **0,042**.*

Le contrôle est cohérent : l'approximation normale donne les mêmes probabilités à ±0,005
(`Φ(dérive × N / (σ√N))`), et **P(ne jamais passer sous zéro)** vaut 0,000 partout sauf sur
M15 or (0,011 au nominal, 0,024 au réel), M5 or (0,002 / 0,005) et M1 bitcoin au réel (0,001).
Autrement dit : sur la cellule **M1 or**, **aucun chemin sur 4 000** n'est positif au terme des
1 000 opérations, et **aucun** n'est même resté au-dessus de zéro à un moment quelconque.

Le résultat est arithmétique, pas psychologique : à 0,166 R perdus par opération, 1000 opérations
coûtent 166 R, et l'écart-type cumulé (1,36 × √1000 ≈ 43 R) est près de **quatre fois plus petit**
que cette dérive. Il n'y a pas de « chance » à espérer à cette échelle.

### 2.3 La martingale : ce qu'un doublement après perte produit exactement

Mise initiale **27,48 €** (0,5 % de 5 496,67 €), doublée après chaque opération perdante,
réinitialisée après une gagnante. Le calcul est exact, pas simulé, pour les seuils :

- Après **k** pertes consécutives, le cumul perdu vaut `27,48 × (2^k − 1)` et la mise suivante
  vaut `27,48 × 2^k`.
- **k = 7** → cumul perdu 3 490,39 €, solde restant **2 006,28 €**, mise suivante **3 517,87 €** :
  la martingale **n'a plus de coup à jouer**. C'est la ruine opérationnelle.
- **k = 8** → cumul perdu **7 008,25 €** : le solde est **effacé** (5 496,67 €).

La probabilité d'atteindre ces seuils est donnée par récurrence exacte (pas d'approximation), avec
le taux de perte **observé** de chaque cellule :

| UT | Marché | P(perte/op) | **P(suite de 7)** à 100 / 500 / **1000** op. | P(suite de 8) à 1000 op. |
|---|---|---|---|---|
| M1 | XAUUSD | 0,623 | 0,770 · 0,9995 · **1,0000** | 0,9999 |
| M1 | BTCUSD | 0,586 | 0,636 · 0,995 · **1,0000** | 0,9976 |
| M5 | XAUUSD | 0,608 | 0,717 · 0,999 · **1,0000** | 0,9996 |
| M5 | BTCUSD | 0,656 | 0,866 · 1,000 · **1,0000** | 1,0000 |
| M15 | XAUUSD | 0,613 | 0,735 · 0,999 · **1,0000** | 0,9997 |
| M15 | BTCUSD | 0,651 | 0,853 · 1,000 · **1,0000** | 1,0000 |

**Une suite de 7 pertes consécutives survient avec probabilité 1,0000 sur 1000 opérations**, dans
les six cellules. Sur 100 opérations seulement, elle survient déjà dans 64 % à 87 % des cas.

Simulation complète (1 000 opérations, 1 000 chemins), martingale contre mise constante — c'est la
comparaison qui isole l'effet du doublement :

| UT | Marché | Ruine martingale | Ruine mise constante | Gain médian martingale | Gain médian mise constante | Moyenne martingale | Moyenne mise constante |
|---|---|---|---|---|---|---|---|
| M1 | XAUUSD | **0,996** | 0,069 | −3 268,16 € | −3 687,03 € | −2 552,21 € | −3 705,67 € |
| M1 | BTCUSD | **0,979** | 0,004 | −3 371,72 € | −2 174,67 € | −1 742,45 € | −2 180,61 € |
| M5 | XAUUSD | **0,981** | 0,000 | −2 997,47 € | −735,87 € | −461,79 € | −780,41 € |
| M5 | BTCUSD | **0,999** | 0,120 | −3 297,13 € | −4 042,95 € | −2 679,15 € | −4 072,37 € |
| M15 | XAUUSD | **0,971** | 0,000 | −2 749,71 € | +615,81 € | +1 813,85 € | +598,54 € |
| M15 | BTCUSD | **0,995** | 0,001 | −2 902,73 € | −1 987,58 € | −1 078,98 € | −2 004,61 € |

Trois faits, sans jugement :

1. **Le doublement ne change pas l'espérance, il change la forme.** Il achète une médiane parfois
   moins mauvaise (M1 or : −3 268 € au lieu de −3 687 €) en échange d'une ruine quasi certaine
   (0,996 au lieu de 0,069). La moyenne, elle, reste négative partout — sauf sur M15 or, où la
   cellule a une espérance nette positive au dimensionnement réel, et où la martingale transforme
   +615,81 € de médiane en **−2 749,71 €** de médiane contre **+1 813,85 €** de moyenne : le
   survivant paie pour les 97,1 % de comptes effacés.
2. **La ruine n'est pas un accident de parcours, c'est l'issue attendue.** Sept pertes consécutives
   ont une probabilité 1,0000 sur 1000 opérations dans les six cellules ; la stratégie perd
   réellement 59 % à 66 % de ses opérations, ce qui rend la série quasi inévitable.
3. **La structure du compte interdit de toute façon la martingale à terme.**
   `config/agent.yaml` plafonne à `max_trades_per_day: 4` (mode simulé) et ce plafond est appliqué
   par le moteur de risque (`risk/checks.py` L275-278, `count < limit`). Il faut **deux jours de
   marché** pour aligner 8 opérations ; la martingale demande au contraire des opérations
   **consécutives**. Ce n'est pas une protection : c'est un délai.

---

## 3. Question 3 — au-delà de quelle fréquence la fréquence devient auto-destructrice

Le raisonnement est linéaire, et c'est tout le problème :

```
perte par jour (€) = opérations par jour × (coût/op − brut/op) × risque par opération (€)
```

Le coefficient `(coût/op − brut/op)` est **négatif dans cinq des six cellules** au dimensionnement
réel. Il n'y a donc pas de « seuil de fréquence » à chercher au-delà duquel ça se dégraderait : la
perte est proportionnelle à la fréquence, et **0 opération par jour est le seul régime non
perdant**. Ce qui se calcule, en revanche, c'est la vitesse à laquelle un rythme donné vide le
compte.

Solde démo **5 496,67 €**, risque **27,48 €/op** (0,5 %) :

| UT | Marché | Op./jour mesuré | Coût/op. | Net/op. | **Net €/op.** | **€/jour** | **%/jour** | Jours jusqu'à −2 % | Jours jusqu'à −10 % |
|---|---|---|---|---|---|---|---|---|---|
| M1 | XAUUSD | 34,48 | 0,1773 R | −0,1340 R | **−3,684 €** | **−127,03 €** | **−2,311 %** | **0,87** | 4,33 |
| M1 | BTCUSD | 76,54 | 0,2056 R | −0,0801 R | **−2,200 €** | **−168,41 €** | **−3,064 %** | **0,65** | 3,26 |
| M5 | XAUUSD | 12,10 | 0,0774 R | −0,0298 R | −0,818 € | −9,90 € | −0,180 % | 11,10 | 55,52 |
| M5 | BTCUSD | 15,30 | 0,0663 R | −0,1481 R | −4,070 € | −62,28 € | −1,133 % | 1,77 | 8,83 |
| M15 | BTCUSD | 4,23 | 0,0385 R | −0,0717 R | −1,970 € | −8,33 € | −0,152 % | 13,19 | 65,96 |
| M15 | XAUUSD | 3,43 | 0,0543 R | +0,0223 R | +0,614 € | +2,11 € | +0,038 % | — | — |

Les deux colonnes de droite se lisent contre les freins déjà configurés : `daily_loss_pct: 2`
(**109,93 €**) et `max_drawdown_pct: 10` (**549,67 €**). **En M1 sur l'or, le frein de perte
quotidienne est atteint en 0,87 jour de marché — moins d'une journée.** En M1 sur bitcoin, en
0,65 jour.

### Le plafond de fréquence, en opérations par jour, pour un budget de perte donné

| UT | Marché | 1 €/jour | 5 €/jour | 10 €/jour |
|---|---|---|---|---|
| M1 | XAUUSD | **0,3 op/j** | 1,4 op/j | 2,7 op/j |
| M1 | BTCUSD | **0,5 op/j** | 2,3 op/j | 4,5 op/j |
| M5 | XAUUSD | 1,2 op/j | 6,1 op/j | 12,2 op/j |
| M5 | BTCUSD | 0,2 op/j | 1,2 op/j | 2,5 op/j |
| M15 | BTCUSD | 0,5 op/j | 2,5 op/j | 5,1 op/j |
| M15 | XAUUSD | aucun plafond (espérance nette positive) | | |

À titre de repère, le plafond configuré `max_trades_per_day: 4` correspond, en M1 or, à
**−14,74 €/jour** (4 × 3,684 €). C'est l'un des freins arithmétiques déjà actifs, et il n'est pas
dans la stratégie : il est dans le moteur de risque.

### Et si l'ATR montait ? Le seuil de rentabilité en volatilité

À espérance brute **mesurée conservée**, la cellule devient rentable quand l'ATR dépasse :

```
ATR de rentabilité = prix × 0,9 bp / (1,5 × (brut mesuré − commission / risque))
```

| Cellule | Risque 10 €/op | Risque démo 27,48 €/op |
|---|---|---|
| M1 XAUUSD | **aucun ATR ne suffit** : la commission seule (0,050 R) dépasse le brut mesuré (0,0432 R) | ATR ≥ **10,263** = **6,21 ×** l'ATR médian mesuré (1,652) |
| M15 XAUUSD | ATR ≥ 10,504 = 1,30 × l'ATR médian mesuré (8,064) | ATR ≥ 4,786 = **0,59 ×** l'ATR mesuré — déjà au-dessus |

---

## 4. Le coût par opération en fonction de l'ATR et du spread

La relation est analytique et exacte sous le modèle du projet :

```
coût/op (R) = prix × 0,9 bp / (1,5 × ATR)  +  commission / risque
              └── spread 0,5 bp + 2 × slippage 0,2 bp, aller-retour ──┘
```

Sur l'or M1 (prix de référence 4 278,15 €) :

| ATR | Stop 1,5 × ATR | Spread (pts) | Coût/op. à 10 €/op | Coût/op. à 27,48 €/op | Brut requis (pts) à 10 € | Brut mesuré − coût (10 €) |
|---|---|---|---|---|---|---|
| 0,250 | 0,375 | 0,21391 | 1,0768 R | 1,0449 R | 0,4038 | −1,0336 |
| 0,500 | 0,750 | 0,21391 | 0,5634 R | 0,5316 R | 0,4225 | −0,5202 |
| 1,000 | 1,500 | 0,21391 | 0,3067 R | 0,2749 R | 0,4600 | −0,2635 |
| **1,652** | **2,478** | 0,21391 | **0,2054 R** | **0,1736 R** | 0,5089 | **−0,1622** ← mesuré |
| 2,000 | 3,000 | 0,21391 | 0,1783 R | 0,1465 R | 0,5350 | −0,1351 |
| 4,000 | 6,000 | 0,21391 | 0,1142 R | 0,0824 R | 0,6850 | −0,0710 |
| 8,000 | 12,000 | 0,21391 | 0,0821 R | 0,0503 R | 0,9850 | −0,0389 |
| 16,000 | 24,000 | 0,21391 | 0,0660 R | 0,0342 R | 1,5850 | −0,0228 |

Les deux points à retenir :

1. **Le coût en R est un `1/ATR` pur, plus une constante.** C'est pour ça qu'il monte quand on
   descend d'unité de temps : de M15 à M1 sur l'or, l'ATR médian passe de 8,064 à 1,652 (÷ 4,9) et
   le coût par opération de 0,0861 à 0,2091 R (× 2,43) — pendant que l'espérance brute, elle,
   baisse de 0,0766 à 0,0432 R. Le rapport brut/coût tombe de 0,89 à 0,21.
2. **La commission est le seul poste qui ne s'exprime pas en ATR.** Elle vaut 0,050 R à 10 € de
   risque et 0,018 R à 27,48 €. C'est le seul levier de coût qu'un changement de taille de compte
   peut actionner — et il ne touche ni le spread ni le slippage.

À noter : le coût en **points de base du prix** est **identique sur l'or et sur le bitcoin**
(0,9 bp), et **identique en M15, M5 et M1**. Ce qui change d'une unité de temps à l'autre, ce n'est
pas le coût : c'est la volatilité qui le normalise.

---

## 5. Ce que cette mesure ne dit pas — réserves

1. **Les fenêtres de validation ne se recouvrent pas** (M15 : 27/07 → 01/09 ; M5 : 14/09 → 25/09 ;
   M1 : 02/10 → 06/10 sur l'or). Le nombre d'opérations par jour mêle donc un effet d'unité de
   temps et un effet de régime de marché. L'effet **coût par opération en R**, lui, est structurel
   (`1/ATR`) et c'est lui qui porte le verdict de la §1 ; la §3, elle, hérite du mélange.
2. **Le jeu M1 ne couvre que 12,8 jours sur l'or et 8,4 jours sur bitcoin**, et sa fenêtre de
   validation 3,77 j et 1,67 j. Ces fenêtres sont courtes : les taux de perte observés (0,586 à
   0,656) sont bruités, et les probabilités de la §2 en héritent directement. Le sens de l'erreur
   n'est pas connu ; l'écart de verdict, lui, est large (P(>0) après 1000 opérations = 0,000 contre
   0,085 en M5 or).
3. **Le repère « démo réel » suppose que la cellule se comporte pareil à 27,48 €/op qu'à 10 €/op.**
   Seule la commission est retraitée (0,50 € / risque) ; le reste du coût est en R et invariant.
   L'hypothèse est en revanche fausse sur un point : le volume réellement exécuté change le
   slippage du broker. Le modèle du projet charge un slippage **fixe en points de base du prix** —
   il ne modélise pas l'impact.
4. **Le bootstrap de la §2 rééchantillonne les issues observées**, ce qui suppose qu'elles sont
   indépendantes et identiquement distribuées. Une série de trading réelle a de la
   surdispersion (volatilité groupée, corrélation de régime). La conséquence est directionnelle :
   les queues réelles sont **plus épaisses** que celles simulées ici, donc la ruine est
   sous-estimée, pas surestimée.
5. **La martingale est simulée sans contraintes de broker** : ni lot minimum/maximum, ni marge
   requise, ni arrondi de volume, ni rejet. Le seul frein appliqué est le solde. En pratique
   `risk.checks.check_margin` et `check_spread` refuseraient une partie de ces mises bien avant la
   ruine — ce qui ne sauve pas le compte, mais change la forme de la fin.
6. **Les artefacts JSON des campagnes portent encore le défaut `costs`/`stress` documenté** dans
   `docs/research/2026-10-08-scalping-m5-m1.md` §4 : les champs `cost_net_profit` et
   `cost_net_profit_factor` y sont les valeurs **à 2× coûts**, identiques aux champs `stressed`.
   Tous les chiffres « 1× » de ce document viennent donc des champs `validation_net_profit` et du
   rejeu direct par le script, jamais des champs `cost_net_*`. C'est la seule façon de rester
   cohérent avec `cost-attribution.txt`.
7. **Deux portes sur neuf restent non évaluables** (`paper`, `risk`) : un backtest sur une histoire
   gelée ne produit ni 30 jours de temps réel ni les verdicts du moteur de risque de production.
   Ce document ne les évalue pas non plus.
8. **Les chiffres publiés par la campagne sont mesurés à 3 candidats confondus**, parce que c'est le
   panier réellement candidat à la promotion. Le détail par candidat est publié en §1 (« 1 cellule
   sur 18 ») : un lecteur qui ne regarderait que le meilleur candidat obtiendrait un tableau
   différent — et se heurterait à la correction de sélection multiple, qui est précisément là pour
   cela (**0 survivant sur 6** aux trois unités de temps, α = 0,10).
9. **Aucun jugement n'est porté ici sur la personne citée par l'opérateur.** Une affirmation
   publiée sur un réseau social n'est pas une mesure : elle ne nomme ni instrument, ni broker, ni
   coût, ni période, et n'est donc pas reproductible dans ce dépôt. Ce document ne compare rien à
   elle — il mesure ce que **ce** système, sur **ces** données, avec **ces** coûts, produit.

---

## 6. Reproduction

Tout ce document est recalculé par un seul script, qui n'écrit rien d'autre que son JSON de faits :

```powershell
# rapport complet (≈ 1 min 30 ; 4000 chemins de marche aléatoire, 1000 de martingale)
uv run python scripts/analysis/scalping_truth.py

# le même rapport + les faits en JSON, pour diff
uv run python scripts/analysis/scalping_truth.py --json scripts/analysis/petits-gains-facts.json

# relire le solde du compte démo (lecture seule) et regeler la copie locale
uv run python scripts/analysis/scalping_truth.py --refresh-account
```

Le JSON ne recalcule rien : il reprend les objets que le rapport vient d'imprimer, pour qu'un
chiffre du document et le chiffre du JSON soient **le même tirage**, pas deux voisins.

Fichiers produits, tous sous `scripts/analysis/` :

| Fichier | Rôle |
|---|---|
| `scalping_truth.py` | le script ; il relit les jeux gelés, rejoue le backtest hors coûts et au modèle chargé, et imprime le rapport |
| `account-snapshot.json` | la copie gelée de `account_snapshots` (solde, horodatage, nombre de lignes), écrite par `--refresh-account` |
| `petits-gains-facts.json` | toutes les valeurs du rapport, en JSON, pour vérifier un chiffre sans relire la sortie |
| `report.txt` | la sortie complète d'un passage (UTF-8) |

Le script ne dépend d'aucun réseau en mode par défaut : la seule étape qui se connecte est
`--refresh-account`, et elle est explicite.

Vérifications passées après écriture : `uv run ruff check .` ✅ · `uv run ruff format --check .` ✅ ·
`uv run mypy` ✅ · `uv run pytest -q` ✅.

## 7. Ce qui n'a pas été touché

Aucun seuil, aucune stratégie, aucun manifeste, aucun fichier sous `config/**` ni sous `src/**`.
Ce document propose **zéro** modification : il mesure, et il publie le résultat de la mesure. Le
delta de cette mission est exactement un document sous `docs/research/` et des fichiers neufs sous
`scripts/analysis/`. L'agent de production tourne en mode DEMO sans avoir été redémarré, arrêté ni
dupliqué.
