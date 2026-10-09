# Stratégie BTCUSD : VWAP + momentum + pullback — implémentée et mesurée

**Date :** 2026-10-09 · **Statut :** implémentée, enregistrée en production sous **`SIGNAL`**,
mesurée sur données réelles. **Aucune promotion.**

---

## 1. Ce qui a été écrit

| Élément | Chemin |
|---|---|
| Règle | `src/tradingagent/strategies/library/vwap_pullback.py` |
| Tests | `tests/strategies/test_vwap_pullback.py` — **15 tests** |
| Manifeste | `config/strategies/vwap_pullback@1.0.0.yaml` |
| Registre | `REGISTRY` inclut `vwap_pullback` |
| Jeu de mesure | `docs/research/datasets-volume/` — 59 999 bougies M15, **volume à 100 %** |

**La règle, telle que l'opérateur l'a spécifiée :** VWAP ancré à **00:00 UTC**, momentum, entrée
sur **pullback** vers le niveau — pas sur cassure. Sorties à **TP1 0,8 R** puis **TP2 1,5 R**,
avec sortie partielle et passage à break-even appliqués par le harnais.

## 2. Trois découvertes de mise au point, toutes mesurées

**1. Le VWAP du jour ne se rejoint pas dans une forte tendance.** Sur mes premiers scénarios, la
distance au VWAP valait **29 fois** la tolérance de 0,4 ATR. Un prix qui monte de 40 % en une
journée laisse son VWAP à 9 points derrière lui : le « pullback » qui le rejoindrait serait un
retournement, pas un repli. La tolérance et les EMA ont donc été calibrées **par mesure**, pas
par goût.

**2. `fast > slow` et « toucher le VWAP » s'excluent presque.** Sur **216 géométries balayées,
4 seulement** satisfont les deux conditions. C'est ce qui a imposé les EMA **20/50** au lieu des
8/21 : la tension est réelle et la règle ne fonctionne que sous ces valeurs.

**3. Le momentum ne peut pas se mesurer sur une barre.** Pendant un pullback, les moyennes
convergent : la pente sur une barre est **négative** (mesurée à −0,005), donc exiger une pente
positive sur une barre **interdit la stratégie entière**. La pente se mesure sur `slope_window`
barres (10 par défaut), normalisée par la fenêtre pour que le seuil ne dépende pas de sa
longueur. **C'était un vrai défaut de mon code** — la docstring promettait une fenêtre, le code
lisait `slow[-2]`.

## 3. Ce que la mesure dit — et elle dit non

**BTCUSD M15, 59 999 bougies avec volume, coûts complets (spread, slippage, commission) :**

| Config | Signaux | Entrées | Trades | Réussite | Net | PF |
|---|---|---|---|---|---|---|
| Objectif unique | 1 973 | 1 133 | 1 133 | 56,0 % | **−934,23 €** | 0,827 |
| Partiel 50/50 + break-even | 1 973 | 1 092 | 1 092 | 56,3 % | −850,01 € | 0,836 |

**Les trades sont non seulement possibles, ils sont abondants : 1 133 opérations.** Et la règle
**perd quand même**, avec 56 % de réussite. C'est exactement le piège que la mesure de la veille
avait annoncé, et MAE/MFE en donne l'arithmétique :

| | Gagnants (233) | Perdants (171) |
|---|---|---|
| MAE médiane | 0,325 R | **1,192 R** |
| MFE médiane | **0,978 R** | 0,234 R |
| Gain / perte médians | **+7,16 €** | **−10,63 €** |

**Ratio gain/perte : 0,673.** À 57,7 % de réussite, il faudrait **64 %** pour atteindre
l'équilibre — soit **6,5 points d'écart**, et les coûts ne font qu'aggraver l'écart.

**La cause est la géométrie des sorties, pas la détection.** Les gagnants encaissent à
**0,978 R**, c'est-à-dire au TP1 de 0,8 R, tandis que les perdants vont jusqu'à **1,192 R**,
c'est-à-dire au stop.

### La géométrie a été balayée, et aucune ne suffit

BTCUSD, 20 000 bougies, sortie partielle 50/50 + break-even :

