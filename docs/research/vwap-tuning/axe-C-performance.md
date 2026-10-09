# Axe C — le goulot de performance : profil, optimisation prouvée, et ce qui reste

**Le chiffre à retenir : un backtest de 20 000 bougies passe de 74,3 s à 61,8 s (médianes de six
exécutions appariées), soit −17 % (×1,20) — à résultat strictement identique.** Même digest,
mêmes 342 opérations, même facteur de profit `0.8712114237539049`.

Les pourcentages plus spectaculaires du banc d'essai (`require_finite` −70 %, `vwap` −37 %,
`atr` −14 %) ne se transposent **pas** au backtest, et c'est normal : l'optimisation ne touche
qu'une partie du temps. Le backtest passe aussi son temps dans le harnais, dans les fenêtres de
400 bougies reconstruites à chaque barre et dans `_entry_features` — trois postes que ce
changement ne touche pas. Un lecteur qui retient « facteur 3 » se trompe : le facteur réel est
**1,20**.

Et la suite est chiffrée : `_entry_features` représente désormais **52 % du temps mural** d'un
backtest. La section 10 en donne la correction exacte, non appliquée ici.

---

## 1. Comment ces chiffres ont été obtenus

Trois autres agents mesuraient en même temps, et la charge de la machine fait varier un backtest
de 20 000 bougies de 57 s à 80 s **à code identique**. Aucun chiffre de temps n'est donc cité
seul : chaque comparaison est **appariée** (avant/après exécutés l'un après l'autre, dans les
mêmes conditions), et quand c'était possible **alternée** (A/B/A/B).

Deux garde-fous matériels :

* **un digest canonique** — SHA-256 de toutes les opérations (dates, sens, PnL, excursions,
  features) et de toutes les statistiques — imprimé à chaque exécution. Deux exécutions qui
  donnent le même digest ont produit exactement les mêmes opérations. C'est le juge de paix :
  le gain ne compte que si le digest ne bouge pas ;
* **un worktree git** pour l'A/B : `git worktree add` sur une copie du dépôt, où seuls les trois
  fichiers d'indicateurs changent de révision. L'arbre principal n'est jamais modifié, et les
  deux côtés tournent dans le même checkout, avec les mêmes fins de ligne, le même harnais, la
  même stratégie et le même jeu de données.

### Révisions mesurées

| côté | commit des indicateurs | `_checks.py` | `volatility.py` | `vwap.py` |
|---|---|---|---|---|
| avant | `bfa0e65` | `501800d7e07c` | `7f4cc9c28092` | `018aa18a0472` |
| après | `fa76d2a` | `898de7c95b66` | `b1ce5429047f` | `fd44798fe3cf` |

Inchangés dans les deux côtés : `moving_average.py=8cb3e55fa4c8`, `features.py=b126b516a2b3`,
`harness.py=b148df971710`, `evaluation.py=986f6a28b146`, `vwap_pullback.py=6a0da2b30cde`.
Les empreintes sont lues sur les modules **réellement importés** (`profile_axe_c.py` les résout
par `inspect.getsourcefile`) : un A/B qui fait pointer `PYTHONPATH` ailleurs s'imprime lui-même
comme tel au lieu de mentir.

Depuis ces mesures, `harness.py` a reçu des modifications **non commitées** d'un autre axe (une
porte `entry_zone_parity`, désactivée par défaut). Toutes les mesures appariées de la section 6
ont été prises dans le worktree, sur la révision committée `b148df971710`, précisément pour que
l'écart mesuré ne vienne que des trois fichiers d'indicateurs. Le digest, lui, est resté
identique sur les deux harnais : la porte est inerte tant qu'on ne l'allume pas.

### Le jeu de mesure

`docs/research/datasets-volume/BTCUSD-M15-mt5-…-2026-10-09.jsonl` (60 000 bougies), tronqué aux
**20 000 dernières** (empreinte `b6550077e579`), stratégie `vwap_pullback@1.0.0` avec les
paramètres de son manifeste de production, modèle de coûts du dépôt (spread 0,5 bp, slippage
0,2 bp, 0,50 € par opération). Le même backtest produit **19 601 décisions, 603 signaux,
342 opérations, PF 0,8712, profit net −206,16 €**.

---

## 2. Le profil reproduit (mesuré par moi, pas repris du lead)

