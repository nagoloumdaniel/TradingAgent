# Axe A — le stop et la géométrie des sorties (VWAP pullback, BTCUSD)

**Date :** 2026-10-09 · **Statut :** mesuré sur le jeu complet, aucune promotion, `max_mode` reste
`SIGNAL` · **Réponse à la question posée : PF > 1,0 NON franchi.**

---

## 1. La prémisse de cet axe était fausse, et il faut le dire d'abord

L'axe partait de ce diagnostic : « les perdants touchent le stop à **1,192 R** en médiane, donc le
stop à 1,5 ATR est **trop large** ».

**Ce chiffre ne peut pas dire cela.** Un trade qui touche son stop a, par construction, une
excursion adverse d'au moins **1 R** : c'est la définition même du stop. Sur le jeu complet, la
MAE médiane des perdants vaut **1,2114 R** (spec opérateur, partiel), **1,2102 R** (même
géométrie, sortie unique) et **1,2195 R** (TP 1,5/3,0) : trois géométries très différentes, et
trois fois le même nombre à un centième près. Ce n'est pas une mesure de la largeur du stop, c'est
la trace d'un stop touché, plus le dépassement de la bougie qui l'a touché. Chercher à ramener
cette valeur vers 1,0 reviendrait à chercher un stop que le marché ne dépasse jamais — c'est-à-dire
un stop qui n'existe pas.

Ce qui informe réellement, ce sont les excursions des deux populations, mesurées sur le jeu
complet :

| Configuration (59 999 bougies) | Perdants : MAE méd. | Perdants : MFE méd. | Gagnants : MAE méd. | Gagnants : MFE méd. | Réussite |
|---|---|---|---|---|---|
| TP 0,8/1,5, partiel (spec opérateur) | 1,2114 R | 0,2404 R | 0,3678 R | 1,5265 R | 56,32 % |
| TP 0,8/1,5, sortie unique | 1,2102 R | 0,2438 R | 0,3264 R | 0,9882 R | 56,05 % |
| TP 1,5/3,0, partiel | 1,2195 R | 0,3874 R | 0,4462 R | 3,0199 R | 40,20 % |
| TP 1,5/3,0, sortie unique | 1,2213 R | 0,4024 R | 0,3679 R | 1,7454 R | 40,27 % |

Trois lectures, et elles orientent tout le reste :

1. **la MAE des perdants vaut 1,21 R quelle que soit la géométrie** — stop à 1,5 ATR et TP à
   0,8 R comme stop à 1,5 ATR et TP à 3,0 R : le même nombre. C'est une conséquence du stop, pas
   une information sur lui ;
2. **les perdants ne vont nulle part** : 0,24 R de MFE médiane à la géométrie de l'opérateur,
   0,39 R à TP 1,5/3,0. Ils sont perdants presque immédiatement ;
3. **les gagnants vont bien plus loin que leur premier objectif** : 3,02 R de MFE médiane quand
   TP2 est à 3,0 R, 1,53 R quand TP2 est à 1,5 R. Les gagnants atteignent leur cible.

Le problème n'est donc pas la largeur du stop, il est dans l'**arbitrage entre ce que les gagnants
laissent sur la table et ce que les perdants coûtent** — et les gagnants qui dipent de 0,33 à
0,49 R en médiane disent déjà pourquoi serrer le stop est risqué : le premier quartile des
gagnants passe sous tout stop raisonnablement serré.

L'exploration le confirme dans l'autre sens : sur 50 configurations, le PF **monte** avec le stop,
partout. C'est l'inverse exact de la prémisse. Les deux étages de mesure ci-dessous arbitrent.

## 2. Méthode, et les trois pièges qu'elle écarte

**Deux étages, parce qu'un run coûte cher.** Un backtest sur 59 999 bougies prend **5 min 45 à
7 min** de temps réel sur cette machine. L'étage 1 (exploration) tourne donc large sur
**4 999 bougies** pour éliminer ; l'étage 2 (décision) tourne sur le **jeu complet, 59 999
bougies**, et c'est le seul qui engage quelque chose. Chaque ligne de mesure porte sa fenêtre :
aucune mesure d'exploration ne peut être lue comme une mesure de décision.

