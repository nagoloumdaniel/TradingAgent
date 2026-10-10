# Géométrie de sortie : le balayage, et ce qu'il désigne vraiment

**Date :** 2026-10-10 · **Nature :** mesure en lecture seule · **Aucune activation, aucune promotion.**
**Question :** la géométrie de sortie (TP1 / TP2 / multiplicateur de stop) est-elle ce qui rend
`vwap_pullback` non rentable sur les deux marchés ?

**Réponse courte : oui pour le bitcoin, non pour l'or — et le refus de la règle d'adoption
cache la découverte la plus utile du lot.**

---

## 1. Comment la décision a été cadrée, avant de regarder les chiffres

La règle est écrite en tête de `scripts/backtest/sweep_exit_geometry.py` et n'a pas été ajustée :

1. même découpage que la campagne (`split_dataset(anchor=True)`, 60 % / 20 % / 20 % scellé),
   mêmes coûts, même sortie au **premier** objectif que la production exécute ;
2. la sélection se fait sur l'**entraînement seul** : meilleur facteur de profit net, moyenné
   sur les deux marchés ;
3. adoption **seulement** si la géométrie retenue atteint PF net ≥ 1,00 sur la **validation des
   deux** marchés. Un seul marché sous le plancher et rien n'est adopté.

Sept géométries, deux marchés, 36 000 barres d'entraînement et 12 000 de validation par marché.
Le jeu scellé (12 001 barres par marché) n'a pas été ouvert.

## 2. Le tableau complet

`tp` désigne le premier et le second objectif en multiples de R ; `stop` le multiplicateur d'ATR.

| géométrie | marché | train n | PF train | val n | PF validation | net validation |
|---|---|---|---|---|---|---|
| **tp2.0 / tp4.0 / stop1.5** | BTCUSD | 549 | 0,7985 | 170 | **1,0275** | **+31,56 €** |
| tp2.0 / tp4.0 / stop1.5 | XAUUSD | 535 | 1,0016 | 185 | 0,7141 | −410,88 € |
| tp1.5 / tp3.0 / stop1.5 | BTCUSD | 594 | 0,7924 | 179 | 0,9114 | — |
| tp1.5 / tp3.0 / stop1.5 | XAUUSD | 562 | 0,8874 | 200 | 0,6848 | — |
| tp1.0 / tp2.0 / stop1.5 | BTCUSD | 669 | 0,7740 | 196 | 0,7113 | — |
| tp1.0 / tp2.0 / stop1.5 | XAUUSD | 630 | 0,8068 | 221 | 0,6040 | — |
| tp1.0 / tp2.0 / stop2.0 | BTCUSD | 563 | 0,7728 | 163 | 0,9091 | — |
| tp1.0 / tp2.0 / stop2.0 | XAUUSD | 544 | 0,8011 | 187 | 0,6859 | — |
| tp1.5 / tp3.0 / stop1.0 | BTCUSD | 733 | 0,7991 | 218 | 0,6758 | — |
| tp1.5 / tp3.0 / stop1.0 | XAUUSD | 693 | 0,7735 | 249 | 0,6427 | — |
| **tp0.8 / tp1.5 / stop1.5** *(actuelle)* | BTCUSD | 709 | 0,7148 | 205 | **0,6818** | −327,71 € |
| **tp0.8 / tp1.5 / stop1.5** *(actuelle)* | XAUUSD | 656 | 0,7229 | 231 | **0,5454** | −621,91 € |
| tp1.0 / tp2.0 / stop1.0 | BTCUSD | 795 | 0,7006 | 234 | 0,5860 | — |
| tp1.0 / tp2.0 / stop1.0 | XAUUSD | 739 | 0,6386 | 260 | 0,5691 | — |

Sortie brute complète : `exit-geometry-sweep-fast.json`. Restitution :
`scripts/backtest/read_geometry_sweep.py`.

## 3. Le verdict de la règle : REFUSÉE

Classement sur l'entraînement seul :

| rang | géométrie | PF moyen entraînement |
|---|---|---|
| 1 | tp2.0 / tp4.0 / stop1.5 | 0,9000 |
| 2 | tp1.5 / tp3.0 / stop1.5 | 0,8399 |
| 3 | tp1.0 / tp2.0 / stop1.5 | 0,7904 |
| 4 | tp1.0 / tp2.0 / stop2.0 | 0,7869 |
| 5 | tp1.5 / tp3.0 / stop1.0 | 0,7863 |
| 6 | **tp0.8 / tp1.5 / stop1.5** *(actuelle)* | **0,7189** |
| 7 | tp1.0 / tp2.0 / stop1.0 | 0,6696 |

