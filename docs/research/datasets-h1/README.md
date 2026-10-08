# Jeux gelés H1 et H4 (TASK-5)

Huit jeux de bougies **gelés**, obtenus en pliant des séries M15 déjà gelées. Rien n'est écrit
en base, aucun fichier source n'est modifié, et aucune bougie incomplète n'est écrite : un
bucket dont il manque une seule jambe M15 est **absent** du fichier, et son absence reste
visible sous forme de discontinuité de temps.

## Contenu

```
datasets-h1/
  build_h1_h4.py                      l'outil de pliage (self-test / build / verify)
  long-60000/                         source : docs/research/datasets-long (60 000 M15)
    H1/{XAUUSD,BTCUSD}-H1-from-M15-2026-10-08.jsonl
    H4/{XAUUSD,BTCUSD}-H4-from-M15-2026-10-08.jsonl
    CENSUS.md                         les deux tableaux ci-dessous, tels que mesurés
  short-11999/                        source : docs/research/datasets (11 999 M15)
    H1/… H4/… CENSUS.md
```

Chaque `*.jsonl` est accompagné de deux sidecars :

* `*.manifest.json` — le manifeste du dépôt (`DatasetManifest.to_dict`), recensement des trous
  contre le calendrier appris sur 4 semaines, écrit comme le fait `scripts/backtest/fetch_mt5_dataset.py` ;
