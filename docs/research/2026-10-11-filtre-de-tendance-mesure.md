# Le filtre de tendance mesuré : prometteur sur le bitcoin, ambigu sur l'or, et il coupe 85 % des entrées

**Date :** 2026-10-11 · **Nature :** mesure en lecture seule · **Aucune activation, `config/` inchangé.**
**Question :** le `trend_filter` déjà présent dans `vwap_pullback` reproduit-il l'effet trouvé le
2026-10-11 (`docs/research/2026-10-11-ou-est-l-avantage.md`), et vaut-il d'être activé ?

**Réponse courte : NON, pas selon la règle de décision posée d'avance.** Le filtre améliore le
bitcoin sur les deux fenêtres, et l'or sur la validation seulement — mais il **détruit 85 % des
opérations**, ce qui laisse 30 et 33 opérations en validation. C'est le seuil du projet, pas une
marge.

---

## 1. Une découverte de méthode : le mécanisme existait déjà

Avant d'écrire une ligne, vérification faite : `strategies/library/vwap_pullback.py` porte déjà
`trend_filter`, et sa ligne 231 est exactement la règle mesurée :

```python
side = Trend.UP if direction is Direction.BUY else Trend.DOWN
trend = trend_of(...) if parameters.trend_filter else None
if parameters.trend_filter and trend is not side:
    return None
```

`trend is not side` **refuse aussi `NEUTRAL`** — un marché sans tendance nommée n'est ni `UP` ni
`DOWN`, donc l'opération est refusée. Le filtre implémente donc déjà « dans la tendance ou rien »,
qui est précisément ce que la mesure du 2026-10-11 a trouvé.

**Ce qui n'était pas établi, et qui est toute la question :** l'effet mesuré vient de
`indicators/features.py::entry_features` — **EMA 10/30, pente sur une barre, seuil 0,05 ATR**. Le
`trend_of` de la règle se calcule avec **ses** paramètres — **EMA 20/50**, seuil `trend_slope_atr`
(0,05 par défaut). Deux mesures différentes de la même idée. Rien ne garantissait que l'effet se
transporte de l'une à l'autre, et personne ne l'avait vérifié. C'est ce que ce travail a fait.

## 2. Les chiffres, sur 59 999 bougies M15 par marché

Sortie au premier objectif (fidélité production), découpage ancré 60/20/20, coûts du dépôt, le
filtre basculé **en mémoire** — aucun manifeste n'est réécrit. Sortie brute :
`docs/research/measurements/trend-filter-effect.json`.

| marché | | opérations | PF net | net | réussite |
|---|---|---|---|---|---|
| **BTCUSD** | sans filtre · entraînement | 709 | 0,7148 | −999,06 € | 55,3 % |
| | sans filtre · validation | 205 | 0,6818 | −327,71 € | 55,1 % |
| | **avec filtre · entraînement** | **90** | **0,9643** | **−13,50 €** | **62,2 %** |
| | **avec filtre · validation** | **30** | **1,5376** | **+47,34 €** | **73,3 %** |
| **XAUUSD** | sans filtre · entraînement | 656 | 0,7229 | −863,80 € | 57,6 % |
| | sans filtre · validation | 231 | 0,5454 | −621,91 € | 46,8 % |
| | **avec filtre · entraînement** | **80** | **0,6782** | **−125,09 €** | 56,3 % |
| | **avec filtre · validation** | **33** | **0,9630** | **−5,27 €** | **60,6 %** |

Le taux de réussite du bitcoin passe de 55,1 % à **73,3 %** en validation, et son net de
−327,71 € à **+47,34 €**. Celui de l'or passe de 46,8 % à 60,6 %. Le filtre ne se contente pas de
changer la distribution : il change la qualité des opérations prises, et c'est cohérent avec la
mécanique trouvée le 2026-10-11 — une opération qui va contre la tendance nommée rend −0,50 R.

## 3. Le verdict de la règle posée d'avance : refusé

La règle était écrite avant la mesure : **un filtre ne vaut que s'il améliore l'entraînement ET la
validation, sur les DEUX marchés.**

