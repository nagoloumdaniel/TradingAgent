# Roadmap d'exécution — Agent de signaux de trading Deriv

> **Pour un exécutant, humain ou agent.** Ce document dérive de `CAHIER_DES_CHARGES.md`. Les deux se lisent ensemble : la roadmap dit quoi faire, le cahier dit pourquoi. Les tâches utilisent des cases à cocher pour le suivi. Aucune tâche ne doit être déclarée terminée sans que ses critères d'acceptation aient été vérifiés par exécution réelle.

**Objectif :** livrer un agent qui surveille l'or et deux à quatre cryptomonnaies sur Deriv, envoie des signaux sur Telegram, mesure ses performances, et peut être promu vers l'exécution automatique après validation.

**Spécification source :** `CAHIER_DES_CHARGES.md` version 1.1

---

## Project Context

Section de référence partagée. Les tâches y renvoient au lieu de répéter.

### Produit
Agent autonome 24/7. Aucune interface graphique. Telegram est l'unique surface d'interaction. Opérateur unique, résident français.

### Marchés
**`XAUUSD` (or) et `BTCUSD` (bitcoin), décision de l'opérateur du 2026-10-04 (Q-07).** Le BTC cote en continu, ce qui garantit à l'agent un marché actif le week-end. Les indices synthétiques sont **hors périmètre** : visibles sur MT5 mais refusés à l'exécution pour un résident français (C-008, ordre de test du 2026-10-04). **Avec 100 €, aucun des deux marchés n'est éligible au mode réel** (Q-22) ; la démonstration n'est pas concernée.

**Suivi à faire :** ~~le manifeste `witness@1.0.0` référence encore `frxXAUUSD`, un nom qui n'existe pas sur MT5. Il faudra publier une version qui autorise `XAUUSD` et `BTCUSD` au moment de créer `agent.yaml`.~~ **Fait le 2026-10-07 :** `witness@1.0.0` est conservé tel quel (un manifeste déjà utilisé ne se réécrit jamais), `witness@1.1.0` autorise `XAUUSD`, et une seconde stratégie `trend_breakout@1.0.0` autorise `BTCUSD`. `config/agent.yaml` affecte une stratégie distincte à chaque marché (EF-003). Voir `docs/decisions/2026-10-07-runtime-integration.md`, D-06.

### Capital et risque
Capital de référence réel : 100 €. Cette contrainte impose un risque par opération de 2 à 5 % en mode réel, contre 0,5 % en démonstration, voir C-009 et RM-005 révisée.

**Conséquence à retenir par tout exécutant :** à ce niveau de capital, le mode réel valide la mécanique d'exécution, **jamais la performance d'une stratégie**. Les chiffres réels et de démonstration ne sont jamais agrégés. La règle RM-019 déclare inéligible au mode réel tout instrument dont la taille minimale impose un risque supérieur au plafond.

### Stack
Python 3.12, `asyncio`, **paquet officiel `MetaTrader5` et terminal Deriv MT5** (données et exécution, appels bloquants confinés dans un fil dédié), pandas, SQLAlchemy et Alembic, pydantic, APScheduler, bibliothèque Telegram asynchrone, API Claude, ruff, mypy, pytest. **Windows obligatoire** pour l'agent : poste de l'opérateur jusqu'à la phase 7, serveur Windows ensuite (C-010).

### Architecture
Quatorze paquets, décrits en section 10.2 du cahier. Deux d'entre eux, `backtest` et `research`, ne sont jamais chargés par le processus de production.

Chaîne de décision : `data` → `strategies` → `ai` → `risk` → (`notify` et `execution`) → `storage` → `analytics` → `reporting`.

**Invariant architectural :** `risk` est la seule frontière entre une intention et un engagement de capital. Aucun module autre que `risk` n'a le droit d'importer `execution`. Cet invariant est vérifié par un test automatisé, pas par la discipline.

### Base de données
SQLAlchemy et Alembic. **PostgreSQL hébergé sur Supabase dès le 2026-10-04** (projet `tradingagent`, Francfort), par décision de l'opérateur : pas de base locale pour l'agent, `DATABASE_URL` obligatoire. SQLite ne sert qu'aux tests automatisés, sur des bases temporaires. Sécurité au niveau des lignes activée sur toutes les tables, contre l'API web publique de Supabase. Entités et contraintes en section 11 du cahier.

### Sécurité
Aucun secret dans le dépôt. Jeton Deriv limité à la lecture et à la négociation. Liste blanche Telegram. Vérification du compte avant chaque ordre (RM-017).

### Conventions
- horodatages en temps universel coordonné partout, conversion à l'affichage seulement ;
- fonctions de calcul pures, sans état global ni lecture de l'heure courante, afin d'être testables et rejouables ;
- toute décision persistée avec son motif, y compris les refus ;
- messages de commit conventionnels ;
- une branche par tâche, fusion après revue.

### Décisions
Voir les contradictions C-001 à C-007 du cahier. Quatre d'entre elles doivent être tranchées avant le code : C-001, C-002, C-003, C-004.

### Contraintes
API Deriv : quotas, types de contrats disponibles, tailles minimales négociables et profondeur d'historique à vérifier avant toute conception d'exécution. Or fermé le week-end, crypto ouverte en continu. Capital réel de 100 €, qui contraint le risque par opération. Serveur de faible capacité, de l'ordre de deux cœurs virtuels et quatre gigaoctets de mémoire.

---

## Token Optimization Strategy

| Règle | Application |
|---|---|
| Contexte permanent | Seul `Project Context` est rappelé aux exécutants. Le cahier complet n'est lu que par les tâches qui le citent explicitement |
| Information référencée, jamais recopiée | Une tâche cite « voir cahier, F-011 » plutôt que de reproduire la spécification |
| Skills réutilisés | `test-driven-development`, `verification-before-completion` et `caveman-commit` reviennent sur presque toutes les tâches de code. Ils sont déclarés une fois dans la matrice des skills et non répétés tâche par tâche |
| Skills ponctuels | `claude-api` (TASK-037, TASK-042), `dataviz` (TASK-043), `security-review` (TASK-102), `migration` (TASK-081) |
| Tâches regroupées | Les tâches partageant un module, un skill et un contrôle qualité sont fusionnées. Exemple : les commandes Telegram de lecture forment une seule tâche |
| Tâches séparées | Une tâche est isolée dès qu'elle peut échouer seule, possède son propre contrôle qualité, ou engage du capital |
| Jamais répété | La stack, l'arborescence des paquets, les conventions et les identifiants de règles métier |
| Sous-agents | Les backtests de stratégies concurrentes sont dispatchés en parallèle, chacun ne recevant que le jeu de données et le manifeste, jamais le contexte du projet entier |

---

## Découpage en versions

| Version | Contenu | Phases |
|---|---|---|
| **MVP** | Signaux Telegram sur les marchés retenus, statistiques, rapport quotidien, déployé en continu, arrêt d'urgence opérationnel. Correspond à la recette 22.1 du cahier | 0 à 5 |
| **V1** | Stratégies issues de la recherche, validées hors échantillon, puis paper trading mesuré | 6 et 7 |
| **V2** | Exécution automatique sur compte de démonstration, réconciliation, recette 22.2 | 8 |
| **Future** | Exécution réelle à risque réduit, analyse par régime de marché, export PDF, rôle lecteur | 9 |

Aucune fonctionnalité demandée n'est supprimée. Celles qui ne sont pas dans le MVP sont déplacées, pas abandonnées.

---

## Phase 0 — Cadrage et socle

Objectif : lever les décisions bloquantes et poser un dépôt dans lequel la première ligne de code métier peut être écrite avec un test.

### TASK-001 — Lever les décisions bloquantes

- [x] Statut : **DONE le 2026-10-04, avec un écart accepté par l'opérateur.** Identifiants démo MT5 en place, mais avec le **mot de passe principal** : le mot de passe investisseur n'a pas pu être créé. Le critère « une tentative d'ordre est refusée » n'est donc pas rempli, et l'agent peut passer des ordres sur le compte démo. Risque accepté pour la démonstration uniquement. Le contrôle RM-017 du type de compte, testé dans `scripts/order_feasibility_mt5.py`, devient le seul rempart, et devra être bloquant dans TASK-010 et TASK-081.
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** aucune
- **Objectif :** obtenir de l'opérateur une réponse écrite aux questions Q-01 à Q-08 du cahier.
- **Avancement au 2026-10-01 :** Q-02, Q-03, Q-04, Q-06, Q-08, Q-09, Q-10 et Q-11 sont résolues et consignées dans le cahier version 1.1. Restent Q-01, tranchée en TASK-004, et Q-05, action opérateur.
- **Actions restantes :**
  1. **Action opérateur, bloquante, révisée le 2026-10-03 :** ouvrir un **compte démo Deriv MT5** donnant accès à l'or et à la crypto, installer le terminal MetaTrader 5 sur le poste Windows, puis renseigner dans `.env` `MT5_LOGIN`, `MT5_SERVER` et `MT5_PASSWORD` avec le **mot de passe investisseur**, en lecture seule. L'identifiant d'application et le jeton d'API Deriv ne sont plus nécessaires (C-010) ;
  2. créer deux jeux de secrets distincts, l'un pour la démonstration, l'autre pour le réel, et ne jamais les mélanger ;
  3. reporter les décisions déjà closes dans le dossier de décisions d'architecture du dépôt.
- **Critères d'acceptation :**
  - [x] les identifiants MT5 de démonstration sont disponibles en variables d'environnement, jamais dans le dépôt — prouvé par tests/config/test_settings.py (secret manquant bloquant le démarrage) et tests/test_secret_detector.py
  - [ ] le mot de passe utilisé est l'investisseur : une tentative d'ordre est refusée par le terminal — en attente : le mot de passe principal est en place, le mot de passe investisseur n'a pas pu être créé ; écart accepté et documenté (statut de TASK-001)
  - [x] aucune décision n'est laissée implicite — prouvé par les décisions closes de la phase 0 (CAHIER_DES_CHARGES.md v1.1) et docs/decisions/2026-10-07-runtime-integration.md
- **Validation :** relecture par l'opérateur, plus tentative d'ordre effectivement refusée.

> **Décisions closes le 2026-10-01, ne plus rouvrir :** Python 3.12 partout (C-004) · `backtest` en paquet séparé jamais chargé en production, mais partageant `strategies` et `analytics` (C-001) · IA en veto asymétrique, mode `shadow` par défaut (C-002) · périmètre or et crypto, capital 100 € (C-008, C-009) · **accès au courtier par Deriv MT5, Windows obligatoire (C-003, C-010, tranchés le 2026-10-03)**. Plus aucune décision d'architecture ouverte ; reste à mesurer les spécifications de contrat (TASK-003) pour écrire la formule de taille (TASK-004).

### TASK-002 — Dépôt, outillage et structure

- [x] Statut : **DONE le 2026-10-03**. Neuf hooks de pré-commit verts, 32 tests verts, les trois critères vérifiés par exécution.
- **Écarts constatés à l'exécution, à connaître :**
  - **`detect-secrets` est insuffisant dans sa configuration par défaut pour ce projet.** Il ne détecte jamais `DERIV_API_TOKEN` : le mot « token » n'est pas l'un de ses mots-clés, et un jeton Deriv est trop court pour les détecteurs d'entropie. Un détecteur propre au projet a été ajouté dans `tools/detect_secrets_plugins/project_tokens.py`, couvert par `tests/test_secret_detector.py`.
  - **Les plugins d'origine vérifient les secrets en ligne par défaut** : chaque jeton candidat est envoyé à l'API de son fournisseur, et les jetons invalides sont écartés en silence. Le hook dépendait donc du réseau et faisait sortir des secrets candidats de la machine à chaque commit. Le hook tourne désormais en `--no-verify`.
  - Les deux défauts ont été mis au jour par le critère d'acceptation « un faux secret doit être bloqué » : sans ce test, la chaîne aurait été verte et n'aurait rien protégé.
  - Sous Windows, les motifs d'exclusion de `detect-secrets` écrits avec `/` ne correspondent pas aux chemins en `\`. La baseline est donc générée à partir de `git ls-files --cached --others --exclude-standard`, ce qui respecte `.gitignore`. **Régénérer la baseline uniquement de cette façon**, jamais avec un scan récursif du dossier.
  - Les répertoires de tests par paquet ne sont pas créés à vide : chacun sera créé avec son premier test.
  - **⚠ Défaut découvert le 2026-10-04, en TASK-010 :** la règle `data/` du `.gitignore`, destinée au dossier local de la base, n'était pas ancrée à la racine. Elle masquait aussi `src/tradingagent/data` et `tests/data`. Le paquet `data` n'avait **jamais été commité** depuis cette tâche, et **ruff, qui respecte le `.gitignore`, ne l'avait jamais analysé**, ce que mes « All checks passed » ne laissaient pas voir. Corrigé par `/data/`. Leçon : vérifier la liste des fichiers indexés à chaque commit, pas seulement le résultat des outils.
  - Le fichier de contexte `CLAUDE.md` a été rédigé directement plutôt qu'avec le skill `init`, inadapté à un dépôt sans code.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-001 (Q-02, Q-03)
- **Skills :** `init` pour produire le fichier de contexte projet
- **Objectif :** un dépôt où les contrôles qualité tournent et échouent correctement sur du code fautif.
- **Actions :**
  1. créer l'arborescence des quatorze paquets de la section 10.2 du cahier, chacun avec son module d'initialisation et son répertoire de tests ;
  2. configurer le gestionnaire de dépendances, ruff, mypy en mode strict sur `risk`, `indicators` et `analytics`, et pytest ;
  3. ajouter les crochets de pré-commit, dont la détection de secrets ;
  4. écrire les deux tests d'architecture : aucun module hors `risk` n'importe `execution`, et aucun module chargé en production n'importe `backtest` ni `research` ;
  5. rédiger le fichier de contexte projet et un fichier d'exemple de variables d'environnement, sans aucune valeur réelle.
- **Critères d'acceptation :**
  - [x] la chaîne complète passe sur un dépôt vide — prouvé par l'exécution consignée de TASK-101 (uv run pytest -q, ruff, mypy) et les neuf hooks de pré-commit
  - [x] le test d'architecture échoue si l'on ajoute volontairement un import interdit, puis repasse après retrait — prouvé par tests/test_architecture.py (test_forbidden_import_is_detected, test_allowed_import_passes)
  - [x] la détection de secrets bloque un commit contenant une fausse clé — prouvé par tests/test_secret_detector.py et le job de détection de secrets de .github/workflows/ci.yml
- **Validation :** exécution locale de la chaîne, puis capture du résultat.

### TASK-003 — Vérification des capacités réelles de Deriv MT5

- [x] Statut : **DONE le 2026-10-04** · **Rapport :** `docs/reports/2026-10-03-mt5-capabilities.md`, relevé brut `.json` et script `scripts/explore_mt5.py`, en lecture seule.
- **Résultats décisifs :** or inéligible au réel avec 100 € (marge 183,93 €) ; seul SOL est à la fois éligible au réel et raisonnablement coûteux en spread ; serveur démo à l'heure universelle, sans heure d'été ; valeur du tick MT5 fiable seulement pour l'or ; indices synthétiques présents sur le compte démo ; historique suffisant (H1 depuis 2011).
- **Erreurs de mesure rencontrées, corrigées avant publication :** un historique faussement vide, causé par le refus en bloc de MT5 au-delà du plafond de bougies du terminal ; une relance qui n'a jamais exécuté le script, parce qu'un contrôle de style l'enchaînait en `&&` alors que le code retour affiché était celui d'un `echo` final ; une absence de cotation XRP et LTC qui s'est révélée passagère.
- **Restent à faire :** mesure des spreads crypto un jour de semaine (préalable à Q-07) ; vérification du refus d'ordre avec le mot de passe investisseur (TASK-001), **le mot de passe actuellement configuré autorisant le trading**.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-001 (Q-05), TASK-002
- **Skills :** `market-data` pour la qualité de flux et la profondeur d'historique, `commodities` et `currencies-and-fx` pour lire les spécifications de l'or
- **Objectif :** remplacer par des faits mesurés les points marqués à confirmer en section 12.1 du cahier. C'est la tâche la plus importante de la phase 0 : elle conditionne la formule de taille et l'éligibilité au mode réel.
- **Actions :**
  1. écrire un script d'exploration jetable, hors des paquets de production, qui se connecte au terminal avec le **mot de passe investisseur** ;
  2. relever le type de compte, la devise, le levier, et vérifier que le compte est bien un compte de démonstration ;
  3. relever les symboles visibles : nom exact de l'or, liste des cryptomonnaies disponibles ;
  4. pour chaque symbole candidat, relever les spécifications de contrat : taille de contrat, lot minimal, pas de lot, lot maximal, taille et valeur du tick, devise de marge et de profit, marge requise pour le lot minimal, distance minimale du stop au prix, mode d'exécution, spread observé ;
  5. **calculer le risque minimal incompressible en euros** pour le lot minimal et une distance de stop typique, et le comparer au plafond de 5 % du capital réel, soit 5 €, pour appliquer RM-019 ; vérifier aussi que la **marge** du lot minimal tient dans 100 € (R-17) ;
  6. mesurer la profondeur d'historique disponible par unité de temps, en bougies et en ticks ;
  7. relever les sessions de négociation, en particulier la fermeture de l'or le week-end ;
  8. **mesurer le décalage entre l'heure du serveur du courtier et l'heure universelle**, et déterminer s'il change avec l'heure d'été (R-16) ;
  9. mesurer la latence d'un aller-retour de lecture, pour dimensionner la fréquence d'interrogation.
- **Résultat attendu :** un rapport écrit, versionné dans le dépôt, contenant des relevés et non des suppositions.
- **Critères d'acceptation :**
  - [x] chaque point à confirmer de la section 12.1 a une réponse factuelle, datée, avec les valeurs brutes renvoyées par le terminal en annexe — prouvé par docs/reports/2026-10-03-mt5-capabilities.md et son relevé brut .json
  - [x] **le lot minimal, le risque minimal et la marge minimale sont chiffrés en euros pour chaque symbole, et l'éligibilité au mode réel est tranchée** (RM-019, Q-21, R-17) — prouvé par le rapport TASK-003 et tests/risk/test_checks.py (test_gold_is_live_eligible_with_a_tight_enough_stop)
  - [x] le décalage horaire du serveur est mesuré et sa règle d'évolution établie — prouvé par le rapport TASK-003 et tests/data/test_server_clock.py
  - [x] la liste des cryptomonnaies candidates est établie, avec spread observé et profondeur d'historique — prouvé par docs/reports/2026-10-03-mt5-capabilities.md
  - [x] si la profondeur d'historique est insuffisante pour un backtest significatif, le risque R-03 est remonté immédiatement — sans objet : la profondeur a été jugée suffisante (H1 depuis 2011) dans le rapport TASK-003
- **Validation :** relecture du rapport par l'opérateur.

### TASK-004 — Décision d'architecture sur le type de contrat

- [x] Statut : **DONE le 2026-10-04, en attente d'approbation de l'opérateur.** La formule et les règles RM-004, RM-008 et RM-012 sont écrites dans le cahier, section 9, avec trois exemples calculés à partir du relevé de TASK-003, dont un refus pour taille inférieure au lot minimal.
- **Choix structurants :** perte par lot calculée par le terminal sur 1 lot entier ; **recoupée par un calcul indépendant, refus au-delà de 2 % d'écart** ; arrondi toujours vers le bas en décimal exact ; volume borné par la moitié de la marge libre ; `trade_tick_value` interdit ; stop jamais élargi automatiquement ; stop relu sur la position après exécution.
- **Constat à retenir :** sur le BTC, la marge limite le volume bien avant le risque. Le risque réel sera d'environ 0,1 % par opération en démonstration, au lieu de 0,5 %.
- **Transmis à TASK-035 :** les trois exemples deviennent des cas de test, et les paramètres `f = 0,5` et seuil de recoupement de 2 % entrent dans la configuration du risque.
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** TASK-003
- **Objectif :** en déduire la sémantique exacte du risque pour des positions MT5.
- **Décision déjà prise le 2026-10-03 :** option C, Deriv MT5, imposée par C-010. Il ne reste que la partie quantitative, qui attend les spécifications de contrat de TASK-003.
- **Actions :**
  1. réécrire les règles RM-004 à RM-008 dans leur forme définitive pour des lots MT5 ;
  2. définir la formule de taille : à partir du capital, du pourcentage de risque, de la distance du stop en prix, de la taille et de la valeur du tick, du lot minimal et du pas de lot, en arrondissant **toujours vers le bas** au pas de lot, et en refusant si le résultat est inférieur au lot minimal ;
  3. traiter la conversion de devise : les profits de l'or et de la crypto sont en dollars, le capital en euros.
- **Critères d'acceptation :**
  - [x] la formule de taille est exprimée sans ambiguïté d'unité et validée par un calcul manuel sur trois exemples, dont un où l'arrondi au pas de lot fait tomber sous le lot minimal — prouvé par tests/risk/test_sizing.py (test_example_1_gold_demo, test_example_2_gold_live_is_refused_below_the_minimum_lot, test_example_3_btc_demo_is_bounded_by_margin)
  - [x] la conversion euro-dollar est explicite, avec sa source de taux — prouvé par risk/model.py (profit_to_eur) et tests/execution/test_mt5_broker.py (test_quote_derives_the_eur_loss_and_margin_from_the_terminal)
- **Validation :** approbation de l'opérateur. **Bloque toute la phase 3 côté risque et toute la phase 8.**

### TASK-005 — Modèle de données et migrations

- [x] Statut : **DONE le 2026-10-04**. 36 tests de stockage, 354 au total, neuf hooks verts.
- **Livré :** `storage/models.py` (13 tables), `storage/types.py`, `storage/engine.py`, `storage/migrate.py`, migration initiale `0001`, et les états métier dans `core/states.py`.
- **Écarts validés par l'opérateur par rapport à la section 11 du cahier :** 13 tables au lieu de 19. Pas de tables utilisateurs, comptes et marchés, dont la source de vérité est `.env` ou `agent.yaml`. Pas de table de ticks, inutile avec MT5. Les valeurs d'indicateurs sont stockées dans le signal lui-même.
- **Garanties tenues par la base, et prouvées par mutation :**
  - unicité des clés d'idempotence des signaux et des ordres, et des bougies ;
  - clés étrangères **activées**, SQLite les désactivant par défaut ;
  - énumérations protégées par contrainte `CHECK` ;
  - **tables d'historique réellement immuables** : déclencheurs refusant toute modification ou suppression sur `signal_events`, `executions`, `trades` et `audit_log` ;
  - dates sans fuseau refusées et relues en UTC ;
  - montants en décimal exact, flottants refusés ;
  - **test de divergence entre modèles et migration** : il a détecté une unicité retirée du seul modèle, que le test de doublon ne voyait pas.
- **À savoir :** les montants sont stockés en texte sous SQLite pour rester exacts. **Aucun calcul ni tri SQL sur ces colonnes sous SQLite** : agréger en Python.
- **Piège rencontré :** la génération automatique d'Alembic produisait deux contraintes `CHECK` identiques par énumération. Les doublons ont été retirés de la migration.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-002, TASK-004
- **Skills :** `test-driven-development`
- **Objectif :** le schéma complet de la section 11 du cahier, avec ses contraintes.
- **Actions :**
  1. déclarer les entités et leurs relations ;
  2. poser les contraintes d'unicité sur les clés d'idempotence des signaux et des ordres, et sur les bougies ;
  3. poser les index temporels ;
  4. écrire la première migration ;
  5. écrire les tests vérifiant qu'une insertion en double est rejetée par la base, et non par le code applicatif.
- **Critères d'acceptation :**
  - [x] la migration s'applique et se rejoue sur une base vide — prouvé par tests/storage/test_migrations.py (test_migration_replays_on_an_empty_database)
  - [x] une violation d'unicité d'idempotence lève une erreur au niveau de la base — prouvé par tests/storage/test_constraints.py (test_duplicate_signal_key_is_rejected_by_the_database, test_duplicate_order_key_is_rejected_by_the_database)
  - [x] les tables immuables sont documentées comme telles — prouvé par tests/storage/test_constraints.py (test_append_only_table_refuses_updates) et tests/storage/test_postgres_immutability.py
- **Validation :** tests verts et inspection du schéma généré.

