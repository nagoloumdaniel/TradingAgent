# Axe B — les filtres de la spec BTCUSD : ce qui est implémenté, ce qui a été mesuré

**Date :** 2026-10-09 · **Statut :** trois filtres implémentés et **désactivés par défaut**, un
candidat identifié, **aucun promu**.

---

## 1. Pourquoi ce document

La spec BTCUSD de l'opérateur demande **six filtres**. La règle `vwap_pullback` en implémente
trois : retour au VWAP, momentum EMA, pente. Ce travail en ajoute **au plus deux**, chacun
désactivable, et **mesure** ce qu'ils changent réellement — parce qu'un filtre qui « a l'air
juste » et qui retire des trades gagnants coûte plus cher que le bruit qu'il prétend écarter.

Le filtre de **coût** de la spec n'est pas implémenté ici, et ne doit pas l'être : la décision
est prise, il appartient au moteur de risque, qui seul connaît le spread réel du broker.

Le troisième candidat — **la tendance** — a été implémenté, testé, mesuré, et il est le seul qui
améliore le résultat de façon cohérente. Il reste **éteint par défaut** : voir § 5 et § 10.

## 2. Ce qui est écrit

| Élément | Chemin |
|---|---|
| Filtres | `src/tradingagent/strategies/library/vwap_pullback.py` |
| Tests | `tests/strategies/test_vwap_pullback.py` — **26 tests**, dont 11 sur les filtres |
| Mesure | `scripts/backtest/tune_filter_vwap.py` |
| Résultats bruts | `docs/research/vwap-tuning/axe-B-mesures.json` |
| Robustesse par moitiés | `axe-B-seance-robustesse.json`, `axe-B-tendance-robustesse.json` |
| Balayage des seuils | `axe-B-seuils-tendance.json` |

Les paramètres ajoutés, et leur état par défaut — c'est-à-dire **la règle livrée** :

| Paramètre | Défaut | Effet du défaut |
|---|---|---|
| `allowed_sessions: tuple[Session, ...]` | `()` | aucune contrainte d'horaire |
| `volume_ratio_min: float \| None` | `None` | aucune contrainte de participation |
| `trend_filter: bool` / `trend_slope_atr: float` | `False` / `0,05` | aucune contrainte de tendance |

Aucun indicateur n'a été écrit : `session_at` (séance), `trend_of` (régime), et le volume déjà
présent dans les bougies. Chaque filtre est une **sortie anticipée** de `evaluate` — il ne peut
que refuser un signal, jamais en créer un, ni desserrer un stop (C-002).

## 3. Protocole de mesure

BTCUSD M15, `docs/research/datasets-volume`, **jeu complet : 59 999 bougies**, du
2025-01-21T16:30 au 2026-10-09T01:45 UTC, empreinte
`2f04c3b23c31e4074bf618612b09f6ec4c2b55bcfa90b48d024918ff93578459`.

```
ema_fast=20, ema_slow=50, vwap_period=20, atr_period=14, stop_atr_multiplier=1.5,
first_target_rr=1.5, final_target_rr=3.0, pullback_atr=0.4, entry_zone_atr=0.1,
min_slope_atr=0.005, slope_window=10
partial_exit_fractions=(0.5, 0.5), move_stop_to_breakeven_after_first_target=True
coûts : spread 0,5 bp, slippage 0,2 bp, commission 0,50 € par opération, mode SIGNAL,
max_concurrent_positions=1, risque 10 € par opération
```

**Le contrôle de non-régression demandé, et il passe.** La règle **sans aucun filtre**, telle
qu'elle est livrée, rend **883 trades, 40,2 %, -392,74 €, PF 0,9314** — exactement la référence
du lead. Les filtres désactivés par défaut ne changent donc rien, au centime près. Le script
vérifie ce chiffre automatiquement à chaque exécution et affiche « aligné » ou « DÉSALIGNÉ ».

**Pourquoi le jeu complet et pas une fenêtre.** La même configuration donne **268 trades / PF
0,900** sur les 20 000 dernières bougies et **301 trades / PF 0,990** sur une autre fenêtre de
20 000. Trois chiffres pour une seule règle : le découpage décide du résultat, et un filtre
choisi sur la fenêtre qui flatte serait choisi sur du bruit.

