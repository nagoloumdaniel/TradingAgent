# La stratégie M1 publiée à 87,11 % — ce que la mesure en dit ici

**Date :** 2026-10-08 · **Famille :** `scalp_triple_filter` · **Jeu :** `docs/research/datasets/M1/`
(XAUUSD et BTCUSD, 11 999 bougies chacun, gelés le 2026-10-08)
**Aucune promotion, aucun seuil touché, aucun manifeste écrit dans `config/strategies/`.**

**En une phrase :** implémentée telle que décrite — Supertrend + Stochastic + EMA d'unité
supérieure + ATR — la règle produit bien des opérations sur l'or M1, mais son taux de
réussite mesuré est de **62,03 %**, pas 87,11 %, et **les coûts du projet la font basculer
en perte nette** (−124,07 € sur 158 opérations).

---

## 1. Ce qui a été implémenté, et par qui

| Élément | Fichier | Nature |
|---|---|---|
| Supertrend (ATR + bandes verrouillées) | `src/tradingagent/indicators/trend.py` | indicateur pur, 14 tests |
| Stochastic (%K lissé, %D) | `src/tradingagent/indicators/stochastic.py` | indicateur pur, 8 tests |
| Agrégation M1 → M5 | `src/tradingagent/backtest/aggregate.py` | fonction pure, 9 tests |
| La stratégie | `src/tradingagent/research/discovery.py` → `ScalpTripleFilter` | candidat de recherche |
| Le gabarit de famille | `scalp_triple_filter_template` | 8 propositions sur la grille par défaut |
| Support multi-unité du harnais | `discovery._series` | agrège la série grossière depuis la série entière |
| Lanceur | `scripts/backtest/discover_m1_scalp.py` | campagne M1 + M5 |

Les quatre composants sont ceux de la description : Supertrend = direction, EMA d'unité
supérieure = contexte, Stochastic = timing (sortie de zone, pas simple présence), ATR =
stop, objectif et plancher d'activité.

**Un écart assumé.** Le modèle de paramètres autorise `take_profit_rr < 1`. Le dépôt
l'interdisait de fait : tous les gabarits existants supposaient un objectif plus lointain que
le stop. Or le profil publié est l'inverse — 87 % de réussite avec un gain moyen valant
0,70 fois la perte moyenne. Un garde-fou qui refuse cette géométrie interdit de mesurer la
stratégie qu'on veut juger.

---

## 2. Le résultat sur le jeu gelé, sans arrondi

Configuration de référence : Supertrend(7, ×2,0), %K(5, lissage 2), EMA M5 (20/50), ATR(14),
stop 3,0 × ATR, objectif 0,7 R, zone d'entrée 0,1 × ATR.

| | **Sans coûts** | **Coûts du dépôt** | **Coûts ×2** |
|---|---|---|---|
| Opérations | 158 | 158 | 158 |
| Gagnants / perdants | 98 / 60 | 98 / 60 | 98 / 60 |
| **Taux de réussite** | **62,03 %** | 62,03 % | 62,03 % |
| Espérance par opération | **+0,487 €** | **−0,785 €** | −1,994 € |
| Résultat net | **+76,91 €** | **−124,07 €** | −315,04 € |
| Profit factor | **1,1267** | **0,8118** | 0,5564 |
| Drawdown maximal | 49,00 € | 151,61 € | 335,60 € |
| Gain moyen / perte moyenne | 0,690 | 0,497 | 0,341 |

Coûts du dépôt (`scripts/backtest/run_campaign.py:173-177`) : spread 0,5 bp du prix,
slippage 0,2 bp, **commission forfaitaire 0,50 € par opération**.

**Le basculement tient à 1,27 € par opération.** L'espérance brute est de +0,487 € ; le coût
mesuré la dépasse. Ce n'est pas une marge : c'est un renversement de signe.

### Les deux chiffres à retenir

1. **62,03 % mesuré contre 87,11 % publié.** L'écart est de 25 points, sur 158 opérations.
   L'arithmétique de la publication est cohérente (un PF de 4,72 avec 87,11 % de réussite
   impose un gain moyen de 0,698 fois la perte moyenne — le rapport mesuré ici est **0,690**,
   quasi identique). C'est donc bien le **taux de réussite** qui ne se reproduit pas, pas la
   géométrie du risque.
2. **Profit factor net 0,81, seuil exigé 1,20.** La porte `costs` échoue, et la porte `stress`
   (coûts ×2) aussi.

---

## 3. Le tour de la grille, et sa limite

Les 8 propositions du gabarit par défaut, mesurées sur le jeu complet (M1 or) :

| Supertrend | objectif | Opérations | Réussite | Net brut | PF brut | Net net | PF net |
|---|---|---|---|---|---|---|---|
| (7, ×2,0) | 0,7 R | 16 | 68,75 % | +26,82 € | **1,5364** | +6,12 € | 1,1128 |
| (7, ×2,0) | 0,5 R | 16 | 68,75 % | +4,84 € | 1,0969 | −15,05 € | 0,7229 |
| (10, ×2,0) | 0,7 R | 18 | 61,11 % | +6,85 € | 1,0979 | −16,03 € | 0,7895 |
| (7, ×1,5) | 0,5 R | 10 | 60,00 % | −10,12 € | 0,7470 | −22,84 € | 0,4827 |
| autres (4) | | 10-18 | 36-61 % | négatif | ≤ 0,79 | négatif | ≤ 0,51 |

