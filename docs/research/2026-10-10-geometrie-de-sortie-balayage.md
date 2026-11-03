# Géométrie de sortie : le balayage, et ce qu'il désigne vraiment

**Date :** 2026-10-10 · **Nature :** mesure en lecture seule · **Aucune activation, aucune promotion.**
**Question :** la géométrie de sortie (TP1 / TP2 / multiplicateur de stop) est-elle ce qui rend
`vwap_pullback` non rentable sur l'or ?

**Réponse courte : non. Aucune des sept géométries n'amène l'or au plancher, et la géométrie
actuelle est la pire en validation.**

---

## 1. Comment la décision a été cadrée, avant de regarder les chiffres

La règle est écrite en tête de `scripts/backtest/sweep_exit_geometry.py` et n'a pas été ajustée :

1. même découpage que la campagne (`split_dataset(anchor=True)`, 60 % / 20 % / 20 % scellé),
   mêmes coûts, même sortie au **premier** objectif que la production exécute ;
2. la sélection se fait sur l'**entraînement seul** : meilleur facteur de profit net ;
3. adoption **seulement** si la géométrie retenue atteint PF net ≥ 1,00 sur la **validation**.
   Sous le plancher, rien n'est adopté.

Sept géométries, 36 000 barres d'entraînement et 12 000 de validation. Le jeu scellé
(12 001 barres) n'a pas été ouvert.

## 2. Le tableau complet

`tp` désigne le premier et le second objectif en multiples de R ; `stop` le multiplicateur d'ATR.

| géométrie | marché | train n | PF train | val n | PF validation | net validation |
|---|---|---|---|---|---|---|
| tp2.0 / tp4.0 / stop1.5 | XAUUSD | 535 | 1,0016 | 185 | 0,7141 | −410,88 € |
| tp1.5 / tp3.0 / stop1.5 | XAUUSD | 562 | 0,8874 | 200 | 0,6848 | — |
| tp1.0 / tp2.0 / stop1.5 | XAUUSD | 630 | 0,8068 | 221 | 0,6040 | — |
| tp1.0 / tp2.0 / stop2.0 | XAUUSD | 544 | 0,8011 | 187 | 0,6859 | — |
| tp1.5 / tp3.0 / stop1.0 | XAUUSD | 693 | 0,7735 | 249 | 0,6427 | — |
| **tp0.8 / tp1.5 / stop1.5** *(actuelle)* | XAUUSD | 656 | 0,7229 | 231 | **0,5454** | −621,91 € |
| tp1.0 / tp2.0 / stop1.0 | XAUUSD | 739 | 0,6386 | 260 | 0,5691 | — |

Restitution : `scripts/backtest/read_geometry_sweep.py`.

## 3. Le verdict de la règle : REFUSÉE

La meilleure géométrie sur l'entraînement est `tp2.0/tp4.0/stop1.5` (PF 1,0016). Sa validation
vaut **0,7141**, sous le plancher de 1,00 : **aucune géométrie n'est adoptée**, et la géométrie
d'origine reste en place.

## 4. Ce que le refus montre

**La géométrie actuelle est la sixième sur sept à l'entraînement, et la dernière en
validation.** Autrement dit, `0.8 / 1.5 / 1.5` n'a jamais été un choix mesuré — c'était la
spécification d'origine, et le balayage montre qu'elle est proche du pire des cas testés.

**L'or ne franchit rien, quelle que soit la géométrie.** Sa meilleure validation reste
**0,7141**, avec un net de −410,88 €. Sept géométries, sept échecs. Aucune géométrie ne
rapproche l'or du plancher.

**Le problème est la règle, pas un paramètre de sortie.** La détection de `vwap_pullback` ne
produit pas d'avantage exploitable sur le marché de l'or.

## 5. Ce que ce balayage ne dit pas

- **Il ne dit rien du jeu scellé**, qui reste fermé (12 001 barres).
- **Il ne compare que sept géométries**, choisies pour couvrir la plage plausible, pas pour
  chercher l'optimum. Une huitième aurait pu faire mieux ; c'est le prix d'un nombre
  d'hypothèses assez petit pour être rapporté honnêtement.
- **Le balayage rapide et la campagne complète ont été confrontés** : sur 20 000 barres, les deux
  donnent des chiffres identiques au chiffre près (mêmes opérations, mêmes PF). La version rapide
  omet les portes, pas la mesure. Le protocole complet (plis walk-forward, Monte-Carlo,
  correction de tests multiples) n'a pas été rejoué : le balayage rapide ne calcule que
  l'entraînement et la validation.

## 6. La suite

1. **Chercher ailleurs pour l'or.** La géométrie est blanchie sur ce marché : sept configurations,
   aucune au-dessus de 0,72 en validation. Le prochain levier n'est pas un paramètre de sortie,
   c'est la règle d'entrée.
2. **Ne pas relancer de balayage de géométrie sur l'or.** Il a été fait, il est négatif, et le
   refaire serait de l'optimisation sur le passé.

---

**Méthode.** `scripts/backtest/sweep_exit_geometry_fast.py` (exécution : 1 952 s). Le script
complet `sweep_exit_geometry.py` existe et produit la même mesure avec les portes du §49 ; il a
été arrêté après 8 435 s de CPU sans résultat, parce qu'il calculait 235 plis walk-forward, un
Monte-Carlo et des variantes de robustesse par candidat dont la règle de décision ne lit aucun.
**Aucun ordre n'a été envoyé, aucun jeu gelé n'a été réécrit, `config/` n'a pas été touché.**