**Le contrôle de robustesse.** Chaque variante est aussi mesurée sur les **deux moitiés** de la
série (29 999 bougies chacune), première et seconde. C'est le test décisif ici : un filtre qui
améliore le PF sur la série entière mais le dégrade sur une moitié a trouvé la moitié qui lui
convenait. Deux variantes passent ce filtre — la séance « overlap seul » et la tendance à 0,05.

## 4. Ce que la mesure dit sur le jeu complet

**1 973 signaux bruts**, dont 883 deviennent des opérations complètes (le reste est refusé faute
de place : une seule position à la fois).

| Variante | Trades | Réussite | Net | PF | t | 1ᵉʳ moitié | 2ᵉ moitié |
|---|---|---|---|---|---|---|---|
| **référence (aucun filtre)** | **883** | **40,2 %** | **-392,74 €** | **0,931** | -0,92 | 0,932 | 0,933 |
| séance : overlap + Londres + New York | 610 | 41,5 % | -75,18 € | 0,981 | -0,22 | 1,040 | 0,924 |
| **séance : overlap seul** | **175** | **43,4 %** | **+107,73 €** | **1,100** | 0,58 | 1,153 | 1,053 |
| volume ≥ 1,0× | 574 | 39,9 % | -372,11 € | 0,901 | -1,16 | 0,955 | 0,824 |
| volume ≥ 1,2× | 275 | 45,5 % | +178,90 € | 1,109 | 0,79 | 1,186 | 1,016 |
| **tendance : seuil du régime (0,05)** | **117** | **53,0 %** | **+331,61 €** | **1,555** | **2,18** | **1,581** | **1,539** |
| tendance : seuil 0,02 | 377 | 43,2 % | +137,01 € | 1,059 | 0,52 | 0,943 | 1,187 |
| tendance : seuil 0,001 | 603 | 41,8 % | -104,21 € | 0,972 | -0,32 | 0,920 | 1,026 |
| tendance : seuil 0,10 | 17 | 58,8 % | +109,48 € | 2,469 | 1,72 | 1,331 | 5,349 |
| tendance : seuil 0,20 | 0 | — | 0,00 € | — | — | — | — |
| séance UT + volume ≥ 1,0× | 387 | 42,6 % | +14,62 € | 1,006 | 0,05 | 1,182 | 0,821 |

Colonnes « moitiés » : PF de chaque moitié de la série. `t` : statistique de Student de
l'espérance par opération — **optimiste**, elle suppose les trades indépendants alors qu'ils se
groupent par régime ; elle sert à écarter une conclusion, pas à la fonder.

### Lecture

**Quatre variantes franchissent PF 1,0, une seule le fait pour de bonnes raisons.** Le
séance-UT + volume à PF 1,006 est un artefact de découpage : 1,182 sur la première moitié, 0,821
sur la seconde. Le seuil de tendance 0,10 affiche PF 2,469 — sur **17 trades**, avec 9 dans une
moitié et 8 dans l'autre : c'est du bruit, pas un résultat. Le volume ≥ 1,2× est positif dans
les deux moitiés (1,186 et 1,016) mais la seconde est trop proche de 1 pour conclure.

**Le filtre de tendance à 0,05 est le seul résultat cohérent :**

1. il améliorerait le PF de **0,931 à 1,555**, le net de **-392,74 € à +331,61 €**, la réussite
   de 40,2 % à 53,0 % ;
2. **les deux moitiés de la série restent au-dessus de 1,5** (60 trades à 1,581, 57 à 1,539) —
   aucun autre candidat ne fait ça ;
3. la statistique t vaut **2,18**, la plus élevée du tableau ;
4. le gradient est **monotone en le seuil**, ce qui est le signe d'une information et non d'un
   accident :

| Seuil de pente sur 1 barre | Trades | Réussite | Net | PF | t |
|---|---|---|---|---|---|
| 0,001 | 603 | 41,8 % | -104,21 € | 0,972 | -0,32 |
| 0,02 | 377 | 43,2 % | +137,01 € | 1,059 | 0,52 |
| **0,05** | **117** | **53,0 %** | **+331,61 €** | **1,555** | **2,18** |
| 0,10 | 17 | 58,8 % | +109,48 € | 2,469 | 1,72 |
| 0,20 | 0 | — | — | — | — |