`uv run python scripts/backtest/profile_axe_c.py --bars 20000 --profile --top 22 --digest`

Les compteurs d'appels collent à ceux annoncés : `require_finite` **8 002 680** appels (annoncé :
8 003 368), `vwap` **19 601** (identique), `atr` 20 969 (annoncé : 21 141), `_entry_features`
**342** appels (annoncé : 385 — la stratégie a été modifiée entre-temps, l'écart vient de là).

Le profil refait montre surtout deux choses que le top-5 initial ne montrait pas.

**a) La cause réelle du coût de `require_finite` : `typical_price`.**

| fonction | appels | temps propre | temps cumulé |
|---|---|---|---|
| `indicators/_checks.py:require_finite` | 8 002 680 | 46,96 s | 74,38 s |
| `{built-in method math.isfinite}` | 142 411 567 | 27,42 s | 27,42 s |
| `indicators/vwap.py:typical_price` | 7 840 400 | 5,38 s | 21,61 s |
| `indicators/volatility.py:_true_range` | 21 859 263 | 21,06 s | 38,71 s |
| `indicators/volatility.py:atr` | 20 969 | 21,73 s | 99,16 s |
| `indicators/vwap.py:vwap` | 19 601 | 17,73 s | 73,74 s |
| `backtest/harness.py:_entry_features` | 342 | 2,16 s | 110,83 s |

**7 840 400 des 8 002 680 appels à `require_finite` — 97,97 %, donc 98 % — venaient de
`typical_price`, appelé une fois par barre dans la boucle de `vwap`.** Or `vwap` venait de
valider les trois séries **entières** dix lignes plus haut : chaque triplet `(high, low, close)`
était revalidé alors qu'il l'était déjà, à l'intérieur de la boucle la plus chaude du dépôt.
C'était la vraie cause, et elle n'est visible qu'en croisant le nombre d'appels de
`typical_price` avec celui de `require_finite`.

**b) `_entry_features` est le premier poste, et il est hors de mon périmètre.**

110,83 s de temps cumulé sur 258,9 s profilées, soit **42,8 %** — dont `structure_of` (28,97 s)
et des `atr` recalculés sur des préfixes de 10 000 à 20 000 barres. La fonction vit dans
`backtest/harness.py`, qui appartient à un autre axe : elle n'a **pas** été touchée, et la
section 10 en donne la correction sous forme de proposition écrite.

---

## 3. Les optimisations envisagées, et ce qu'elles sont devenues

| # | idée | sort | pourquoi |
|---|---|---|---|
| 1 | `require_finite` : parcours en C (`all(map(math.isfinite, …))`) au lieu d'une boucle Python | **appliquée** | même contrat, même message ; l'index n'est reconstruit que dans le cas fautif. 400 valeurs : 25,8 µs → 7,6 µs |
| 2 | `vwap` : écrire le prix typique en ligne au lieu d'appeler `typical_price` | **appliquée** | la finitude des trois séries vient d'être vérifiée **en entier** : revalider chaque triplet ne protège rien de plus. Supprime 7,84 M appels |
| 3 | `vwap` : lire `moment.timestamp()` sans `astimezone(UTC)` par barre | **appliquée** | `timestamp()` porte déjà l'instant absolu : `02:00+02:00` et `00:00Z` donnent le même nombre. Prouvé sur quatre écritures du même instant |
| 4 | `atr` : écrire le true range en ligne au lieu d'appeler `_true_range` | **appliquée** | helper privé, un seul appelant, même ordre d'opérations (`max` reçoit ses trois candidats dans le même ordre) |
| 5 | `atr` : écrire le lissage de Wilder en ligne au lieu d'appeler `wilder` | **rejetée** | `wilder` est partagé avec le RSI : l'écrire en ligne ici dupliquerait une formule pour ~2 % de gain |
| 6 | `vwap` : remplacer `moment.utcoffset() is None` par `moment.tzinfo is None` | **rejetée** | plus rapide, mais **pas équivalent** : un `tzinfo` exotique dont `utcoffset()` rend `None` serait accepté à tort, et un datetime naïf serait alors lu en heure locale. Le contrat de sûreté passe avant 1,5 s |
| 7 | `require_finite` : ne valider que les valeurs nouvelles, ou mémoriser les séries déjà vues | **rejetée** | exige un état ; le dépôt impose des fonctions pures, sans état global. Le contrat ne doit pas dépendre de l'ordre des appels |
| 8 | `_entry_features` : ne calculer le contexte que sur les N dernières barres | **rejetée** | **n'est pas neutre** : l'ATR de Wilder est récursif, sa valeur dépend de tout l'historique. Tronquer change les nombres publiés — donc les résultats de recherche |
| 9 | `_entry_features` : porter les récurrences dans la boucle du harnais | **proposée** (section 10) | bit à bit identique, et vaut 52 % du temps mural. Hors périmètre : `harness.py` appartient à un autre axe |

