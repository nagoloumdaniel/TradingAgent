# Dossier de décisions — intégration du 2026-10-07

> TASK-103 : toute décision d'architecture ou de risque est consignée, datée et justifiée.
> Les décisions déjà closes (C-001 à C-010) ne sont pas rouvertes ici.

## D-01 — Le runtime est câblé par une racine de composition unique

**Décision.** La boucle d'agent vit dans le paquet `tradingagent.runtime` et la racine de
composition dans `tradingagent.app`. `app` est le seul module, avec `risk`, autorisé à
importer `execution` : c'est l'exception déjà prévue par `tests/test_architecture.py`, qui
n'a pas été assouplie.

**Pourquoi.** Le runtime reçoit un exécuteur par protocol structurel (`runtime/ports.py`)
et ne connaît donc que `risk.model`. La frontière « seul `risk` atteint `execution` » reste
vérifiée par test, sans dérogation nouvelle.

**Conséquence.** `uv run tradingagent-run` démarre l'agent ; `--once` exécute un cycle et
sort, ce qui donne un moyen de vérification reproductible côté exploitation.

## D-02 — Un seul écrivain par table

**Décision.** L'exécuteur (`execution/journal.py`, `OrderJournal`) est le seul écrivain des
tables `orders`, `executions`, `positions` et `trades`. Le runtime est le seul écrivain de
`signals`, `signal_events`, `risk_decisions`, `system_events`, `account_snapshots` et
`reports`.

**Pourquoi.** Deux écrivains concurrents sur les mêmes lignes auraient produit des
doublons subtils (clé d'idempotence, ticket de position) et rendu la réconciliation
ambiguë. La table `orders` porte la clé d'idempotence unique : c'est l'exécuteur qui la
remplit avant d'appeler le courtier, ce qui rend une réponse perdue réconciliable (R-05).

**Conséquence.** Pour que le cycle de vie du signal suive une clôture sans que le runtime
lise les tables de l'exécuteur, `ClosedPosition` porte `signal_id`. La réconciliation est
exposée par `Broker.reconcile() -> tuple[str, ...]` : le runtime ne reçoit qu'une
description de divergence, jamais de quoi la corriger.

## D-03 — L'IA bloquée produit une expiration, pas un refus risque

**Décision.** Quand le filtre IA bloque un candidat (`advisory` ou `required`), le signal
passe de `CANDIDATE` à `EXPIRED`, avec le motif `ai_filter <état>: <raison>`, et un
événement `ai_filter_blocked` est écrit.

**Pourquoi.** RM-018 n'offre aucune transition `CANDIDATE → IGNORED`, et `RISK_REJECTED`
est réservé aux refus du moteur de risque : l'y mélanger fausserait le comptage des refus
par contrôle qui alimente le rapport quotidien. `EXPIRED` est la seule transition légale
qui exprime « l'offre n'est pas émise ».

**Alternative écartée.** Ajouter un état `AI_REJECTED` aurait modifié la machine à états de
TASK-040 pour un cas que `EXPIRED` couvre sans ambiguïté.

## D-04 — La clé d'API du modèle devient optionnelle

**Décision.** `ANTHROPIC_API_KEY` n'est plus obligatoire. Vide ou absente, l'agent démarre,
le filtre IA n'est pas câblé, et les règles déterministes décident seules.

**Pourquoi.** RM-011 dit explicitement que l'indisponibilité de l'IA ne doit pas bloquer
l'agent. Exiger la clé au démarrage contredisait cette règle et empêchait tout démarrage
en son absence. Le mode `shadow` par défaut (C-002) rend l'IA non décisionnaire, donc son
absence ne change aucune décision de capital.

**Conséquence.** `config/settings.py` et `tests/config/test_settings.py` sont mis à jour ;
`.env.example` porte la mention « optionnel ».

## D-05 — Le filtre IA est lu dans le manifeste de la stratégie

**Décision.** `AiFilterLayer.review` accepte un paramètre `ai_filter` qui prime sur la
configuration de la couche. Le pipeline transmet le `ai_filter` du manifeste figé de la
stratégie (`strategy_versions.manifest`).

**Pourquoi.** TASK-037 exige que le comportement en panne corresponde au `ai_filter`
déclaré. Sans cela, toutes les stratégies partageaient le réglage de la couche, et une
promotion de filtre par stratégie était impossible — c'est pourtant la décision de
TASK-044 (garder `shadow` ou promouvoir en `advisory`).

## D-06 — Publication de `witness@1.1.0` et introduction de `trend_breakout@1.0.0`

**Décision.** `config/strategies/witness@1.1.0.yaml` autorise `XAUUSD` et remplace
`frxXAUUSD`, nom inexistant sur MT5 (voir ROADMAP, Project Context). Une seconde stratégie,
`trend_breakout@1.0.0` (cassure de canal de Donchian filtrée par une EMA longue), est
affectée à `BTCUSD`, ce qui satisfait EF-003 « une stratégie distincte par marché » avec
deux règles réellement différentes et non deux jeux de paramètres.

**Pourquoi.** La roadmap signalait explicitement le suivi à faire. Les deux manifestes
restent plafonnés à `max_mode: SIGNAL` : aucune exécution réelle n'est possible avant
TASK-064 et TASK-065 (RM-016). `witness@1.0.0` est conservé : un manifeste déjà utilisé ne
se réécrit jamais.

**Mise à jour du 2026-10-08.** Les versions publiées ensuite (`witness@1.1.1`,
`trend_breakout@1.0.1`) relèvent le plafond à `DEMO`, à la demande de l'opérateur, pour
répéter le système sur un compte de démonstration : la campagne a refusé 6 portes sur 9, et
les chiffres sont écrits dans l'en-tête de chaque manifeste. L'invariant « jamais au-dessus de
`SIGNAL` » est remplacé par « jamais au-dessus sans dérogation écrite et datée » — voir
`docs/decisions/2026-10-08-mode-ceiling-derogation.md`. `LIVE` reste hors d'atteinte sans la
validation complète des neuf portes du §49.

## D-07 — Capital de départ du paper trading

**Décision.** Le compte simulé du `PaperBroker` démarre à 1 000 € (constante
`PAPER_STARTING_CAPITAL`). Le mode `DEMO` lit le compte réel de démonstration.

**Pourquoi.** Le mode `PAPER` est une simulation hors exposition : son capital de départ
n'a pas à être le capital réel de 100 € (C-009), qui n'est pas éligible au réel de toute
façon (RM-019) et n'est utilisé que pour dimensionner le mode `LIVE`.

## D-08 — L'agent refuse de démarrer si un symbole configuré n'existe pas

**Décision.** Au démarrage, `app` vérifie chaque symbole de `agent.yaml` contre le terminal
(`symbol_spec`). Un symbole inconnu lève `ConfigError` et l'agent ne démarre pas.

**Pourquoi.** La validation de TASK-006 exige qu'un symbole inconnu empêche le démarrage ;
or la liste des symboles de la liste blanche ne peut venir que du courtier. Le contrôle est
donc fait au premier moment où le fait est disponible, avant tout abonnement.