| marché | PF entraînement | PF validation | opérations conservées | verdict |
|---|---|---|---|---|
| BTCUSD | 0,7148 → **0,9643** | 0,6818 → **1,5376** | **14,6 %** | améliore les deux fenêtres |
| XAUUSD | 0,7229 → **0,6782** | 0,5454 → **0,9630** | **14,3 %** | **n'améliore pas l'entraînement** |

**L'or échoue sur l'entraînement** : 0,6782 contre 0,7229. Le filtre y dégrade légèrement la
fenêtre qui sert à sélectionner, et n'améliore que la validation. Ce n'est pas un résultat à
retenir : c'est un résultat à refuser par la règle qu'on s'était donnée, et la refuser est
exactement ce qui empêche de choisir après coup la lecture qui arrange.

## 4. Ce que le refus cache, et qui compte pour la suite

**Le bitcoin passe les deux fenêtres, et largement.** 0,71 → 0,96 en entraînement, 0,68 → **1,54**
en validation, avec un net qui passe de **−327,71 € à +47,34 €**. C'est le premier réglage de ce
projet qui rend la validation du bitcoin positive sur les deux fenêtres à la fois.

**Mais 30 opérations en validation, c'est le seuil du projet, pas une preuve.** `min_trades` vaut
30 : la mesure atteint exactement la barre. Un PF de 1,54 sur 30 opérations a un intervalle de
confiance large, et la mesure du 2026-10-11 l'a montré — l'effet existe avec un IC qui exclut zéro
sur **157** opérations, pas sur 30.

**Et le filtre coupe 85 % des entrées.** C'est la contrainte pratique la plus lourde, et elle ne
se lit dans aucun facteur de profit : à ce rythme, une campagne de paper trading qui exige trente
opérations par stratégie demanderait **plusieurs mois** au lieu de quelques jours. L'agent
signalerait environ une opération tous les trois jours sur le bitcoin.

**L'or ne gagne pas, il cesse de perdre.** Entraînement 0,68 et validation 0,96 : deux nombres
sous 1,00. Le filtre rapproche l'or de l'équilibre sans le franchir. C'est un progrès par rapport
à 0,55, et ce n'est pas une rentabilité.

**Un point de méthode à ne pas perdre :** l'effet mesuré le 2026-10-11 portait sur 157 et 133
opérations. Le filtre de la règle, lui, ne laisse passer que 90 et 80 en entraînement. **Le filtre
de la règle est plus sélectif que la mesure qui l'a motivé** — parce que 20/50 est plus lent que
10/30, donc plus souvent `NEUTRAL`, donc plus souvent refusé. C'est la différence entre les deux
lectures, et elle est mesurée plutôt que supposée.

## 5. La suite, et ce qu'elle coûte

Trois options, et aucune n'est gratuite :

1. **Poursuivre avec `trend_filter: true` sur le bitcoin seulement.** Le meilleur résultat mesuré
   du projet, mais 30 opérations de validation et une activité très réduite. Il faudrait le
   protocole complet — et le holdout du bitcoin est **déjà dépensé** par `vwap_pullback@1.3.0`.
2. **Le protocole complet sur l'or.** Son holdout n'a **jamais été ouvert** — c'est la dernière
   fenêtre vraiment hors échantillon du projet. La dépenser sur une configuration dont
   l'entraînement se dégrade est un mauvais calcul, mais c'est le seul moyen de trancher.
3. **Chercher un filtre moins sélectif.** L'effet existe à 157 opérations avec 10/30 ; la règle
   n'en garde que 90 avec 20/50. Un seuil de pente ou des périodes intermédiaires garderaient
   plus d'opérations pour le même effet. C'est une piste mesurable, et elle élargit le nombre
   d'hypothèses — donc elle exige la correction de tests multiples, pas un balayage libre.

**Ma recommandation : l'option 3 avant toute dépense de holdout.** Elle est gratuite en ressource
non renouvelable, elle répond à la vraie contrainte (85 % de refus), et elle ne consomme pas la
dernière fenêtre intacte du projet sur une configuration qu'on sait déjà imparfaite.

---

**Méthode.** `scripts/backtest/measure_trend_filter.py` — le filtre est basculé dans un
`CandidateSpec` en mémoire, donc aucune version publiée n'est réécrite et `config/` n'est pas
touché. Le jeu scellé n'a pas été ouvert. Aucun ordre n'a été envoyé.
