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
| Signal d'origine (tous les champs, y compris les indicateurs) | `signals` | `indicators` est un JSON ; les clés sont triées à l'affichage pour que la page se lise toujours pareil |
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
| Chronologie | Tous les horodatages ci-dessus, fusionnés et triés | Voir ci-dessous |

La résolution suit les clés étrangères réelles : `signals` → `strategy_versions`,
`signals` → `risk_decisions`, `signals` → `orders` → `positions` → `trades`. Un chaînon
manquant produit une section vide **explicite**, jamais une exception.

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

## Lecture seule

La page n'exécute que des `SELECT`. Aucune nouvelle route n'accepte un verbe d'écriture, et
le test empirique de `tests/web/test_read_only.py` visite désormais `/trades/1` **et**
`/trades/9999` : le compte de lignes de chaque table est identique avant et après.

## Tests

`tests/web/test_replay.py` (21 cas) couvre : la chaîne complète, l'ordre chronologique, leslatences mesurées, l'analyse IA, le trade sans exécution ni analyse, la position jamais
clôturée, le signal refusé par le risque, le 404, et le fait qu'un `elapsed_ms` absent n'est
jamais inventé. Les fixtures vivent dans `tests/web/conftest.py` (`replay_telemetry`) et
`tests/web/seed.py` (`add_chain`, qui ajoute une chaîne — les tables sont *append-only*, un
test ajoute au lieu de supprimer).