---

## 4. Ce qui a été appliqué, et pourquoi c'est neutre

Quatre changements, dans trois fichiers (`indicators/_checks.py`, `indicators/volatility.py`,
`indicators/vwap.py`), commit `fa76d2a`.

1. **`require_finite`** parcourt la séquence avec `all(map(math.isfinite, values))` et ne
   reconstruit l'index qu'en cas d'échec. Même exception, même message, même premier index
   fautif. Garde finale ajoutée : si un appelant passait un itérateur à usage unique — ce que la
   signature `Sequence` interdit — la fonction refuse au lieu de laisser passer une valeur non
   finie en silence.
2. **`vwap`** calcule `(high + low + close) / 3.0` en ligne : la validation des trois séries
   entières, faite avant la boucle, couvre déjà chaque élément que la boucle touche.
   `typical_price` reste publique et garde son propre contrat pour ses appelants directs.
3. **`vwap`** appelle `_session_index(moment, …)` sans `astimezone(UTC)` : pour un datetime
   conscient du fuseau, `timestamp()` rend l'instant absolu, identique quelle que soit
   l'écriture du fuseau.
4. **`atr`** écrit son true range en ligne ; `_true_range`, privé et sans autre appelant, est
   supprimé. L'ordre des opérations est conservé à l'identique — c'est lui qui fixe le bit.

---

## 5. La preuve d'invariance

`tests/indicators/test_perf_invariance.py` — **111 tests, verts avant et après le changement.**
Écrit **avant** l'optimisation, avec les digests figés à ce moment-là.

Trois étages indépendants :

1. **égalité bit à bit avec la version d'avant**, recopiée mot pour mot dans le test
   (`_reference_atr`, `_reference_vwap`, `_reference_require_finite`, `_reference_true_range`).
   La comparaison porte sur les octets IEEE-754 via `struct.pack(">d", …)`, pas sur
   `pytest.approx` : `-0.0` et `0.0` sont égaux en Python et ne le sont pas pour un harnais qui
   trie des niveaux. Sont couverts : longueurs 0 à 1 003, périodes 1 à 20, séries entières,
   marché plat, valeurs extrêmes (`1e300`, `1e-300`), volumes nuls, volumes manquants (`None`),
   quatre ancres de session, quatre écritures du même instant ;
