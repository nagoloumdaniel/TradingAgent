# Le filtre « cassures seulement » : réfuté par le walk-forward

**Date :** 2026-10-09 · **Famille :** `breakout_only` · **Jeux :** `docs/research/datasets-long`
(BTCUSD et XAUUSD, 60 000 bougies M15 chacun) · **Aucune promotion, aucun seuil touché.**

**En une phrase :** le filtre qui semblait transformer deux stratégies perdantes en stratégies
gagnantes **ne survit pas au walk-forward** — 14 candidats sur 16 rejetés pour surapprentissage,
1 seul pli rentable sur 6 en médiane — et c'est **exactement ce que la porte existait pour
attraper**.

---

## 1. Ce qui a été construit, et pourquoi

La mesure du 2026-10-08 (`2026-10-08-recommandations-consolidees.md`) avait montré que sur les
deux marchés et les deux stratégies de production, les entrées prises en range perdent tout
l'argent et celles prises sur une cassure gagnent : PF **1,652** et **1,656** sur l'or,
**1,719** et **1,658** sur le BTC, contre un seuil de 1,20.

Ce découpage avait été fait **après** avoir vu les résultats, sur les données qui les avaient
produits. Il était donc une **hypothèse**, et le rapport le disait déjà. Pour la traiter comme
telle, deux pièces ont été écrites :

| Pièce | Rôle |
|---|---|
| `indicators/regime.is_breakout` | le verdict de cassure, nommé pour son seul usage : autoriser une entrée |
| `research/discovery.BreakoutOnly` | une garde qui **délègue** à la règle parente et refuse le range |

`BreakoutOnly` ne réécrit jamais le signal du parent : il refuse de le laisser passer. Une garde
qui modifierait le signal serait une autre règle, et il faudrait la mesurer comme telle.

## 2. La mesure

`uv run python scripts/backtest/discover_m1_scalp.py --families breakout_only --datasets docs/research/datasets-long`

16 candidats (8 par marché), chacun passé aux échelons du protocole : apprentissage,
validation, walk-forward à 6 plis, robustesse des paramètres.

| Marché | Cause de rejet | Candidats |
|---|---|---|
| BTCUSD | `overfitting` | 6 |
| BTCUSD | `parameter_dispersion` | 2 |
| XAUUSD | `overfitting` | 8 |

**Détail des 16 candidats :**

| Marché | Label | Cause | Plis rentables /6 | Ops (train) | Net train | Net validation |
|---|---|---|---|---|---|---|
| BTCUSD | 00 | overfitting | 1 | 173 | −149,97 € | −179,96 € |
| BTCUSD | 01 | overfitting | 1 | 169 | −141,87 € | −184,13 € |
| BTCUSD | 02 | overfitting | **0** | 52 | −18,62 € | +29,47 € |
| BTCUSD | 03 | overfitting | **0** | 52 | −42,61 € | −29,67 € |
| BTCUSD | 04 | dispersion | **3** | 179 | **+307,29 €** | −23,25 € |
| BTCUSD | 05 | dispersion | **3** | 177 | −17,26 € | −131,91 € |
| BTCUSD | 06 | overfitting | 1 | 78 | −112,81 € | +65,11 € |
| BTCUSD | 07 | overfitting | 1 | 77 | −125,17 € | +44,17 € |
| XAUUSD | 00 | overfitting | 1 | 173 | −464,81 € | −182,63 € |
| XAUUSD | 01 | overfitting | 1 | 172 | −339,70 € | −143,01 € |
| XAUUSD | 02 | overfitting | 1 | 74 | −182,62 € | −83,72 € |
| XAUUSD | 03 | overfitting | 1 | 74 | −109,38 € | −88,82 € |
| XAUUSD | 04 | overfitting | 1 | 172 | −144,10 € | −211,47 € |
| XAUUSD | 05 | overfitting | 1 | 169 | −42,42 € | −151,40 € |
| XAUUSD | 06 | overfitting | **2** | 75 | −89,85 € | −89,68 € |
| XAUUSD | 07 | overfitting | **0** | 73 | −126,42 € | −119,60 € |