Plus la pente exigée est raide, plus le taux de réussite monte. Un filtre qui ne ferait que
réduire l'échantillon au hasard ne produirait pas ce gradient.

## 5. Pourquoi le filtre de tendance marche, alors qu'il a l'air contradictoire

**L'objection, et elle est sérieuse.** `trend_of` lit la pente de la moyenne lente sur **une**
barre. L'événement que cette règle attend est le **retour** du prix sur le VWAP : les moyennes
convergent, la pente s'aplatit. Exiger « ça pousse encore » à cet instant précis ressemble à une
contradiction — et sur le papier, ça en est une.

**La mesure dit autre chose.** Sur les 1 973 signaux du jeu complet, la pente au moment du signal
est positive sur **120 seulement** (6,1 %), et 62 portent une tendance nommée. Le filtre ne garde
donc qu'environ **12 % des signaux** — mais il garde les bons. L'explication économique tient :
un repli qui **rejoint** le VWAP et dont la pente d'EMA est encore positive à cet instant n'est
pas le même événement qu'un repli qui casse la moyenne. Le premier est une respiration dans une
tendance intacte ; le second est le début d'un retournement. La pente d'une barre est la mesure
la plus réactive qui distingue les deux — c'est précisément parce qu'elle est presque toujours
négative pendant un repli qu'elle est **informative** quand elle ne l'est pas.

**Ce que cela ne prouve pas.** Le filtre coupe 88 % des signaux, et un résultat sur 117 trades
n'est pas un résultat sur 883. La statistique t de 2,18 est optimiste — les trades se groupent
par régime, une seule position est ouverte à la fois — et elle est tirée de **16 mesures** : sur
une famille de variantes testées, une t de 2,18 est attendue au moins une fois par hasard. Le
gradient monotone et la cohérence entre les deux moitiés sont plus convaincants que la t, mais
ils portent sur **un seul marché**.

**Conclusion : candidat, pas réglage.** Il reste **éteint** par défaut. Le rallumer demande un
test hors échantillon — or natif, autre découpage, ou XAUUSD — pas un nouvel ajustement.

## 6. Le filtre de séance : vrai mais faible

**Justification économique.** Le VWAP du jour est un niveau que le marché défend quand il est
là. La fenêtre **overlap** (13:00-16:00 UTC) est la plus liquide de la journée, Londres et New
York ouvertes ensemble. Un contact sur un carnet mince — Tokyo, 00:00-07:00 UTC — est une barre
traversée, pas un rejet.

**Le résultat est réel mais fragile.** Le filtre overlap seul rend PF 1,100 sur 175 trades, et
il est positif dans les deux moitiés (1,153 et 1,053). Mais sa statistique t vaut **0,58** :
l'espérance par opération n'est pas distinguable de zéro. Le filtre plus large — overlap +
Londres + New York — est nettement moins bon (0,981) et n'est pas cohérent entre moitiés
(1,040 puis 0,924) : ajouter les heures de séance sans le croisement détruit l'effet.

**Décision : laissé éteint**, mais c'est le second candidat à un test hors échantillon.

## 7. Le filtre de volume : le seuil décide du signe, ce qui est mauvais signe

**Justification économique.** Un retour au VWAP sans volume est une barre traversée, pas un
niveau défendu. Le seuil est **relatif** — volume de la barre de contact divisé par la moyenne
des 20 barres **précédentes** — parce qu'un volume absolu ne veut rien dire sans le contexte du
marché et de l'heure. La barre courante est exclue de sa propre référence : l'inclure ferait
qu'un pic se rabote lui-même d'autant plus qu'il est fort.

**Le résultat est incohérent, et c'est le plus instructif.** À 1,0× (le volume est au moins à sa
moyenne), le filtre **dégrade** : PF 0,901 contre 0,931, et 0,824 sur la seconde moitié. À 1,2×
il **améliore** : PF 1,109, 1,186 puis 1,016. Un filtre dont le signe s'inverse quand on bouge
le seuil de 20 % ne mesure pas une propriété du marché, il mesure le seuil. Le recensement
explique pourquoi le voisinage est si sensible : la médiane du volume relatif au moment du signal
vaut **0,965** — les signaux arrivent légèrement *sous* le volume habituel, et le seuil à 1,0×
coupe donc la population en son milieu, là où le bruit domine.