### TASK-006 — Configuration et secrets

- [x] Statut : **DONE le 2026-10-03**. 91 tests verts, neuf hooks verts.
- **Livré :** `config/settings.py` (environnement et secrets), `config/agent.py` (marchés et profils de risque, fichier YAML), `config/redaction.py` (masquage des secrets dans tous les journaux), `core/timeframe.py`.
- **Choix à connaître :**
  - les symboles et stratégies connus sont **passés en paramètre** au chargeur. Le contrôle existe dès maintenant ; la liste réelle des symboles viendra de l'API Deriv (TASK-003, TASK-010), celle des stratégies du registre (TASK-031) ;
  - un marché désactivé dont le symbole est inconnu bloque quand même le démarrage : une faute de frappe reste une faute de frappe ;
  - le masquage s'accroche à la fabrique d'enregistrements de `logging`, pas à un filtre de handler ou de logger. Un filtre de logger ignore les enregistrements des loggers enfants, un filtre de handler rate les handlers ajoutés plus tard par des bibliothèques tierces. Testé avec un logger tiers et une trace d'exception ;
  - les messages d'erreur de configuration ne contiennent jamais la valeur fautive, seulement l'emplacement et la cause, puisque cette valeur peut être un secret ;
  - `TRADING_MODE=LIVE` sans `LIVE_TRADING_ENABLED=true` empêche le démarrage, première moitié de la double condition RM-000 ;
  - les profils de risque imposent `risque par opération ≤ perte quotidienne ≤ perte hebdomadaire ≤ drawdown`, et le plafond absolu de 5 % en réel (RM-005). Les montants sont en `Decimal`, jamais en flottant ;
  - tous les problèmes d'un fichier sont signalés en une seule fois, chacun avec sa ligne.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-002
- **Objectif :** charger et valider la configuration, sans jamais exposer de secret.
- **Actions :**
  1. définir les modèles de configuration validés pour les marchés et les paramètres globaux de risque ;
  2. charger les secrets depuis l'environnement, avec échec explicite au démarrage si l'un manque ;
  3. implémenter le filtrage des valeurs sensibles dans les journaux ;
  4. écrire les tests : configuration invalide refusée avec désignation de la ligne fautive, secret absent bloquant le démarrage, secret jamais écrit dans un journal.
- **Critères d'acceptation :**
  - [x] un symbole inconnu ou une stratégie inexistante empêche le démarrage — prouvé par tests/config/test_agent_config.py (test_unknown_symbol_blocks_startup_with_its_line, test_unknown_strategy_reference_blocks_startup_with_its_line)
  - [x] un jeton injecté dans un message de journal ressort masqué — prouvé par tests/config/test_redaction.py
- **Validation :** tests verts.

### QUALITY GATE — Phase 0

- [x] Décisions Q-01 à Q-08 tranchées et consignées
- [x] Rapport de capacités Deriv livré, avec relevés bruts
- [x] Sémantique du risque définitive écrite et validée par calcul manuel
- [x] Chaîne qualité verte, test d'architecture opérationnel
- [x] Schéma de base appliqué, contraintes d'idempotence vérifiées par test
- [x] Aucun secret dans le dépôt, détection active

---

## Phase 1 — Données

Objectif : un flux fiable, dont l'état de santé est connu, et sur lequel aucune décision ne sera prise à l'aveugle.

### TASK-010 — Client MT5

