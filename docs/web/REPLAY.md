# `/trades/{id}` — le replay d'un trade (§28)

> Le tableau de bord répond à deux questions d'audit :
> **pourquoi ce trade a-t-il été exécuté ?** et **quelle version de la stratégie l'a exécuté ?**
> Cette page les reconstruit à partir des lignes stockées, sans rien recalculer (§34).

## Entrée et identité

- URL : `/trades/{id}`, où `id` est **l'identifiant du signal** (`signals.id`), pas celui de
  la ligne `trades`. Un signal produit au plus un ordre, donc au plus une position et un
  trade dans ce modèle ; l'identifiant du signal est la clé qui rend la chaîne entière
  résoluble.
- Le lien vient de `/trades` : la colonne *Détail* de chaque ligne pointe sur
  `/trades/{signal_id}` (« rejouer »). L'ancienne forme `/trades?trade_id=N` reste servie —
  elle affiche un aperçu condensé sous le tableau — et renvoie vers le replay complet.
- Un `id` inconnu est un **404** avec un message explicite (« Aucun signal N en base »).
  Aucune figure du tableau de bord ne fuit dans cette page d'erreur.
- Un signal qui **n'a jamais donné de position** (refusé par le risque, expiré, rejeté)
  n'est pas une erreur : la page affiche le signal, sa version et le verdict, et déclare
  explicitement l'absence d'ordre, de position et de clôture.

## Ce que la page reconstruit, et d'où vient chaque donnée

| Section | Source | Remarque |
|---|---|---|
| Signal d'origine (tous les champs scalaires) | `signals` | Les `indicators` sont un JSON ; les clés sont triées à l'affichage pour que la page se lise toujours pareil. Ils sont présentés une seule fois, dans *Conditions de marché*, où l'ATR devient un régime |
| Version de la stratégie qui a exécuté (§27) | `strategy_versions` (snapshot immuable pris à la première vue du manifeste) + `strategy_registry` (état de déploiement actuel) | Le registre peut manquer : la page écrit « absente du registre » plutôt que d'inventer un statut |
| Décision de risque, contrôle par contrôle | `risk_decisions` | `checks` est rendu ligne par ligne : un booléen devient un badge *vérifié* / *refusé*, toute autre valeur est affichée telle qu'elle a été stockée (le tableau de bord ne connaît pas le seuil) |
| Ordre | `orders` | Prix **demandé**, volume, stop, objectif, `state`, `retcode` courtier, ticket, commentaire, clé d'idempotence |
| Exécutions | `executions` | Prix **obtenu**, volume, glissement et ticket de deal, dans l'ordre des horodatages |
| Position | `positions` | Entrée, volume, SL, TP, mode, état, ouverture |
| Clôture | `trades` | Prix de clôture, P&L en EUR, motif de sortie, mode |
| R réalisé | `trades.pnl_eur / trades.risk_eur` | **La définition de la couche de stockage elle-même** (`DailyPerformance.r_multiple`, `storage/daily.py`) : un rapport de deux colonnes stockées, jamais un chiffre recalculé |
| Durée signal → clôture | `trades.closed_at - signals.generated_at` | Différence de deux horodatages stockés, comme l'âge d'une position sur `/positions` |
| Événements d'exécution et latences | `execution_events`, rattachés par `signal_id` **ou** `order_id` | `detail.elapsed_ms` est la mesure enregistrée par le runtime (`runtime/pipeline.py`) ; une étape sans mesure affiche `n/a`, jamais `0 ms` |
| Analyse IA rattachée | `ai_analyses` via `signal_id` | Modèle, type, réponse, constats et coût. L'IA ne décide pas (C-002) |
| **Conditions de marché au moment du signal** | `candles` autour de `signals.generated_at`, `signals.indicators`, `ai.daily.regime_of`, `ai.daily.session_of`, `executions` | Voir la section suivante : c'est la réponse à « dans quel marché ce signal est-il né ? » |
| Chronologie | Tous les horodatages ci-dessus, fusionnés et triés | Voir ci-dessous |

La résolution suit les clés étrangères réelles : `signals` → `strategy_versions`,
`signals` → `risk_decisions`, `signals` → `orders` → `positions` → `trades`. Un chaînon
manquant produit une section vide **explicite**, jamais une exception.