**Décision : laissé éteint.** Le filtre est implémenté et testé, mais aucune de ses deux valeurs
ne justifie de le rallumer.

## 8. Les tests qui prouvent chaque filtre

TDD : les 11 tests ont été écrits **avant** l'implémentation, et les 11 échouaient d'abord
(`extra_forbidden` sur des paramètres inexistants). Chaque filtre a son test de refus et son test
de laisser-passer.

| Filtre | Refuse | Laisse passer |
|---|---|---|
| Séance | `test_the_session_filter_refuses_a_bar_outside_the_allowed_windows` — 18:00 UTC appartient à New York, pas à l'overlap | `test_the_session_filter_lets_the_declared_session_through` |
| Volume | `test_the_volume_filter_refuses_a_touch_on_a_dried_up_bar` — volume 10 contre une moyenne de 100 | `test_the_volume_filter_lets_a_touch_on_an_active_bar_through` |
| Tendance | `test_the_trend_filter_refuses_a_downtrend_for_a_buy` | `test_the_trend_filter_lets_a_real_uptrend_through` |
| Tous | `test_every_filter_is_off_by_default` — un filtre qui change le comportement sans qu'on l'ait demandé n'est pas un filtre | — |

Deux tests méritent d'être signalés parce qu'ils ont **trouvé** quelque chose :

- `test_the_volume_filter_measures_the_bar_against_its_predecessors_only` : si la barre courante
  entrait dans sa propre moyenne, un pic de volume vaudrait 160/103 ≈ 1,55 au lieu de 1,60 — un
  pic fort se raboterait lui-même d'autant plus qu'il est fort. Le test fige le 1,60.
- `test_the_trend_filter_lets_a_real_uptrend_through` : sa première rédaction utilisait la
  fixture nominale de la règle, et elle échouait — la pente d'une barre y vaut **-0,0022**. Il a
  fallu balayer **105 géométries de repli** pour en trouver une où les deux mesures s'accordent,
  et abaisser le seuil à `1e-9` pour la voir passer. Ce test est la trace du fait que le cas
  « laisser passer » est **rare**, pas normal.

## 9. Reproduire

```bash
uv run python scripts/backtest/tune_filter_vwap.py                      # jeu complet, ~20 min
uv run python scripts/backtest/tune_filter_vwap.py --split              # + les deux moitiés
uv run python scripts/backtest/tune_filter_vwap.py --bars 20000         # la fenêtre, pour comparer
uv run pytest tests/strategies/test_vwap_pullback.py -q
```

Le script confronte automatiquement la mesure sans filtre aux chiffres de référence et affiche
« aligné » ou « DÉSALIGNÉ » avec l'écart : un chiffre qu'on cherche à atteindre finit par être
atteint par un réglage.

## 10. Ce qui reste ouvert

1. **Le filtre de tendance à 0,05 n'a pas vu de données neuves.** 117 trades sur un seul marché et
   une seule période. Ce qu'il faut, dans cet ordre : XAUUSD, H1 natif depuis 2011, et un
   découpage H4 de la série BTCUSD. Rien de tout cela n'est un ajustement de paramètre — c'est le
   même réglage, appliqué ailleurs.
2. **La statistique t est optimiste et non corrigée.** Les trades se groupent par régime et une
   seule position est ouverte à la fois : l'intervalle réel est plus large que celui publié, et
   les 16 mesures n'ont pas été corrigées pour comparaisons multiples. Une correction honnête
   exigerait un test de permutation ou un bootstrap par blocs.
3. **Le filtre de volume mérite d'être repris autrement.** Deux seuils, deux signes opposés : ce
   n'est pas la bonne forme de mesure. Une pente de volume (le volume monte-t-il pendant le
   repli ?) est une hypothèse différente de « le volume est-il au-dessus de sa moyenne ».
4. **La moitié des signaux est écartée faute de place** : sur 1 973 signaux, 883 opérations, le
   reste étant refusé parce qu'une position est déjà ouverte. Toute mesure de filtre est donc
   partiellement une mesure de **priorité** : quelle position occupe le créneau. C'est un axe à
   part entière, et il n'appartient pas à cette tâche.
5. **Le filtre de coût n'est pas dans la stratégie.** Décision déjà prise : il appartient au
   moteur de risque, qui seul connaît le spread réel du broker. Rien de cet axe ne le déplace.