**Le jeu de signaux est invariant, et c'est vérifié sur les mesures, pas supposé.** Toutes les
configurations du balayage gardent `pullback_atr=0,4`, `entry_zone_atr=0,1`, les EMA 20/50 et la
pente inchangées : seuls changent le stop, les objectifs et la gestion de position. Le script
`tune_stop_analysis.py` le contrôle sur les compteurs produits — **149 signaux** sur les cinq
stops de l'exploration pour chaque paire de TP, **1 973 signaux** sur le jeu complet — et conclut
« 0 groupe instable ». Un écart de PF est donc attribuable à la géométrie, pas à un changement de
détection.

**La source mesurée a été gelée, puis l'équivalence a été vérifiée.** Pendant le premier passage
du 2026-10-09, `src/tradingagent/strategies/library/vwap_pullback.py` était **en cours d'édition
par l'axe B** : 50 mesures d'affilée ont rendu « 0 trade » avec `TypeError: must be real number,
not str` sur chaque barre. Ces mesures ne disaient rien de la règle, elles disaient que le fichier
changeait ; elles ont été jetées. La règle a été gelée au commit `bfa0e65` dans le périmètre de
cet axe (`vwap_pullback_pinned_bfa0e65.py`, sha256 `17d48adf…`), puis les mesures de décision ont
été refaites **sur la source vivante** après le commit `fa76d2a` de l'axe B. Les trois
configurations mesurées sur les deux sources donnent **le même résultat au chiffre près** :

| Configuration | Copie gelée `bfa0e65` | Source vivante `fa76d2a` |
|---|---|---|
| TP 0,8/1,5, sortie unique | 1 133 trades, PF 0,8275 | 1 133 trades, PF 0,8275 |
| TP 0,8/1,5, partiel 50/50 + BE | 1 092 trades, PF 0,8362 | 1 092 trades, PF 0,8362 |

Les filtres de l'axe B sont donc bien inertes à leurs valeurs par défaut, et les mesures de
décision qui suivent portent sur la règle réellement en place.

**Un confondant subsiste, et il est réel : les places occupées.** Le harnais tourne avec
`max_concurrent_positions=1` : un signal n'est exécuté que si aucune position n'est ouverte. Or un
stop plus serre libère la place plus vite. À jeu de signaux **strictement constant** (149 signaux
dans l'exploration), le nombre de trades exécutés va de **48 à 108** selon la configuration. Deux
lignes du tableau ne comparent donc pas exactement les mêmes trades : elles comparent deux
échantillons tirés du même flux de signaux, par une règle de sélection qui dépend elle-même de la
géométrie mesurée. C'est écrit ici pour que personne ne lise le tableau comme une comparaison
contrôlée trade à trade.

**Coûts.** Modèle du dépôt, à chaque fenêtre : `spread = close₀ × 5·10⁻⁵`,
`slippage = close₀ × 2·10⁻⁵`, `commission = 0,50 €` par trade. Le prix de référence étant celui de
la **première bougie de la fenêtre**, les coûts absolus diffèrent d'une fenêtre à l'autre — sur le
jeu complet (première bougie du 2025-01-21, close 105 082,44) le spread vaut **5,254122** et le
slippage **2,101649**, contre 3,214140 et 1,285656 sur les 4 999 dernières bougies.

## 3. Étage 1 — exploration sur 4 999 bougies (élimination seulement)

<!-- TABLEAUX -->

## 4. Étage 2 — décision sur le jeu complet (59 999 bougies)

<!-- TABLEAUX-DECISION -->

## 5. Conclusion

## 6. Sorties brutes

```bash
$ uv run python scripts/backtest/tune_stop_sweep.py --group decision_core60 --range 3:18 \
    --bars 59999 --live --out docs/research/vwap-tuning/axe-A-decision-60k.jsonl
```

<!-- SORTIES -->