2. **les erreurs** : même type, même message, et **même ordre** de refus (NaN dans `highs` *et*
   volume négatif → c'est la finitude qui parle, comme avant) ;
3. **un backtest complet** sur un jeu synthétique déterministe (graine fixe, 2 400 bougies) et
   une sonde locale qui **dépend numériquement** de `atr` et de `vwap` : zone d'entrée, stop et
   objectifs sont calculés à partir de leurs valeurs. Un ulp de différence déplace un
   remplissage, donc change le digest. Deux digests figés avant le changement :

   * `GOLDEN_PROBE_DIGEST` = `281279991f638cdc4dd5a88030766807f84c4bf7d9e82cdf5d722ca1e800c6d5`
     (83 opérations, PF 0,6230) ;
   * `GOLDEN_INDICATOR_DIGEST` = `d68c8dadbed4a5785cb08f608cb2c4b97c2a9d19d073f69ed500ca5bcc37b861`
     (chaque flottant des séries ATR et VWAP).

`uv run pytest tests/indicators/test_perf_invariance.py -q` → **111 passed**.

Et sur le vrai jeu de 20 000 bougies, le digest est resté identique dans les douze exécutions de
l'A/B : `80904482de5d4d61cd04adfd45e2c2bfdf3bf74c3a42058ec43ddf2f415dd748`.

---

## 6. Le gain mesuré

### 6.1 Banc d'essai : le coût intrinsèque d'une fenêtre de 400 bougies

A/B dans le **même processus**, en alternance, meilleur de 7 × 400 appels — la charge de la
machine frappe les deux candidats de la même façon :

| fonction | avant | après | gain |
|---|---|---|---|
| `vwap(400)` | 757,7 µs | 479,9 µs | **−36,7 %** (×1,58) |
| `atr(400)` | 277,8 µs | 237,8 µs | **−14,4 %** (×1,17) |

Isolément, `require_finite` sur 400 valeurs : 25,8 µs → 7,6 µs (**−70 %**) ; sur 3 valeurs :
0,46 µs → 0,36 µs (−22 %).

Les projections faites *avant* d'appliquer quoi que ce soit (vwap −45 %, atr −28 %) étaient donc
un peu optimistes pour `atr` : elles mesuraient des variantes dans lesquelles `require_finite`
était **déjà** la version rapide, donc elles isolaient le reste du changement, pas l'ensemble.

### 6.2 Le backtest complet — le chiffre qui compte

Même worktree, mêmes fins de ligne, même harnais, seuls les trois fichiers d'indicateurs
changent de révision. Deux tours, en alternance avant/après, deux exécutions par invocation :

| tour | avant (s) | après (s) |
|---|---|---|
| 1 | 76,337 / 76,020 | 60,292 / 61,439 |
| 2 | 73,376 / 72,266 | 62,390 / 64,290 |
| 3 (préalable) | 72,043 / 75,171 | 62,216 / 60,755 |
| **médiane** | **74,27** | **61,83** |
| meilleur temps | 72,04 | 60,29 |

**Gain : −16,8 % sur les médianes, −16,3 % sur les meilleurs temps, soit ×1,20.** Douze
exécutions, douze digests identiques.

Commande exacte :

```bash
# côté « avant » : les indicateurs de bfa0e65 dans un worktree
git worktree add --detach "$TEMP/ta_before" HEAD
git -C "$TEMP/ta_before" checkout bfa0e65 -- \
    src/tradingagent/indicators/_checks.py \
    src/tradingagent/indicators/volatility.py \
    src/tradingagent/indicators/vwap.py
PYTHONPATH="$TEMP/ta_before/src" uv run python scripts/backtest/profile_axe_c.py \
    --bars 20000 --repeat 2 --digest
# côté « après » : remettre les trois fichiers de fa76d2a dans le worktree, relancer la même
# commande (mêmes trois chemins, avec fa76d2a au lieu de bfa0e65)
git -C "$TEMP/ta_before" checkout fa76d2a -- \
    src/tradingagent/indicators/_checks.py \
    src/tradingagent/indicators/volatility.py \
    src/tradingagent/indicators/vwap.py
PYTHONPATH="$TEMP/ta_before/src" uv run python scripts/backtest/profile_axe_c.py \
    --bars 20000 --repeat 2 --digest
```

Le script imprime les empreintes des modules réellement importés : la sortie dit donc elle-même
quel côté vient de tourner.

### 6.3 Le profil, avant et après (même harnais des deux côtés)

| | avant | après |
|---|---|---|
| appels de fonction (profilés) | 382 679 022 | **195 057 111** (−49 %) |
| `require_finite` | 8 002 680 appels, 74,38 s cumulées | 162 280 appels (calculé), **hors du top 22** |
| `typical_price` | 7 840 400 appels, 21,61 s | **0** |
| `_true_range` | 21 859 263 appels, 38,71 s | **0** |
| `astimezone` | 7 897 748 appels, 3,18 s | **0** |
| `vwap` | 73,74 s cumulées | 31,76 s |
| `atr` | 99,16 s cumulées | 62,73 s |
| `_entry_features` | 110,83 s cumulées (42,8 %) | 82,68 s (48,8 %) |
| `structure_of` | 28,97 s | 30,31 s (inchangé) |

Les temps *profilés* ne sont pas des temps muraux : `cProfile` ajoute un événement par appel, et
cette optimisation supprime 188 millions d'appels. C'est pourquoi le profil s'améliore de 35 %
quand le temps mural ne s'améliore que de 17 %. `math.isfinite` disparaît du profil non pas
parce qu'il n'est plus appelé, mais parce qu'il est désormais appelé **depuis `map`**, en C, où
`cProfile` ne le voit plus.

Le compte des appels restants de `require_finite` est exact et se déduit du profil :
3 par `vwap` (19 601), 3 par `atr` (20 969) et 1 par `ema` (40 570) → **162 280 appels**.

---

## 7. Deux chiffres corrigés, et pourquoi

**Les « 3 minutes par backtest » du lead étaient un artefact de mesure.** Ce chiffre venait d'un
run **profilé** sous `cProfile` (facteur 2 à 3 sur du code à appels minuscules) sur une machine
occupée par trois agents. La baseline non profilée, mesurée deux fois, est **79,8 s / 80,4 s le
jour du changement** et **72,0-76,3 s sur machine libre** — jamais 180 s. C'est la deuxième fois
dans la journée qu'un chiffre du lead tombe devant une mesure indépendante, et c'est exactement
ce que le dispositif doit produire : un chiffre non reproductible n'est pas un chiffre.

**Mon propre « 79,8 s → 57,3 s, soit −28 % » était trop flatteur.** Ces deux mesures n'ont pas
été prises sous la même charge : le 79,8 s date du moment où trois agents balayaient, le 57,3 s
d'un creux d'activité. Refait en alternance sur machine libre, l'écart réel est **74,3 s →
61,8 s (−17 %, ×1,20)**. Le −28 % était une erreur de protocole, pas une erreur de code ; c'est
la version appariée qui est publiée ici, et c'est celle qu'il faut citer.

---

## 8. Ce que l'optimisation ne fait pas

* Elle ne touche **pas** `_entry_features` (52 % du temps mural) : `harness.py` appartient à un
  autre axe. Section 10 pour la correction proposée.
* Elle ne touche **pas** `structure.py` (`structure_of` : 13,10 s de temps mural mesuré),
  `regime.py`, `features.py`, ni `_last_smoothed` (~7 s profilés, 19 601 appels) — hors du
  périmètre d'écriture. `structure_of` est pourtant, comme l'ATR, une récurrence qu'on peut
  porter : `_confirmed_extremes` est déjà une mise à jour de gauche à droite à fenêtre bornée.
* Elle ne réduit pas le nombre de fenêtres recalculées : la stratégie recalcule toujours
  `vwap(400)`, `atr(400)` et deux `ema(400)` à chaque barre. C'est un choix d'architecture
  (fonctions pures, aucun état), et il reste 1,4 ms par décision à payer pour lui.
* Elle ne change aucun résultat, et c'est le point : **zéro** chiffre déplacé sur 12 exécutions
  complètes et 111 tests.

---

## 9. Ce qui reste à faire, en une phrase

Le poste dominant est désormais `_entry_features` : **34,3 s sur 65,7 s, soit 52 % du temps
mural** (mesuré par instrumentation, section 10). Le corriger vaut bien plus que tout ce qui a
été fait ici.

---

## 10. Proposition écrite : `_entry_features` incrémental, bit à bit identique

**Non appliquée.** `backtest/harness.py` appartient à un autre axe au moment de la rédaction.

### 10.1 Le problème, chiffré

`harness._entry_features(primary, index, config)` est appelée à chaque remplissage et
recalcule le contexte de marché sur `primary[: index + 1]` — **tout le préfixe depuis le début du
jeu**, alors que les indicateurs qu'elle compose sont des récurrences : leur valeur à la barre `i`
s'obtient à partir de leur valeur à la barre `i-1`, sans relire ce qui précède.

Mesuré sur les 20 000 bougies, révision optimisée, avec
`uv run python scripts/backtest/profile_axe_c_entry_features.py --bars 20000 --detail` :

| | valeur |
|---|---|
| backtest complet | 65,7 s |
| appels de `_entry_features` | 342 |
| temps total dans `_entry_features` | **34,3 s — 52,3 % du run** |
| coût moyen par appel | 100,4 ms |
| avant 5 000 barres | 25,3 ms par appel |
| après 15 000 barres | **172,4 ms par appel** |
| index des remplissages | min 510, médian 10 103, max 19 929 |

Le coût par appel est **linéaire dans la longueur du préfixe** : la fonction paie
**3 509 958 pas de barre** (somme exacte des 342 préfixes, index moyen 10 262) pour produire 342
dictionnaires. Répartition du temps (temps inclusifs, appels imbriqués compris) :

| brique | temps | part |
|---|---|---|
| `structure_of` | 13,10 s | 38 % |
| `trend_of` (contient `slope_in_atr` et un `atr`) | 5,73 s | 17 % |
| `slope_in_atr` (contient un `atr` et un `ema`) | 4,07 s | 12 % |
| `atr_ratio` (contient un `atr` de période 100) | 3,39 s | 10 % |
| `atr` (période 14, pour `values["atr"]`) | 3,38 s | 10 % |
| `market_structure` (fenêtre bornée) | ~0 s | 0 % |
| `session_at` (un seul instant) | ~0 s | 0 % |

### 10.2 Pourquoi on ne peut pas simplement tronquer le préfixe

**C'est le point délicat, et il interdit la solution facile.** `entry_features` appelle des
indicateurs **récursifs** : l'ATR de Wilder est
`atr[i] = (atr[i-1] × (p-1) + true_range[i]) / p`, donc `atr[i]` dépend de tout l'historique
avant `i` (décroissance géométrique de raison `1 − 1/p` par barre, soit 0,929 pour `p = 14` :
après 100 barres il reste 0,06 % de l'historique lointain, ce qui n'est pas zéro). Les EMA
(`fast=10`, `slow=30`) sont récursives de la même façon, et `atr_ratio` moyenne les 100 dernières
valeurs d'un **ATR de période 100**, encore plus lent à oublier.

Calculer le contexte sur les 400 dernières barres au lieu du préfixe complet **changerait les
nombres publiés**, donc les `features` enregistrées sur chaque opération, donc toute analyse qui
corrèle un contexte à un résultat. Ce n'est pas une optimisation : c'est une autre mesure.

### 10.3 La correction exacte : porter les récurrences dans la boucle du harnais

`run_backtest` parcourt déjà **chaque barre dans l'ordre**, une fois. Il suffit d'y porter l'état
des récurrences et de l'avancer d'un pas par barre. Le coût passe de 3 509 958 pas à
**20 000** — 175 fois moins — et, parce que les opérations sont appliquées **dans le même ordre**
que dans `atr`/`ema`, les valeurs sont **bit à bit identiques**.

État à porter (une instance locale à `run_backtest`, jamais globale) :

| état | amorçage (identique à `atr`/`ema`) | pas suivant |
|---|---|---|
| `atr14` (pour `values["atr"]`, `trend_of`, `slope_in_atr`) | à `index == 14` : `math.fsum(ranges[:14]) / 14` | `(current × 13 + ranges[i-1]) / 14` |
| `atr100` (pour `atr_ratio`, `lookback=100`) | à `index == 100` : `math.fsum(ranges[:100]) / 100` | `(current × 99 + ranges[i-1]) / 100` |
| anneau des 100 derniers `atr100` définis | — | `atr_ratio = atr100[i] / moyenne(anneau)` |
| `ema10`, `ema30` | à `index == p-1` : `math.fsum(closes[:p]) / p` | `alpha × closes[i] + (1 − alpha) × current`, `alpha = 2/(p+1)` |
| `ema30_previous` | — | la valeur d'`ema30` à `i-1`, pour `slope_in_atr` |
| `swing_high_latest`, `swing_low_latest` | `None` | `index - 2` si `_is_extreme(valeurs, index - 2, 2)`, sinon inchangé |
| les deux derniers extrêmes **distincts** par côté | — | recopie du balayage de `_last_two` |

Deux précisions qui évitent les pièges :

* `ranges[i-1]` est le true range de la barre `i` : `max(high[i]-low[i], |high[i]-close[i-1]|,
  |low[i]-close[i-1]|)`. Il doit être calculé **avec le même ordre d'arguments** que dans `atr`
  — `max` rend le premier de ses plus grands, donc `-0.0` contre `0.0` se décide là ;
* `market_structure` (canal de 20 barres précédentes) et `session_at` sont déjà bornés : rien à
  faire. `structure_of` se ramène à `_confirmed_extremes`, qui **est déjà** la récurrence
  `latest = index - strength si _is_extreme(...) sinon latest` ; il suffit de la faire vivre
  barre par barre au lieu de la relancer sur tout le préfixe.

### 10.4 Comment le prouver, dans cet ordre

1. **le test d'abord**, avant toute modification : pour chaque barre `i` d'une série de ~600
   bougies (déterministe, graine fixe), `state.snapshot()` doit être **bit à bit** égal à
   `entry_features(moments[:i+1], highs[:i+1], lows[:i+1], closes[:i+1], atr_period=…)` —
   comparaison sur `struct.pack(">d", …)`, comme dans
   `tests/indicators/test_perf_invariance.py`. Aux 342 remplissages d'un backtest réel, le
   dictionnaire doit être identique **clé par clé** ;
2. **le digest du backtest** : `Trade.features` entre dans le digest de toutes les opérations.
   `uv run python scripts/backtest/profile_axe_c.py --bars 20000 --digest` doit rendre
   `80904482de5d4d61cd04adfd45e2c2bfdf3bf74c3a42058ec43ddf2f415dd748`. Attention : ce digest est
   celui de la révision courante ; si la stratégie ou le jeu gelé changent, il change pour de
   bonnes raisons — il faut alors comparer **avant/après dans le même worktree**, comme en 6.2 ;
3. **la mesure** : `uv run python scripts/backtest/profile_axe_c_entry_features.py --bars 20000`
   avant et après. Attendu : les 342 appels passent de ~100 ms à ~0,2 ms, et le backtest complet
   de ~62-66 s à ~31-33 s (les 34,3 s disparaissent, il reste le coût des 20 000 pas — environ
   0,2 % du travail actuel).

### 10.5 Ce qu'il ne faut pas oublier

* l'état doit être avancé **depuis la première barre du jeu**, y compris avant
  `evaluation_start` : la récurrence dépend de tout l'historique antérieur ;
* si `entry_features` gagne un indicateur plus tard, l'état doit être étendu — et le test du
  point 1 le signalera, puisque la comparaison se fait barre par barre sur le dictionnaire
  entier ;
* la même correction s'applique à `_atr_at` (utilisé quand le slippage dépend de l'ATR ou avec
  un trailing) et au trailing sur structure : ce sont les mêmes récurrences.