| TP1 / TP2 | Trades | Réussite | Net | PF |
|---|---|---|---|---|
| 0,8 / 1,5 (la spec de l'opérateur) | 385 | 58,4 % | −122,10 € | 0,929 |
| 1,0 / 2,0 | 360 | 53,6 % | −61,80 € | 0,966 |
| **1,5 / 3,0** | 301 | 41,9 % | **−18,24 €** | **0,990** |
| 2,0 / 4,0 | 260 | 33,8 % | −77,45 € | 0,958 |

**Élargir les objectifs rapproche de l'équilibre sans jamais le franchir** : le meilleur réglage
(1,5 / 3,0) atteint PF **0,990** — à un centième de l'équilibre. La détection n'est donc pas le
problème : elle produit 300 à 400 trades exploitables, et c'est **la géométrie** qui reste sous
le seuil.

## 3 bis. Les deux marchés produisent bien des trades

Jeu `docs/research/datasets-volume`, **volume à 100 %** sur les deux marchés, 20 000 dernières
bougies, TP1 1,5 / TP2 3,0, partiel 50/50 + break-even :

| Marché | Bougies | Volume | Signaux | Trades | Réussite | Net | PF |
|---|---|---|---|---|---|---|---|
| **BTCUSD** | 20 000 | **20 000** | 603 | **268** | 39,2 % | −177,71 € | 0,900 |
| **XAUUSD** | 20 000 | **20 000** | 607 | **291** | 35,7 % | −578,16 € | 0,719 |

**La réponse à « les trades sont-ils possibles et fonctionnels sur les deux marchés » est oui :
603 et 607 signaux, 268 et 291 opérations complètes, avec des niveaux valides.** Le chemin
décision → signal → entrée → sortie → résultat tourne sur les deux marchés.

**Et la règle perd sur les deux.** L'or est nettement pire (PF 0,719 contre 0,900), ce qui est
cohérent avec tout ce que ce projet a mesuré sur ce marché : le filtre de cassure y échouait
déjà aux quatre découpages.

**Un point de méthode important :** c'est la **seconde** fois dans ce projet qu'une règle
paraît correcte sur données synthétiques et se comporte autrement sur le vrai marché. Mes
premiers scénarios de test ne produisaient aucun signal réel parce que la géométrie du VWAP
réel n'a rien à voir avec une rampe linéaire.

**Ce que cela désigne, et ce n'est pas un réglage de détection :** avec un stop à 1,5 ATR et un
TP1 à 0,8 R, la règle **exige d'être gagnante plus de deux fois sur trois** pour survivre. Le
premier objectif doit être plus loin, ou le stop plus serré — les deux se mesurent, et c'est le
prochain chantier.

## 4. Deux défauts de performance corrigés en chemin

**Le trailing sur structure était en O(n²).** Il recalculait la série de swings entière à chaque
barre : **plus de 20 minutes de CPU** pour une passe sur 60 000 barres, contre quelques secondes
pour le reste du harnais. Un swing d'indice `k` se confirme exactement quand la barre
`k + strength` ferme, donc un préfixe qui grandit d'un cran ne peut ajouter **que ce seul
candidat**. La version incrémentale est équivalente et linéaire. Les 7 tests du trailing restent
verts.

**Le lissage du VWAP matérialisait toute la série à chaque évaluation.** Seule la fin est
nécessaire : la fenêtre glissante ne dépend que des dernières valeurs définies.

**La leçon honnête :** mes mesures précédentes étaient justes mais **lentes au point d'être
inutilisables en campagne**. Un backtest qui prend vingt minutes ne peut pas être lancé des
centaines de fois, et c'est ce qui a rendu les campagnes de découverte si longues.

## 5. Ce qui reste ouvert

- **Le premier objectif est trop proche** : chiffrer TP1/TP2 et le stop pour que la géométrie
  cesse d'exiger 64 % de réussite. C'est mesurable et c'est la prochaine étape.
- **La règle n'est déclarée que sur BTCUSD** dans son manifeste. La tester sur l'or est possible
  et instruirait la question, mais son manifeste ne le déclare pas.
- **Le jeu `datasets-long` n'a aucun volume** (0 sur 60 000 bougies) : la règle **refuse** d'y
  tourner, et elle a raison — un VWAP sans volume n'est qu'une moyenne mobile. Toute mesure de
  cette règle exige donc le jeu `datasets-volume`.
- **`require_finite` coûte 47 s** des 167 s du profil, sur 8 millions d'appels. À optimiser si
  les campagnes doivent tourner plus vite.