## Les conditions de marché au moment du signal

`queries.market_context(engine, replay)` rassemble, pour le signal rejoué, ce que la base
sait du marché à cet instant. Chaque valeur absente reste `None` et s'affiche `n/a` : la page
ne comble aucun trou par un chiffre plausible.

| Ce qui est affiché | D'où cela vient | Règle appliquée |
|---|---|---|
| Bougies autour du signal | `candles` : au plus **30 avant** et **10 après** `signals.generated_at`, triées chronologiquement | La fenêtre est bornée en **temps** autant qu'en lignes, avec la durée de l'unité de temps (`Timeframe.seconds`) : une bougie isolée écrite des heures plus loin n'est pas dessinée comme si elle jouxtait les autres — l'axe place les bougies par index, une ligne périmée mentirait |
| Unité de temps tracée | L'unité du signal d'abord, puis, si elle ne contient pas assez de bougies, l'unité la plus fournie de ce marché | Un graphique ne mélange **jamais** deux unités dans une même vue. Celle qui a été tracée est écrite sous le graphique, et la page dit pourquoi quand ce n'est pas celle du signal |
| Indicateurs enregistrés | `signals.indicators`, tel quel | L'ordre des clés est figé au tri (`_signal_view`) pour que deux rendus se lisent pareil |
| ATR du signal | `signals.indicators` sous les clés `atr`, `ATR` ou `atr14` | Les mêmes orthographes que la passe quotidienne (`ai/daily.py`) |
| Médiane des ATR du marché | Les **30 derniers signaux du même marché**, bornés par `signals.generated_at <= ce signal` | La borne est l'instant du signal, jamais l'horloge : un signal plus récent ne peut pas réécrire le régime d'un vieux trade, et deux rendus du même replay lisent la même valeur. Médiane haute (`sorted(...)[n // 2]`), la définition de la passe quotidienne |
| Régime de volatilité | `ai.daily.regime_of(atr, median_atr)` | Fonction pure déjà testée, réutilisée telle quelle. Sans ATR **ou** sans médiane, la valeur reste `None` → `n/a` : ce n'est pas « volatilité normale », c'est une absence |
| Session UTC | `ai.daily.session_of(signals.generated_at)` | `asie`, `londres`, `new_york`, `apres_cloture` ; `format.session_label` ne fait que traduire le libellé |
| Prix observé → prix obtenu | `signals.observed_price` et le **premier** `executions.price` | Le prix obtenu est celui de la première exécution enregistrée ; sans exécution, `n/a` |
| Glissement | `executions.slippage` de cette même exécution | La valeur **stockée**. La page ne calcule pas `observé - obtenu` : ce serait une deuxième définition du glissement, et elle ne saurait pas laquelle est la bonne |
| Bougies réellement stockées | Le nombre de lignes lues | Affiché même quand il n'y a pas de graphique : « 0 » est une information |

### Le graphique

Dessiné en SVG dans le gabarit (`trade_replay.html`), sans bibliothèque et sans JavaScript :
des hairlines pour les mèches et les deux niveaux extrêmes réellement stockés, des corps
remplis aux fonds `--up` / `--down` du système de design, des étiquettes en Geist Mono. La
ligne pointillée verticale marque `signals.generated_at` — une position sur l'axe, jamais un
niveau de prix.

**Un graphique se mérite** : en dessous de `queries.REPLAY_MIN_CANDLES` (10) bougies, rien
n'est dessiné et la page écrit « Aucune bougie stockée pour cet instant ». C'est la même règle
que celle des filigranes (§51) : avec trois points, une « courbe » n'est qu'une diagonale qui
traverse la page.

La géométrie (`queries._chart`) est du calcul d'affichage pur : elle projette des nombres
déjà en base sur un `viewBox`. Aucun indicateur n'est calculé à partir des bougies, aucune
figure n'en dérive — le §34 tient.

## Ce qui reste indisponible

- **Le spread courtier en temps réel.** Le modèle ne stocke pas le spread au moment du
  signal : aucune colonne `spread` n'existe sur `signals`, et l'inventer à partir du
  `high - low` d'une bougie serait une autre grandeur, pas le spread. La page n'affiche donc
  pas de spread dans cette section.