---

## 11. Reproductibilité

```bash
# le profil reproduit, avec les compteurs d'appels et le digest
uv run python scripts/backtest/profile_axe_c.py --bars 20000 --profile --top 22 --digest

# le coût intrinsèque d'une fenêtre de 400 bougies (vwap / atr), sans backtest
uv run python scripts/backtest/profile_axe_c.py --bench 400

# le gain sur le backtest complet, apparié (protocole de la section 6.2)
uv run python scripts/backtest/profile_axe_c.py --bars 20000 --repeat 2 --digest

# le coût réel de _entry_features, et sa répartition par brique
uv run python scripts/backtest/profile_axe_c_entry_features.py --bars 20000 --detail

# la preuve d'invariance
uv run pytest tests/indicators/test_perf_invariance.py -q
```

`profile_axe_c.py` lit la stratégie et ses paramètres dans
`config/strategies/vwap_pullback@1.0.0.yaml` : la mesure porte sur ce que l'agent exécute, pas
sur une copie. Il imprime aussi l'empreinte SHA-256 de chaque module du chemin mesuré, lue sur
les modules importés — deux exécutions ne se comparent que si ces empreintes sont identiques.

---

## 12. En résumé

| question | réponse |
|---|---|
| Gain réel sur un backtest de 20 000 bougies | **74,3 s → 61,8 s (médianes, 12 exécutions appariées), −17 %, ×1,20** |
| Gain micro (`vwap(400)` / `atr(400)`) | −36,7 % / −14,4 % — **ne pas citer seul** |
| Résultat inchangé ? | oui : 12 digests identiques, 342 opérations, PF `0,8712114237539049` |
| Preuve | `tests/indicators/test_perf_invariance.py`, 111 tests bit à bit, digests figés avant |
| Cause réelle du goulot | 98 % des 8 002 680 appels à `require_finite` venaient de `typical_price` dans la boucle de `vwap`, qui revalidait ce que `vwap` venait de valider en entier |
| Reste à faire | `_entry_features` : **52 % du temps mural**, correction écrite en section 10, non appliquée (hors périmètre) |

**Une optimisation qui change un seul chiffre n'est pas une optimisation.** Celle-ci n'en change
aucun, et c'est la seule raison pour laquelle elle est dans le dépôt.