La géométrie retenue est `tp2.0/tp4.0/stop1.5`. Sa validation la plus faible vaut **0,7141**
(l'or), sous le plancher de 1,00 : **aucune géométrie n'est adoptée**, et la géométrie d'origine
reste en place.

## 4. Ce que le refus cache, et qui est le vrai résultat

**La géométrie actuelle est la sixième sur sept.** Ce n'est pas un détail de classement : elle
est battue par cinq configurations sur six, y compris sur son propre marché. Autrement dit,
`0.8 / 1.5 / 1.5` n'a jamais été un choix mesuré — c'était la spécification d'origine, et le
balayage montre qu'elle est proche du pire des cas testés.

**Le bitcoin franchit le plancher.** Avec `tp2.0/tp4.0/stop1.5`, sa validation donne
**PF 1,0275** et **+31,56 €** sur 170 opérations, contre PF 0,6818 et −327,71 € aujourd'hui. La
progression est monotone dans l'élargissement des objectifs (0,68 → 0,71 → 0,91 → 1,03), ce qui
est la signature d'un effet réel et non d'un point chanceux : élargir les objectifs laisse courir
les gagnants, ce que la règle ne faisait pas.

**L'or ne franchit rien, quelle que soit la géométrie.** Sa meilleure validation reste **0,7141**,
avec un net de −410,88 €. Sept géométries, sept échecs. Aucune géométrie ne rapproche l'or du
plancher.

**Les deux marchés ne répondent donc pas au même levier.** Le bitcoin a un problème de
géométrie, réparable par un paramètre. L'or a un problème de règle : sa détection ne produit pas
d'avantage exploitable sur le marché de l'or. C'est exactement la raison pour laquelle les deux
manifestes ont été séparés le 2026-10-10 (EF-003) : ils n'ont pas le même défaut, donc ils ne
recevront pas le même remède.

## 5. Ce que ce balayage ne dit pas

- **Il ne valide pas `tp2.0/tp4.0/stop1.5` pour le bitcoin.** Le plancher de 1,00 était la
  condition d'adoption pour les **deux** marchés ; le bitcoin l'atteint à 1,0275, soit une marge
  de 2,75 % sur 170 opérations. C'est mince, et une seule fenêtre de validation. Le protocole
  complet (plis walk-forward, Monte-Carlo, correction de tests multiples) n'a pas été rejoué sur
  cette géométrie — le balayage rapide ne calcule que l'entraînement et la validation. Avant
  toute adoption, elle doit passer les neuf portes, et rien ici ne l'en dispense.
- **Il ne dit rien du jeu scellé**, qui reste fermé (12 001 barres par marché).
- **Il ne compare que sept géométries**, choisies pour couvrir la plage plausible, pas pour
  chercher l'optimum. Une huitième aurait pu faire mieux ; c'est le prix d'un nombre
  d'hypothèses assez petit pour être rapporté honnêtement.
- **Le balayage rapide et la campagne complète ont été confrontés** : sur 20 000 barres, les deux
  donnent des chiffres identiques au chiffre près (mêmes opérations, mêmes PF). La version rapide
  omet les portes, pas la mesure.

## 6. La suite, par ordre de valeur

1. **Soumettre `tp2.0/tp4.0/stop1.5` au protocole complet pour le bitcoin.** C'est un candidat
   réel, mesuré, avec un net positif en validation. Il mérite les neuf portes avant toute
   décision, et le bitcoin a désormais son propre manifeste pour le porter.
2. **Chercher ailleurs pour l'or.** La géométrie est blanchie sur ce marché : sept configurations,
   aucune au-dessus de 0,72. Le prochain levier n'est pas un paramètre de sortie, c'est la règle
   d'entrée — ou le choix du marché.
3. **Ne pas relancer de balayage de géométrie sur l'or.** Il a été fait, il est négatif, et le
   refaire serait de l'optimisation sur le passé.

---

**Méthode.** `scripts/backtest/sweep_exit_geometry_fast.py` (exécution : 1 952 s). Le script
complet `sweep_exit_geometry.py` existe et produit la même mesure avec les portes du §49 ; il a
été arrêté après 8 435 s de CPU sans résultat, parce qu'il calculait 235 plis walk-forward, un
Monte-Carlo et des variantes de robustesse par candidat dont la règle de décision ne lit aucun.
**Aucun ordre n'a été envoyé, aucun jeu gelé n'a été réécrit, `config/` n'a pas été touché.**