- **Le carnet d'ordres.** Aucune table ne le porte, ni sa profondeur, ni les volumes. Rien
  n'est approximé.
- **Les bougies de l'unité du signal**, quand la base n'en a pas : la page trace celles du
  marché dans une autre unité et **le dit**, plutôt que de laisser croire à une vue complète.
- **Une bougie précisément à l'instant du signal.** `candles.open_time` est le début de la
  bougie ; un signal né au milieu d'une bougie n'a pas de ligne à lui. La marque verticale
  indique l'instant, pas une bougie « du signal ».

## La chronologie

`views.replay_timeline` fusionne les horodatages stockés et les trie par instant croissant.
Quand deux étapes partagent la même seconde — c'est courant, le verdict de risque est écrit
dans le même cycle que le signal — l'ordre est fixé par `views.TIMELINE_RANK` :

```
signal (0) < risque (1) < ordre (2) < exécution (3) < position (4) < événement (5)
          < clôture (6) < analyse IA (7)
```

Rien n'est déduit : un événement que la base ne contient pas n'apparaît pas. Le tableau de
bord ne chronomètre rien lui-même — il restitue les `elapsed_ms` que le runtime a mesurés.

## Sections vides, jamais d'erreur 500

Chaque section a un texte d'absence qui dit *ce qui* manque :

- « Aucun ordre enregistré : le signal s'est arrêté avant l'envoi au courtier. »
- « Aucune exécution enregistrée pour cet ordre. »
- « Aucune position enregistrée : le signal n'a pas été exécuté. »
- « Aucune clôture enregistrée : la position est ouverte ou n'a jamais existé. »
- « Aucun événement d'exécution enregistré pour ce signal ou cet ordre. »
- « Aucune analyse IA rattachée à ce signal. »
- « Aucun contrôle détaillé enregistré : le motif ci-dessus est la seule trace. »
- « Aucune bougie stockée pour cet instant » — moins de 10 bougies autour du signal :
  aucun graphique n'est dessiné, et la carte *Bougies stockées* affiche le compte réel.
- « Aucun indicateur enregistré pour ce signal » — donc aucun ATR, donc aucun régime : la
  carte affiche `n/a`, jamais « volatilité normale ».

## Lecture seule

La page n'exécute que des `SELECT`. Aucune nouvelle route n'accepte un verbe d'écriture, et
le test empirique de `tests/web/test_read_only.py` visite désormais `/trades/1` **et**
`/trades/9999` : le compte de lignes de chaque table est identique avant et après.

La lecture des conditions de marché ne change rien à cette promesse : `market_context` ne fait
que des `SELECT`, aucune table ni colonne n'a été ajoutée, et
`tests/web/test_replay_context.py` recompte les lignes de `candles` et `signals` avant et
après le rendu d'une page qui **dessine** un graphique.

## Tests

`tests/web/test_replay.py` (21 cas) couvre : la chaîne complète, l'ordre chronologique, les
latences mesurées, l'analyse IA, le trade sans exécution ni analyse, la position jamais
clôturée, le signal refusé par le risque, le 404, et le fait qu'un `elapsed_ms` absent n'est
jamais inventé. Les fixtures vivent dans `tests/web/conftest.py` (`replay_telemetry`) et
`tests/web/seed.py` (`add_chain`, qui ajoute une chaîne — les tables sont *append-only*, un
test ajoute au lieu de supprimer).

`tests/web/test_replay_context.py` (23 cas) couvre les conditions de marché : fenêtre 30/10
et ordre chronologique, indicateurs lus tels quels, régime comparé à la médiane d'un marché
(haute / basse / normale, valeurs calculées à la main), session aux quatre bornes UTC, prix
observé/obtenu et glissement, **base sans bougies** (aucun 500, aucun graphique), seuil de
10 bougies, unité de repli quand celle du signal est vide, géométrie du SVG calculée à la main
depuis les constantes du module, marqueur absent quand le signal est hors fenêtre, et le
compte de lignes inchangé après le rendu. `tests/web/seed.py` gagne `add_candles`, qui ajoute
des bougies autour d'un instant — *append-only*, comme le reste.
