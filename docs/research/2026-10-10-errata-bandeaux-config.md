# Errata — les bandeaux de `config/` cités par le rapport d'état de l'art

**Date :** 2026-10-10 · **Portée :** `docs/research/2026-10-10-strategies-or-bitcoin-etat-de-l-art.md`,
section 1.3 et constat n°1 · **Aucun code touché, aucune promotion, aucun ordre.**

## Pourquoi cette note existe

Le rapport d'état de l'art cite les bandeaux de dérogation de `config/strategies/` **dans leur
état du 2026-10-10**, avant correction. Les valeurs qu'il rapporte — 0,92 pour le BTC et 1,04
pour l'or — sont **celles qui ont été relevées et réfutées**, pas celles qui sont en vigueur.

Le rapport n'a pas été réécrit, et c'est délibéré : il enregistre un constat daté. Réécrire un
document de recherche pour qu'il colle au présent détruirait la qualité qui le rend utile. Cette
note rétablit la chronologie sans effacer la trace.

## Ce qui a changé, et quand

| Moment | Bandeau `config/` | Réalité mesurée |
|---|---|---|
| Avant le 2026-10-10 | PF net 0,92 (BTC), 1,04 (or) | — |
| **2026-10-10, relevé** | *(inchangé)* | **0,8391** (BTC, validation, 47 opérations) · **0,8841** (or, validation, 38 opérations) |
| 2026-10-10, après correction | **0,8391** et **0,8841**, avec les deux causes de l'écart | — |

Les valeurs citées par le rapport sont celles de la **deuxième** ligne. Celles en vigueur sont
celles de la **quatrième**.

## Les deux causes de l'écart, toutes deux vérifiées

1. **Le modèle de spread.** Les valeurs publiées le 2026-10-08 venaient d'une campagne
   antérieure au correctif du commit `ec29b38` (« le modèle de spread était faux de quatre et
   demi, et il était copié douze fois »). Le spread de l'or est passé de 0,233 à 1,044 $ par
   leçon, ce qui suffit à faire basculer le résultat.
2. **Une porte invoquée qui n'était pas la porte mesurée.** Les chiffres publiés ne
   correspondaient pas à la porte `costs` des rapports qu'ils citaient.

L'écart était **optimiste de 10 % sur le BTC**, précisément sur la grandeur que le bandeau
invoquait pour justifier la dérogation.

## Ce que la correction ne change pas

La dérogation `max_mode: DEMO` de `witness@1.1.1` et `trend_breakout@1.0.1` **reste en place**.
Elle n'a jamais reposé sur un franchissement de seuil : elle repose sur le fait que le compte de
démonstration n'engage aucun argent réel. Aucune version de manifeste n'a été créée ni
supprimée — seuls les commentaires de dérogation ont été corrigés, et un manifeste publié ne se
réécrit jamais dans ses **paramètres**, ce qui n'était pas le cas ici.

## Où lire les chiffres en vigueur

`docs/research/stats/XAUUSD-witness@1.1.1.json` et
`docs/research/stats/BTCUSD-trend_breakout@1.0.1.json`, produits par
[scripts/backtest/current_stats.py](../../scripts/backtest/current_stats.py).