**Les chiffres qui décident :**

- **1 candidat sur 16** est rentable en apprentissage, **3 sur 16** en validation — mais aucun
  des deux groupes ne se recoupe de façon exploitable ;
- **plis rentables : médiane 1 sur 6**, exigence 3 sur 6 (50 %) ;
- **12 candidats sur 16** sont rejetés pour rétention hors échantillon **0,00**, et deux pour
  dispersion des paramètres (0,72 et 0,96 contre un plafond de 0,50) ;
- **le jeu scellé n'a jamais été ouvert** : 0 ouverture sur les deux marchés. Les 12 000
  bougies scellées de chaque marché restent intactes, donc réutilisables.

## 3. Ce que ça veut dire, et pourquoi c'est une bonne nouvelle

**L'hypothèse est morte, et elle est morte correctement.** Le PF de 1,65 à 1,72 mesuré sur le
jeu complet était un **artefact** : il venait de regarder les résultats et de choisir le
découpage après coup. Le walk-forward, qui réapprend sur chaque pli et juge sur le pli suivant,
ne retrouve rien.

**C'est la démonstration que la porte fonctionne.** Elle a attrapé exactement ce qu'un backtest
naïf aurait présenté comme une découverte. Sans elle, ce filtre aurait pu être promu, et le
dépôt aurait ajouté une règle qui perd en réel en croyant avoir trouvé un edge.

**Et c'est la deuxième fois que le laboratoire rend un zéro, avec deux causes différentes.**
La campagne du 2026-10-08 rejetait 34 candidats sur 34 ; celle-ci en rejette 16 sur 16. Le
laboratoire ne trouve pas « rien » par incapacité : il refuse, et il nomme pourquoi.

## 4. Ce que ça n'autorise pas à conclure

**Le concept de filtre de régime n'est pas réfuté.** Ce qui est réfuté, c'est **ce** filtre,
avec **ces** paramètres et **ce** protocole :

- le canal de cassure est adossé à `ema_slow` (30 ou 60), un choix arbitraire qui n'a pas été
  exploré. Un canal plus court ou plus long n'a pas été testé ;
- le walk-forward à 6 plis de 350/250 barres ne couvre que **3,3 %** de la fenêtre roulante
  (constat déjà fait le 2026-10-08, non résolu). Les 14 rejets « surapprentissage » reposent
  donc sur les semaines les plus anciennes du jeu, pas sur les deux ans et demi ;
- **la structure au moment de l'entrée reste un fait mesuré**, et son croisement avec le
  résultat reste valide comme **description** : les entrées en range perdent, celles en
  cassure gagnent, sur 977 et 1 812 opérations. Ce qui ne tient pas, c'est d'en faire une
  **règle** qui prédit le pli suivant.

Autrement dit : le fait est solide, la règle ne l'est pas. La distinction compte, et c'est
elle qu'il faut retenir.

## 5. Ce qu'il faudrait pour rouvrir la question

| Piste | Pourquoi |
|---|---|
| Refaire le walk-forward en ancrant les plis **sur la fin** de l'historique | les 6 plis actuels jugent 3 semaines de 2024 au lieu de deux ans et demi |
| Tester le canal de cassure comme **paramètre libre** | il vaut aujourd'hui `ema_slow`, ce qui lie deux choix qui n'ont pas de raison de l'être |
| Explorer en **H1**, où l'historique remonte à 2011 | 87 573 bougies contre 3 259 en H4, et un rapport coût/ATR plus favorable |
| Mesurer le filtre sur une **autre règle parente** | ici il n'a été testé qu'autour du croisement EMA |

**Rien n'a été promu.** `config/strategies/` n'a pas été touché, `strategies/registry.py` non
plus, et `BreakoutOnly` n'est pas au registre de production — un test le vérifie.