**Une seule configuration sur huit est positive nette**, et elle l'est de **6,12 €** sur
**16 opérations** — un échantillon qui ne prouve rien, ni dans un sens ni dans l'autre. Le
meilleur profit factor net de la grille est **1,1128**, toujours sous le seuil de 1,20.

**Réserve honnête sur la grille :** le nombre d'opérations par proposition est très faible
(10 à 18), parce que `history_bars` consomme une grande part des 11 999 bougies et parce que
les conditions d'entrée sont restrictives. Ces lignes ne jugent pas la famille ; elles
montrent qu'aucune de ces combinaisons n'atteint le seuil sur cette fenêtre.

---

## 4. Ce que la mesure ne peut pas dire, et il faut le dire

### 4.1 Le walk-forward n'a pas pu tourner

`insufficient_data : 8/8` propositions lors de la première campagne. La cause est
structurelle, pas un défaut du code :

| Contrainte | Valeur |
|---|---|
| Plage d'historique du plan walk-forward | `train_bars=350`, `validation_bars=250` |
| Chauffe exigée par l'EMA 50 sur M5 | **250 bougies M1** (l'unité est 5 fois plus grossière) |
| Résultat | un pli de 250 bougies ne peut pas nourrir une règle qui en réclame 250 |

Une fenêtre glissante de 6 plis de 350/250 bougies est un protocole pour du M15. En M1, elle
est plus courte que la chauffe des indicateurs qu'elle doit juger. **Aucune des portes
walk-forward, hors-échantillon scellé, Monte-Carlo ou robustesse des paramètres n'a donc été
franchie ou échouée** : elles n'ont pas pu être évaluées.

### 4.2 Le scellé n'a jamais été ouvert

0 ouverture sur les deux marchés. Les 2 401 bougies scellées de chaque marché restent
intactes, donc réutilisables — **mais aucun p-value n'a été calculée**, et la correction de
sélection multiple n'a rien eu à démettre.

### 4.3 La fenêtre de données ne peut pas trancher

Le test publié couvre 2020 → 2026, soit ~2 281 jours. Le jeu gelé M1 couvre **12,8 jours**,
parce que le courtier ne sert pas de M1 au-delà du 2026-06-23 pour l'or (plafond de bougies
du terminal, `docs/reports/2026-10-03-mt5-capabilities.md:86`).

Conséquence statistique : les 158 opérations mesurées **ne sont pas 158 observations
indépendantes**, elles se répartissent sur un seul régime de marché. Ce document ne valide
ni n'invalide le chiffre publié ; il mesure ce que cette fenêtre peut porter, et elle ne peut
pas porter 2020-2026.

### 4.4 Le biais de résolution intrabar

La stratégie vise un objectif serré (0,7 R) derrière un stop large (3,0 × ATR), sur des
bougies M1. Quand une bougie touche l'objectif **et** le stop, c'est le **stop** qui est
retenu : `backtest/harness.py:361-362`, *« Pessimistic tie-break: a bar touching both the
stop and a target stops out first »*. C'est le choix honnête, et c'est aussi celui qui
abaisse le taux de réussite par rapport à un moteur qui trancherait en faveur de l'objectif.
Un backtest à 87 % de réussite sur des bougies — même à 78 % de qualité tick — peut gagner
plusieurs points de réussite uniquement par ce choix de résolution. **C'est l'hypothèse la
plus probable pour expliquer l'écart de 25 points**, et elle ne peut être testée qu'avec des
données tick, dont l'or dispose depuis le 2019-01-02.

---

## 5. Verdict

**Sur ce que cette fenêtre peut mesurer : la stratégie ne passe pas.** 158 opérations,
62,03 % de réussite, profit factor net 0,81 pour un seuil de 1,20, et un résultat net négatif
de 124,07 €.

**Ce que cela ne dit pas.** Que la stratégie publiée est fausse : elle n'a pas pu être
reproduite dans les conditions de sa publication (six ans d'historique, données tick,
résolution intrabar différente). Une règle à objectif serré et stop large est précisément
celle dont le résultat dépend le plus de la résolution du moteur et de la qualité des ticks.
Le chiffre publié appartient donc à la catégorie « non vérifié », pas à la catégorie
« réfuté ».

**Les trois questions qui restent ouvertes, dans l'ordre de valeur :**

1. **La résolution intrabar explique-t-elle l'écart ?** Reproductible avec des données tick
   XAUUSD (disponibles depuis 2019-01-02), sur une période courte.
2. **La règle est-elle viable en M5 ou M15 ?** Le rapport coût/ATR y est plus favorable et
   l'historique est long (2,5 ans en M15, depuis 2011 en H1). Même règle, unité plus lente.
3. **Y a-t-il un edge hors coûts qui mérite qu'on cherche ?** Le brut est positif
   (+76,91 €, PF 1,13) sur une configuration et une seule. C'est mince, et ce n'est pas un
   edge tant que les coûts le dépassent.

**Ce qu'il ne faut pas faire :** promouvoir quoi que ce soit de cette famille. Aucun
manifeste n'a été écrit, aucune entrée n'a été ajoutée au registre, et `max_mode` reste
`SIGNAL` sur toutes les propositions.