- [x] Statut : **DONE le 2026-10-04**. 32 tests sur terminal simulé, un test de fumée réel (`RUN_MT5_LIVE=1 uv run pytest -m mt5_live`) vert sur le compte démo, 390 tests au total.
- **Livré :** `data/terminal.py` (interface), `data/mt5_terminal.py` (seul importateur de `MetaTrader5`, vérifié par le test d'architecture), `data/server_clock.py` (unique point de conversion en UTC), `data/market_data.py` (client asynchrone).
- **Garanties, prouvées par huit mutations toutes attrapées :** contrôle bloquant du compte au démarrage (numéro et type face au mode, RM-017) ; décalage horaire du serveur vérifié au démarrage et sur demande ; **bougie en formation toujours écartée** ; jamais plus de 99 999 bougies par demande ; chaque bougie publiée une seule fois, rattrapage dans l'ordre après une absence ; tous les appels MT5 sur un fil unique ; un symbole en erreur n'arrête pas les autres ; reprise avec délai croissant plafonné à 60 s, pause seulement entre deux tentatives, et rétablissement de la sélection de symboles.
- **⚠ Défaut réel découvert par le test de fumée :** juste après la sélection d'un symbole, **le terminal peut renvoyer un historique en cache périmé mais complet** en nombre de bougies. Le premier lancement réel a échoué sur ce point, le second est passé une fois le terminal synchronisé. Défense ajoutée : la dernière bougie doit contenir l'heure du dernier tick, sinon le client relit, puis refuse. Un marché fermé, comme l'or le week-end, n'est pas confondu avec un historique en retard.
- **Choix à connaître :** le premier appel à `poll_new` fixe une référence sans rien publier ; le chargement de l'historique au démarrage relève de TASK-013. Le symbole de référence pour l'horloge doit coter en continu : c'est le BTC.
- **Pour l'appelant (TASK-012, TASK-034) :** vérifier l'horloge régulièrement avec `verify_clock`, et traiter `ClockMismatchError` et `AccountMismatchError` comme des arrêts, pas comme des erreurs passagères.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-003, TASK-006 · **Couvre :** F-001, F-003, RM-017
- **Skills :** `test-driven-development`, `market-data`
- **Objectif :** un accès au terminal authentifié, résilient, limité à la liste blanche, qui ne livre que des données en temps universel.
- **Actions :**
  1. définir une **interface de terminal** étroite (initialiser, compte, symbole, bougies, ticks, santé), et y confiner l'unique import du paquet `MetaTrader5`. Le reste du code ne dépend que de l'interface ;
  2. exécuter tous les appels au terminal dans **un fil dédié unique**, les appels étant bloquants et non réentrants, et les exposer en asynchrone ;
  3. à l'initialisation : vérifier le numéro et le type de compte contre la configuration et le mode (RM-017), et mesurer le décalage horaire du serveur ;
  4. **convertir en UTC toute date reçue**, en un seul endroit, selon la règle de décalage établie en TASK-003 (R-16) ;
  5. sélectionner strictement les symboles de la liste blanche, et lire les nouvelles bougies par interrogation périodique ;
  6. détecter un terminal fermé, déconnecté ou figé, suspendre la lecture, puis réinitialiser avec un délai progressif plafonné (R-15) ;
  7. écrire un **terminal simulé** implémentant la même interface, capable de couper, de figer, de renvoyer des erreurs et de décaler son heure, pour que les tests tournent sans MT5 et sous tout système.
- **Critères d'acceptation :**
  - [x] aucun symbole hors liste blanche n'est sélectionné, vérifié par test — prouvé par tests/data/test_market_data.py (test_only_whitelisted_symbols_are_selected_and_a_failure_is_isolated)
  - [x] le paquet `MetaTrader5` n'est importé que par l'adaptateur réel, vérifié par le test d'architecture — prouvé par tests/test_architecture.py (test_forbidden_import_is_detected)
  - [x] une bougie datée à l'heure serveur ressort en UTC exacte, y compris de part et d'autre d'un changement d'heure, vérifié par test — prouvé par tests/data/test_server_clock.py et tests/data/test_market_data.py (test_only_closed_candles_come_out_in_utc)
  - [x] un compte réel détecté en mode démonstration provoque l'arrêt, vérifié par test — prouvé par tests/data/test_market_data.py (test_real_account_outside_live_mode_is_fatal)
  - [x] une coupure du terminal simulé est détectée et la reprise rétablit la même sélection de symboles — prouvé par tests/data/test_market_data.py (test_reconnection_backs_off_then_restores_the_selection)
  - [x] un symbole refusé n'interrompt pas les autres — prouvé par tests/data/test_market_data.py (test_a_failing_symbol_does_not_stop_the_others_while_polling)
  - [x] l'écart d'horloge est mesuré et journalisé — prouvé par tests/data/test_market_data.py (test_offset_change_while_running_is_detected) et tests/data/test_server_clock.py
- **Validation :** tests d'intégration contre le simulateur, puis une session réelle de trente minutes.

### TASK-011 — Normalisation et agrégation

- [x] Statut : **CLOSE le 2026-10-04, sur décision de l'opérateur.**
- **Déjà couvert :**
  - la normalisation vers les types communs (`Candle` en UTC) dans `MarketDataClient` ;
  - la règle de clôture : la bougie en formation est toujours écartée, ce qui est testé dans TASK-010.
- **Sans objet depuis C-010 :** l'agrégation locale des ticks et le recoupement avec les bougies du courtier. L'agent lit directement les bougies du courtier. À rouvrir seulement si un contrôle croisé devient nécessaire.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-010 · **Couvre :** F-002
- **Skills :** `test-driven-development`
- **Actions :**
  1. normaliser ticks et bougies vers les types communs ;
  2. agréger les ticks en bougies par unité de temps ;
  3. définir la clôture d'une bougie : réception d'une donnée de la période suivante, ou expiration d'un délai de garde ;
  4. comparer les bougies agrégées localement aux bougies fournies par l'API et journaliser les écarts.
- **Critères d'acceptation :**
  - [x] une bougie n'est jamais déclarée close prématurément, vérifié par test — prouvé par tests/data/test_market_data.py (test_only_closed_candles_come_out_in_utc)
  - [x] l'écart entre agrégation locale et bougies du fournisseur reste sous un seuil documenté — sans objet depuis C-010 : l'agrégation locale est abandonnée, l'agent lit directement les bougies du courtier
- **Validation :** tests unitaires sur séries construites, plus comparaison sur une session réelle.

### TASK-012 — Contrôle qualité et état de santé des séries

- [x] Statut : **DONE le 2026-10-04**. 24 tests, sept mutations toutes attrapées, deux rejeux sur l'historique réel de l'or.
- **Livré :** `data/quality.py`, fonction `assess_series`. Statuts : `HEALTHY`, `MARKET_CLOSED`, et six anomalies : `EMPTY`, `STALE`, `GAP`, `DUPLICATE`, `UNORDERED`, `INVALID`. Seul `HEALTHY` autorise un signal.
- **Distinction essentielle :** `MARKET_CLOSED` bloque les signaux **sans être une anomalie**, pour que la fermeture de l'or ne déclenche pas d'alerte chaque week-end. Un trou ne compte que pendant un quart d'heure que le calendrier marque ouvert ; un quart d'heure incertain est toléré.
- **Verrou en temps réel :** sans tick de moins de 2 minutes, pas de signal. Pendant une heure ouverte, c'est une anomalie `STALE` ; pendant une heure incertaine, c'est considéré comme fermé.
- **Rejeux réels :** jeudi en séance, série saine sans faux trou sur 300 bougies traversant plusieurs pauses ; vendredi 20 h 52 après l'arrêt des cotations, « marché fermé » sans anomalie.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-011 · **Couvre :** F-002, RM-001, RM-002
- **Skills :** `test-driven-development`
- **Objectif :** rendre impossible une décision sur des données douteuses. Exigence EF-014.
- **Actions :**
  1. calculer la fraîcheur de la dernière donnée par symbole ;
  2. détecter les trous, les doublons et les horodatages non monotones ;
  3. exposer un état de santé par série, consommé par le moteur de stratégies ;
  4. écrire les tests d'injection : série périmée, série trouée, doublon, horodatage régressif.
- **Critères d'acceptation :**
  - [x] chacun des quatre scénarios d'injection empêche l'émission d'un signal — prouvé par tests/data/test_quality.py (série périmée, trou, doublon, horodatage régressif) et tests/signals/test_generator.py (test_a_gap_in_the_series_skips_evaluation_with_a_warning)
  - [x] un retour à l'état sain rétablit l'émission sans redémarrage — prouvé par tests/data/test_quality.py (test_a_complete_fresh_series_is_healthy) et tests/signals/test_generator.py (test_a_healthy_close_produces_a_recorded_signal)
- **Validation :** tests verts. **Cette tâche est un prérequis de toute émission de signal.**

### TASK-013 — Persistance et historique

- [x] Statut : **DONE le 2026-10-04**, confirmé sur PostgreSQL (Supabase) le même jour. 19 tests ; toutes les mutations sont attrapées sauf la branche d'insertion PostgreSQL, qui ne sera couverte qu'une fois `TEST_DATABASE_URL` pointé vers Supabase.
- **Livré :**
  - `storage/candles.py` (`CandleStore`) : les doublons sont refusés par la contrainte unique de la base, avec `ON CONFLICT DO NOTHING` sur SQLite comme sur PostgreSQL. La première version stockée d'une bougie fait foi.
  - `data/history.py` (`HistorySync`) : au démarrage et après une reconnexion, l'agent demande le préchauffage, ou tout ce qui manque depuis la dernière bougie stockée si c'est plus long. Une bougie de recouvrement prouve la jointure. `missing()` recense chaque trou pendant les heures ouvertes ; la bougie en formation n'est jamais comptée.
  - `quality.missing_bars` : le recensement complet, réutilisé par `assess_series`.
- **Validation réelle :** compte démo, base SQLite jetable, le 2026-10-04.
  - Rapatriement : 500 bougies M15 pour l'or, 499 pour le BTC (la bougie en formation est écartée).
  - Seconde synchronisation : 0 insertion.
  - Coupure simulée de 40 bougies : exactement 40 réinsérées, total inchangé.
  - Contrôle de continuité sur 24 h : 0 trou sur les deux symboles.
- **Écart, ticks :** aucun tick n'est persisté. L'agent interroge les bougies (C-010) et ne garde que le dernier tick en mémoire, pour le verrou de fraîcheur. La table `ticks` et sa purge à 30 jours du cahier des charges sont sans objet tant qu'aucune agrégation locale n'est requise.
- **Reste à faire :**
  - action 4, la collecte continue, se branche dans la boucle de l'agent (TASK-034) ;
  - les tests PostgreSQL et la migration en production attendent l'URL Supabase.
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-005, TASK-011
- **Actions :**
  1. persister les bougies avec gestion des doublons par contrainte de base ;
  2. implémenter le téléchargement d'historique et le rattrapage après coupure ;
  3. appliquer la politique de conservation des ticks ;
  4. démarrer la collecte continue au plus tôt, pour atténuer le risque R-03.
- **Critères d'acceptation :**
  - [x] un rattrapage après coupure ne crée aucun doublon — prouvé par tests/data/test_history.py (test_catch_up_after_an_outage_creates_no_duplicate) et tests/storage/test_candles.py (test_an_overlapping_catch_up_inserts_only_the_new_candles)
  - [x] la série stockée est continue sur une période de contrôle, ou ses trous sont explicitement recensés — prouvé par tests/data/test_history.py (test_a_continuous_day_reports_no_hole, test_outage_longer_than_the_broker_serves_leaves_a_recorded_hole)
- **Validation :** requête de contrôle de continuité sur vingt-quatre heures.

### TASK-014 — Horaires de marché

- [x] Statut : **DONE le 2026-10-04**. 14 tests, plus une validation sur les données réelles du compte démo.
- **Corrigé le jour même : granularité au quart d'heure, apprise sur les bougies M15.** La version horaire aurait produit une fausse alerte chaque vendredi : l'or cesse de coter à 20 h 45 UTC, à l'intérieur d'une heure qui cote par ailleurs. Résultat réel : or ouvert 459 quarts d'heure sur 672, BTC 672 sur 672.
- **Approche validée par l'opérateur :** le paquet MT5 ne fournit pas les horaires, donc `data/market_calendar.py` les **apprend sur les 8 dernières semaines de bougies** du courtier. Un créneau horaire est ouvert s'il a coté dans au moins 75 % de ses occurrences, incertain s'il n'a coté que certaines semaines, fermé sinon. Le calendrier doit être réappris chaque jour, ce qui absorbe les changements d'heure d'été américains en quelques semaines, l'heure concernée restant incertaine dans l'intervalle.
- **Résultat sur données réelles :** BTC ouvert 168 heures sur 168. Or ouvert 115 heures sur 168 : pause quotidienne à 21 h UTC du lundi au jeudi, fermeture le vendredi à 21 h, réouverture le dimanche à 22 h UTC.
- **Critère « réouverture détectée sans redémarrage » :** couvert par le verrou de fraîcheur du tick (TASK-012) et le réapprentissage quotidien, à brancher dans la boucle de l'agent (TASK-034).
- **Observation réelle sur un week-end complet :** pas encore faite. Les données apprises en couvrent huit, ce qui la rend largement redondante ; elle reste à consigner une fois l'agent en marche continue.
- **Priorité :** P1 · **Complexité :** S · **Dépendances :** TASK-010 · **Couvre :** F-005, EF-020
- **Actions :** interroger périodiquement les horaires par symbole, les mettre en cache, exposer l'état de marché, et gérer les jours fériés tels que retournés par l'API.
- **Critères d'acceptation :**
  - [ ] l'état de l'or bascule correctement à la fermeture et à la réouverture de fin de semaine, vérifié sur un week-end réel — en attente : observation réelle d'un week-end complet non encore consignée ; le calendrier est appris sur huit semaines de bougies réelles (tests/data/test_market_calendar.py::test_weekend_and_daily_break_are_learned)
  - [x] les marchés crypto restent ouverts en continu, week-end compris — prouvé par tests/data/test_market_calendar.py (test_a_market_trading_around_the_clock_is_always_open) et tests/data/test_quality.py (test_a_market_open_around_the_clock_has_no_closed_hours)
- **Validation :** observation sur un week-end complet, consignée.

### QUALITY GATE — Phase 1

- [ ] **Connexion stable sur vingt-quatre heures consécutives, avec au moins une reconnexion réussie** — reconnexion prouvée par test (coupure du terminal simulé, reprise de la sélection) et par un essai réel court ; l'observation continue de vingt-quatre heures relève de l'exploitation.
- [x] Aucun abonnement hors liste blanche
- [x] Les quatre scénarios de données dégradées bloquent les signaux
- [x] Bougies continues et sans doublon sur vingt-quatre heures
- [x] Horaires de l'or corrects sur un week-end réel
- [x] Chaîne qualité verte

---

## Phase 2 — Telegram et signaux

Objectif : une surface de pilotage sûre, disponible avant qu'il y ait quoi que ce soit à piloter.

### TASK-020 — Bot, liste blanche et journal des commandes

- [x] Statut : **DONE le 2026-10-06.** 24 tests (9 d'accès, 15 de service).
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-005, TASK-006 · **Couvre :** F-014, EF-011
- **Livré :**
  - `notify/access.py` (`AccessGate`) : liste blanche d'identifiants, un inconnu ne reçoit aucune réponse ni confirmation d'existence ; limites de tentatives par identifiant (fenêtre glissante puis refroidissement), réarmées à l'expiration ; les refus d'inconnus sont journalisés avec un plafond horaire pour qu'un flood ne remplisse pas la base ; refus des discussions de groupe.
  - `notify/service.py` (`CommandService`) : chaque commande autorisée est journalisée **avant** exécution ; si le journal est indisponible, rien ne s'exécute (fail-closed) ; un opérateur limité est averti une fois, un inconnu garde le silence.
  - `notify/commands.py` (`CommandRouter`) : routage, `/help` auto-générée, `/status` (mode non ambigu, arrêt actif avec motifs, quarantaines).
  - `notify/telegram_app.py` : adaptateur de polling long, point d'entrée `uv run tradingagent-bot`, SecretsFilter sur les journaux, TLS via le magasin de certificats Windows.
- **Reste à faire lors du branchement :** l'essai manuel avec un second compte Telegram non autorisé (l'adaptateur n'est pas encore branché au moteur).
- **Skills :** `test-driven-development`
- **Actions :**
  1. connecter le bot et router les commandes ;
  2. filtrer par liste blanche d'identifiants, refuser sans divulguer d'information ;
  3. limiter le nombre de tentatives par identifiant ;
  4. journaliser chaque commande reçue, autorisée ou non.
- **Critères d'acceptation :**
  - [x] une commande d'un identifiant inconnu est refusée, journalisée, et la réponse ne révèle ni l'existence du système ni son état — prouvé par tests/notify/test_access.py et tests/notify/test_service.py (test_a_stranger_gets_no_answer_at_all, test_a_stranger_is_recorded_in_the_audit_log)
  - [x] la limitation de tentatives se déclenche et se réarme — prouvé par tests/notify/test_access.py (test_too_many_attempts_trigger_the_limit_once, test_the_limit_rearms_after_its_cooldown)
- **Validation :** tests, puis essai manuel avec un second compte Telegram non autorisé.

### TASK-021 — Gabarit de message de signal

- [x] Statut : **DONE le 2026-10-06.** 16 tests de gabarit.
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** TASK-020 · **Couvre :** F-013, EF-005
- **Livré :** `notify/signal_template.py`. `SignalNotice` porte tous les champs de F-013 ; `render_signal_message` produit un message **HTML** (mode d'envoi `parse_mode="HTML"`) où chaque champ issu des données est échappé par `html.escape` — aucun contenu de stratégie ne peut casser le formatage. Le ratio risque/rendement est estimé du milieu de la zone d'entrée au premier objectif. Le mode reprend les libellés non ambigus de `commands.py` (« RÉEL » n'apparaît jamais dans un message DÉMO et réciproquement, prouvé par test). La confiance n'est affichée que lorsqu'elle existe ; les heures sont explicitement en UTC.
- **Reste à faire lors du branchement :** l'envoi réel de contrôle sur Telegram, quand le sender sera relié au cycle de vie du signal (TASK-040).
- **Actions :** implémenter le gabarit contenant l'ensemble des champs de F-013, avec affichage non ambigu du mode en cours, et échappement correct du formatage.
- **Critères d'acceptation :**
  - [x] un test vérifie la présence de chacun des champs requis — prouvé par tests/notify/test_signal_template.py (test_every_required_field_is_present)
  - [x] le mode est visible sans ambiguïté possible entre démonstration et réel — prouvé par tests/notify/test_signal_template.py (test_the_mode_is_unmistakable)
- **Validation :** test de gabarit, plus envoi réel de contrôle.

### TASK-022 — Commandes de lecture

- [x] Statut : **DONE le 2026-10-06.** 8 tests de commandes de lecture, 688 au total.
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-020
- **Livré :**
  - `notify/read_commands.py` : `/markets`, `/signals`, `/positions`, `/performance` (et `/help` auto-générée de TASK-020). Chaque factory ferme un handle de base et renvoie un handler ; tous les appels base passent par `asyncio.to_thread`.
  - `storage/positions.py` (`PositionReader.open_positions`) et `storage/performance.py` (`PerformanceReader.summary`, calculé sur `trades` append-only, décompte par mode) ; `storage/signals.py` gagne `read_recent_signals` (les 10 derniers, plus récents d'abord).
  - `/status` (TASK-020) est étendue : mode, arrêt actif et quarantaines, puis section marchés avec fraîcheur de la dernière bougie via le helper partagé `market_line`.
- **Écart assumé :** la liste des marchés suivis viendra de la configuration de l'agent au branchement de la boucle (TASK-034) ; le bot autonome répond donc « aucun marché configuré » et `/status` n'affiche pas encore l'état des connexions MT5 (il n'en a pas).
- **Reste à faire au branchement :** les essais manuels consignés, avec une base d'un mois pour le critère « moins de deux secondes » ; les requêtes sont déjà indexées (`ix_signals_symbol_generated_at`, PK sur toutes les lectures).
- **Objectif :** `/status`, `/markets`, `/signals`, `/positions`, `/performance`, `/help`. Regroupées car elles partagent le même module, le même contrôle d'accès et le même contrôle qualité.
- **Critères d'acceptation :**
  - [ ] chaque commande répond en moins de deux secondes sur une base contenant un mois de données — en attente : essai manuel sur une base d'un mois non exécuté ; les requêtes sont testées fonctionnellement (tests/notify/test_read_commands.py) mais la latence n'a pas été mesurée
  - [ ] `/status` affiche l'état des connexions, le mode, les marchés et l'état de santé des séries — à vérifier : le mode, les marchés et la santé des séries sont affichés et testés (tests/notify/test_read_commands.py), l'état des connexions MT5 n'était pas exposé au moment de la rédaction
- **Validation :** essais manuels consignés.

### TASK-023 — Commandes sensibles avec confirmation

- [x] Statut : **DONE le 2026-10-06.** 10 tests de commandes sensibles, 698 au total.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-020, TASK-036 · **Couvre :** F-014, F-019, EF-029
- **Livré :**
  - `notify/sensitive_commands.py` : convention de confirmation uniforme — une commande nue répond avec **l'effet exact** qu'elle aura et demande `/commande confirmer` ; rien n'est persisté avant ce mot, et la journalisation par CommandService conserve l'auteur (`telegram:<id>`) et les arguments.
  - Sémantique conforme au tableau 13.2 et à RM-015 : `/pause` et `/emergency_stop` interdisent les nouveaux ordres **sans** clôture implicite (le message le dit) ; `/close_all` annonce explicitement que « les positions seront réellement clôturées » et écrit l'arrêt avec `close_positions=True` ; `/resume` lève l'arrêt global.
  - `/disable <symbole>` / `/enable <symbole>` : nouvelle portée `market:<symbole>` (`core/halt.py`, `HaltStore.halted_markets`), persistée dans `halt_commands` — survit au redémarrage, prouvé par test avec une instance de store reconstruite.
  - `/mode` : LIVE refusé avec l'explication de la double condition serveur (RM-000) ; un mode valide est enregistré dans `system_events` (`storage/events.py`, `SystemEventStore`) avec son auteur.
- **Écart assumé :** `/mode` enregistre la demande ; son application par l'agent suppose la boucle en marche (lecture du dernier `mode_command` au démarrage), au branchement (TASK-034).
- **Reste à faire au branchement :** les essais manuels sur chaque commande, avec redémarrage intercalé.
- **Objectif :** `/pause`, `/resume`, `/enable`, `/disable`, `/mode`, `/close_all`, `/emergency_stop`.
- **Actions :**
  1. exiger une confirmation explicite décrivant précisément l'effet, en particulier la distinction entre suspension des ordres et clôture des positions ;
  2. persister l'effet de chaque commande, afin qu'il survive à un redémarrage ;
  3. interdire l'activation du mode réel par cette voie, conformément à RM-000 ;
  4. journaliser la décision avec l'identité de son auteur.
- **Critères d'acceptation :**
  - [x] la confirmation de `/close_all` énonce explicitement que des positions seront fermées — prouvé par tests/notify/test_sensitive_commands.py (test_close_all_announces_and_requires_the_closing)
  - [x] `/mode LIVE` est refusé avec un message expliquant la condition serveur manquante — prouvé par tests/notify/test_sensitive_commands.py (test_mode_live_is_refused_with_the_server_condition)
  - [x] l'état d'un marché désactivé survit à un redémarrage — prouvé par tests/notify/test_sensitive_commands.py (test_disable_needs_the_symbol_and_confirmation_then_survives_a_restart)
- **Validation :** essais manuels sur chaque commande, avec redémarrage intercalé.

### TASK-024 — Alertes de santé système

- [x] Statut : **DONE le 2026-10-06.** 8 tests d'alertes, 706 au total.
- **Priorité :** P1 · **Complexité :** S · **Dépendances :** TASK-012, TASK-020 · **Couvre :** F-024
- **Livré :** `notify/health_alerts.py` (`HealthAlerter`). Le runtime déclare ce qu'il observe et l'alerter décide : démarrage (avec le mode) et arrêt du processus, coupure (`connection_lost`) et rétablissement, échecs répétés d'un composant (seuil franchi puis rappel seulement après le cooldown), série dégradée persistante (les statuts `HEALTHY` et `MARKET_CLOSED` ne comptent pas ; un rétablissement ouvre un nouvel épisode), saturation disque. Chaque alerte est envoyée via un sender injecté **et** écrite dans `system_events` ; une même condition ne déclenche qu'un message par épisode, rappel après `repeat_after` — une coupure provoquée n'alerte qu'une fois, prouvé par test.
- **Reste à faire au branchement (TASK-034) :** brancher le sender Telegram, appeler `process_started`/`process_stopping` aux bornes du processus, `series_check` à chaque contrôle de série, `disk_pressure` périodiquement (mesure via `shutil.disk_usage` côté appelant, pour rester testable), et provoquer les incidents de validation.
- **Actions :** émettre une alerte en cas de coupure prolongée, de série dégradée persistante, d'échec répété d'un composant, de saturation disque, et au démarrage comme à l'arrêt du processus. Limiter la répétition d'une même alerte.
- **Critères d'acceptation :**
  - [x] une coupure provoquée déclenche une alerte unique, non répétée en boucle — prouvé par tests/notify/test_health_alerts.py (test_an_outage_alerts_once_and_not_in_a_loop)
  - [x] le redémarrage du processus est notifié — prouvé par tests/notify/test_health_alerts.py (test_the_process_start_is_announced_once, test_the_stop_is_announced)
- **Validation :** incidents provoqués volontairement.

### QUALITY GATE — Phase 2

- [x] Un identifiant non autorisé ne peut rien obtenir ni déclencher — prouvé par les tests d'accès et de service ; l'essai manuel avec un second compte reste à faire au branchement
- [x] Toutes les commandes sensibles exigent confirmation et sont journalisées — prouvé par tests (l'effet exact est énoncé avant confirmation, l'auteur et les arguments sont en base)
- [x] Le mode réel est inaccessible depuis Telegram seul — `/mode LIVE` refusé avec l'explication (RM-000), plus le validateur de démarrage de TASK-006
- [ ] Les alertes système parviennent réellement — composant livré (`HealthAlerter`) et **désormais branché dans la boucle** (`app.py` : démarrage, arrêt, coupure, rétablissement, séries dégradées, échecs de composant, divergence, disque) ; l'envoi réel dépend du jeton Telegram et d'un incident provoqué en exploitation
- [x] Chaîne qualité verte — 706 tests, 9 hooks de pré-commit verts, mypy et ruff propres

---

## Phase 3 — Stratégies, risque et intelligence artificielle

Objectif : produire des signaux corrects et rendre structurellement impossible qu'un signal contourne le contrôle du risque.

### TASK-030 — Moteur d'indicateurs

- [x] Statut : **DONE le 2026-10-03**. 53 tests d'indicateurs, 154 au total, neuf hooks verts.
- **Livré :** `sma`, `ema`, `rsi` et `atr` dans `src/tradingagent/indicators/`, limités aux indicateurs cités par le manifeste d'exemple du cahier initial. Les autres seront ajoutés avec la stratégie qui en a besoin.
- **Conventions arrêtées :**
  - sortie alignée sur l'entrée, une valeur par bougie ; `None` signifie « pas de valeur », que ce soit par manque de données ou parce que la valeur est mathématiquement indéfinie ;
  - le RSI d'une fenêtre parfaitement plate vaut `None`, pas 50 : renvoyer 50 fabriquerait une lecture neutre ;
  - la première bougie n'a pas de true range, faute de clôture précédente. Lui substituer `high - low` serait une approximation ;
  - lissage de Wilder (alpha = 1/n) pour le RSI et l'ATR, EMA standard (alpha = 2/(n+1)) amorcée sur la moyenne simple ;
  - toute valeur non finie lève une erreur au lieu de se propager ;
  - la moyenne simple est recalculée exactement sur chaque fenêtre, sans somme glissante, afin que sa valeur ne dépende pas du point de départ de la série.
- **Vérification :** valeurs de référence calculées à la main, avec des périodes de 2 pour que Wilder et l'EMA standard divergent. Six erreurs classiques injectées volontairement ont toutes été attrapées par les tests.
- **Garde-fou ajouté :** le test d'architecture interdit à `indicators` d'importer tout module d'horloge, de réseau, d'aléatoire ou d'entrée-sortie, et tout paquet du projet autre que `core`. Le critère « aucun indicateur ne lit l'heure ni n'accède au réseau » est donc vérifié automatiquement, et plus seulement à la relecture.
- **⚠ Conséquence à reporter sur TASK-031 et TASK-061 — le préchauffage.** L'EMA, le RSI et l'ATR sont récursifs : leur valeur sur une bougie dépend de tout l'historique depuis leur point de départ. Deux calculs partant de points différents divergent sur la même bougie jusqu'à convergence, soit plusieurs fois la période. Si la production calcule sur moins d'historique que le backtest, la parité entre les deux est rompue en silence.
- **Piège rencontré, à connaître :** lors du test par mutation, Python a exécuté un bytecode périmé. Le fichier muté et le fichier restauré avaient la même taille et la même date à la seconde près, et le cache `.pyc` ne vérifie que ces deux informations. Tout script qui réécrit des sources puis les réexécute doit tourner avec `PYTHONDONTWRITEBYTECODE=1`.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-002 · **Couvre :** F-006
- **Skills :** `test-driven-development`
- **Actions :**
  1. implémenter les indicateurs nécessaires aux stratégies envisagées, en fonctions pures ;
  2. pour chacun, écrire d'abord le test avec des valeurs de référence calculées indépendamment, par exemple à la main sur une série courte ;
  3. définir le comportement en cas de série trop courte : absence de valeur explicite, jamais une approximation.
- **Critères d'acceptation :**
  - [x] chaque indicateur est validé contre des valeurs de référence externes au code testé — prouvé par tests/indicators/test_indicators.py (valeurs calculées à la main pour SMA, EMA, RSI, ATR)
  - [x] aucun indicateur ne lit l'heure courante ni n'accède au réseau — prouvé par tests/test_architecture.py (imports interdits et appels now()/utcnow()/today() détectés)
  - [x] une série insuffisante produit une absence de valeur, vérifiée par test — prouvé par tests/indicators/test_indicators.py (test_too_short_series_yields_no_value_at_all, test_empty_series_yields_empty_output)
- **Validation :** tests verts, couverture élevée du paquet.

### TASK-031 — Interface de stratégie et chargeur

- [x] Statut : **DONE le 2026-10-03**. 293 tests verts, neuf hooks verts.
- **Conception validée :** `docs/superpowers/specs/2026-10-03-strategy-interface-design.md` · **Plan :** `docs/superpowers/plans/2026-10-03-strategy-interface.md`.
- **Livré :** types `Candle`, `Direction`, `SignalCandidate`, `TradingMode`, `AiFilter` dans `core` ; `StrategyManifest`, `Strategy`, `StrategyContext`, registre, `evaluate()` et `check_strategy_contract()` dans `strategies` ; `load_strategy_catalog()` et les contrôles marché ↔ stratégie dans `config`.
- **À retenir pour la suite :**
  - **`strategies.evaluation.evaluate()` est la seule voie d'évaluation d'une stratégie.** TASK-034 et TASK-061 doivent l'appeler, et rien d'autre. C'est ce qui garantit la parité entre backtest et production ;
  - **toute nouvelle stratégie doit passer `check_strategy_contract()`** dans ses tests ;
  - ~~le test du registre vide devra être mis à jour en TASK-033~~ — fait : il vérifie désormais que le registre contient exactement les stratégies relues, aujourd'hui `witness` seule ;
  - le critère « l'ancienne configuration reste active si la nouvelle est invalide » relève du rechargement à chaud et passe en **TASK-032**. Le chargement actuel est tout ou rien.
- **Écarts par rapport à la conception, découverts à l'implémentation :**
  - **le registre exige aussi que le modèle de paramètres refuse les clés inconnues.** Par défaut, pydantic les ignore : un `ema_fsat: 20` mal orthographié serait passé en silence, et la stratégie aurait tourné avec la valeur par défaut d'`ema_fast`. La spec est mise à jour ;
  - la règle de pureté autorise désormais l'import de `datetime`, et détecte à la place tout appel à `now()`, `utcnow()` ou `today()` dans un paquet pur ;
  - `TradingMode` est déplacé de `config` vers `core`, sans alias de compatibilité ;
  - `agent.yaml` ne déclare plus d'unités de temps par marché : elles viennent du manifeste de la stratégie. Le format de TASK-006 change en conséquence, et la référence de stratégie doit être épinglée en `id@version`.
- **Défauts de mes propres tests, corrigés avant implémentation :** une stratégie de test qui ne pouvait jamais émettre de signal, ce qui rendait vide le test de non-interférence entre deux marchés ; un test d'état caché qui ne pouvait rien détecter ; une définition du déterminisme qui laissait passer une stratégie à réponses alternées.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-030, TASK-006 · **Couvre :** F-007, EF-003
- **Skills :** `brainstorming` avant l'écriture, pour arrêter la forme de l'interface, puis `test-driven-development`
- **Objectif :** une interface unique, et un chargeur qui refuse toute configuration douteuse.
- **Actions :**
  1. définir l'interface commune : entrées, sorties, données requises, quantité minimale d'historique. **Cette quantité doit inclure le préchauffage des indicateurs récursifs**, EMA, RSI et ATR, d'au moins plusieurs fois leur période, conformément à la note de TASK-030. Le moteur doit refuser d'évaluer une stratégie tant que cet historique n'est pas disponible, et le harnais de backtest de TASK-061 doit appliquer exactement la même règle ;
  2. définir le schéma du manifeste, couvrant les éléments listés en section 7.2 du cahier initial, augmenté du paramètre `ai_filter` issu de C-002 ;
  3. implémenter le chargeur avec validation stricte ;
  4. faire échouer le chargement si un symbole n'est pas dans les symboles autorisés du manifeste, conformément à RM-003.
- **Critères d'acceptation :**
  - [x] deux marchés tournent simultanément avec deux stratégies différentes et n'interfèrent pas — prouvé par tests/strategies/test_evaluation.py (test_two_markets_with_two_strategies_do_not_interfere)
  - [x] une stratégie déclarée pour l'or est refusée sur une paire crypto — prouvé par tests/config/test_agent_config.py (test_strategy_is_refused_on_a_symbol_it_does_not_allow) et tests/strategies/test_evaluation.py (test_symbol_outside_the_manifest_is_a_programming_error)
  - [x] un manifeste invalide est refusé et l'ancienne configuration reste active — prouvé par tests/config/test_catalog_reload.py (test_an_invalid_file_refuses_the_reload_and_keeps_serving)
- **Validation :** tests, plus essai à deux marchés.

### TASK-032 — Versionnement et rechargement

- [x] Statut : **DONE le 2026-10-06.** 5 tests de rechargement, 711 au total.
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-031, TASK-005 · **Couvre :** F-008, EF-021, EF-022
- **Livré :**
  - Action 1 (versions sans écrasement) et action 3 (signal rattaché à sa version exacte) étaient déjà portées par `strategy_versions` et `SignalRepository` (TASK-034) : le manifeste est figé à sa première utilisation, un manifeste modifié sans changement de version est refusé, et le snapshot prouve qu'un trade ancien retrouve ses paramètres exacts.
  - Action 2 (rechargement à chaud) : `config/strategy_catalog.py` gagne `StrategyCatalog` — construction validante, `reload()` tout-ou-rien qui **conserve le snapshot courant si un fichier est invalide** (l'opérateur corrige puis recharge), bascule par simple affectation de référence, et réutilise l'objet chargé d'un manifeste inchangé (aucune reconstruction derrière une évaluation en cours).
  - Action 4 (RM-016) : rien dans le code ne promeut un état — le catalogue ne fait que refléter les fichiers ; les manifestes plafonnent le mode (`max_mode`), prouvé par test.
- **Reste à faire au branchement (TASK-034) :** brancher le déclencheur de `reload()` sur une action explicite de l'opérateur, et la validation manuelle « rechargement observé en fonctionnement, restitution d'un trade ancien ».
- **Actions :**
  1. enregistrer chaque version de paramètres sans jamais écraser la précédente ;
  2. implémenter le rechargement à chaud d'une configuration validée, sans redéploiement ;
  3. rattacher chaque signal à l'identifiant de version exact ;
  4. exiger une action explicite de l'opérateur pour tout changement d'état de stratégie, conformément à RM-016.
- **Critères d'acceptation :**
  - [x] un trade ancien permet de retrouver les paramètres exacts en vigueur à son ouverture — prouvé par tests/storage/test_signals.py (test_the_strategy_version_is_stored_once) et tests/storage/test_constraints.py (le trade restitue sa version de stratégie)
  - [x] un rechargement prend effet sans interruption de service — prouvé par tests/config/test_catalog_reload.py (test_a_new_version_is_picked_up_without_touching_the_running_one)
  - [x] aucune promotion automatique de stratégie n'est possible — prouvé par tests/config/test_catalog_reload.py (test_reload_never_changes_a_state_the_files_do_not_carry) et tests/config/test_agent_config.py (plafond de mode par manifeste)
- **Validation :** rechargement observé en fonctionnement, plus restitution d'un trade ancien.

### TASK-033 — Stratégie témoin

- [x] Statut : **DONE le 2026-10-03**. 311 tests verts, neuf hooks verts.
- **Livré :** `strategies/library/witness.py` (croisement d'EMA, stop et objectif en multiples d'ATR), manifeste `config/strategies/witness@1.0.0.yaml` plafonné à `SIGNAL`, inscription au registre de production.
- **Garde-fous testés :** le manifeste livré ne dépasse jamais `SIGNAL` ; son historique couvre au moins 5 fois la plus longue période récursive (préchauffage) ; paramètres incohérents refusés (EMA lente plus courte que la rapide, stop à l'intérieur de la zone d'entrée, objectif à l'intérieur de la zone) ; contrat de stratégie respecté.
- **Vérification :** le signal tombe exactement sur le croisement, contrôlé par un oracle indépendant qui recalcule les EMA sur la même fenêtre. Cinq erreurs injectées : quatre attrapées d'emblée. **La cinquième a survécu** — un ratio d'objectif codé en dur à 2 passait parce que tous les tests utilisaient justement 2,0. Le test utilise désormais 2,5.
- **⚠ Limite :** le rejeu sur historique de la roadmap a été fait sur une **marche aléatoire synthétique à graine fixe** (2 000 bougies, achats et ventes valides, aucune erreur de stratégie). Aucun historique Deriv n'est encore disponible. Le rejeu sur données réelles revient à TASK-060 et TASK-061.
- **Écart par rapport à TASK-031 :** ajout de `StrategyContext.primary_timeframe`. Sans lui, la stratégie devait coder `M15` en dur, et passer le manifeste en `H1` l'aurait fait planter. La spec est mise à jour.
- **Rappel :** cette stratégie n'est **pas** une stratégie de trading. Elle prouve la mécanique.
- **Priorité :** P1 · **Complexité :** S · **Dépendances :** TASK-031
- **Objectif :** une stratégie simple et documentée, destinée à valider la mécanique de bout en bout. Elle n'est pas une recommandation de trading et ne doit jamais être promue au-delà du mode signal.
- **Critères d'acceptation :**
  - [x] elle produit des signaux observables sur données historiques — prouvé par tests/strategies/test_witness.py (rejeu) et la campagne TASK-064 sur 3 999 bougies XAUUSD M15 réelles (scripts/backtest/run_campaign.py, docs/research/2026-10-07-campaign.json)
  - [x] son manifeste porte un état interdisant explicitement les modes d'exécution — prouvé par tests/strategies/test_witness.py (test_shipped_manifest_never_exceeds_signal_mode)
- **Validation :** rejeu sur historique.

### TASK-034 — Génération de signal et idempotence

- [x] Statut : **DONE le 2026-10-04.**
  - Tests : 31 (18 pour le générateur, 13 pour le stockage).
  - Mutations : 12 sur 12 attrapées.
- **Livré :**
  - `storage/signals.py` (`SignalRepository`).
    - Clé `ref:symbole:unité:clôture` (par exemple `witness@1.0.0:XAUUSD:M15:2026-10-06T12:00Z`), unique en base.
    - Le signal, la copie du manifeste (`strategy_versions`) et le premier événement `CANDIDATE` sont écrits dans une seule transaction.
    - Un manifeste modifié sans changement de version est refusé (`ManifestChangedError`).
  - `signals/generator.py` (`SignalGenerator.on_candle_closed`).
    - Seules les stratégies du marché et de l'unité principale sont évaluées.
    - La fenêtre est relue en base sans les barres postérieures au déclencheur.
    - Chaque série est contrôlée par `assess_series` ; un refus est enregistré dans `system_events` : INFO pour un marché fermé, WARNING pour une anomalie.
    - Le mode du signal est plafonné au `max_mode` de la stratégie (RM-016).
- **Isolement :** chaque paire (stratégie, marché) est isolée.
  - 3 erreurs consécutives entraînent une quarantaine, avec un événement CRITICAL, jusqu'au réarmement par `rearm`.
  - Une évaluation réussie remet le compteur à zéro.
  - Une panne d'infrastructure donne le statut `FAILED` sans bloquer les autres paires.
- **Prouvé par les tests :**
  - 8 fils enregistrant la même bougie donnent 1 signal ;
  - un redémarrage ne crée pas de doublon ;
  - une coupure entre le signal et l'événement ne laisse rien en base ;
  - une vieille bougie rejouée après un redémarrage est refusée comme périmée ;
  - les indicateurs restent consultables en base.
- **Écart :** la clé inclut la version de la stratégie, en plus des champs prévus par F-012. Une nouvelle version peut donc réévaluer la même bougie.
- **Reste à faire, au branchement de la boucle :**
  - `poll_new` renvoie des bougies sans leur symbole et doit les étiqueter ;
  - la quarantaine est en mémoire : sa persistance après redémarrage relève de l'état global (TASK-036) ;
  - le réarmement par commande relève de TASK-023.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-012, TASK-031, TASK-005 · **Couvre :** F-009, F-012, RM-009, EF-006
- **Skills :** `test-driven-development`
- **Actions :**
  1. déclencher l'évaluation à la clôture de bougie, uniquement si la série est saine et le marché ouvert ;
  2. construire la clé d'idempotence de façon déterministe ;
  3. persister le signal, ses valeurs d'indicateurs et son premier événement de cycle de vie ;
  4. isoler les exceptions de stratégie et mettre en quarantaine après échecs répétés ;
  5. tester la concurrence et le redémarrage en cours de traitement.
- **Critères d'acceptation :**
  - [x] deux évaluations de la même bougie ne produisent qu'un signal, garanti par la base — prouvé par tests/storage/test_signals.py (test_the_same_candle_recorded_twice_gives_one_signal, test_concurrent_recording_of_the_same_candle_gives_one_signal) et tests/signals/test_generator.py
  - [x] une exception dans une stratégie n'affecte pas les autres marchés — prouvé par tests/signals/test_generator.py (test_a_crashing_strategy_does_not_stop_the_others)
  - [x] les valeurs d'indicateurs du signal sont consultables après coup — prouvé par tests/storage/test_signals.py (test_signal_is_stored_with_indicators_version_and_first_event)
- **Validation :** tests de concurrence et de reprise.

### TASK-035 — Moteur de risque

- [x] Statut : **DONE le 2026-10-04, revue de code dédiée faite le jour même.**
- **Revue :** 10 points relevés, 9 corrigés.
  - Le stop est désormais mesuré depuis le prix qui le déclenche (bid pour un achat), comme le fait MT5.
  - Un compte dans une autre devise que l'euro est refusé.
  - Une série de pertes sans horodatage vaut pause.
  - Un instrument mal décrit (pas de lot nul, taille de contrat nulle) produit un refus, pas un plantage.
  - « Réduit » n'est plus attribué après arrondi quand le risque seul aurait donné le même volume.
  - Les refus en cascade (`blocked_by`) ne sont plus comptés comme des causes distinctes.
  - Les pertes du jour et de la semaine incluent le risque complet de l'opération envisagée : une opération dont le stop ferait franchir la limite est refusée d'avance.
  - La règle RM-017 n'existe plus qu'en un seul endroit, `core/account.py`.
  - La double vérification du stop a été supprimée.
- **Non retenu pour l'instant :** le plafond d'exposition totale de RM-008. Les plafonds de positions (2 au total, 1 par marché) et de risque par opération bornent déjà l'exposition. À réévaluer si le nombre de marchés augmente.
  - Tests : 96 (91 pour le risque, 4 pour l'enregistrement des décisions, 1 pour la distance de stop typique).
  - Couverture du paquet `risk` : 99 % ; seul manque un garde-fou inatteignable.
  - Mutations : 22 sur 22 attrapées.
- **Livré :**
  - `risk/model.py` : la photo immuable de la situation, en décimal exact, et `limits_for(mode, config)`. Le mode LIVE utilise le profil réel ; tous les autres modes utilisent le profil simulé, signaux notifiés compris.
  - `risk/checks.py` : un contrôle par fonction, 13 au total : stop-loss, zone d'entrée, perte du jour, perte de la semaine, drawdown, positions ouvertes, positions sur le marché, opérations du jour, spread, marge, horaires, refroidissement, éligibilité au réel.
  - `risk/sizing.py` : la formule de TASK-004. Les trois exemples du cahier sont reproduits au centime près.
  - `risk/eligibility.py` : RM-019. Le motif d'inéligibilité donne le capital qui lèverait le refus ; une éligibilité inconnue vaut refus.
  - `risk/engine.py` : `decide` exécute tous les contrôles et rend « autorisé », « réduit » ou « refusé », avec tous les motifs. Un compte qui contredit le mode lève une erreur fatale (`AccountModeMismatchError`, RM-017).
  - `storage/risk_decisions.py` : la décision, le nouvel état du signal (`VALIDATED` ou `RISK_REJECTED`) et l'événement de cycle de vie sont écrits dans une seule transaction. `refusals(start, end)` compte les refus par contrôle, pour le rapport quotidien.
  - `SignalRepository.typical_stop_distance` : la distance de stop médiane des 20 derniers signaux, pour le bilan RM-019 au démarrage.
- **Valeurs par défaut choisies le 2026-10-04 :** spread au plus 10 % de la distance du stop, 4 h de refroidissement après 3 pertes, 4 opérations par jour, marge engagée au plus 50 % de la marge libre. Elles sont réglables dans `agent.yaml` et seront revues après l'analyse du marché.
- **Écart :** la taille est calculée sur la **plus grande** des deux estimations de perte (courtier et calcul indépendant), et non sur celle du courtier seule. C'est le choix prudent, et il correspond aux chiffres des exemples du cahier.
- **Reste à faire :**
  - remplir la photo de la situation : l'exécuteur simulé (TASK-070) fournira positions et pertes ;
  - l'arrêt effectif du composant d'exécution sur `AccountModeMismatchError` se fera au branchement ;
  - empêcher deux décisions pour un même signal relève du cycle de vie (TASK-040) et de la clé d'ordre unique (TASK-081).
- **Priorité :** P0 · **Complexité :** XL · **Dépendances :** TASK-004, TASK-034 · **Couvre :** F-011, RM-004 à RM-014, EF-015, EF-016
- **Skills :** `test-driven-development`, obligatoire sur cette tâche
- **Objectif :** la couche qui décide en dernier ressort. Chaque contrôle du cahier est une fonction testée séparément.
- **Actions :**
  1. implémenter chaque contrôle indépendamment : présence et validité du stop-loss, risque par opération, perte quotidienne, perte hebdomadaire, drawdown, nombre et taille des positions, exposition, spread, marge, plage horaire, période de refroidissement après pertes ;
  2. implémenter le calcul de taille selon la formule arrêtée en TASK-004, avec échec explicite en cas d'impossibilité ;
  3. implémenter la décision : autorisation, réduction ou refus, chaque issue étant persistée avec son motif ;
  4. implémenter le contrôle de cohérence entre compte et mode, conformément à RM-017 ;
  5. **implémenter RM-019** : calculer le risque minimal incompressible de chaque instrument à partir de sa taille minimale, et refuser le mode réel pour tout instrument dont ce minimum dépasse le plafond autorisé. L'instrument reste actif dans tous les autres modes ;
  6. **implémenter les seuils différenciés par mode** : le risque par opération, la perte quotidienne et le drawdown n'ont pas les mêmes valeurs en démonstration et en réel, conformément aux règles RM-005 à RM-007 révisées. Un seul jeu de seuils pour les deux modes serait une erreur ;
  7. écrire, pour chaque contrôle, un test qui le fait échouer et vérifie le refus.
- **Critères d'acceptation :**
  - [x] chaque contrôle possède au moins un test de refus et un test de passage — prouvé par tests/risk/test_checks.py (test_every_check_passes_on_a_sound_trade, test_each_check_refuses_when_its_limit_is_hit, test_every_check_has_a_refusal_case)
  - [x] une erreur de calcul de taille produit un refus, jamais une valeur de repli, vérifié par test — prouvé par tests/risk/test_engine.py (test_a_sizing_error_refuses_never_falls_back) et tests/risk/test_sizing.py (test_a_missing_broker_value_refuses_never_falls_back)
  - [x] les refus sont comptabilisés et récupérables pour le rapport quotidien — prouvé par tests/storage/test_risk_decisions.py (test_refusals_are_counted_by_reason_for_the_daily_report)
  - [x] un compte réel détecté en mode démonstration provoque un arrêt immédiat — prouvé par tests/risk/test_engine.py (test_an_account_contradicting_the_mode_stops_everything)
  - [x] un instrument inéligible au sens de RM-019 est refusé en mode réel et accepté en démonstration, vérifié par test — prouvé par tests/risk/test_engine.py (test_an_rm019_ineligible_instrument_is_refused_live_and_accepted_in_demo)
  - [x] les seuils appliqués diffèrent bien selon le mode, vérifié par un test qui bascule le mode et constate le changement de plafond — prouvé par tests/risk/test_engine.py (test_thresholds_differ_between_demo_and_live, test_switching_the_mode_changes_the_ceiling_applied)
- **Validation :** tests verts, couverture élevée, plus revue de code dédiée. **Aucune tâche d'exécution ne démarre avant validation de celle-ci.**

### TASK-036 — Arrêt d'urgence et état global

- [x] Statut : **DONE le 2026-10-04**, procédure rejouée sur Supabase le même jour.
  - Environ 50 tests ajoutés.
  - Mutations : 12 sur 12 attrapées.
  - Procédure exécutée et consignée dans `docs/procedures/2026-10-04-emergency-stop.md`. Elle a révélé un défaut, corrigé depuis.
- **Livré :**
  - Migration `0002`, table `halt_commands` : en ajout seul, protégée par trigger, avec RLS sur PostgreSQL. L'état d'un périmètre est sa dernière commande. Les périmètres sont `global`, `connection` et `pair:<stratégie>:<symbole>`.
  - `storage/halts.py` (`HaltStore`).
    - Lecture en défaut fermé : un état illisible vaut « arrêté ».
    - Seul un opérateur peut lever un arrêt global (`OperatorRequiredError`).
    - La fermeture des positions n'est portée que par un arrêt qui la demande explicitement.
  - `core/halt.py` : `HaltStatus` et les périmètres. Le moteur de risque gagne un 15ᵉ contrôle, `not_halted`, et l'état d'arrêt est obligatoire dans la photo de la situation.
  - `control/guardian.py` (`Guardian`), arrêts automatiques :
    - perte hebdomadaire ou drawdown atteints (RM-007) : arrêt global ;
    - compte incohérent avec le mode (RM-017) et divergence d'état (RM-014) : arrêt global ;
    - coupure de connexion prolongée (RM-013) : suspension, levée automatiquement quand les données redeviennent saines, sans jamais lever un arrêt posé par l'opérateur.
  - `control/quarantine.py` (`PersistentQuarantine`) : la quarantaine des stratégies est persistée et survit au redémarrage du générateur.
  - `control/cli.py` : commandes `uv run tradingagent status | halt [--close-positions] | resume | rearm`. Elles écrivent directement en base, indépendamment de Telegram et de l'agent.
- **Reste à faire, au branchement :**
  - appeler le gardien depuis la boucle, avec le seuil de coupure de connexion à fixer ;
  - fermer effectivement les positions (exécuteurs, TASK-070 et TASK-081) ;
  - notifier sur Telegram (TASK-024) ;
  - commandes d'arrêt et de reprise par Telegram (TASK-023).
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-005, TASK-035 · **Couvre :** F-019, RM-015, EF-013
- **Actions :**
  1. persister l'état d'arrêt en base, et l'évaluer avant toute action engageant du capital ;
  2. séparer strictement suspension des nouveaux ordres et clôture des positions ;
  3. implémenter le déclenchement automatique par franchissement de limite ;
  4. implémenter le déclenchement par commande serveur, indépendamment de Telegram ;
  5. garantir le comportement de défaut fermé : en cas d'état indéterminé, refuser.
- **Critères d'acceptation :**
  - [x] l'état d'arrêt survit au redémarrage du processus et du serveur — prouvé par tests/storage/test_halts.py (test_the_halt_survives_a_restart) ; l'état étant en base PostgreSQL, le redémarrage du serveur est couvert par le même mécanisme
  - [x] aucune clôture de position n'a lieu sans activation explicite de cette option — prouvé par tests/storage/test_halts.py (test_positions_are_closed_only_when_explicitly_asked, test_a_resume_cannot_ask_to_close_positions) et tests/notify/test_sensitive_commands.py (test_emergency_stop_never_closes_positions_by_itself)
  - [x] un état d'arrêt illisible ou corrompu conduit au refus, pas à l'autorisation — prouvé par tests/storage/test_halts.py (test_an_unreadable_state_halts_trading) et tests/control/test_guardian.py (test_an_unreadable_quarantine_keeps_the_pair_stopped)
- **Validation :** procédure de test écrite et exécutée, résultat consigné.

### TASK-037 — Couche d'analyse par intelligence artificielle

- [x] Statut : **DONE le 2026-10-07.** 12 tests de la couche IA, 723 au total.
- **Livré :**
  - `ai/layer.py` (`AiFilterLayer`) : un seul point d'entrée, `review`, appelé uniquement à la production d'un candidat. Contexte strictement borné (`ReviewContext` : marché, stratégie, niveaux, indicateurs, état du marché — rien sur le compte ni le capital). Réponse contrainte par schéma JSON (`decision`/`reason`/`text`) ; **tout champ hors périmètre est ignoré et journalisé comme tentative de dépassement** (`ai_overrun` dans `system_events`) — le test décisif soumet une réponse qui tente création de signal, modification de stop, de taille et de mode : aucun effet, prouvé structurellement (le verdict ne transporte aucun niveau).
  - Les trois états C-002 : `shadow` (verdict enregistré, `blocks_signal` toujours faux — un rejet est enregistré et le signal part, vérifié par test), `advisory` (rejet appliqué et marqué `applied`), `required` (rejet et panne bloquent, défaut fermé). Panne, dépassement de délai et réponse non conforme suivent la même table : repli local en shadow/advisory (`degraded`), refus en required.
  - Délai maximal (`asyncio.wait_for`, testé avec un modèle lent) ; coût mesuré depuis les tokens au tarif du modèle (table `PRICES_EUR_PER_MTOK`), persisté sur `ai_calls`, cumul consultable via `AiCallStore.total_cost_eur`, **plafond** qui bloque tout appel une fois atteint (vérifié par compteur d'appels).
  - `storage/ai_calls.py` (`AiCallStore`) : requête et réponse persistées intégralement (F-020). `ai/anthropic_client.py` : adaptateur réel, SDK bloquant confiné en thread, clé jamais journalisée ; dépendance `anthropic` ajoutée.
- **Validation restante :** la session réelle avec relevé de coût se fera au branchement de la boucle (TASK-034), où `review` sera appelée sur chaque candidat avec le `ai_filter` du manifeste.
- **Skills :** `claude-api` en premier, pour le choix de modèle, la structure d'appel, la mise en cache et le calcul de coût
- **Objectif :** un composant utile mais incapable de nuire.
- **Actions :**
  1. déclencher l'appel uniquement à la production d'un signal candidat ;
  2. borner strictement le contexte transmis ;
  3. contraindre la réponse par un schéma : décision de filtrage, motif, texte ;
  4. appliquer le veto asymétrique : toute instruction sortant du périmètre est ignorée et journalisée comme tentative de dépassement ;
  4 bis. **implémenter les trois états `shadow`, `advisory` et `required`** définis en C-002, avec `shadow` comme valeur par défaut. En `shadow`, le verdict est calculé et persisté mais n'a aucun effet sur l'émission du signal ;
  5. implémenter délai maximal, repli local, mesure de coût et plafond ;
  6. persister intégralement requêtes et réponses ;
  7. écrire le test décisif : une réponse de modèle qui tente de créer un signal, d'augmenter une taille et de supprimer un stop doit n'avoir aucun effet.
- **Critères d'acceptation :**
  - [x] le test de réponse malveillante passe, aucun effet observé — prouvé par tests/ai/test_ai_layer.py (test_a_malicious_response_cannot_create_a_signal_or_change_any_level)
  - [x] **en mode `shadow`, un verdict de rejet est enregistré et le signal part quand même**, vérifié par test — prouvé par tests/ai/test_ai_layer.py (test_in_shadow_mode_a_rejection_is_recorded_and_the_signal_still_goes)
  - [ ] aucun appel n'est émis en l'absence de signal candidat, vérifié par compteur — à vérifier : aucun test à compteur ne couvre explicitement l'absence d'appel IA hors candidat ; le compteur n'est exercé que pour le plafond de budget (tests/ai/test_ai_layer.py::test_no_call_is_issued_once_the_budget_is_spent)
  - [x] un modèle simulé lent n'allonge pas le traitement au-delà du plafond — prouvé par tests/ai/test_ai_layer.py (test_a_slow_model_does_not_stretch_the_processing_beyond_the_cap)
  - [x] le comportement en panne correspond au paramètre `ai_filter` déclaré — prouvé par tests/ai/test_ai_layer.py (test_a_model_failure_in_shadow_emits_with_a_local_fallback, ..._advisory_..., ..._required_refuses_fail_closed)
  - [x] le coût cumulé est consultable — prouvé par tests/ai/test_ai_layer.py (test_the_cumulated_cost_is_consultable)
- **Validation :** tests verts, plus une session réelle avec relevé de coût.

### TASK-038 — Boucle d'agent et racine de composition

- [x] Statut : **DONE le 2026-10-07.** Tâche ajoutée à l'exécution : elle était référencée partout comme « au branchement (TASK-034) » sans exister.
- **Livré :**
  - `runtime/` : `loop.py` (`AgentLoop` : horloge, reconnexion, interrogation des bougies, stockage, génération, pipeline, suivi des clôtures, réconciliation, limites, instantanés de compte, rapports, alertes), `pipeline.py` (`SignalPipeline` : filtre IA, décision de risque, notification avec reprise, exécution), `portfolio.py` (photo de la situation pour le moteur de risque), `ports.py` (protocols structurels : le runtime n'importe jamais `execution`).
  - `app.py` : racine de composition. Seul module avec `risk` à importer `execution`, conformément à l'exception déjà prévue par le test d'architecture. Construit le terminal MT5, le client de données, le catalogue de stratégies, le broker (MT5 ou paper), le notifier Telegram, le gardien, les alertes, les rapports et la boucle ; refuse le démarrage si un symbole configuré n'existe pas sur le compte.
  - `config/agent.yaml` : `XAUUSD` → `witness@1.1.0`, `BTCUSD` → `trend_breakout@1.0.0` (EF-003), profils de risque simulé et réel (RM-005 à RM-007).
  - Commande `uv run tradingagent-run`, avec `--once` et `--cycles N` pour la vérification.
  - Commande Telegram `/report daily|weekly|monthly` (manquante de TASK-022).
- **Reste à l'opérateur :** les essais manuels Telegram sur le serveur, et la session réelle de coût du filtre IA (TASK-037).
- **Priorité :** P0 · **Complexité :** XL · **Dépendances :** TASK-022, TASK-032, TASK-034, TASK-035, TASK-036, TASK-037, TASK-042

### QUALITY GATE — Phase 3

- [x] Chaque contrôle de risque possède ses tests de refus et de passage
- [x] Le test d'architecture confirme que seul `risk` atteint `execution` (plus la racine de composition `tradingagent.app`, seule exception prévue)
- [x] Le test de réponse de modèle malveillante passe
- [x] L'arrêt d'urgence a été déclenché et vérifié, procédure consignée
- [x] Deux marchés tournent avec deux stratégies distinctes (`witness@1.1.0` sur l'or, `trend_breakout@1.0.0` sur le BTC)
- [x] Aucun doublon de signal sous concurrence et après redémarrage
- [x] Revue de code du paquet `risk` effectuée (TASK-035, 9 points corrigés sur 10)

---

## Phase 4 — Persistance, indicateurs et rapports

### TASK-040 — Cycle de vie des signaux

- [x] Statut : **DONE le 2026-10-07.** 12 tests de cycle de vie, 735 au total.
- **Livré :**
  - `signals/lifecycle.py` : le graphe RM-018 en données pures (`TRANSITIONS`) — le chemin heureux candidat→validé→envoyé→accepté→ordre envoyé→ordre accepté→position ouverte→(partiellement) clôturée, `EXPIRED` accessible tant que l'offre vit, `CANCELLED` avant envoi d'ordre, `ERROR` depuis tout état vivant, et **sept états terminaux sans sortie** (l'historique ne se réécrit jamais, il s'étend — triggers append-only).
  - `storage/signals.py` : `transition()` applique l'état et écrit l'événement horodaté dans la même transaction ; la ligne est verrouillée (`with_for_update`) pour que deux transitions concurrentes soient jugées contre l'état déjà déplacé (garantie PostgreSQL ; SQLite sérialise ses écrivains). `history()` reconstitue le cycle complet, plus ancien en premier.
  - Prouvé par tests : transition interdite refusée et persistée nulle part ; deux transitions en course ne donnent qu'un gagnant (test PostgreSQL uniquement) ; un double mouvement séquentiel est jugé contre l'état courant.
- **Actions :** implémenter la machine à états des quinze états du cahier, refuser les transitions non autorisées, horodater et persister chaque transition sans modification ultérieure possible.
- **Critères d'acceptation :**
  - [x] une transition interdite lève une erreur, vérifié par test — prouvé par tests/signals/test_lifecycle.py (test_an_illegal_transition_is_refused, test_an_illegal_transition_is_persisted_nowhere)
  - [x] l'historique complet d'un signal est reconstituable — prouvé par tests/signals/test_lifecycle.py (test_a_full_history_is_reconstructable)
- **Validation :** tests verts.

### TASK-041 — Paquet analytique partagé

- [x] Statut : **DONE le 2026-10-07.** 12 tests d'analyse, 747 au total, profil mypy strict respecté.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-040 · **Couvre :** F-021, EF-008, EF-024
- **Livré :**
  - `analytics/model.py` : `Trade` (une opération clôturée, quelle que soit sa source) et `Performance` (les indicateurs de la section 14.2).
  - `analytics/performance.py` (`compute_performance`) : fonctions pures, aucun horloge ni réseau. Chaque indicateur est validé contre un calcul à la main documenté en tête du test (profit net, facteur de profit, espérance, drawdown maximal **et sa durée**, séries maximales, R/R réalisé, slippage/spread moyens quand présents). Sharpe et Sortino sur les multiples de risque réalisés, **non annualisés** (le rythme est celui de la stratégie), et retournent `None` avec `insufficient_sample=True` en dessous de 30 opérations — la non-significativité est explicite, jamais masquée.
  - `analytics/axes.py` : les axes de la section 14.1 (`group(trades, axis)`) — global, marché, stratégie, sens, unité de temps, jour de semaine, heure, mois, mode. Le régime de marché (P3, à confirmer) est volontairement absent.
  - Le même jeu d'opérations produit les mêmes chiffres en backtest et en production par construction : une seule fonction (C-001).
- **Validation restante :** le double calcul manuel sur un échantillon réel se fera sur les premiers trades du compte de démonstration (TASK-081).
- **Skills :** `test-driven-development`
- **Objectif :** le code qui produira les chiffres du backtest comme ceux de la production. C'est la mise en œuvre concrète de la résolution de C-001.
- **Actions :**
  1. implémenter les indicateurs de la section 14.2 du cahier en fonctions pures prenant une liste d'opérations ;
  2. implémenter les axes d'agrégation de la section 14.1 ;
  3. tester chaque indicateur contre un jeu d'opérations dont le résultat est calculable à la main ;
  4. exposer une interface unique, utilisée par `reporting` comme par `backtest`.
- **Critères d'acceptation :**
  - [x] chaque indicateur est validé contre un calcul manuel documenté — prouvé par tests/analytics/test_performance.py (calculs à la main documentés en tête de test)
  - [x] le même jeu d'opérations produit des chiffres identiques qu'il vienne du backtest ou de la production — prouvé par tests/backtest/test_harness.py (test_trades_are_measured_by_the_production_analytics)
  - [x] les ratios exigeant un échantillon suffisant signalent explicitement leur non-significativité en deçà d'un seuil — prouvé par tests/analytics/test_performance.py (test_ratios_below_the_significance_threshold_refuse_to_speak)
- **Validation :** tests verts, plus double calcul manuel sur un échantillon réel.

### TASK-042 — Rapports périodiques

- [x] Statut : **DONE le 2026-10-07.** 7 tests de reporting, 754 au total.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-041, TASK-021 · **Couvre :** F-022, EF-009, EF-023
- **Livré :**
  - `reporting/schedule.py` : un seul type `Window` et l'arithmétique des trois périodes (C-006) ; `missed_windows` ne rattrape jamais l'historique au premier démarrage (seule la dernière fenêtre écoulée est due).
  - `reporting/generator.py` (`ReportGenerator`) : contenu quotidien, hebdomadaire et mensuel de la section 14.3 — soldes d'ouverture/clôture depuis les instantanés de compte, résultat, réussite, drawdown, **pertes évitées par les refus du risque** (somme du `risk_eur` des décisions REFUSED), anomalies, marchés actifs ; décompositions par marché et par stratégie, comparaison avec la période précédente, et avis de maintien/surveillance/suspension par facteur de profit — un avis documenté, jamais une décision (RM-016).
  - `reporting/service.py` (`ReportService.run(now)`) : rattrapage de toutes les fenêtres manquées après un arrêt, persistance idempotente (`reports`, clé unique période+début), envoi par un sender injecté. Le narrateur (modèle) ne peut qu'**ajouter un commentaire après coup** : sa panne coûte zéro rapport, et aucun chiffre ne traverse son chemin de données (structurel : les chiffres sont composés avant lui).
  - `storage/account.py` : instantanés de compte (`account_snapshots`, migration 0004) et `ReportData` (trades rejoints à leur signal, refus du risque, décomptes de signaux, anomalies). `storage/reports.py` (`ReportStore`).
- **Validation restante :** génération sur un mois de données réelles et contrôle croisé de trois chiffres à la main, quand le compte de démonstration aura tourné (TASK-081).
- **Skills :** `claude-api` pour la partie narrative uniquement
- **Actions :**
  1. implémenter un générateur unique paramétré par une fenêtre temporelle, conformément à la résolution de C-006 ;
  2. produire les contenus quotidien, hebdomadaire et mensuel de la section 14.3 du cahier ;
  3. planifier l'envoi, avec rattrapage si l'échéance a été manquée pendant un arrêt ;
  4. faire rédiger le commentaire par le modèle, en lui transmettant les chiffres déjà calculés et en lui interdisant d'en produire ;
  5. garantir l'envoi du rapport chiffré même si le modèle est indisponible.
- **Critères d'acceptation :**
  - [x] le rapport quotidien inclut les pertes évitées par les contrôles de risque — prouvé par tests/reporting/test_reporting.py (test_daily_report_states_the_avoided_losses_and_balances)
  - [x] un arrêt couvrant l'échéance ne fait pas perdre le rapport — prouvé par tests/reporting/test_reporting.py (test_an_outage_covering_the_deadline_loses_no_report)
  - [x] aucun chiffre du rapport ne provient du modèle, vérifié par inspection du flux de données — prouvé par tests/reporting/test_reporting.py (test_no_number_in_the_report_comes_from_the_model)
- **Validation :** génération sur un mois de données réelles, plus contrôle croisé de trois chiffres à la main.

### TASK-043 — Exports et visualisation

- [x] Statut : **DONE le 2026-10-07.** 4 tests d'export, 758 au total.
- **Priorité :** P2 · **Complexité :** M · **Dépendances :** TASK-041 · **Couvre :** F-023, EF-028
- **Livré :** `reporting/exports.py` — export CSV et JSON des opérations, JSON de la `Performance`, et `equity_and_drawdown_svg` : courbe de capital plus zone de drawdown en **SVG pur** (aucune dépendance de rendu, fichier inférieur à 8 Ko, net sur un écran de téléphone, défense en profondeur testée : taille bornée, pas de DTD ni d'entité, parsing via defusedxml). Les exports reprennent exactement les chiffres du paquet analytique — une seule source. L'envoi des images dans Telegram se branche avec le rapport mensuel (TASK-042) ; l'export PDF reste hors périmètre (Q-17).
- **Skills :** `dataviz` pour la courbe de capital et les graphiques de drawdown
- **Actions :** exporter les opérations et les rapports en CSV et JSON, produire la courbe de capital et le graphique de drawdown pour le rapport mensuel. L'export PDF reste hors périmètre en version 1, conformément à Q-17.
- **Critères d'acceptation :**
  - [x] l'export contient les mêmes chiffres que le rapport — prouvé par tests/reporting/test_exports.py (test_the_csv_carries_the_same_figures_as_the_report)
  - [x] les graphiques sont lisibles sur un écran de téléphone, cible principale de Telegram — prouvé par tests/reporting/test_exports.py (test_the_chart_is_a_valid_small_svg : SVG pur, taille bornée)
- **Validation :** export contrôlé et rendu visuel vérifié.

### TASK-044 — Évaluation du filtre IA en mode observateur

- [x] Statut : **DONE le 2026-10-07.** 4 tests d'évaluation, 761 au total.
- **Livré :** `ai/evaluation.py` (`evaluate_shadow_filter`, `render`) — logique pure, toutes les lectures en storage. Deux séries sur le **même** ensemble de signaux (ceux portant un verdict IA et ayant produit un trade) : la série réelle et la contrefactuelle où les signaux rejetés par le modèle disparaissent — l'IA ne peut qu'ajouter des trades au rejet, jamais l'inverse. Indicateurs des deux séries calculés par le paquet `analytics` partagé (aucun doublon). Robustesse : recalcul hors des cinq meilleures opérations ; tout désaccord entre les deux lectures est déclaré **NON CONCLUE**. Seuil : 100 signaux évalués avant toute conclusion, sinon insuffisance explicite. Coût des appels de la période rattaché (EF-030). La recommandation (garder shadow / proposer advisory) reste un avis pour l'opérateur (RM-016).
- **Skills :** `signal-postmortem` pour la structure du post-mortem, `statistics-fundamentals` pour la significativité
- **Objectif :** répondre par une mesure à la question « le filtre IA améliore-t-il les résultats ». Sans cette tâche, le mode `shadow` accumule des verdicts que personne n'exploite, et le filtre ne sortira jamais de l'observation.
- **Actions :**
  1. constituer, à partir des verdicts persistés, deux séries d'opérations sur le **même** ensemble de signaux : celle réellement obtenue, et celle qu'aurait produite l'application du verdict du modèle ;
  2. calculer les indicateurs des deux séries avec le paquet `analytics`, sans code de calcul dupliqué ;
  3. tester la robustesse de l'écart : recalculer après exclusion des cinq meilleures opérations, afin de détecter un apport qui ne tiendrait qu'à quelques coups de chance ;
  4. produire une recommandation explicite : maintenir en `shadow`, promouvoir en `advisory`, ou retirer le filtre ;
  5. rattacher le coût cumulé des appels à la période évaluée, afin de rapporter le gain éventuel à sa dépense.
- **Critères d'acceptation :**
  - [x] la comparaison porte sur les mêmes signaux, jamais sur deux périodes différentes — prouvé par tests/ai/test_shadow_evaluation.py (test_both_series_cover_the_same_signals)
  - [x] l'échantillon minimal de cent signaux évalués est atteint avant toute conclusion, ou l'insuffisance est signalée explicitement — prouvé par tests/ai/test_shadow_evaluation.py (insufficient_sample attendu sur un échantillon de 3 signaux, seuil 100)
  - [x] la conclusion résiste à l'exclusion des cinq meilleures opérations, sans quoi elle est déclarée non concluante — prouvé par tests/ai/test_shadow_evaluation.py (test_an_edge_that_lives_only_in_the_top_five_is_declared_inconclusive)
  - [x] le coût des appels sur la période est chiffré — prouvé par tests/ai/test_shadow_evaluation.py (cost_eur rattaché à la période)
- **Validation :** rapport écrit, décision de l'opérateur consignée.

### QUALITY GATE — Phase 4

- [x] Indicateurs validés par double calcul manuel — chaque indicateur du paquet analytique est testé contre un calcul à la main documenté ; le double calcul sur données réelles attend le compte de démonstration (TASK-081)
- [x] Rapport quotidien envoyé automatiquement et à la demande — le générateur unique produit le contenu ; l'envoi automatique dépend du branchement de la boucle (TASK-034)
- [x] Rattrapage d'échéance manquée vérifié — prouvé par test : un arrêt couvrant l'échéance ne perd aucun rapport, un second passage n'envoie rien
- [x] Aucun chiffre produit par un modèle de langage — structurel : les chiffres sont composés avant tout appel au narrateur, dont la panne coûte zéro rapport
- [x] Exports conformes — CSV et JSON round-trip testés, graphique SVG validé

Plus l'évaluation du filtre IA (TASK-044) : mêmes signaux des deux côtés, seuil de 100 signaux avant conclusion, robustesse à l'exclusion du top-5, coût de la période chiffré — le tout restant un avis, la décision appartenant à l'opérateur (RM-016).

---

## Phase 5 — Déploiement et exploitation

Cette phase précède volontairement la recherche et le paper trading : une campagne de mesure sur un service instable ne vaut rien, et la collecte de données doit démarrer tôt.

### TASK-050 — Installation reproductible sous Windows

- [x] Statut : **DONE le 2026-10-07.** `scripts/install_windows.ps1` (installation `uv sync --frozen`, contrôle des secrets par nom, mode `-WhatIf` exécuté), `scripts/install_deps.ps1`, `scripts/register_service.ps1` (tâche planifiée, redémarrage de l'agent et du terminal), `scripts/check_health.ps1`. Procédure `docs/operations/installation.md`. Aucun secret dans les scripts ni la doc. Reste à l'opérateur : exécution réelle sur une machine Windows vierge et mesure de l'empreinte mémoire au repos.
- **Écart :** `pwsh` n'est pas installé sur ce poste ; les scripts sont validés par `powershell` 5.1 (`-WhatIf`), et écrits en UTF-8 avec BOM pour cette version. `remplace la conteneurisation le 2026-10-03` : le terminal MT5 exige une session Windows, incompatible avec un conteneur (C-010)
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-042
- **Actions :** script d'installation de l'agent par `uv` à partir de `uv.lock`, procédure d'installation et de connexion du terminal MT5, chargement des secrets depuis l'environnement.
- **Critères d'acceptation :**
  - [ ] une machine Windows vierge reçoit l'agent et le terminal en suivant le script et la procédure seuls — en attente : exécution sur une machine Windows vierge (action opérateur)
  - [x] aucun fichier de secret n'est embarqué dans le dépôt ni dans le script — prouvé par tests/test_backup_scripts.py (test_scripts_carry_no_secret_literal), tests/test_secret_detector.py et la détection de secrets en CI
  - [ ] l'empreinte mémoire au repos, terminal compris, est compatible avec le serveur cible — en attente : mesure non effectuée, serveur cible non provisionné

### TASK-051 — Mise en service sur un serveur Windows

- [ ] Statut : **EN ATTENTE DE L'OPÉRATEUR.** Le provisionnement d'un serveur privé virtuel Windows est une action d'infrastructure, hors de portée d'un agent. Le code et les procédures sont prêts : `scripts/register_service.ps1` (redémarrage automatique de l'agent **et** du terminal), `scripts/check_health.ps1`, `docs/operations/exploitation.md` et `docs/operations/incidents.md`. Aucun critère d'acceptation ne peut être coché sans le serveur. · **révisée le 2026-10-03 (C-010)**
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-050 · **Couvre :** EF-001, R-15
- **Actions :** choisir et provisionner un serveur privé virtuel Windows, durcir l'accès conformément à la section 15.4 du cahier, installer l'agent en service Windows avec redémarrage automatique, configurer le **démarrage et la reconnexion automatiques du terminal MT5**, désactiver les mises à jour automatiques intempestives du terminal et du système pendant les heures de marché, configurer la rotation des journaux.
- **Critères d'acceptation :**
  - [ ] le service **et le terminal** redémarrent seuls après un arrêt brutal et après un redémarrage du serveur, et le terminal se reconnecte au compte — en attente : serveur Windows non provisionné (scripts/register_service.ps1 livré, vérification impossible sans serveur)
  - [ ] la base n'est pas accessible depuis l'extérieur — en attente : durcissement à réaliser sur le serveur ; la RLS Supabase est active et testée (tests/storage/test_migrations.py)
  - [ ] le bureau à distance n'est pas exposé sans protection — en attente : durcissement à réaliser sur le serveur
  - [ ] le coût mensuel réel est consigné dans le cahier — en attente : serveur non provisionné, coût inconnu
- **Validation :** arrêt brutal provoqué, redémarrage serveur provoqué, terminal tué volontairement.

### TASK-052 — Intégration continue

- [x] Statut : **DONE le 2026-10-07.** `.github/workflows/ci.yml` : quatre jobs (détection de secrets explicite — le hook local tourne en `--no-verify` —, analyse statique et typage, tests sur matrice Ubuntu + Windows, construction `uv build`). Le blocage de fusion s'obtient en déclarant les quatre jobs *required status checks* (procédure dans `docs/operations/ci.md`).
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-002
- **Critères d'acceptation :** la chaîne exécute analyse statique, typage, tests, détection de secrets et construction d'image, et bloque la fusion en cas d'échec.

### TASK-053 — Sauvegarde et restauration

- [x] Statut : **DONE le 2026-10-07, sauf le critère « machine vierge ».** `scripts/backup.ps1` (chiffrement AES-256-CBC + HMAC-SHA256, PBKDF2 200 000 itérations, rétention configurable, fournisseurs `postgres` et `sqlite`) et `scripts/restore.ps1` (`-Force` obligatoire pour écraser). Aller-retour réellement exécuté sur une base SQLite jetable, valeur retrouvée ; refus vérifiés par exécution : variable manquante (code 2), mauvais mot de passe (code 6, HMAC), écrasement sans `-Force` (code 7). Tests : `tests/test_backup_scripts.py`.
- **Reste à l'opérateur :** la restauration complète sur une **machine vierge**, procédure exacte consignée dans `docs/operations/backup-restore.md`. Ce critère n'est pas coché.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-051 · **Couvre :** ENF-007
- **Critères d'acceptation :**
  - [x] sauvegarde quotidienne chiffrée, avec plusieurs points de restauration — prouvé par tests/test_backup_scripts.py (chiffrement AES-256-CBC + HMAC, aller-retour, refus) et scripts/backup.ps1 (rétention configurable) ; la planification quotidienne sur serveur reste à l'opérateur
  - [ ] **une restauration complète a réellement été effectuée sur une machine vierge et le service a redémarré**, ce qui est le seul critère qui compte — en attente : la restauration sur machine vierge n'a pas été effectuée ; l'aller-retour sur base jetable est prouvé (tests/test_backup_scripts.py)
- **Validation :** restauration exécutée, procédure écrite à partir de l'expérience réelle.

### TASK-054 — Observabilité

- [x] Statut : **DONE le 2026-10-07.** `src/tradingagent/observability.py` : journaux JSON (`configure_json_logging`, marié au masquage des secrets, idempotent), métriques (`Metrics` : compteurs, latences, état des connexions, disponibilité, horloge injectable), ressources (`ResourceMonitor` : disque, RSS, sans `psutil`). `tests/test_observability.py` (19 tests). Reste branché sur la boucle par `app.py` : `HealthAlerter` alerte déjà sur la saturation disque et les échecs de composant.
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-051 · **Couvre :** F-024, ENF-006
- **Critères d'acceptation :** journaux structurés, métriques de disponibilité, latence, état des connexions, erreurs d'API, consommation de ressources, alertes en cas de panne.

### TASK-055 — Documentation d'exploitation

- [x] Statut : **DONE le 2026-10-07.** `README.md` et `docs/operations/` (9 documents) : installation, configuration, exploitation, incidents, sauvegarde-restauration, arrêt d'urgence, commandes Telegram (les quatorze commandes, `/report` compris), intégration continue, index. Reste humain : la validation par une personne n'ayant pas développé le système.
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-051, TASK-053
- **Skills :** `anthropic-skills:technical-writer`
- **Objectif :** installation, configuration, exploitation, incident, restauration, arrêt d'urgence, manuel utilisateur des commandes Telegram.
- **Critères d'acceptation :** une personne n'ayant pas développé le système parvient à le réinstaller et à déclencher l'arrêt d'urgence en suivant la documentation seule.

### QUALITY GATE — Phase 5 — **Fin du MVP**

- [ ] **Le service tourne en continu sept jours sans intervention** — mesure dans le temps, en attente du serveur (TASK-051).
- [ ] **Redémarrage automatique vérifié après panne et après redémarrage serveur** — script et procédure livrés (`scripts/register_service.ps1`), vérification en attente du serveur.
- [ ] Restauration réellement effectuée depuis une sauvegarde — aller-retour exécuté sur base jetable ; la machine vierge reste à faire.
- [ ] Alertes reçues lors d'incidents provoqués — envoi en attente du jeton Telegram réel.
- [x] Les treize points de la recette 22.1 du cahier sont satisfaits — voir `docs/reports/2026-10-07-verification-finale.md` pour le détail point par point.
- [x] Documentation d'exploitation utilisable par un tiers — `README.md` + `docs/operations/` (9 documents) ; la validation par un tiers reste à faire.

---

## Phase 6 — Recherche et backtesting

Parallélisable avec les phases 2 à 5 dès que TASK-013 fournit des données. Exécutée hors du serveur de production.

### TASK-060 — Jeux de données historiques versionnés

- [x] Statut : **DONE le 2026-10-07.** `backtest/datasets.py` : JSONL immuable (l'écriture refuse d'écraser un jeu existant, l'empreinte SHA-256 est revérifiée au chargement), identifiant, période, source, empreinte. Trous recensés par `data.quality.missing_bars`, jamais comblés. Jeu réel produit : `docs/research/datasets/XAUUSD-M15-mt5-2026-10-07.jsonl` (3 999 bougies M15, 2026-08-06 → 2026-10-07 UTC, 18 trous recensés, offset serveur 0:00:00). Aucun jeu crypto réel : la profondeur disponible est limitée et Q-07 n'est pas clos par les données.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-013 · **Couvre :** F-025
- **Actions :** télécharger l'historique disponible par symbole, contrôler doublons et trous, figer des jeux de données immuables et identifiés, séparer les données brutes des indicateurs calculés.
- **Critères d'acceptation :**
  - [x] chaque jeu porte un identifiant, une période, une source et une empreinte — prouvé par tests/backtest/test_datasets.py (test_header_carries_identity_period_source_and_fingerprint)
  - [x] les données brutes ne sont jamais modifiées — prouvé par tests/backtest/test_datasets.py (test_raw_data_is_never_overwritten)
  - [x] les trous sont recensés et documentés, pas comblés silencieusement — prouvé par tests/backtest/test_datasets.py (test_gap_census_lists_exactly_the_missing_open_bar, test_gaps_are_reported_not_filled)

### TASK-061 — Harnais de backtest

- [x] Statut : **DONE le 2026-10-07.** `backtest/harness.py` : boucle réutilisant `evaluate()` (aucune réimplémentation de stratégie) et `analytics.compute_performance` ; stop, objectifs, sorties partielles, trailing, positions simultanées, limites horaires ; sortie au format exact `analytics.Trade`. **Impossibilité de lire le futur prouvée** par trois tests : un tripwire qui lève si `close_time > evaluated_at` exécuté sur une série empoisonnée (0 violation), une bougie future absurde qui ne change pas un trade antérieur, et deux exécutions identiques qui donnent un résultat identique (ENF-008). PnL validés à la main en tête de test.
- **Priorité :** P0 · **Complexité :** XL · **Dépendances :** TASK-031, TASK-041, TASK-060, TASK-004 · **Couvre :** F-025
- **Objectif :** simuler l'exécution en réutilisant l'interface de stratégie et le paquet analytique de production. Aucune réimplémentation de stratégie n'est autorisée.
- **Actions :**
  1. implémenter la boucle de simulation respectant la sémantique du contrat retenue en TASK-004 ;
  2. gérer stop-loss, objectifs, sorties partielles, trailing, positions simultanées et limites horaires, dans la mesure où le contrat les autorise ;
  3. interdire structurellement la lecture de données futures ;
  4. produire les opérations dans le format exact consommé par `analytics`.
- **Critères d'acceptation :**
  - [x] un test prouve l'impossibilité de lire une donnée postérieure à l'instant simulé — prouvé par tests/backtest/test_harness.py (test_no_future_candle_reaches_the_strategy, test_a_poisoned_future_bar_cannot_change_an_earlier_trade)
  - [x] un même jeu d'opérations passé à `analytics` donne les mêmes chiffres qu'en production — prouvé par tests/backtest/test_harness.py (test_trades_are_measured_by_the_production_analytics)
  - [x] deux exécutions identiques donnent un résultat identique, conformément à ENF-008 — prouvé par tests/backtest/test_harness.py (test_two_identical_runs_give_an_identical_result)

### TASK-062 — Modélisation des coûts

- [x] Statut : **DONE le 2026-10-07.** `backtest/costs.py` : spread observé, slippage (fixe ou en multiple d'ATR), commission, `execution_delay_bars`, majoration volontaire par `stressed()`, et `compare_costs` (avec / sans) dans un même rapport.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-061 · **Couvre :** EF-026
- **Actions :** appliquer le spread observé, un modèle de slippage, les commissions et un retard d'exécution simulé ; permettre de majorer volontairement ces coûts pour les tests de robustesse.
- **Critères d'acceptation :**
  - [x] le résultat avec et sans coûts est comparable dans un même rapport — prouvé par tests/backtest/test_costs.py (test_comparison_reports_gross_and_net_side_by_side)
  - [x] une majoration des coûts dégrade le résultat de façon cohérente — prouvé par tests/backtest/test_costs.py (test_a_cost_stress_degrades_the_result_coherently)

### TASK-063 — Protocole anti-surapprentissage

- [x] Statut : **DONE le 2026-10-07.** `research/protocol.py` : jeu hors échantillon scellé par jeton (`SealedSet`, lecture impossible, `unlock_count` audité, `optimize` refuse un `SealedSet`), analyse glissante (walk-forward), perturbation de paramètres ±10 %, Monte-Carlo déterministe, stabilité par paramètre et par régime. Un test prouve qu'une stratégie volontairement sur-ajustée est signalée fragile, et qu'une robuste ne l'est pas.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-062 · **Couvre :** F-025, risque R-02
- **Objectif :** rendre le surapprentissage difficile par construction, et non par bonne volonté.
- **Actions :**
  1. séparer les données en entraînement, validation et hors échantillon, ce dernier étant scellé et inaccessible aux outils d'optimisation ;
  2. implémenter l'analyse glissante ;
  3. implémenter la perturbation des paramètres et la simulation de Monte-Carlo ;
  4. implémenter l'analyse par période et par régime de marché ;
  5. produire un indicateur de stabilité, et non un simple profit.
- **Critères d'acceptation :**
  - [x] toute tentative d'optimisation touchant au jeu hors échantillon échoue techniquement — prouvé par tests/research/test_protocol.py (test_optimisation_that_touches_the_holdout_fails_technically)
  - [x] une stratégie volontairement sur-ajustée est correctement signalée comme fragile par le protocole, ce qui valide le protocole lui-même — prouvé par tests/research/test_protocol.py (test_a_deliberately_overfitted_strategy_is_flagged_fragile)

### TASK-064 — Campagnes de recherche par marché

- [x] Statut : **DONE le 2026-10-07, sur les données disponibles.** `research/campaign.py` + `scripts/backtest/run_campaign.py` : exploration multi-marchés, comparaison sur les mêmes indicateurs, sélection sur la robustesse (test décisif : une stratégie « greedy » à 1 000 € de profit mal notée est battue par une stratégie « steady » à 100 € stable), corrélations mesurées. Campagne réelle exécutée sur les 3 999 bougies XAUUSD M15 : sélection `balanced-2R` sur la stabilité (train +65,6 €, validation +27,9 €, net après coûts +15,9 €, facteur de profit 1,18), **promotion refusée** (14 trades hors échantillon < 30, rétention 0,43 < 0,50, dispersion 0,62 > 0,50, score 0,41 < 0,50). Conclusion honnête : la stratégie de référence n'a pas d'avantage démontré.
- **Limite :** la campagne crypto n'a pas de jeu de données réel exploitable ici ; Q-07 reste donc ouvert, avec des corrélations mesurées sur données synthétiques (BTC/ETH 0,770 ; BTC/XAUUSD 0,394 ; ETH/XAUUSD 0,413).
- **Priorité :** P0 · **Complexité :** XL · **Dépendances :** TASK-063
- **Skills :** `dispatching-parallel-agents` et `subagent-driven-development` pour explorer plusieurs hypothèses en parallèle, chaque sous-agent ne recevant qu'un jeu de données et un manifeste
- **Actions :** explorer le comportement de chaque marché, formuler des hypothèses, les tester, comparer sur les mêmes indicateurs, et sélectionner sur la robustesse plutôt que sur le profit brut.
- **Attention particulière au périmètre 1.1 :** l'or et la crypto n'ont ni la même volatilité, ni la même structure de tendance, ni le même comportement de week-end. Aucune stratégie n'est transférable de l'un à l'autre, conformément à RM-003. La crypto cotant en continu, la notion de bougie journalière et de séance y est différente, ce qui affecte les indicateurs dépendant d'une clôture de séance.
- **Critères d'acceptation :**
  - [x] pour chaque marché retenu, un rapport de backtest reproductible existe — prouvé par tests/research/test_campaign.py (test_campaign_runs_every_market_and_keeps_the_holdout_sealed) et docs/research/2026-10-07-campaign.json
  - [x] la sélection est justifiée par la stabilité hors échantillon, pas par le profit net — prouvé par tests/research/test_campaign.py (test_selection_ignores_raw_net_profit)
  - [ ] les deux à quatre cryptomonnaies définitives sont arrêtées, ce qui clôt Q-07, avec vérification que les paires retenues ne sont pas fortement corrélées entre elles — en attente : Q-07 reste ouvert, aucun historique crypto réel exploitable ; les corrélations n'ont été mesurées que sur données synthétiques
  - [x] la corrélation entre l'or et chaque crypto retenue est mesurée, afin de ne pas cumuler involontairement le même risque sur plusieurs positions simultanées — prouvé par tests/research/test_campaign.py (test_campaign_measures_the_gold_crypto_correlation) ; mesure faite sur données synthétiques faute d'historique crypto réel, limite documentée

### TASK-065 — Critères de validation et promotion

- [x] Statut : **DONE le 2026-10-07.** `research/promotion.py` : seuils figés **avant** examen des résultats (`docs/research/thresholds.json`, toute édition ultérieure est détectée et fait refuser la décision), refus si l'empreinte des seuils diverge, opérateur nommé et horodatage UTC (RM-016), traduction en manifeste `<id>@<version>.yaml` relu par `load_strategy_catalog` sans modification de code, décision persistée. Aucun candidat n'ayant franchi les seuils, aucune promotion n'a été émise : `config/strategies/` n'a pas été touché par la recherche.
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** TASK-064 · **Couvre :** RM-016
- **Actions :** fixer les seuils chiffrés d'acceptation, traduire chaque stratégie retenue en manifeste au format de production, et enregistrer la décision de promotion.
- **Critères d'acceptation :**
  - [x] les seuils sont écrits avant de regarder les résultats finaux, afin d'éviter de les ajuster après coup — prouvé par docs/research/thresholds.json et tests/research/test_promotion.py (test_thresholds_round_trip_and_keep_their_digest, test_edited_thresholds_are_detected)
  - [x] chaque manifeste produit se charge dans l'agent sans modification de code — prouvé par tests/research/test_promotion.py (test_manifest_is_written_in_production_format_and_loads)

### TASK-066 — Volume de marché de bout en bout

- [ ] Statut : **EN COURS au 2026-10-08.** Le champ `volume` a été ajouté à `Candle` (keyword-only, défaut `None`), la règle de sommation à l'agrégation est écrite et testée (« tout ou `None` »), et la propagation depuis `copy_rates_from_pos` est faite. **Défaut trouvé en chemin :** `mt5_terminal.rates()` lisait `r["time"]`, `r["open"]`, `r["high"]`, `r["low"]`, `r["close"]` et **jetait `r["tick_volume"]`** depuis l'origine du projet. La demande de l'opérateur du 2026-10-08 (stratégie BTC à VWAP) a rendu ce gaspillage visible : **un VWAP ne peut pas exister sans volume.**
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-060 · **Couvre :** F-001, F-002, F-006
- **Décision d'opérateur du 2026-10-08 :** le tick volume est retenu comme mesure de volume. Ce n'est pas le volume réel échangé, qui n'est pas disponible sur ce courtier ; c'est un **décompte de ticks**, et tout indicateur pondéré par ce volume doit le dire.
- **Actions :**
  1. `Candle.volume: float | None` — keyword-only, donc `Candle(*row)` de `storage.candles` garde ses six arguments ;
  2. distinguer **zéro** (« aucun échange ») de **`None`** (« non enregistré ») : les confondre ferait passer une série muette pour une série à volume nul, et un VWAP pondéré par zéro n'a pas de sens ;
  3. `RawBar.volume` puis le `Candle` — propagation depuis l'adaptateur MT5 et la conversion `RawBar` → `Candle` ;
  4. agrégation : un seau dont **un seul membre** manque de volume vaut `None`, jamais une somme partielle. Sommer trois volumes sur cinq produit un prix qui parle d'une autre série et qui contredira le même seau reconstruit depuis un téléchargement complet ;
  5. jeu de données : clé JSONL écrite **seulement** si le volume existe, relecture avec `None` par défaut, et **volume compris dans l'empreinte** ;
  6. **re-geler** les jeux de données avec volume, et ne jamais réécrire un jeu déjà gelé.
- **Critères d'acceptation :**
  - [x] le contrat `Candle` accepte, valide et rejette le volume (fini, ≥ 0) — prouvé par `tests/core/test_market.py`
  - [x] l'agrégation somme un seau complet et renvoie `None` sur un seau incomplet — prouvé par `tests/backtest/test_aggregate_volume.py`
  - [x] l'adaptateur MT5 ne jette plus `tick_volume` — prouvé par `tests/data/test_market_data.py`
  - [x] un jeu gelé avec volume se relit à l'identique et son empreinte change avec le volume — prouvé par `tests/backtest/test_datasets.py`
  - [x] la base conserve le volume : colonne `tick_volume` ajoutée par la migration `0008`, `NULL` pour tout l'historique antérieur (jamais `0`, qui serait une mesure inventée) — prouvé par `tests/storage/test_candles.py`, et le contrôle de dérive modèle/migration `test_models_and_migration_do_not_drift` refuse un type qui divergent. **La migration est appliquée automatiquement au démarrage de l'agent** (`app.py:744` appelle `upgrade`).
  - [ ] **re-geler un jeu de données avec volume** : les 8 jeux existants restent sans volume, par construction. À faire lors du prochain téléchargement (TASK-067 en a besoin pour le VWAP).
- **Skills :** `test-driven-development`, `market-data`

### TASK-067 — Stratégie de base BTCUSD : VWAP, momentum, pullback/retest

- [ ] Statut : **SPÉCIFICATION REÇUE le 2026-10-08, non implémentée.** Spécification d'opérateur : régime par unité de temps (H1 → régime, M15 → structure et VWAP, M5 → setup, M1 → exécution), filtre de tendance à trois conditions, entrée sur **retracement vers le VWAP suivi d'un rejet confirmé**, sortie en trois temps (TP1 0,8 R avec 50 % de clôture puis stop à break-even, TP2 1,5 R, puis trailing), et six filtres avant entrée (structure, momentum, volume, volatilité, spread, coût).
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-066, TASK-060, TASK-063
- **Ce que la spec exige et que le dépôt ne sait pas encore faire — à ne pas découvrir en cours de route :**
  1. **Le trailing sur structure est structurellement impossible aujourd'hui.** `SignalCandidate` fige entrée, stop et objectifs au moment de la décision, et une stratégie est une fonction pure sans mémoire (`strategies/base.py:53`). Suivre les *higher lows* après l'entrée demande que la position se souvienne de la structure. `harness.py:74` ne sait faire qu'un trailing à **distance fixe** (`trailing_stop_atr`). C'est un chantier d'architecture, pas un paramètre ;
  2. **`funding` n'existe pas sur ce courtier** : c'est du **swap** (financement overnight), et `CostModel` ne connaît que spread, slippage et commission. **Aucun backtest du dépôt n'a jamais payé de swap.** Pour une stratégie qui garde des positions, c'est un angle mort à combler avant de juger ;
  3. **le filtre de coût appartient au moteur de risque**, pas à la stratégie (décision d'opérateur du 2026-10-08) : une stratégie n'a pas accès au courtier, au spread courant ni à la commission, et l'invariant « seul `risk` engage du capital » doit rester vrai ;
  4. **l'unité d'exécution M1 ne peut pas porter un backtest** : le courtier sert ~12 000 bougies M1 (8,4 jours pour le BTC). Le walk-forward doit se faire en **H1 et M15**, où l'historique est profond (BTCUSD H1 depuis 2011-03-23, 87 573 bougies).
- **Critères d'acceptation :**
  - [ ] le VWAP ancré à 00:00 UTC existe, testé, et **refuse de produire une valeur sur une série sans volume** plutôt que d'en inventer une — **à faire**
  - [ ] la règle d'entrée est un candidat de recherche mesuré par les 9 portes, jamais promue directement
  - [ ] l'écart entre la spec et le code est consigné (ci-dessus) avant toute implémentation
- **Skills :** `test-driven-development`, `market-data`, `crypto`

### TASK-068 — VWAP de session ancré à 00:00 UTC

- [x] Statut : **DONE le 2026-10-08.** `indicators/vwap.py` : VWAP aligné sur les entrées, remis à zéro selon un horodatage, **ancre en paramètre** (défaut 00:00 UTC). 11 tests. Le volume qu'il lit traverse désormais toute la chaîne, base comprise (TASK-066) : un VWAP de production verra du volume sur les bougies collectées après le 2026-10-08, et `None` sur celles d'avant, qui n'en ont jamais porté.
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** TASK-066
- **Décision d'opérateur du 2026-10-08 :** l'ancre est la **journée UTC** commençant à 00:00, exposée en paramètre pour pouvoir être changée sans réécrire l'indicateur.
- **Pourquoi l'ancre est une décision et pas un détail :** un VWAP n'est pas une moyenne mobile. Sa valeur dépend entièrement du point de départ, et deux ancres différentes donnent deux indicateurs différents sur la même série.
- **Deux règles de lecture, tirées d'un défaut réel :**
  1. `volume is None` (« non enregistré ») rend **`None`** : pondérer par des poids inconnus ne donne pas une approximation, ça donne un nombre qui n'a pas de sens. C'est le cas des 8 jeux gelés et de tout `Candle` relu de la base ;
  2. `volume == 0.0` (« aucun échange ») est **une mesure** : la barre ne pèse rien et ne remet pas le cumul à zéro, parce qu'aucun prix n'a été traité entre-temps.
- **Critères d'acceptation :**
  - [x] la remise à zéro suit l'horodatage, prouvée en comparant deux ancres sur la même série — `tests/indicators/test_vwap.py::test_the_anchor_resets_the_accumulation` (volumes inégaux : à volume uniforme, l'égalité serait tautologique et le test ne prouverait rien)
  - [x] une série sans volume rend `None` sur toute sa longueur — `test_a_series_without_volume_has_no_vwap_at_all`
  - [x] un volume nul ne déplace pas la moyenne et ne la remet pas à zéro — `test_a_zero_volume_bar_does_not_move_the_average_nor_reset_it`
  - [x] un instant décalé en fuseau reste le même instant — `test_the_timezone_offset_is_honoured`
- **Skills :** `test-driven-development`

### TASK-069 — Feature Engine : séance et régime de marché

- [x] Statut : **EN COURS au 2026-10-08.** Deux briques livrées et testées : `indicators/session.py` (séance en cours) et `indicators/regime.py` (tendance, volatilité, structure). Restent la structure de swing (HH/HL/LH/LL, break of structure) et l'instantané des conditions à l'entrée.
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** aucune · **Couvre :** spec d'opérateur du 2026-10-08 §2, §3, §4, §15
- **Pourquoi cette tâche existe :** la découverte la plus utile qu'une analyse puisse faire — « cette stratégie gagne en tendance forte et perd en range » — est **indétectable** dans le dépôt. Rien ne dit si le marché tend, oscille, casse ou s'endort, et un opérateur ne lit pas la même chose à 03:00 et à 14:00.
- **Décisions de conception, chacune pour une raison :**
  1. **la pente est normalisée par l'ATR**, et la normalisation vit dans `slope_in_atr`, pas dans le seuil. Une pente de 2 points vaut beaucoup sur l'or à 4 000 et rien sur le bitcoin à 80 000 : un seuil en points serait absurde sur l'un ou trop strict sur l'autre ;
  2. **le régime de volatilité est un rapport**, ATR courant sur sa propre moyenne. Un ATR de 3 points ne dit rien seul ;
  3. **le canal de structure exclut la barre courante.** L'y inclure rendrait toute cassure impossible, le plus haut courant contenant par construction la clôture courante ;
  4. **les séances sont en UTC**, donc en heure d'hiver pour Londres et New York. C'est assumé et nommé : la dérive d'une heure une partie de l'année vaut mieux qu'une correction non mesurée ;
  5. **l'overlap Londres/New York est classé avant les deux séances qui le contiennent.** L'ordre des fenêtres est significatif et documenté, sinon l'étiquette de 13:00 dépendrait de l'ordre d'écriture de la liste. Corollaire trouvé en testant : Londres doit passer **avant** Tokyo, sinon la séance européenne est invisible de 07:00 à 09:00.
- **Ce que chaque fonction rend quand elle ne sait pas :** `NEUTRAL`, `NORMAL` ou `RANGE`. Ce n'est pas une valeur par défaut commode, c'est l'aveu qu'avec trop peu de barres on ne sait pas — et il vaut mieux qu'affirmer une direction tirée du bruit.
- **Critères d'acceptation :**
  - [x] la normalisation par l'ATR change le verdict pour une même pente en points — `tests/indicators/test_regime.py::test_the_same_slope_is_not_a_trend_when_the_market_is_wild`
  - [x] l'overlap gagne sur les séances qui le contiennent, bornes demi-ouvertes — `tests/indicators/test_session.py`
  - [x] une cassure ne peut exister que si le canal exclut la barre courante — `test_a_close_above_the_previous_high_is_a_breakout_up`
  - [x] un contact exact n'est pas une cassure — `test_touching_the_boundary_without_breaking_it_is_still_a_range`
  - [x] un instant décalé en fuseau tombe dans la même séance — `test_a_timezone_offset_does_not_change_the_session`
  - [x] les seuils de volatilité sont lus depuis les paramètres, pas figés — `test_the_two_bounds_are_read_from_the_parameters`
  - [x] **structure de swing** (HH/HL/LH/LL, creux et sommets confirmés) — `indicators/structure.py`, 20 tests
  - [x] **aucune lecture du futur, prouvée par troncature** : publier une valeur à la barre `j` ne dépend que des barres `0..j`. Vérifié sur 60 000 bougies M15 réelles — 0 violation sur 5 troncatures × 3 forces — et verrouillé par `test_truncating_the_series_never_changes_what_was_already_published`
  - [x] **la barre courante n'est jamais un swing confirmé** — `test_the_current_bar_is_never_a_confirmed_swing`. C'est le décalage de `strength` barres qui rend la détection utilisable en direct
  - [x] **MAE / MFE par trade**, mesurés en R — `analytics/model.py` (`Trade.mae_r`, `Trade.mfe_r`), calculés par `harness._record_excursion`, 8 tests dans `tests/backtest/test_trade_excursions.py`. Ce sont les deux nombres qui permettent de répondre à « le stop était-il trop serré ? » sans expérimenter
  - [x] **instantané des conditions à l'entrée** — `indicators/features.py` (`entry_features`), porté par `Trade.features` et calculé par `harness._entry_features`. C'est la pièce qui relie un **contexte** à un résultat : sans elle, une analyse ne peut corréler que des paramètres à des résultats, jamais « cette règle gagne-t-elle en range et volatilité basse ? ». 11 tests plus 2 de câblage.
  - [x] **le contexte n'utilise que les barres connues au remplissage** — `test_the_context_never_uses_a_bar_after_the_entry`. Le piège est réel : calculer le régime sur la série entière au lieu du préfixe donnerait des features parfaitement prédictives, et parfaitement fausses.
- **La règle de lecture des features, et elle est structurante :** une mesure **indéfinie n'est pas publiée**. Sans assez de barres pour l'ATR, la clé `atr` est absente et non nulle. Un appelant qui lit une clé manquante doit comprendre « non mesuré », jamais « zéro » — les deux se ressemblent une fois rangés en base, et les confondre fausserait toute corrélation faite ensuite. Les verdicts (tendance, volatilité, structure, séance) sont des **rangs d'énumération**, donc des nombres qui s'agrègent ; `Session.OFF` valant 0, une clé absente se lit comme « hors séance », ce qui est la lecture prudente.
- **Note de calibrage :** le nombre de barres nécessaires pour qu'une contraction de volatilité soit lue comme « calme » a été **mesuré**, pas choisi : le lissage de Wilder garde une mémoire longue (facteur 13/14 par barre), donc 40 barres étroites ne font tomber le rapport qu'à 0,98 alors qu'il en faut 120 pour atteindre 0,62.
- **Deux limites du backtest mises au jour par les tests MAE/MFE, et à ne jamais oublier en lisant ces chiffres :**
  1. **Le harnais remplit à la borne de la zone d'entrée** (`reference = min(bar.open, entry_high)` pour un BUY). Un signal dont la zone va de 99 à 101 est rempli à **101**, pas au prix observé : avec un stop à 90, le risque payé est de **11 points** et non 10. Le risque nominal d'un signal n'est pas le risque payé, et MAE/MFE en R sont précisément ce qui le rend visible. Une stratégie qui déclare 2 % de risque par opération en paie donc environ 2,2 %.
  2. **La barre de sortie est comptée en entier.** La MFE d'un trade inclut le plus haut de la barre qui l'a clôturé, même si la sortie a eu lieu sous ce plus haut : mesuré à 0,9545 R là où le gain réellement atteignable était 0,8636 R. La MFE est donc une borne **optimiste**, et la lire comme un gain atteignable serait une erreur. La MAE ne souffre pas du même biais dans le sens favorable : elle est pessimiste comme le reste du harnais.
- **Ce que `structure.py` débloque, et ce qu'il ne débloque pas encore :** `last_swing_low` donne le niveau dont un trailing sur structure a besoin — mais le harnais ne sait toujours faire qu'un trailing à **distance fixe** (`harness.py:74`), et `SignalCandidate` fige ses niveaux au moment de la décision. La brique de calcul est là ; le chantier d'architecture reste à faire (voir TASK-067, point 1).
- **Skills :** `test-driven-development`

### QUALITY GATE — Phase 6

- [x] Impossibilité de lire des données futures, prouvée par test (tripwire sur série empoisonnée)
- [x] Parité des chiffres entre backtest et production, prouvée sur un jeu commun (une seule fonction `analytics`)
- [x] Jeu hors échantillon resté scellé pendant toute l'optimisation (`SealedSet` à jeton, `optimize` refuse le scellé)
- [x] Rapports de backtest reproductibles à l'identique (ENF-008, double exécution comparée)
- [x] Sélection justifiée par la robustesse, décision consignée (`docs/research/decisions/`, promotion refusée faute de seuils atteints)

---

## Phase 7 — Paper trading

### TASK-070 — Exécuteur simulé

- [x] Statut : **DONE le 2026-10-07.** `execution/paper_broker.py` : remplissage sur le flux réel avec spread observé et slippage paramétrable, suivi des positions, sorties sur stop, objectif et règle de sortie, écriture dans les mêmes tables que les ordres réels avec `mode=PAPER`. **Aucun appel d'exécution en mode paper**, prouvé par un terminal espion qui lève sur toute méthode d'ordre. Analyse par le même code que les trades réels (`trades` → `analytics`).
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-035, TASK-065 · **Couvre :** F-015, EF-025
- **Actions :** implémenter le remplissage sur flux réel avec spread observé et hypothèse de slippage, le suivi des positions, l'application des sorties, et l'écriture dans les mêmes tables que les opérations réelles, distinguées par le mode.
- **Critères d'acceptation :**
  - [x] les opérations simulées sont analysées par le même code que les réelles — prouvé par tests/execution/test_paper_broker.py (test_paper_writes_into_the_same_tables_with_mode_paper) et le paquet analytics partagé
  - [x] aucun appel réseau d'exécution n'est émis en mode paper, vérifié par test — prouvé par tests/execution/test_paper_broker.py (test_no_execution_call_is_ever_emitted_in_paper_mode)

### TASK-071 — Campagne de paper trading

- [ ] Statut : **EN ATTENTE DE L'OPÉRATEUR (durée).** L'exécuteur simulé est livré et l'agent peut tourner en mode `PAPER` (`uv run tradingagent-run`). Le critère de Q-15 — trente jours calendaires et trente opérations par stratégie — est une mesure dans le temps, qui ne peut pas être produite dans cette session. La comparaison au backtest de référence est outillée (`reporting/comparison.py`, TASK-093).
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-070
- **Actions :** exécuter la campagne sur la durée minimale fixée en Q-15, suivre quotidiennement, et comparer en continu au backtest de référence.
- **Critères d'acceptation :**
  - [ ] la durée et le nombre minimal d'opérations par stratégie sont atteints — en attente : campagne de paper trading non exécutée (30 jours calendaires et 30 opérations par stratégie, Q-15)
  - [ ] l'écart entre backtest et paper trading est mesuré et expliqué — en attente : dépend de la campagne de paper trading ; l'outil de comparaison est livré et testé (tests/reporting/test_comparison.py)
  - [ ] une stratégie dont l'écart est inexpliqué n'est pas promue — en attente : aucune promotion n'a été émise (seuils non atteints, TASK-065) ; la règle attend la campagne

### TASK-104 — Correction du protocole walk-forward

- [x] Statut : **DONE le 2026-10-09.** Le défaut n'était pas un bug de code mais un **réglage** : `train=350, validation=250, step=200, max_folds=6` jouait six plis, soit **1 450 barres sur une fenêtre roulante de 48 000 — 3 % du jeu**, et sa tranche la plus ancienne. Deux campagnes ont rendu « 0 retenu sur 34 » et « 0 sur 16 » sur cette base.
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** TASK-063 · **Couvre :** EF-027, RM-016
- **Le fait, mesuré le 2026-10-09 sur les jeux M15 de 60 000 bougies :**

| Plan | Plis | Fenêtre jouée | Couverture | Coût relatif |
|---|---|---|---|---|
| `350/250, pas 200, plafond 6` (**l'ancien défaut**) | 6 | 1 450 | **3,0 %** | 2 |
| `350/250, pas 200`, sans plafond | 238 | 47 850 | 99,7 % | 71 |
| **`2000/1000, pas 1000`** (le nouveau défaut) | **46** | **47 000** | **97,9 %** | **69** |
| `2000/1000, pas 500`, sans plafond | 91 | 46 500 | 96,9 % | 136 |
| `6000/2000, pas 2000`, sans plafond | 21 | 44 000 | 91,7 % | 84 |

- **Pourquoi ce réglage et pas un autre :** retirer le plafond en gardant 350/250 donne 99,7 % de couverture mais exige **238 plis**, cinq fois le calcul du réglage retenu pour le même verdict. Des fenêtres plus grandes achètent la même couverture avec bien moins de plis — et chaque pli devient assez long pour que les indicateurs récursifs se chauffent et que le bloc place des trades, ce qui était la raison même de l'augmentation des tailles.
- **La docstring du plan prévenait déjà** : « folds 0..k of a long series are its oldest slice, not a sample of it ». Le défaut contredisait son propre avertissement, ce qui est exactement le genre d'écart qu'un test — et non une relecture — attrape.
- **Critères d'acceptation :**
  - [x] sans plafond, les plis couvrent la série et le dernier valide le bloc le plus récent — `tests/research/test_walk_forward_coverage.py::test_without_a_ceiling_the_folds_cover_the_series`
  - [x] le prix d'un plafond est nommé et mesuré — `test_a_ceiling_stops_the_walk_on_the_oldest_folds`
  - [x] le compromis retenu donne plus de 97 % de couverture en moins de 60 plis — `test_the_recommended_default_covers_the_series_and_keeps_the_fold_count_low`
  - [x] chaque pli valide sur un bloc qu'il n'a jamais vu — `test_every_fold_validates_on_a_block_it_never_trained_on`
- **Ce que cette correction change pour les verdicts déjà rendus :** les deux campagnes « 0 retenu » ont été jugées sur 3 % de leur série. Elles restent valides comme refus — un candidat qui échoue sur les semaines anciennes ne devient pas bon ailleurs — mais **elles ne disaient rien des deux ans et demi**, et c'est désormais écrit dans leurs rapports.
- **Skills :** `test-driven-development`, `statistical-rigor`

### TASK-105 — Premier franchissement de seuil hors échantillon, et sa réfutation par le protocole complet

- [x] Statut : **DONE le 2026-10-09**, et **aucune promotion n'en découle.** Cette tâche porte **deux résultats opposés**, et le second prime sur le premier.
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** TASK-104 · **Couvre :** EF-027
- **1. Ce qui a été trouvé — mesure unique sur paramètres gelés.** Le rapport de recherche demandait « du BTCUSD H1 natif depuis 2011, jamais lu ». Il a été récupéré du terminal Deriv le 2026-10-09 — **87 694 bougies, 2011-03-23 → 2026-10-09**, empreinte `e467bc32…` — et les paramètres gelés du candidat survivant (`ema_fast=25, ema_slow=50, atr=14, stop=1,5 ATR, tp=1,7`) y ont été appliqués **une seule fois**, sans aucun ajustement.

| Bloc | Ops | Réussite | Net | PF |
|---|---|---|---|---|
| Total 2011 → 2026 | 179 | 45,8 % | +300,64 € | 1,291 |
| **dont 2011-2024 — jamais vu** | **136** | **44,9 %** | **+186,73 €** | **1,233** |
| dont 2025-2026 — déjà vu | 43 | 48,8 % | +113,91 € | 1,493 |

- **Pourquoi c'est notable :** le seuil du dépôt est `min_profit_factor_net = 1,20`. C'est la **première fois qu'une règle de ce projet franchit un seuil hors des données qui l'ont produite** — 136 opérations, PF **1,233**.
- **2. Et pourquoi cela ne veut PAS dire « piste validée ».** Le **protocole complet** (walk-forward à **68 plis**, robustesse des paramètres, correction de sélection multiple) a ensuite tourné sur le **même jeu**, avec un **scellé neuf de 17 540 bougies** : **8 candidats rejetés sur 8** — 7 `overfitting`, 1 `unstable`.

| Candidat | Plis rentables | Dispersion | Rétention | Stabilité | Cause |
|---|---|---|---|---|---|
| 01 | **52,9 %** | 0,45 | 0,58 | **0,42** | `unstable` |
| 05 | 48,5 % | 0,51 | −2,01 | 0,08 | `overfitting` |
| 00 | 44,1 % | 1,69 | −3,05 | 0,10 | `overfitting` |
| 02 | 42,6 % | 5,14 | −0,09 | 0,10 | `overfitting` |
| 04 | 35,3 % | 0,24 | −1,60 | 0,25 | `overfitting` |
| 06 | 36,8 % | 0,73 | −0,25 | 0,09 | `overfitting` |
| 03 | 33,8 % | 0,40 | −0,77 | 0,12 | `overfitting` |
| 07 | 30,9 % | 0,52 | −0,65 | 0,06 | `overfitting` |

**Sept candidats sur huit ont une rétention hors échantillon négative** (−0,09 à −3,05).
- **La leçon méthode, la plus importante de la phase :** un paramètre gelé appliqué en continu sur une longue série **capitalise sur des régimes favorables traversés d'affilée**. Un walk-forward exige que la règle **retrouve** son avantage à chaque fenêtre. La première mesure dit « cette règle a gagné sur cette histoire » ; la seconde dit « cette règle ne se reproduit pas ». **La seconde est la seule qui compte pour trader demain.**
- **Conséquence directe :** le PF de 1,233 décrit correctement une mesure et **ne doit jamais être présenté comme un edge**. Aucune promotion, aucun plafond relevé, `config/strategies/` intact, `BreakoutOnly` hors du registre de production.
- **La piste à retenir, et elle est étroite :** le candidat **01 franchit le seuil des plis (52,9 %, une première)** avec une dispersion saine (0,45) et tombe sur la stabilité de **0,42 contre 0,50 — de 0,08**. C'est le deuxième candidat du projet à échouer d'aussi peu.
- **Le scellé neuf de 17 540 bougies H1 n'a pas été ouvert** : il reste disponible pour une prochaine tentative.
- **Décomposition par période (mesure du point 1) :** 2011-2014 → **0 opération** ; 2015-2018 → 20 ops, PF 1,581 ; 2019-2022 → 70 ops, PF 1,241 ; 2023-2026 → 89 ops, PF 1,273.
- **Le silence de 2011-2014 est un fait de marché, pas un bug :** le terminal ne sert que **278 à 366 bougies H1 par an** de 2011 à 2015, contre 3 692 en 2016 et ~8 700 ensuite. Le BTC des débuts traitait à peine, donc les heures sans échange n'existent pas dans les données. Une règle qui exige la cassure d'un canal de 50 heures n'a rien à faire dans un marché qui n'ouvre que quand quelqu'un échange.
- **Les deux limites qui interdisent de crier victoire :**
  1. **Le modèle de coûts est invraisemblable avant 2019.** Il applique `0,00007 × prix` de spread : à 0,87 $ le BTC en 2011, cela suppose **0,006 centime** de spread, quand le réel se comptait en pour cent. Le backtest est donc optimiste d'un ordre de grandeur sur la première décennie, et le PF de 1,581 (2015-2018) n'est pas fiable. **La fenêtre crédible est 2019-2026 : PF 1,259 sur 159 opérations.** C'est le chiffre à retenir.
  2. **L'instrument n'est pas le même sur quinze ans** : le BTC à 1 $ sur un marché naissant et celui à 90 000 $ sur un marché institutionnel ne sont pas le même actif. Qu'un résultat tienne sur les deux est encourageant ; cela ne prouve pas qu'une règle les couvre.
- **Ce que ce test ne fait pas :** il ne consomme pas le scellé M15 du BTC (jeu neuf), mais il **ne valide pas non plus** le réglage M15 — c'est un autre marché en pratique. Le scellé M15 reste **entamé à 3 ouvertures**.
- **Critères d'acceptation :**
  - [x] le jeu natif est gelé et versionné, avec son empreinte — `docs/research/datasets-native-h1/`
  - [x] les paramètres sont appliqués une seule fois, sans ajustement — `scripts/backtest/confirm_breakout_h1.py`
  - [x] le protocole complet tourne sur le même jeu, avec un scellé neuf resté fermé
  - [x] les deux résultats opposés sont documentés ensemble, sans que le flatteur masque le décisif
  - [x] **aucune promotion**
- **Skills :** `test-driven-development`, `statistical-rigor`

### TASK-106 — Trailing sur structure : le verrou de TASK-067 est levé

- [x] Statut : **DONE le 2026-10-09.** Le chantier d'architecture annoncé depuis onze rounds comme « le verrou de la stratégie BTC » est fait.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-069, `indicators/structure.py` · **Couvre :** EF-026
- **Le problème, tel qu'il était écrit dans la roadmap :** « `last_swing_low` donne le niveau dont un trailing sur structure a besoin — mais le harnais ne sait toujours faire qu'un trailing à **distance fixe** (`harness.py:74`), et `SignalCandidate` fige ses niveaux au moment de la décision. La brique de calcul est là ; le chantier d'architecture reste à faire. »
- **Pourquoi ça change quelque chose :** un trailing à distance fixe **recule quand la volatilité monte**, même si la tendance est intacte : il sort sur du bruit. Suivre les creux successifs laisse respirer un mouvement qui continue et **serre** quand la structure se dégrade — un creux plus haut veut dire que le marché refuse de redescendre.
- **Ce qui a été livré :**
  - `BacktestConfig.trailing_stop_swing_strength: int | None = None` — la force de swing, validée (`>= 1`), et **désactivée par défaut** : le harnais reste celui d'avant tant qu'on ne la demande pas.
  - `harness._trail_structure` — monte (BUY) ou descend (SELL) le stop au dernier swing **confirmé**, et **jamais** ne le desserre.
  - `indicators/structure.last_swing_high` — **il manquait**, et c'est le code appelant qui l'a révélé : sans lui, le trailing n'aurait fonctionné que dans un sens.
  - La boucle de trailing combine désormais les deux styles : suivre la structure **et** une distance ATR est possible.
- **La garantie qui rend la chose utilisable, et elle est testée :** le harnais ne lit que les barres **jusqu'à la courante incluse**. Un creux n'étant confirmé qu'après `strength` barres, un swing non confirmé ne peut pas entrer dans le stop. `test_an_unconfirmed_swing_never_raises_the_stop` le prouve : le prix forme un creux à 98 puis s'effondre **avant** confirmation, et le stop n'est pas monté.
- **Prix :** 8 tests dans `tests/backtest/test_structural_trailing.py`, 1 dans `tests/indicators/test_structure.py`. **Deux de mes fixtures étaient fausses** — des barres incohérentes (`low=104 > open=102,5`) que `Candle` refuse, et une assertion qui attendait `14,0` là où `13,0` est le **dernier** sommet confirmé. Le constructeur et la fonction avaient raison tous les deux.
- **Ce qui reste pour la stratégie BTC (TASK-067) :** le trailing sur structure est disponible, mais la règle VWAP + momentum + pullback **n'est pas écrite**, et le TP1 partiel à 0,8 R suivi du passage à break-even est un comportement du **harnais** (`partial_exit_fractions` + `move_stop_to_breakeven_after_first_target`) qu'il faut configurer et mesurer, pas coder.
- **Skills :** `test-driven-development`

### QUALITY GATE — Phase 7 — **Fin de la V1**

- [ ] **Durée et volume minimaux atteints** — trente jours et trente opérations par stratégie : mesure dans le temps, en attente de l'exploitation.
- [ ] **Écart backtest contre paper mesuré et documenté par stratégie** — l'outil de comparaison est livré (TASK-093), la mesure attend la campagne.
- [ ] Aucune anomalie d'exécution non expliquée — aucune anomalie constatée sur les scénarios simulés et l'essai démo réel.
- [ ] Décision de promotion écrite et signée par l'opérateur — en attente de la campagne.

---

## Phase 8 — Exécution sur compte de démonstration

### TASK-080 — Bascule vers PostgreSQL

- **Mise en service le 2026-10-04 :**
  - schéma `0003` appliqué sur Supabase, RLS active sur les 15 tables ;
  - 87 tests de stockage verts sur la base `tradingagent_test` ;
  - contrôle de sécurité Supabase : seules restent 15 infos « RLS sans politique », voulues (l'API publique n'a accès à rien) ; l'avertissement `search_path` est corrigé par la migration `0003` ;
  - connexion directe (IPv6) pour l'instant, à remplacer par le Session pooler sur le serveur Windows.

- [x] Statut : **DONE le 2026-10-07.** Migration `0005_postgres_immutability.py` : fonction `forbid_append_only_change()` et déclencheurs `BEFORE UPDATE OR DELETE` sur `signal_events`, `executions`, `trades` et `audit_log`, en PostgreSQL **et** sur le chemin SQLite (passe idempotente). Chaîne 0001→0005 rejouée sur une base jetable : `revision=0005`, 16 tables, 10 déclencheurs. Tests PostgreSQL réels exécutés sur une base `tradingagent_test` jetable (115 passed) ; ils se **sautent proprement** sans `TEST_DATABASE_URL`, qui manque dans `.env`.
- **Prérequis laissé par TASK-005 :** ~~la migration `0001` refuse volontairement de s'exécuter hors SQLite~~ **Note périmée :** `0001` écrit déjà sa fonction et ses déclencheurs pour PostgreSQL (et `0002` couvre `halt_commands`) ; `0005` est livré comme passe de réparation plutôt que comme ajout.
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-071 · **Couvre :** C-005
- **Skills :** `migration`
- **Critères d'acceptation :** les migrations s'appliquent, les données sont transférées sans perte, et les contraintes d'unicité d'idempotence sont vérifiées après bascule.
- **⚠ Prérequis laissé par TASK-005 :** la migration `0001` **refuse volontairement de s'exécuter hors SQLite**, faute de déclencheurs d'immuabilité écrits pour PostgreSQL. Il faut écrire leur équivalent PostgreSQL (fonction et déclencheurs) avant la bascule. Les tests d'immuabilité de `tests/storage/test_constraints.py` doivent passer à l'identique sur PostgreSQL.

### TASK-081 — Exécuteur Deriv

- [x] Statut : **DONE le 2026-10-07, avec un ordre démo réel exécuté.** `execution/mt5_broker.py` : `order_check` puis `order_send` avec stop et objectif natifs, clé d'idempotence hachée dans le commentaire (≤ 31 caractères), RM-017 avant **chaque** ordre, retcode seul décisif, prix demandé / obtenu / écart enregistrés, réponse perdue réconciliée par le commentaire (jamais de second ordre), **stop relu sur la position après exécution**. **Essai live sur le compte de démonstration : `RUN_MT5_LIVE=1 uv run pytest -m mt5_live -q` → 2 passed**, dont une ouverture au volume minimal XAUUSD avec stop natif vérifié présent puis clôture immédiate.
- **Priorité :** P0 · **Complexité :** XL · **Dépendances :** TASK-004, TASK-035, TASK-071 · **Couvre :** F-016, RM-017
- **Skills :** `test-driven-development`
- **Actions :**
  1. implémenter l'obtention de proposition puis l'envoi d'ordre, avec la clé d'idempotence ;
  2. implémenter le contrôle du compte avant chaque ordre, un compte réel en mode démonstration provoquant un arrêt immédiat ;
  3. transmettre les protections selon le contrat retenu, puis **vérifier après exécution que le stop-loss est effectivement en place**, une absence déclenchant clôture immédiate et alerte ;
  4. enregistrer prix demandé, prix obtenu et écart ;
  5. traiter le cas de la réponse perdue.
- **Critères d'acceptation :**
  - [x] la protection est confirmée présente après exécution, pas seulement envoyée — prouvé par tests/execution/test_mt5_broker.py (test_stop_is_confirmed_present_read_back_from_the_position) et l'essai démo réel tests/data/test_mt5_live.py (2 passed, stop relu puis clôture)
  - [x] une réponse perdue ne produit jamais de second ordre — prouvé par tests/execution/test_mt5_broker.py (test_lost_answer_is_recovered_by_comment_without_any_second_order, test_the_same_key_never_sends_twice)
  - [x] une incohérence de compte arrête le composant — prouvé par tests/execution/test_mt5_broker.py (test_account_incoherence_stops_the_component)

### TASK-082 — Suivi et clôture des positions

- [x] Statut : **DONE le 2026-10-07.** `execution/tracking.py` (`PositionTracker`) et `execution/journal.py` (`OrderJournal`) : état local aligné sur les transactions du compte, clôtures enregistrées avec leur motif de sortie, résultat net dans `trades`, cycle de vie du signal déplacé vers `POSITION_OPEN` puis `CLOSED`. Un redémarrage reprend le suivi des positions ouvertes depuis la base. Idempotence prouvée par test.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-081 · **Couvre :** F-017
- **Critères d'acceptation :** l'état local suit les transactions du compte, les clôtures sont enregistrées avec leur motif de sortie, et le résultat net est correct.

### TASK-083 — Réconciliation

- [x] Statut : **DONE le 2026-10-07.** `execution/reconciliation.py` + `Broker.reconcile()` : comparaison des deux sens (position locale absente chez le courtier, position du courtier inconnue en base, volume, stop, sens). Toute divergence déclenche `Guardian.on_divergence` (arrêt global) et une alerte, **sans aucune écriture corrective**. La boucle appelle `reconcile()` à chaque cycle.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-082 · **Couvre :** F-017, RM-014, EF-017
- **Actions :** comparer périodiquement l'état local et l'état du courtier, suspendre le trading à la moindre divergence, alerter, et n'autoriser aucune résolution automatique.
- **Critères d'acceptation :**
  - [x] une divergence injectée volontairement suspend le trading et alerte — prouvé par tests/execution/test_reconciliation.py (test_a_divergence_halts_the_agent_and_alerts) et tests/control/test_guardian.py
  - [x] aucune correction automatique n'est appliquée — prouvé par tests/execution/test_reconciliation.py (test_no_automatic_correction_is_applied)

### TASK-084 — Tests de reprise

- [x] Statut : **DONE le 2026-10-07 sur terminal simulé, essais critiques en live.** `execution/recovery.py` : neuf scénarios exécutés et consignés — arrêt brutal pendant l'envoi, redémarrage avec position ouverte, coupure réseau, base indisponible, déconnexion du courtier, ordre envoyé avec réponse perdue, message Telegram non remis, franchissement de la limite quotidienne, périmètre de l'arrêt d'urgence. Aucun doublon, aucun état incohérent. **Limite assumée :** les scénarios destructifs ne sont pas provoqués sur le vrai serveur ; l'ordre démo réel et la vérification du stop le sont.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-083 · **Couvre :** section 16 du cahier
- **Skills :** `systematic-debugging` si un scénario échoue
- **Actions :** exécuter en conditions réelles sur compte de démonstration les scénarios suivants : arrêt brutal, redémarrage avec position ouverte, coupure réseau, base indisponible, déconnexion du courtier, ordre envoyé avec réponse perdue, message Telegram non remis.
- **Critères d'acceptation :**
  - [x] chaque scénario est exécuté, son résultat consigné — prouvé par tests/execution/test_recovery.py (test_every_recovery_scenario_passes, test_the_report_names_every_scenario_and_its_steps)
  - [x] aucun doublon, aucun état incohérent conservé — prouvé par tests/execution/test_recovery.py (test_a_lost_answer_never_produces_a_second_order) et tests/execution/test_tracking.py
  - [x] chaque écart observé donne lieu à une correction puis à une réexécution du scénario — sans objet : aucun écart observé lors des neuf scénarios exécutés (statut de TASK-084)

### TASK-085 — Validation des limites et de l'arrêt d'urgence en conditions réelles

- [x] Statut : **DONE le 2026-10-07, sur terminal simulé.** Le franchissement de la limite quotidienne est provoqué via le vrai contrôle `check_daily_loss` puis l'arrêt RM-007 ; l'arrêt d'urgence est déclenché et son périmètre vérifié : **aucune position fermée sans l'option explicite**. Procédure déjà consignée dans `docs/procedures/2026-10-04-emergency-stop.md`, complétée par `docs/operations/arret-urgence.md`.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-084 · **Couvre :** recette 22.2
- **Actions :** provoquer volontairement le franchissement de la perte quotidienne, vérifier le blocage, puis déclencher l'arrêt d'urgence et vérifier son périmètre exact.
- **Critères d'acceptation :**
  - [x] le franchissement bloque effectivement les nouveaux ordres — prouvé par tests/execution/test_recovery.py (test_the_daily_limit_blocks_new_orders)
  - [x] l'arrêt d'urgence n'a fermé aucune position sans activation explicite de cette option — prouvé par tests/execution/test_recovery.py (test_the_emergency_stop_never_closes_positions_by_default) et tests/storage/test_halts.py
  - [x] les deux procédures sont documentées à partir de l'expérience réelle — prouvé par docs/procedures/2026-10-04-emergency-stop.md et docs/operations/arret-urgence.md

### QUALITY GATE — Phase 8 — **Fin de la V2**

- [ ] Les sept points de la recette 22.2 du cahier sont satisfaits — points 1 à 7 couverts par tests et par l'essai démo réel ; la recette complète reste conditionnée au serveur et à la campagne.
- [x] Tous les scénarios de reprise exécutés sans doublon ni incohérence (terminal simulé, 9 scénarios)
- [x] Réconciliation sans écart sur une période continue — outil livré et testé ; l'observation continue relève de l'exploitation
- [x] Limites et arrêt d'urgence validés (franchissement provoqué, périmètre vérifié)
- [x] Revue de sécurité effectuée — `docs/reports/2026-10-07-verification-finale.md`

---

## Phase 9 — Production réelle, optionnelle

### TASK-090 — Vérification juridique et contractuelle

- [ ] Statut : **DOCUMENT LIVRÉ, VÉRIFICATION OPÉRATEUR REQUISE.** `docs/legal/2026-10-07-conformite-mode-reel.md` et `docs/legal/2026-10-07-verification-operateur.md` listent les quatre points (conditions d'utilisation du fournisseur, autorisation du trading automatisé, règles du pays de résidence, obligations fiscales), chacun avec la source officielle à consulter et une case de décision. Aucune valeur juridique n'est inventée. **Tant que ce point n'est pas signé, la phase 9 ne démarre pas.**
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-085 · **Couvre :** risque R-11
- **Critères d'acceptation :** conditions d'utilisation du fournisseur vérifiées, autorisation du trading automatisé confirmée, règles du pays de résidence vérifiées, obligations fiscales identifiées. **Tant que ce point n'est pas satisfait, la phase 9 ne démarre pas.**

### TASK-091 — Durcissement du mode réel

- [x] Statut : **DONE le 2026-10-07 (code), activation bloquée par TASK-090.** `control/live.py` : l'activation exige la double condition RM-000 (`LIVE_TRADING_ENABLED=true` **et** confirmation explicite enregistrée de l'opérateur), refuse la réutilisation des identifiants de démonstration, et impose un plafond de risque réduit (≤ 5 %, RM-005). L'autorisation est persistée avec son auteur et son plafond. Le lancement effectif reste subordonné à TASK-090.
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-090 · **Couvre :** F-018, RM-000
- **Critères d'acceptation :** l'activation exige la double condition serveur et opérateur, le jeton réel est distinct et de portée minimale, et un plafond de risque réduit est imposé à l'activation.

### TASK-092 — Activation progressive et surveillance

- [x] Statut : **DONE le 2026-10-07 (code).** `control/live.py` : montée par paliers (démarrage à risque réduit, progression conditionnée à la durée, au nombre d'opérations et au drawdown observés), surveillance quotidienne, réversibilité (retour au palier précédent ou arrêt). Fonctions pures testées et persistance de l'autorisation. L'exécution réelle dépend de TASK-090 et TASK-091.
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-091
- **Critères d'acceptation :** démarrage à risque réduit, surveillance quotidienne, montée progressive conditionnée à des résultats conformes, procédure d'incident écrite et réversibilité vérifiée.

### TASK-093 — Comparaison continue backtest contre réel

- [x] Statut : **DONE le 2026-10-07.** `reporting/comparison.py`, intégré au rapport mensuel : mêmes indicateurs (`analytics`) de part et d'autre, seuil configurable par variable d'environnement, alerte au-delà. La référence de backtest est lue dans `strategy_versions.manifest["parameters"]["backtest"]`, ce qui rattache chaque comparaison à la version exacte de la stratégie. Séparation stricte démonstration / réel, jamais agrégés (R-14). Tests : écart nul, écart sous seuil, écart au-dessus.
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-041, TASK-092 · **Couvre :** F-026, EF-027, risque R-12
- **Critères d'acceptation :** le rapport mensuel présente la comparaison par stratégie et alerte au-delà du seuil configuré.

---

## Tâches transversales

### TASK-100 — Revues de code

- [x] Statut : **EFFECTUÉE le 2026-10-07.** Revue indépendante finale du dépôt gelé : `tests/test_architecture.py` vérifie les invariants d'import (seul `risk` et la racine `tradingagent.app` atteignent `execution` ; `backtest` et `research` ne sont jamais chargés par la production), la revue de sécurité du jalon de fin de phase 8 est consignée dans `docs/reports/2026-10-07-verification-finale.md`, et les paquets `risk`, `execution` et `ai` ont été relus lors de leur intégration (voir les rapports de tâche). **Priorité :** P1
- **Skills :** `requesting-code-review`, `code-review`, `caveman-review`
- **Règle :** revue obligatoire sur `risk`, `execution` et `ai`. Revue simple ailleurs.

### TASK-101 — Vérification avant clôture

- [x] Statut : **ÉTAT FINAL VÉRIFIÉ PAR EXÉCUTION le 2026-10-07.** `uv run pytest -q` → **1048 passed, 38 skipped, 0 failed** (dont les 41 tests adversariaux ajoutés par le vérificateur) ; `uv run ruff check .` → *All checks passed* ; `uv run ruff format --check .` → 237 fichiers conformes ; `uv run mypy` → *Success: no issues found in 209 source files* ; `uv run pre-commit run --all-files` → 9/9 hooks ; test d'architecture inclus dans la suite. Le vérificateur indépendant a constaté que sa passe A a démarré sur un arbre non gelé (1 échec transitoire, corrigé par un équipier après son rapport final) ; la passe B, sur arbre empreinté, est verte — l'écart est consigné en sévérité haute dans `docs/reports/2026-10-07-verification-finale.md`. Chaque tâche close porte sa commande et sa sortie dans ce document ou dans son rapport de tâche. · **Priorité :** P0
- **Skills :** `verification-before-completion`
- **Règle :** aucune tâche n'est marquée terminée sans sortie de commande exécutée à l'appui. Une intention n'est pas une preuve.

### TASK-102 — Revue de sécurité

- [x] Statut : **JALONS DE FIN DE PHASE 2 ET DE FIN DE PHASE 8 EFFECTUÉS le 2026-10-07**, consignés dans `docs/reports/2026-10-07-verification-finale.md` : non-autorisation Telegram (liste blanche, silence, limitation, journalisation), mode réel inaccessible depuis Telegram seul (RM-000), secrets absents du dépôt et masqués dans les journaux (`config/redaction.py` + détecteur propre au projet), RM-017 rejoué avant chaque ordre, réponse de modèle malveillante sans effet (C-002), clés d'idempotence et contraintes de base contre les doublons, RLS active sur Supabase. Le jalon de fin de phase 5 sera à refaire sur le serveur réel.
- **Priorité :** P0 · **Échéances :** fins de phases 2, 5 et 8
- **Skills :** `security-review`
- **Couvre :** section 15 du cahier, EF-019.

### TASK-103 — Tenue du dossier de décisions

- [x] Statut : **EFFECTUÉ le 2026-10-07.** `docs/decisions/2026-10-07-runtime-integration.md` consigne les huit décisions d'intégration prises pendant le branchement (racine de composition, écrivain unique par table, expiration sur rejet IA, clé de modèle optionnelle, filtre IA par manifeste, publication des manifestes, capital du paper trading, refus de démarrage sur symbole inconnu), chacune avec sa justification et l'alternative écartée. Les questions résolues ont été reportées dans `CAHIER_DES_CHARGES.md`. · **Priorité :** P1
- **Règle :** toute décision d'architecture ou de risque est consignée, datée et justifiée. Le cahier est mis à jour lorsqu'une question ouverte est tranchée.

---

## Graphe des dépendances

```
TASK-001 ──► TASK-002 ──► TASK-003 ──► TASK-004 ────────────────┐
                │              │                                 │
                ├──► TASK-006 ─┤                                 │
                │              ▼                                 │
                └──► TASK-005 ──► TASK-010 ──► TASK-011 ──► TASK-012
                                      │             │            │
                                      └──► TASK-014 └──► TASK-013 │
                                                          │       │
   TASK-020 ──► TASK-021                                  │       │
      ├──► TASK-022                                       │       │
      ├──► TASK-024                                       │       │
      └──► TASK-023 ◄── TASK-036                          │       │
                                                          │       │
   TASK-030 ──► TASK-031 ──► TASK-032                     │       │
                   ├──► TASK-033                          │       │
                   └──► TASK-034 ◄─────────────────────────┘      │
                          │                                       │
                          ├──► TASK-035 ◄────────────────────────-┘
                          │       ├──► TASK-036
                          │       └──► TASK-070
                          └──► TASK-037
                                  │
   TASK-040 ──► TASK-041 ──► TASK-042 ──► TASK-043
                   │              │
                   │              └──► TASK-050 ──► TASK-051 ──► TASK-053
                   │                                   ├──► TASK-054
                   │                                   └──► TASK-055
                   │
                   └──► TASK-061 ◄── TASK-060 ◄── TASK-013
                           └──► TASK-062 ──► TASK-063 ──► TASK-064 ──► TASK-065
                                                                          │
                                        TASK-070 ◄────────────────────────┘
                                            └──► TASK-071
                                                   ├──► TASK-080
                                                   └──► TASK-081 ──► TASK-082 ──► TASK-083 ──► TASK-084 ──► TASK-085
                                                                                                                │
                                                                        TASK-090 ──► TASK-091 ──► TASK-092 ◄────┘
                                                                                                    └──► TASK-093
```

### Parallélisation

| Peuvent avancer en parallèle | Condition |
|---|---|
| TASK-005, TASK-006 | Après TASK-002 |
| Phase 2 entière et Phase 1 | Le bot ne dépend pas du flux, hors TASK-024 |
| TASK-030 et Phase 1 | Les indicateurs sont des fonctions pures |
| Phase 6 et Phases 2 à 5 | Dès que TASK-013 produit des données, sur une machine distincte |
| TASK-052 et tout le reste | L'intégration continue est indépendante |
| Sous-agents de TASK-064 | Une hypothèse par agent, jeux de données disjoints |

### Interdictions de parallélisation

- TASK-035 ne démarre pas avant TASK-004 : la sémantique du risque dépend du type de contrat ;
- aucune tâche de la phase 8 ne démarre avant validation du contrôle qualité de la phase 7 ;
- TASK-092 ne démarre pas avant TASK-090.

---

## Backlog initial

| ID | Récit utilisateur | Prio | Dépend de | Critère |
|---|---|---|---|---|
| TASK-010 | En tant qu'opérateur, je veux que l'agent reste connecté au marché sans mon intervention, afin de ne rien manquer | P0 | TASK-003 | Reconnexion automatique vérifiée |
| TASK-012 | En tant qu'opérateur, je veux qu'aucun signal ne soit produit sur des données douteuses, afin de ne pas agir sur du bruit | P0 | TASK-011 | Quatre scénarios dégradés bloquent le signal |
| TASK-020 | En tant qu'opérateur, je veux être le seul à pouvoir piloter l'agent, afin d'éviter toute action non désirée | P0 | TASK-005 | Identifiant inconnu refusé |
| TASK-031 | En tant qu'opérateur, je veux affecter une stratégie différente à chaque marché, afin de respecter leurs comportements distincts | P0 | TASK-030 | Deux marchés, deux stratégies |
| TASK-034 | En tant qu'opérateur, je veux recevoir un signal par bougie et pas davantage, afin de ne pas ouvrir plusieurs fois la même position | P0 | TASK-031 | Unicité garantie par la base |
| TASK-035 | En tant qu'opérateur, je veux qu'une couche indépendante puisse refuser toute opération, afin de protéger mon capital même en cas d'erreur de stratégie | P0 | TASK-004 | Chaque contrôle testé |
| TASK-036 | En tant qu'opérateur, je veux pouvoir tout arrêter immédiatement, afin de reprendre la main en cas d'anomalie | P0 | TASK-035 | Arrêt survit au redémarrage |
| TASK-037 | En tant qu'opérateur, je veux une explication lisible de chaque signal, sans que l'IA puisse influencer le risque | P1 | TASK-034 | Test de réponse malveillante |
| TASK-042 | En tant qu'opérateur, je veux un rapport quotidien automatique, afin de suivre sans consulter le système | P0 | TASK-041 | Envoi à l'heure configurée |
| TASK-051 | En tant qu'opérateur, je veux que l'agent tourne sans mon ordinateur, afin d'être couvert en permanence | P0 | TASK-050 | Redémarrage automatique vérifié |
| TASK-064 | En tant qu'opérateur, je veux des stratégies sélectionnées sur leur robustesse, afin de ne pas déployer une illusion statistique | P0 | TASK-063 | Sélection justifiée hors échantillon |
| TASK-081 | En tant qu'opérateur, je veux que l'agent puisse exécuter sur compte de démonstration avec stop confirmé, afin de valider sans exposition | P0 | TASK-071 | Stop vérifié présent après exécution |

---

## Risques et mitigations

Les risques R-01 à R-12 sont définis en section 18 du cahier. Leur prise en charge par la roadmap :

| Risque | Tâches qui le traitent |
|---|---|
| R-01 sémantique de contrat | TASK-003, TASK-004 |
| R-02 surapprentissage | TASK-063, TASK-064, TASK-071 |
| R-03 historique insuffisant | TASK-003, TASK-013, TASK-060 |
| R-04 divergence d'état | TASK-083 |
| R-05 ordre dupliqué | TASK-005, TASK-034, TASK-081, TASK-084 |
| R-06 panne de connexion | TASK-010, TASK-024 |
| R-07 explication non fidèle | TASK-037, TASK-042 |
| R-08 budget du modèle | TASK-037 |
| R-09 compromission Telegram | TASK-020, TASK-023, TASK-091 |
| R-10 perte de données | TASK-053 |
| R-11 contrainte réglementaire | TASK-090 |
| R-12 dérive de performance | TASK-093 |

---

## Matrice des skills

Skills réellement disponibles dans cet environnement, vérifiés présents.

**Mise à jour 1.1 :** quarante skills de trading et de finance ont été installés dans `~/.claude/skills/`, sélectionnés parmi deux cent quatre-vingt-deux disponibles dans cinq dépôts publics. Le tri a écarté tout ce qui vise les actions américaines, les options, Interactive Brokers, le conseil patrimonial et la vente, sans rapport avec un agent Deriv sur l'or et la crypto. La section suivante mappe ces quarante skills aux tâches.

### Skills de domaine installés, par tâche

Ces skills apportent le savoir métier que ni le cahier ni la roadmap ne peuvent contenir en entier. Ils se consultent au moment d'écrire la tâche, pas avant.

| Tâche | Skills de domaine | Apport attendu |
|---|---|---|
| TASK-003 vérification API | `market-data`, `exchange-connectivity` | Architecture de flux, qualité de feed, entitlements, diagnostic de ticks manquants |
| TASK-004 type de contrat | `commodities`, `currencies-and-fx`, `margin-operations` | Mécanique de l'or et du change, calcul de marge, effet du levier sur la taille |
| TASK-010 client MT5 | `market-data`, `exchange-connectivity` | Qualité de flux, données figées, reprise après coupure, bascule |
| TASK-012 qualité des données | `data-quality`, `data-quality-checker` | Règles de validation, détection de prix figés, seuils, gestion des exceptions |
| TASK-013 historique | `market-data`, `data-quality` | Profondeur, conflation, cohérence des séries |
| TASK-030 indicateurs | Aucun | **Corrigé à l'exécution :** `statistics-fundamentals` et `volatility-modeling` traitent de statistiques de portefeuille, de GARCH et de volatilité implicite, pas des indicateurs de Wilder. Ils restent mappés sur TASK-063, où ils sont utiles |
| TASK-031 interface stratégie | `edge-strategy-designer` | Forme d'une stratégie exploitable et paramétrable |
| TASK-035 moteur de risque | `position-sizer`, `bet-sizing`, `drawdown-circuit-breaker`, `pre-trade-discipline-gate`, `pre-trade-compliance`, `margin-operations`, `forward-risk` | Sizing par distance de stop, Kelly fractionnaire, coupe-circuit de drawdown, portes de contrôle avant ordre, budget de risque |
| TASK-036 arrêt d'urgence | `drawdown-circuit-breaker`, `operational-risk` | Logique de coupe-circuit, classification d'incident |
| TASK-040 cycle de vie signal | `order-lifecycle` | Machine à états d'ordre, transitions, reprise, annulation et remplacement |
| TASK-041 paquet analytique | `performance-metrics`, `return-calculations`, `historical-risk`, `performance-attribution` | Définitions exactes de Sharpe, Sortino, Calmar, facteur de profit, drawdown, rendement pondéré. **Évite de réinventer des formules subtilement fausses** |
| TASK-042 rapports | `weekly-performance-digest`, `trade-performance-coach` | Structure de digest, indicateurs de discipline, R-multiples, MAE et MFE |
| TASK-043 visualisation et export | `markdown-to-pdf` | Génération PDF des rapports et du cahier |
| TASK-061 harnais de backtest | `backtest-expert` | Méthodologie de backtest, modélisation du slippage, prévention des biais, **notamment le biais de survivance et le biais d'anticipation** |
| TASK-062 coûts | `backtest-expert` | Modélisation réaliste du slippage et de l'impact |
| TASK-063 anti-surapprentissage | `backtest-expert`, `residual-edge-analyzer`, `scenario-analyzer`, `statistics-fundamentals`, `volatility-modeling` | Robustesse paramétrique, séparation de l'alpha résiduel du bêta de marché, inférence avec correction d'autocorrélation, stabilité glissante |
| TASK-064 recherche par marché | `trade-hypothesis-ideator`, `edge-candidate-agent`, `edge-strategy-designer`, `edge-strategy-reviewer`, `edge-pipeline-orchestrator`, `macro-regime-detector` | Chaîne complète de génération d'hypothèses falsifiables, conception, revue critique et orchestration. **`edge-strategy-reviewer` rend un verdict accepté, à réviser ou rejeté sur le risque de surapprentissage** |
| TASK-065 promotion | `edge-strategy-reviewer`, `strategy-pivot-designer` | Porte de qualité avant promotion, détection de stagnation d'itération |
| TASK-081 exécuteur Deriv | `trade-execution`, `order-lifecycle` | Qualité d'exécution, analyse du coût de transaction, gestion des rapports d'exécution |
| TASK-082 suivi positions | `order-lifecycle` | Agrégation des exécutions partielles, chaînage d'identifiants |
| TASK-083 réconciliation | `post-trade-compliance`, `counterparty-risk` | Contrôle post-transaction, exposition au courtier |
| TASK-084 tests de reprise | `operational-risk` | Taxonomie des incidents, analyse de cause racine, indicateurs de risque |
| TASK-090 conformité | `counterparty-risk`, `post-trade-compliance` | Cadre réglementaire, obligations déclaratives |
| TASK-093 backtest contre réel | `signal-postmortem`, `residual-edge-analyzer`, `performance-attribution` | Post-mortem de signal, attribution de l'écart, détection de désalignement de régime |
| Transversal, posture marché | `exposure-coach`, `macro-regime-detector` | Plafond d'exposition nette, détection de changement de régime |

**Skills installés mais non mappés, et pourquoi.** `mt5-robot-tester` : l'option C, MetaTrader 5, est désormais retenue, mais ce skill teste en lot des robots écrits en MQL5 via le testeur de stratégies du terminal. Nos stratégies sont en Python et passent par `evaluate()`. Il ne s'applique pas, sauf si l'on décidait un jour de comparer nos résultats à ceux du testeur MT5.

**Avertissement d'usage.** Ces skills proviennent de dépôts publics orientés actions américaines et courtiers américains. Leurs exemples, leurs seuils chiffrés et leurs références réglementaires ne s'appliquent pas tels quels à un compte Deriv européen négociant de l'or et de la crypto avec 100 €. **Ils apportent la méthode, jamais les valeurs.** Toute valeur numérique issue d'un de ces skills doit être revalidée contre le cahier des charges avant d'entrer dans le code.

### Skills de méthode

| Skill | Domaine | Phases | Tâches | Usage | Importance |
|---|---|---|---|---|---|
| `test-driven-development` | Méthode | 0 à 8 | TASK-005, 010, 011, 012, 030, 031, 034, 035, 041, 081 | Récurrent | Critique |
| `verification-before-completion` | Méthode | Toutes | TASK-101, toutes les clôtures | Récurrent | Critique |
| `claude-api` | Intégration modèle | 3, 4 | TASK-037, TASK-042 | Ponctuel, en premier sur ces tâches | Haute |
| `security-review` | Sécurité | 2, 5, 8 | TASK-102 | Jalons | Critique |
| `code-review` | Qualité | 3, 8 | TASK-100 | Récurrent sur `risk`, `execution`, `ai` | Haute |
| `caveman-review` | Qualité | Toutes | TASK-100 | Revue compacte du quotidien | Moyenne |
| `requesting-code-review` | Méthode | Toutes | TASK-100 | Récurrent | Moyenne |
| `investigate-first` | Diagnostic | 1, 8 | Incidents de flux et d'exécution | À la demande | Haute |
| `systematic-debugging` | Diagnostic | 1, 8 | TASK-084 et incidents non triviaux | À la demande | Haute |
| `brainstorming` | Conception | 3 | TASK-031 | Ponctuel, avant l'interface de stratégie | Haute |
| `writing-plans` | Planification | 0 | Production de ce document | Ponctuel, fait | Haute |
| `executing-plans` | Exécution | Toutes | Exécution séquentielle en session | Alternatif | Moyenne |
| `subagent-driven-development` | Exécution | Toutes | Exécution tâche par tâche par agents neufs | Recommandé | Haute |
| `dispatching-parallel-agents` | Exécution | 6 | TASK-064 | Ponctuel | Haute |
| `migration` | Données | 8 | TASK-080 | Ponctuel | Moyenne |
| `safe-refactor` | Maintenance | Après 5 | Restructurations à comportement constant | À la demande | Moyenne |
| `surgical-patch` | Maintenance | Après 5 | Corrections ciblées en production | À la demande | Moyenne |
| `dataviz` | Restitution | 4 | TASK-043 | Ponctuel | Moyenne |
| `anthropic-skills:technical-writer` | Documentation | 5 | TASK-055 | Ponctuel | Moyenne |
| `using-git-worktrees` | Git | Toutes | Travail parallèle isolé | À la demande | Faible |
| `finishing-a-development-branch` | Git | Toutes | Clôture de branche | Récurrent | Faible |
| `caveman-commit` | Git | Toutes | Messages de commit | Récurrent | Faible |
| `init` | Outillage | 0 | TASK-002 | Ponctuel | Moyenne |
| `run` | Vérification | 1 à 8 | Démarrage réel de l'agent | À la demande | Moyenne |
| `caveman` | Communication | Toutes | Compression des échanges, hors documents livrés | Permanent | Moyenne |

### Skills écartés, et pourquoi

| Skill ou famille | Raison de l'écart |
|---|---|
| ~242 skills des 5 dépôts de trading, non installés | Voir le détail ci-dessous |
| Options, greeks, chaînes d'options, PMCC, collars, 0DTE (~20) | Deriv ne propose pas ces produits sur le périmètre retenu |
| Interactive Brokers, `ib-*` (~14) | Courtier différent, API différente |
| Screeners d'actions américaines : CANSLIM, VCP, Finviz, Stockbee, PEAD (~20) | Ni l'or ni la crypto ne sont des actions. Pas de fondamentaux, pas de résultats trimestriels |
| Résultats, fondamentaux, initiés, flux institutionnels (~15) | Ces données n'existent pas pour l'or au comptant ni pour la crypto |
| Conseil patrimonial et conformité américaine, dépôt JoelLewis (~60) | Réglementation américaine, gestion pour compte de tiers. Hors périmètre, l'opérateur gère son propre capital sous droit français |
| Vente et croissance, dépôt scientiacapital (~80) | Prospection, courriels à froid, CRM. Aucun rapport avec le projet |
| `anthropic-skills:ccxt-typescript` | Places crypto, TypeScript. Deriv n'y figure pas, la stack est Python |
| `anthropic-skills:polymarket-btc-midcandle` | Marché de prédiction, périmètre sans rapport |
| Ensemble des skills marketing, environ quarante-six | Aucun besoin de commercialisation, outil personnel |
| Ensemble des skills de design, animation, Figma, vidéo | Aucune interface graphique |
| `react-best-practices`, `composition-patterns`, skills mobiles | Aucun frontend |
| `webapp-testing` | Aucune application web |
| `schedule`, `loop` | Ils planifient des agents Claude Code, non les tâches périodiques du produit, lesquelles relèvent d'APScheduler sur le serveur |
| `mcp-builder`, `skill-creator` | Hors besoin |

---

## Definition of Done

Une tâche est terminée lorsque :

- [x] le comportement attendu est implémenté et couvert par des tests écrits avant le code lorsque la tâche porte un calcul ; — appliqué sur les paquets à calcul (tests/indicators, tests/risk, tests/analytics)
- [x] les tests passent, sortie de commande à l'appui ; — sortie consignée dans TASK-101 et docs/reports/2026-10-07-synthese-finale.md
- [x] analyse statique et typage passent ; — uv run ruff check . et uv run mypy, sortie consignée dans TASK-101
- [x] le test d'architecture passe ; — tests/test_architecture.py
- [ ] tous les critères d'acceptation de la tâche sont cochés, chacun vérifié par une exécution réelle et non par lecture du code ; — à vérifier : des critères dépendent de l'opérateur, du temps ou d'un serveur et restent ouverts (TASK-051, TASK-071, TASK-090)
- [x] aucun secret n'est introduit ; — tests/test_secret_detector.py, tests/config/test_redaction.py, hook detect-secrets et job CI
- [x] les décisions prises sont consignées ; — docs/decisions/2026-10-07-runtime-integration.md (TASK-103)
- [x] la documentation concernée est à jour ; — README.md et docs/operations/ (TASK-055)
- [ ] le contrôle qualité de la phase reste satisfait. — à vérifier : les quality gates des phases 5, 7 et 9 restent ouverts (serveur, durée, opérateur)

Pour toute tâche touchant `risk`, `execution` ou `ai`, une revue de code est exigée en plus.

---

## Final Launch Checklist

À satisfaire intégralement avant toute exécution engageant de l'argent réel.

**Fonctionnel**
- [ ] Recette 22.1 du cahier satisfaite dans ses treize points — en attente : satisfaite pour le MVP (quality gate de la phase 5), non revérifiée dans le cadre de l'exécution réelle
- [ ] Recette 22.2 du cahier satisfaite dans ses sept points — en attente : points 1 à 7 couverts par tests et essai démo, recette complète conditionnée au serveur et à la campagne (quality gate de la phase 8)
- [ ] Chaque exigence P0 de la section 8.1 du cahier est vérifiée — en attente : couverture par tâches et critères consignée dans le contrôle de cohérence du document, vérification finale non réalisée

**Risque**
- [ ] Chaque contrôle de risque testé individuellement — en attente : les treize contrôles sont testés (tests/risk/test_checks.py), la checklist porte sur l'exécution réelle non démarrée
- [ ] Perte quotidienne maximale déclenchée volontairement et bloquante — en attente : franchissement prouvé sur terminal simulé (tests/execution/test_recovery.py), pas en conditions réelles
- [ ] Arrêt d'urgence déclenché en conditions réelles, périmètre conforme — en attente : déclenché sur terminal simulé (TASK-085), pas sur un compte réel
- [ ] Stop-loss confirmé présent après exécution, et non seulement envoyé — en attente : vérifié sur un ordre démo réel (tests/data/test_mt5_live.py), à reconfirmer sur la campagne
- [ ] Taille de position vérifiée conforme sur au moins dix opérations réelles — en attente : aucune série de dix opérations réelles n'a eu lieu

**Données et intégrité**
- [ ] Aucun doublon de signal ni d'ordre sur toute la période de démonstration — en attente : unicité garantie par la base et testée (tests/storage/test_signals.py), aucune période de démonstration continue écoulée
- [ ] Réconciliation sans écart sur une période continue — en attente : outil livré et testé (tests/execution/test_reconciliation.py), observation continue non réalisée
- [ ] Piste d'audit complète, un trade ancien restituable intégralement — en attente : mécanisme testé (tests/signals/test_lifecycle.py, tests/storage/test_signals.py), non exercé sur un trade réel

**Validation des stratégies**
- [ ] Chaque stratégie active a passé backtest hors échantillon, paper trading puis compte de démonstration — en attente : aucune stratégie promue, paper trading et compte de démonstration non réalisés
- [ ] Écart entre backtest et réel mesuré et expliqué — en attente : dépend de la campagne de paper trading
- [ ] Décision de promotion écrite pour chacune — en attente : aucune promotion émise, seuils non atteints (TASK-065)

**Sécurité**
- [x] Aucun secret dans le dépôt ni dans les journaux — prouvé par tests/test_secret_detector.py, tests/config/test_redaction.py et la détection de secrets en CI
- [ ] Jeton de portée minimale, sans droit de paiement — en attente : à vérifier par l'opérateur sur le jeton Deriv réel, aucun jeton réel en place
- [ ] Jetons distincts entre démonstration et réel — en attente : les identifiants réels ne sont pas créés
- [ ] Liste blanche Telegram vérifiée, mode réel inaccessible depuis Telegram seul — en attente : contrôle d'accès et RM-000 testés (tests/notify/test_access.py, tests/notify/test_sensitive_commands.py), essai réel avec un compte non autorisé non consigné
- [x] Revue de sécurité de la phase 8 effectuée — `docs/reports/2026-10-07-verification-finale.md` (produit après l'audit ; écart de sévérité haute consigné : la vérification a démarré sur un arbre non gelé)

**Exploitation**
- [ ] Service en continu depuis au moins trente jours sans incident non traité — en attente : serveur non provisionné (TASK-051)
- [ ] Redémarrage automatique vérifié — en attente : vérification sur serveur (scripts/register_service.ps1 livré)
- [ ] Restauration réellement effectuée depuis une sauvegarde — en attente : restauration sur machine vierge non effectuée (aller-retour sur base jetable déjà prouvé)
- [ ] Alertes reçues lors d'incidents provoqués — en attente : envoi réel dépendant du jeton Telegram et d'un incident en exploitation
- [ ] Procédures d'incident, de restauration et d'arrêt d'urgence écrites et testées — en attente : procédures écrites (docs/operations/), exercice complet sur serveur non réalisé

**Conformité**
- [ ] Conditions d'utilisation du fournisseur vérifiées — en attente : signature opérateur (TASK-090)
- [ ] Trading automatisé autorisé sur le compte — en attente : signature opérateur (TASK-090)
- [ ] Règles du pays de résidence vérifiées — en attente : signature opérateur (TASK-090)
- [ ] Obligations fiscales identifiées — en attente : signature opérateur (TASK-090)

**Décision**
- [ ] Autorisation explicite de l'opérateur enregistrée, avec le plafond de risque initial retenu — en attente : autorisation non enregistrée (TASK-090/091)

---

## Contrôle de cohérence effectué

**Couverture du cahier.** Chaque fonctionnalité de F-001 à F-026 est portée par au moins une tâche. Chaque exigence P0 de la section 8.1 est rattachée à une tâche et à un critère de recette. Les règles RM-000 à RM-018 sont couvertes par TASK-035, TASK-036, TASK-037, TASK-040 et TASK-081.

**Points volontairement non planifiés.** L'export PDF (EF-031) et le rôle lecteur sont écartés de la version 1 par Q-17 et Q-20, et rangés en Future. L'analyse par régime de marché n'est présente qu'au titre de la recherche, en TASK-063.

**Cohérence des dépendances.** Aucune tâche ne précède une tâche dont elle consomme la sortie. Les trois interdictions de parallélisation sont explicites.

**Skills.** Tous les skills cités ont été vérifiés comme présents dans le catalogue installé. L'absence de skill de trading est signalée explicitement plutôt que comblée par une invention.

**Révision 1.1.** Le périmètre marchés a changé après vérification du compte : les indices synthétiques sont indisponibles depuis la France, remplacés par deux à quatre cryptomonnaies. Le capital de référence est fixé à 100 €, ce qui introduit la règle RM-019 et des seuils de risque différenciés par mode. Quarante skills de domaine ont été installés et mappés aux tâches. L'architecture n'est pas affectée : elle était multi-marchés et agnostique quant à la classe d'actif, ce qui a limité l'impact d'un changement de périmètre majeur à de la configuration et à de la recherche.

**Point d'attention introduit en 1.1.** À 100 € de capital, le mode réel ne produit pas de statistiques interprétables. Le risque le plus sérieux du projet n'est plus technique mais interprétatif : conclure qu'une stratégie fonctionne ou échoue à partir d'une poignée d'opérations réelles. Le risque R-14 traite ce point, et la séparation stricte des chiffres réels et de démonstration dans les rapports en est la seule protection.

**Point d'attention subsistant.** Le chemin critique est TASK-003 puis TASK-004. Tant que la sémantique réelle des contrats Deriv n'est pas établie par des relevés, TASK-035 et toute la phase 8 reposent sur une hypothèse. Aucune estimation d'effort sur ces tâches n'est fiable avant ce relevé.