* `*.aggregation.json` — le recensement du pliage : source et son empreinte, règle appliquée,
  **liste de chaque bucket incomplet avec les heures de jambe manquantes**, runs de buckets vides,
  et le détail des jambes non pliées (bords d'export / bords de fermeture / non expliquées / marché fermé).

Un seul jeu par symbole et par dossier : `DatasetStore.load_all()` indexe **par symbole** et
refuse deux jeux pour le même marché. D'où un sous-dossier par unité de temps — et non un
mélange H1/H4 dans un même dossier.

## Provenance

| Jeu | Bougies | Fenêtre UTC | Empreinte SHA-256 |
|---|---|---|---|
| long XAUUSD H1 | 14 894 | 2024-03-25 03:00 → 2026-10-08 16:00 | `7116718d458f70ddb3797e422ea67698971bf178fc2deca363f08fbf288cc84b` |
| long BTCUSD H1 | 14 992 | 2025-01-21 07:00 → 2026-10-08 16:00 | `a15b41b31cded31e11dc7b7ab37c9b312efc577ace99cf660fe2e3b5a3626ee3` |
| long XAUUSD H4 | 3 259 | 2024-03-25 04:00 → 2026-10-08 16:00 | `17b1126f3d2a1af63d06aa816cc35a192f8dcf198c5d35c9295f2acd7ebce073` |
| long BTCUSD H4 | 3 739 | 2025-01-21 08:00 → 2026-10-08 16:00 | `f78e350cdbd6da8894b6e9900978bc849f2c6522929e9a93fd5167fdb3a5100c` |
| short XAUUSD H1 | 2 980 | 2026-04-07 18:00 → 2026-10-08 02:00 | `94c8cc28c24fd8d5b70cd053c029318c37d1b290d3d7a2acf5c02fc921e235ff` |
| short BTCUSD H1 | 2 998 | 2026-06-05 00:00 → 2026-10-08 02:00 | `4f95e09e92ed42d64d7797139a083cb5d52757c4c92b73a02f121836f5412571` |
| short XAUUSD H4 | 651 | 2026-04-08 00:00 → 2026-10-07 20:00 | `4631ba0ecb9f9ed9ba379369b81c7d74d284967bbb237a5046b444935e4c19c0` |
| short BTCUSD H4 | 747 | 2026-06-05 00:00 → 2026-10-08 00:00 | `4bedec02b7e6dc45b7c8cbd1407a51b05eec155a7385d706c96db98a52439431` |

Sources, empreintes telles que recalculées par `load_dataset` :

| Source M15 | Bougies | Empreinte |
|---|---|---|
| `datasets-long/XAUUSD-M15-mt5-XAUUSD-M15-2026-10-08.jsonl` | 60 000 | `06e1e39aaffb5b47cf96bc86f1fff78cdca54cc40d1db6b6bb2c7d7972dd278a` |
| `datasets-long/BTCUSD-M15-mt5-BTCUSD-M15-2026-10-08.jsonl` | 60 000 | `95e4e18b4bed1ef618f374a2c2c0c2f8c7d2503f651089e672f5072a2a40681d` |
| `datasets/XAUUSD-M15-mt5-XAUUSD-2026-10-08.jsonl` | 11 999 | `3906afa2c63dab5317e63d32b15bbfc7199c3cf218bf45a7cf642e5bd79961f0` |
| `datasets/BTCUSD-M15-mt5-BTCUSD-2026-10-08.jsonl` | 11 999 | `96f80664d572e28c8e5e560b70f1fdbea98a8512d8bec26ecac8ae4b861920c2` |

L'identité figée (`dataset_id`) est `agg-m15-<symbole>-<unité>-<date de la source>`, et le champ
`source` de chaque jeu porte le nom du fichier M15 d'origine **et les 12 premiers caractères de
son empreinte** : la chaîne de provenance est vérifiable de bout en bout.

## Reproduire et vérifier

```bash
uv run python docs/research/datasets-h1/build_h1_h4.py self-test
uv run python docs/research/datasets-h1/build_h1_h4.py build \
    --source docs/research/datasets-long --output docs/research/datasets-h1/long-60000
uv run python docs/research/datasets-h1/build_h1_h4.py verify \
    --source docs/research/datasets-long --output docs/research/datasets-h1/long-60000
```

`build` refuse d'écraser un fichier existant (`write_dataset` : un jeu gelé est immuable) ;
`--rebuild` supprime **uniquement** les fichiers de sortie de cet outil, après avoir vérifié
qu'ils sont bien dans le dossier de sortie.

Ce que `verify` exige, sur les octets réellement sur disque :

1. rechargement par `load_dataset` → l'empreinte SHA-256 des lignes de bougies est recalculée et
   comparée à celle de l'en-tête ; le chargement lève si elle diffère ;
2. **chaque barre gelée est re-dérivée de ses jambes M15** relues depuis la source : les 4 (H1) ou
   16 (H4) jambes existent aux emplacements exacts, et `open`/`close`/`high`/`low` sont ceux des
   jambes — aucune bougie incomplète n'a été écrite ;
3. le recensement des buckets couvre exactement la grille cible, et le nombre de discontinuités de
   temps dans la série gelée est **égal** au nombre de runs de buckets écartés strictement
   intérieurs : aucun trou masqué, aucune bougie comblée depuis une voisine ;
4. le sidecar `.aggregation.json` est égal au recensement recalculé ;
5. les jambes se conservent : `jambes pliées + jambes dans les buckets incomplets == 60 000`
   (resp. 11 999) ; et `pliées + incomplètes + non pliées == buckets × jambes par barre`.

`self-test` prouve la règle sur des séries fabriquées, avant de la faire tourner sur 60 000
barres : un bucket à 3 jambes sur 4 est absent, un bucket sans aucune jambe est absent et
compté, un bucket H4 à 4 jambes M15 contiguës est bien écarté (le compte est comparé au ratio de
l'unité cible, pas à un nombre quelconque), une jambe hors grille est refusée.

## Ce que le pliage coûte (source longue, 60 000 M15)

| Marché | UT | Bougies écrites | Buckets écartés (incomplets / vides) | Discontinuités | Jambes M15 non pliées | dont bords d'export | dont bords de fermeture | dont non expliquées | dont marché fermé |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSD | H1 | 14 992 | 14 / 4 | 10 | 40 | 2 | 0 | 38 | 0 |
| XAUUSD | H1 | 14 894 | 148 / 7 221 | 658 | 29 052 | 5 | 840 | 7 | 28 200 |
| BTCUSD | H4 | 3 739 | 15 / 0 | 10 | 64 | 26 | 0 | 38 | 0 |
| XAUUSD | H4 | 3 259 | 809 / 1 499 | 658 | 29 072 | 25 | 840 | 7 | 28 200 |

Lecture :

* « marché fermé » = le créneau tombe dans une fermeture que le marché a faite lui-même
  (week-ends, coupure quotidienne de l'or). Aucune barre n'y était due : ce ne sont pas des
  données perdues, ce sont les 27 464 créneaux M15 absents de la source elle-même.
* « bords d'export » = le créneau est dans le premier ou le dernier bucket de la grille : l'export
  a commencé ou fini au milieu de cette barre.
* « bords de fermeture » = le créneau appartient à un run de créneaux absents qui contient un
  créneau fermé : c'est la fermeture que la règle **élargit** d'au plus une barre cible, parce
  qu'une coupure commence et finit rarement sur une frontière d'heure.
* « non expliquées » = un run de créneaux absents que le calendrier dit ouverts et qui ne contient
  aucun créneau fermé. Ce sont les seuls candidats au statut de trou de données, et ils sont
  listés nommément :

  * **XAUUSD : 7 jambes, 2 jours** — 2024-05-02 09:30 → 10:30 (5 jambes) et 2025-11-28 08:15,
    08:30 (2 jambes). Rien d'autre sur 2,5 ans et 60 000 barres.
  * **BTCUSD : 38 jambes, 10 jours** — 2025-03-29 (1), 2025-04-06 (1), 2025-04-20 (1),
    2025-05-03 (1), 2025-05-04 (3), 2025-06-07 (2), 2025-12-06 (8), 2026-02-28 (12),
    2026-08-07 (1), 2026-09-05 (8). Le marché BTCUSD est 24/7 d'après le calendrier : ces
    38 jambes (9 h 30 de cotation) sont de vrais trous dans l'historique du broker, et ils se
    retrouvent identiques en H1 et en H4.

Aucun de ces candidats n'est comblé : ils sont absents des fichiers, comptés, et datés ici.

## Folds de walk-forward réellement jouables

Plan lu dans le dépôt (`research.campaign.walk_forward_plan_for`, qui ajoute au bloc de mesure
l'historique déclaré par le manifeste) et folds comptés par `research.protocol.walk_forward`.

| Marché | UT | Bougies | Manifeste du marché | Historique déclaré | Bougies par fold | Folds jouables |
|---|---|---|---|---|---|---|
| BTCUSD | H1 | 14 992 | `trend_breakout@1.0.1` | 600 | 1 450 | **68** |
| XAUUSD | H1 | 14 894 | `witness@1.1.1` | 300 | 900 | **70** |
| BTCUSD | H4 | 3 739 | `trend_breakout@1.0.1` | 600 | 1 450 | **12** |
| XAUUSD | H4 | 3 259 | `witness@1.1.1` | 300 | 900 | **12** |
| BTCUSD | H1 (court) | 2 998 | `trend_breakout@1.0.1` | 600 | 1 450 | 8 |
| XAUUSD | H1 (court) | 2 980 | `witness@1.1.1` | 300 | 900 | 11 |
| BTCUSD | H4 (court) | 747 | `trend_breakout@1.0.1` | 600 | 1 450 | **0** |
| XAUUSD | H4 (court) | 651 | `witness@1.1.1` | 300 | 900 | **0** |

Deux corrections à la lecture initiale :

* le H1 **n'était pas** condamné sur les jeux courts : 2 980 et 2 998 bougies donnent 11 et
  8 folds. C'est le **H4** court qui est hors jeu (651 et 747 bougies pour un fold de 900) ;
* le H4 **n'est pas** hors jeu sur les jeux longs : 12 folds des deux côtés. Il reste beaucoup
  plus pauvre que le H1 (12 contre 70), et sur l'or il paie la coupure quotidienne : un bucket
  H4 sur six contient la coupure et est donc écarté presque chaque jour.

Réserve à connaître avant de lancer une campagne sur les jeux courts : `split_dataset` y donne
un bloc de validation de 599 (BTC H1) et 596 (XAU H1) bougies ; pour `trend_breakout@1.0.1`, qui
déclare 600 barres d'historique, ce bloc est **trop court** (`backtest/harness.py` lève
« cannot feed 600 history bars ») alors que les folds, eux, se jouent. Sur les jeux longs, la
validation fait 2 998 et 2 978 bougies : le problème disparaît.

## Limites, dites franchement

* **Il n'existe aucun module d'agrégation dans le dépôt.** `src/tradingagent/data/aggregation.py`
  n'existe pas, et `src/tradingagent/` ne contient aucun code de pliage ou de rééchantillonnage :
  les jeux M1 et M5 de `docs/research/datasets/M1|M5/` ont été **téléchargés** depuis MT5
  (`scripts/backtest/fetch_mt5_dataset.py --timeframe`), pas dérivés du M15. La règle d'or
  n'était donc implémentée nulle part, et il n'y avait rien à réutiliser. Le pliage vit ici,
  dans le dossier de recherche, et **n'est couvert par aucune suite de tests du dépôt** : sa
  preuve est le `self-test` et le `verify` ci-dessus, exécutables.
* Les sidecars `.aggregation.json` nomment les heures manquantes ; ils ne sont pas lus par le
  chargeur, qui ne lit que `*.jsonl`. Un jeu gelé reste une suite de bougies ordonnées : c'est
  la discontinuité de temps qui porte le trou, et le sidecar qui l'explique.
* Le classement « non expliquée » repose sur deux calendriers appris par le dépôt
  (`learn_calendar`, 4 semaines à 0,75 et toute la série à 0,90). Un férié mobile reste donc un
  candidat : c'est voulu, il vaut mieux 7 candidats examinés que 7 fermetures supposées.
* Aucune donnée de volume n'existe dans le format du dépôt (`t, o, h, l, c`) : une bougie
  incomplète ne mentirait pas sur le volume, mais sur le `high`, le `low` et le `close`.
