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

**Suivi à faire :** le manifeste `witness@1.0.0` référence encore `frxXAUUSD`, un nom qui n'existe pas sur MT5. Il faudra publier une version qui autorise `XAUUSD` et `BTCUSD` au moment de créer `agent.yaml`.

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
  - [ ] les identifiants MT5 de démonstration sont disponibles en variables d'environnement, jamais dans le dépôt
  - [ ] le mot de passe utilisé est l'investisseur : une tentative d'ordre est refusée par le terminal
  - [ ] aucune décision n'est laissée implicite
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
  - [ ] la chaîne complète passe sur un dépôt vide
  - [ ] le test d'architecture échoue si l'on ajoute volontairement un import interdit, puis repasse après retrait
  - [ ] la détection de secrets bloque un commit contenant une fausse clé
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
  - [ ] chaque point à confirmer de la section 12.1 a une réponse factuelle, datée, avec les valeurs brutes renvoyées par le terminal en annexe
  - [ ] **le lot minimal, le risque minimal et la marge minimale sont chiffrés en euros pour chaque symbole, et l'éligibilité au mode réel est tranchée** (RM-019, Q-21, R-17)
  - [ ] le décalage horaire du serveur est mesuré et sa règle d'évolution établie
  - [ ] la liste des cryptomonnaies candidates est établie, avec spread observé et profondeur d'historique
  - [ ] si la profondeur d'historique est insuffisante pour un backtest significatif, le risque R-03 est remonté immédiatement
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
  - [ ] la formule de taille est exprimée sans ambiguïté d'unité et validée par un calcul manuel sur trois exemples, dont un où l'arrondi au pas de lot fait tomber sous le lot minimal
  - [ ] la conversion euro-dollar est explicite, avec sa source de taux
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
  - [ ] la migration s'applique et se rejoue sur une base vide
  - [ ] une violation d'unicité d'idempotence lève une erreur au niveau de la base
  - [ ] les tables immuables sont documentées comme telles
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
  - [ ] un symbole inconnu ou une stratégie inexistante empêche le démarrage
  - [ ] un jeton injecté dans un message de journal ressort masqué
- **Validation :** tests verts.

### QUALITY GATE — Phase 0

- [ ] Décisions Q-01 à Q-08 tranchées et consignées
- [ ] Rapport de capacités Deriv livré, avec relevés bruts
- [ ] Sémantique du risque définitive écrite et validée par calcul manuel
- [ ] Chaîne qualité verte, test d'architecture opérationnel
- [ ] Schéma de base appliqué, contraintes d'idempotence vérifiées par test
- [ ] Aucun secret dans le dépôt, détection active

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
  - [ ] aucun symbole hors liste blanche n'est sélectionné, vérifié par test
  - [ ] le paquet `MetaTrader5` n'est importé que par l'adaptateur réel, vérifié par le test d'architecture
  - [ ] une bougie datée à l'heure serveur ressort en UTC exacte, y compris de part et d'autre d'un changement d'heure, vérifié par test
  - [ ] un compte réel détecté en mode démonstration provoque l'arrêt, vérifié par test
  - [ ] une coupure du terminal simulé est détectée et la reprise rétablit la même sélection de symboles
  - [ ] un symbole refusé n'interrompt pas les autres
  - [ ] l'écart d'horloge est mesuré et journalisé
- **Validation :** tests d'intégration contre le simulateur, puis une session réelle de trente minutes.

### TASK-011 — Normalisation et agrégation

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-010 · **Couvre :** F-002
- **Skills :** `test-driven-development`
- **Actions :**
  1. normaliser ticks et bougies vers les types communs ;
  2. agréger les ticks en bougies par unité de temps ;
  3. définir la clôture d'une bougie : réception d'une donnée de la période suivante, ou expiration d'un délai de garde ;
  4. comparer les bougies agrégées localement aux bougies fournies par l'API et journaliser les écarts.
- **Critères d'acceptation :**
  - [ ] une bougie n'est jamais déclarée close prématurément, vérifié par test
  - [ ] l'écart entre agrégation locale et bougies du fournisseur reste sous un seuil documenté
- **Validation :** tests unitaires sur séries construites, plus comparaison sur une session réelle.

### TASK-012 — Contrôle qualité et état de santé des séries

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-011 · **Couvre :** F-002, RM-001, RM-002
- **Skills :** `test-driven-development`
- **Objectif :** rendre impossible une décision sur des données douteuses. Exigence EF-014.
- **Actions :**
  1. calculer la fraîcheur de la dernière donnée par symbole ;
  2. détecter les trous, les doublons et les horodatages non monotones ;
  3. exposer un état de santé par série, consommé par le moteur de stratégies ;
  4. écrire les tests d'injection : série périmée, série trouée, doublon, horodatage régressif.
- **Critères d'acceptation :**
  - [ ] chacun des quatre scénarios d'injection empêche l'émission d'un signal
  - [ ] un retour à l'état sain rétablit l'émission sans redémarrage
- **Validation :** tests verts. **Cette tâche est un prérequis de toute émission de signal.**

### TASK-013 — Persistance et historique

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-005, TASK-011
- **Actions :**
  1. persister les bougies avec gestion des doublons par contrainte de base ;
  2. implémenter le téléchargement d'historique et le rattrapage après coupure ;
  3. appliquer la politique de conservation des ticks ;
  4. démarrer la collecte continue au plus tôt, pour atténuer le risque R-03.
- **Critères d'acceptation :**
  - [ ] un rattrapage après coupure ne crée aucun doublon
  - [ ] la série stockée est continue sur une période de contrôle, ou ses trous sont explicitement recensés
- **Validation :** requête de contrôle de continuité sur vingt-quatre heures.

### TASK-014 — Horaires de marché

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** S · **Dépendances :** TASK-010 · **Couvre :** F-005, EF-020
- **Actions :** interroger périodiquement les horaires par symbole, les mettre en cache, exposer l'état de marché, et gérer les jours fériés tels que retournés par l'API.
- **Critères d'acceptation :**
  - [ ] l'état de l'or bascule correctement à la fermeture et à la réouverture de fin de semaine, vérifié sur un week-end réel
  - [ ] les marchés crypto restent ouverts en continu, week-end compris
- **Validation :** observation sur un week-end complet, consignée.

### QUALITY GATE — Phase 1

- [ ] Connexion stable sur vingt-quatre heures consécutives, avec au moins une reconnexion réussie
- [ ] Aucun abonnement hors liste blanche
- [ ] Les quatre scénarios de données dégradées bloquent les signaux
- [ ] Bougies continues et sans doublon sur vingt-quatre heures
- [ ] Horaires de l'or corrects sur un week-end réel
- [ ] Chaîne qualité verte

---

## Phase 2 — Telegram et signaux

Objectif : une surface de pilotage sûre, disponible avant qu'il y ait quoi que ce soit à piloter.

### TASK-020 — Bot, liste blanche et journal des commandes

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-005, TASK-006 · **Couvre :** F-014, EF-011
- **Skills :** `test-driven-development`
- **Actions :**
  1. connecter le bot et router les commandes ;
  2. filtrer par liste blanche d'identifiants, refuser sans divulguer d'information ;
  3. limiter le nombre de tentatives par identifiant ;
  4. journaliser chaque commande reçue, autorisée ou non.
- **Critères d'acceptation :**
  - [ ] une commande d'un identifiant inconnu est refusée, journalisée, et la réponse ne révèle ni l'existence du système ni son état
  - [ ] la limitation de tentatives se déclenche et se réarme
- **Validation :** tests, puis essai manuel avec un second compte Telegram non autorisé.

### TASK-021 — Gabarit de message de signal

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** TASK-020 · **Couvre :** F-013, EF-005
- **Actions :** implémenter le gabarit contenant l'ensemble des champs de F-013, avec affichage non ambigu du mode en cours, et échappement correct du formatage.
- **Critères d'acceptation :**
  - [ ] un test vérifie la présence de chacun des champs requis
  - [ ] le mode est visible sans ambiguïté possible entre démonstration et réel
- **Validation :** test de gabarit, plus envoi réel de contrôle.

### TASK-022 — Commandes de lecture

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-020
- **Objectif :** `/status`, `/markets`, `/signals`, `/positions`, `/performance`, `/help`. Regroupées car elles partagent le même module, le même contrôle d'accès et le même contrôle qualité.
- **Critères d'acceptation :**
  - [ ] chaque commande répond en moins de deux secondes sur une base contenant un mois de données
  - [ ] `/status` affiche l'état des connexions, le mode, les marchés et l'état de santé des séries
- **Validation :** essais manuels consignés.

### TASK-023 — Commandes sensibles avec confirmation

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-020, TASK-036 · **Couvre :** F-014, F-019, EF-029
- **Objectif :** `/pause`, `/resume`, `/enable`, `/disable`, `/mode`, `/close_all`, `/emergency_stop`.
- **Actions :**
  1. exiger une confirmation explicite décrivant précisément l'effet, en particulier la distinction entre suspension des ordres et clôture des positions ;
  2. persister l'effet de chaque commande, afin qu'il survive à un redémarrage ;
  3. interdire l'activation du mode réel par cette voie, conformément à RM-000 ;
  4. journaliser la décision avec l'identité de son auteur.
- **Critères d'acceptation :**
  - [ ] la confirmation de `/close_all` énonce explicitement que des positions seront fermées
  - [ ] `/mode LIVE` est refusé avec un message expliquant la condition serveur manquante
  - [ ] l'état d'un marché désactivé survit à un redémarrage
- **Validation :** essais manuels sur chaque commande, avec redémarrage intercalé.

### TASK-024 — Alertes de santé système

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** S · **Dépendances :** TASK-012, TASK-020 · **Couvre :** F-024
- **Actions :** émettre une alerte en cas de coupure prolongée, de série dégradée persistante, d'échec répété d'un composant, de saturation disque, et au démarrage comme à l'arrêt du processus. Limiter la répétition d'une même alerte.
- **Critères d'acceptation :**
  - [ ] une coupure provoquée déclenche une alerte unique, non répétée en boucle
  - [ ] le redémarrage du processus est notifié
- **Validation :** incidents provoqués volontairement.

### QUALITY GATE — Phase 2

- [ ] Un identifiant non autorisé ne peut rien obtenir ni déclencher
- [ ] Toutes les commandes sensibles exigent confirmation et sont journalisées
- [ ] Le mode réel est inaccessible depuis Telegram seul
- [ ] Les alertes système parviennent réellement
- [ ] Chaîne qualité verte

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
  - [ ] chaque indicateur est validé contre des valeurs de référence externes au code testé
  - [ ] aucun indicateur ne lit l'heure courante ni n'accède au réseau
  - [ ] une série insuffisante produit une absence de valeur, vérifiée par test
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
  - [ ] deux marchés tournent simultanément avec deux stratégies différentes et n'interfèrent pas
  - [ ] une stratégie déclarée pour l'or est refusée sur une paire crypto
  - [ ] un manifeste invalide est refusé et l'ancienne configuration reste active
- **Validation :** tests, plus essai à deux marchés.

### TASK-032 — Versionnement et rechargement

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-031, TASK-005 · **Couvre :** F-008, EF-021, EF-022
- **Actions :**
  1. enregistrer chaque version de paramètres sans jamais écraser la précédente ;
  2. implémenter le rechargement à chaud d'une configuration validée, sans redéploiement ;
  3. rattacher chaque signal à l'identifiant de version exact ;
  4. exiger une action explicite de l'opérateur pour tout changement d'état de stratégie, conformément à RM-016.
- **Critères d'acceptation :**
  - [ ] un trade ancien permet de retrouver les paramètres exacts en vigueur à son ouverture
  - [ ] un rechargement prend effet sans interruption de service
  - [ ] aucune promotion automatique de stratégie n'est possible
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
  - [ ] elle produit des signaux observables sur données historiques
  - [ ] son manifeste porte un état interdisant explicitement les modes d'exécution
- **Validation :** rejeu sur historique.

### TASK-034 — Génération de signal et idempotence

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-012, TASK-031, TASK-005 · **Couvre :** F-009, F-012, RM-009, EF-006
- **Skills :** `test-driven-development`
- **Actions :**
  1. déclencher l'évaluation à la clôture de bougie, uniquement si la série est saine et le marché ouvert ;
  2. construire la clé d'idempotence de façon déterministe ;
  3. persister le signal, ses valeurs d'indicateurs et son premier événement de cycle de vie ;
  4. isoler les exceptions de stratégie et mettre en quarantaine après échecs répétés ;
  5. tester la concurrence et le redémarrage en cours de traitement.
- **Critères d'acceptation :**
  - [ ] deux évaluations de la même bougie ne produisent qu'un signal, garanti par la base
  - [ ] une exception dans une stratégie n'affecte pas les autres marchés
  - [ ] les valeurs d'indicateurs du signal sont consultables après coup
- **Validation :** tests de concurrence et de reprise.

### TASK-035 — Moteur de risque

- [ ] Statut : TODO
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
  - [ ] chaque contrôle possède au moins un test de refus et un test de passage
  - [ ] une erreur de calcul de taille produit un refus, jamais une valeur de repli, vérifié par test
  - [ ] les refus sont comptabilisés et récupérables pour le rapport quotidien
  - [ ] un compte réel détecté en mode démonstration provoque un arrêt immédiat
  - [ ] un instrument inéligible au sens de RM-019 est refusé en mode réel et accepté en démonstration, vérifié par test
  - [ ] les seuils appliqués diffèrent bien selon le mode, vérifié par un test qui bascule le mode et constate le changement de plafond
- **Validation :** tests verts, couverture élevée, plus revue de code dédiée. **Aucune tâche d'exécution ne démarre avant validation de celle-ci.**

### TASK-036 — Arrêt d'urgence et état global

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-005, TASK-035 · **Couvre :** F-019, RM-015, EF-013
- **Actions :**
  1. persister l'état d'arrêt en base, et l'évaluer avant toute action engageant du capital ;
  2. séparer strictement suspension des nouveaux ordres et clôture des positions ;
  3. implémenter le déclenchement automatique par franchissement de limite ;
  4. implémenter le déclenchement par commande serveur, indépendamment de Telegram ;
  5. garantir le comportement de défaut fermé : en cas d'état indéterminé, refuser.
- **Critères d'acceptation :**
  - [ ] l'état d'arrêt survit au redémarrage du processus et du serveur
  - [ ] aucune clôture de position n'a lieu sans activation explicite de cette option
  - [ ] un état d'arrêt illisible ou corrompu conduit au refus, pas à l'autorisation
- **Validation :** procédure de test écrite et exécutée, résultat consigné.

### TASK-037 — Couche d'analyse par intelligence artificielle

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** L · **Dépendances :** TASK-001 (Q-04), TASK-034 · **Couvre :** F-010, RM-010, RM-011, EF-018, EF-030
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
  - [ ] le test de réponse malveillante passe, aucun effet observé
  - [ ] **en mode `shadow`, un verdict de rejet est enregistré et le signal part quand même**, vérifié par test
  - [ ] aucun appel n'est émis en l'absence de signal candidat, vérifié par compteur
  - [ ] un modèle simulé lent n'allonge pas le traitement au-delà du plafond
  - [ ] le comportement en panne correspond au paramètre `ai_filter` déclaré
  - [ ] le coût cumulé est consultable
- **Validation :** tests verts, plus une session réelle avec relevé de coût.

### QUALITY GATE — Phase 3

- [ ] Chaque contrôle de risque possède ses tests de refus et de passage
- [ ] Le test d'architecture confirme que seul `risk` atteint `execution`
- [ ] Le test de réponse de modèle malveillante passe
- [ ] L'arrêt d'urgence a été déclenché et vérifié, procédure consignée
- [ ] Deux marchés tournent avec deux stratégies distinctes
- [ ] Aucun doublon de signal sous concurrence et après redémarrage
- [ ] Revue de code du paquet `risk` effectuée

---

## Phase 4 — Persistance, indicateurs et rapports

### TASK-040 — Cycle de vie des signaux

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-005, TASK-034 · **Couvre :** F-020, RM-018, EF-007
- **Actions :** implémenter la machine à états des quinze états du cahier, refuser les transitions non autorisées, horodater et persister chaque transition sans modification ultérieure possible.
- **Critères d'acceptation :**
  - [ ] une transition interdite lève une erreur, vérifié par test
  - [ ] l'historique complet d'un signal est reconstituable
- **Validation :** tests verts.

### TASK-041 — Paquet analytique partagé

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-040 · **Couvre :** F-021, EF-008, EF-024
- **Skills :** `test-driven-development`
- **Objectif :** le code qui produira les chiffres du backtest comme ceux de la production. C'est la mise en œuvre concrète de la résolution de C-001.
- **Actions :**
  1. implémenter les indicateurs de la section 14.2 du cahier en fonctions pures prenant une liste d'opérations ;
  2. implémenter les axes d'agrégation de la section 14.1 ;
  3. tester chaque indicateur contre un jeu d'opérations dont le résultat est calculable à la main ;
  4. exposer une interface unique, utilisée par `reporting` comme par `backtest`.
- **Critères d'acceptation :**
  - [ ] chaque indicateur est validé contre un calcul manuel documenté
  - [ ] le même jeu d'opérations produit des chiffres identiques qu'il vienne du backtest ou de la production
  - [ ] les ratios exigeant un échantillon suffisant signalent explicitement leur non-significativité en deçà d'un seuil
- **Validation :** tests verts, plus double calcul manuel sur un échantillon réel.

### TASK-042 — Rapports périodiques

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-041, TASK-021 · **Couvre :** F-022, EF-009, EF-023
- **Skills :** `claude-api` pour la partie narrative uniquement
- **Actions :**
  1. implémenter un générateur unique paramétré par une fenêtre temporelle, conformément à la résolution de C-006 ;
  2. produire les contenus quotidien, hebdomadaire et mensuel de la section 14.3 du cahier ;
  3. planifier l'envoi, avec rattrapage si l'échéance a été manquée pendant un arrêt ;
  4. faire rédiger le commentaire par le modèle, en lui transmettant les chiffres déjà calculés et en lui interdisant d'en produire ;
  5. garantir l'envoi du rapport chiffré même si le modèle est indisponible.
- **Critères d'acceptation :**
  - [ ] le rapport quotidien inclut les pertes évitées par les contrôles de risque
  - [ ] un arrêt couvrant l'échéance ne fait pas perdre le rapport
  - [ ] aucun chiffre du rapport ne provient du modèle, vérifié par inspection du flux de données
- **Validation :** génération sur un mois de données réelles, plus contrôle croisé de trois chiffres à la main.

### TASK-043 — Exports et visualisation

- [ ] Statut : TODO
- **Priorité :** P2 · **Complexité :** M · **Dépendances :** TASK-041 · **Couvre :** F-023, EF-028
- **Skills :** `dataviz` pour la courbe de capital et les graphiques de drawdown
- **Actions :** exporter les opérations et les rapports en CSV et JSON, produire la courbe de capital et le graphique de drawdown pour le rapport mensuel. L'export PDF reste hors périmètre en version 1, conformément à Q-17.
- **Critères d'acceptation :**
  - [ ] l'export contient les mêmes chiffres que le rapport
  - [ ] les graphiques sont lisibles sur un écran de téléphone, cible principale de Telegram
- **Validation :** export contrôlé et rendu visuel vérifié.

### TASK-044 — Évaluation du filtre IA en mode observateur

- [ ] Statut : TODO
- **Priorité :** P2 · **Complexité :** M · **Dépendances :** TASK-037, TASK-041 · **Couvre :** C-002, EF-030
- **Skills :** `signal-postmortem` pour la structure du post-mortem, `statistics-fundamentals` pour la significativité
- **Objectif :** répondre par une mesure à la question « le filtre IA améliore-t-il les résultats ». Sans cette tâche, le mode `shadow` accumule des verdicts que personne n'exploite, et le filtre ne sortira jamais de l'observation.
- **Actions :**
  1. constituer, à partir des verdicts persistés, deux séries d'opérations sur le **même** ensemble de signaux : celle réellement obtenue, et celle qu'aurait produite l'application du verdict du modèle ;
  2. calculer les indicateurs des deux séries avec le paquet `analytics`, sans code de calcul dupliqué ;
  3. tester la robustesse de l'écart : recalculer après exclusion des cinq meilleures opérations, afin de détecter un apport qui ne tiendrait qu'à quelques coups de chance ;
  4. produire une recommandation explicite : maintenir en `shadow`, promouvoir en `advisory`, ou retirer le filtre ;
  5. rattacher le coût cumulé des appels à la période évaluée, afin de rapporter le gain éventuel à sa dépense.
- **Critères d'acceptation :**
  - [ ] la comparaison porte sur les mêmes signaux, jamais sur deux périodes différentes
  - [ ] l'échantillon minimal de cent signaux évalués est atteint avant toute conclusion, ou l'insuffisance est signalée explicitement
  - [ ] la conclusion résiste à l'exclusion des cinq meilleures opérations, sans quoi elle est déclarée non concluante
  - [ ] le coût des appels sur la période est chiffré
- **Validation :** rapport écrit, décision de l'opérateur consignée.

### QUALITY GATE — Phase 4

- [ ] Indicateurs validés par double calcul manuel
- [ ] Rapport quotidien envoyé automatiquement et à la demande
- [ ] Rattrapage d'échéance manquée vérifié
- [ ] Aucun chiffre produit par un modèle de langage
- [ ] Exports conformes

---

## Phase 5 — Déploiement et exploitation

Cette phase précède volontairement la recherche et le paper trading : une campagne de mesure sur un service instable ne vaut rien, et la collecte de données doit démarrer tôt.

### TASK-050 — Installation reproductible sous Windows

- [ ] Statut : TODO · **remplace la conteneurisation le 2026-10-03** : le terminal MT5 exige une session Windows, incompatible avec un conteneur (C-010)
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-042
- **Actions :** script d'installation de l'agent par `uv` à partir de `uv.lock`, procédure d'installation et de connexion du terminal MT5, chargement des secrets depuis l'environnement.
- **Critères d'acceptation :**
  - [ ] une machine Windows vierge reçoit l'agent et le terminal en suivant le script et la procédure seuls
  - [ ] aucun fichier de secret n'est embarqué dans le dépôt ni dans le script
  - [ ] l'empreinte mémoire au repos, terminal compris, est compatible avec le serveur cible

### TASK-051 — Mise en service sur un serveur Windows

- [ ] Statut : TODO · **révisée le 2026-10-03 (C-010)**
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-050 · **Couvre :** EF-001, R-15
- **Actions :** choisir et provisionner un serveur privé virtuel Windows, durcir l'accès conformément à la section 15.4 du cahier, installer l'agent en service Windows avec redémarrage automatique, configurer le **démarrage et la reconnexion automatiques du terminal MT5**, désactiver les mises à jour automatiques intempestives du terminal et du système pendant les heures de marché, configurer la rotation des journaux.
- **Critères d'acceptation :**
  - [ ] le service **et le terminal** redémarrent seuls après un arrêt brutal et après un redémarrage du serveur, et le terminal se reconnecte au compte
  - [ ] la base n'est pas accessible depuis l'extérieur
  - [ ] le bureau à distance n'est pas exposé sans protection
  - [ ] le coût mensuel réel est consigné dans le cahier
- **Validation :** arrêt brutal provoqué, redémarrage serveur provoqué, terminal tué volontairement.

### TASK-052 — Intégration continue

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-002
- **Critères d'acceptation :** la chaîne exécute analyse statique, typage, tests, détection de secrets et construction d'image, et bloque la fusion en cas d'échec.

### TASK-053 — Sauvegarde et restauration

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-051 · **Couvre :** ENF-007
- **Critères d'acceptation :**
  - [ ] sauvegarde quotidienne chiffrée, avec plusieurs points de restauration
  - [ ] **une restauration complète a réellement été effectuée sur une machine vierge et le service a redémarré**, ce qui est le seul critère qui compte
- **Validation :** restauration exécutée, procédure écrite à partir de l'expérience réelle.

### TASK-054 — Observabilité

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-051 · **Couvre :** F-024, ENF-006
- **Critères d'acceptation :** journaux structurés, métriques de disponibilité, latence, état des connexions, erreurs d'API, consommation de ressources, alertes en cas de panne.

### TASK-055 — Documentation d'exploitation

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-051, TASK-053
- **Skills :** `anthropic-skills:technical-writer`
- **Objectif :** installation, configuration, exploitation, incident, restauration, arrêt d'urgence, manuel utilisateur des commandes Telegram.
- **Critères d'acceptation :** une personne n'ayant pas développé le système parvient à le réinstaller et à déclencher l'arrêt d'urgence en suivant la documentation seule.

### QUALITY GATE — Phase 5 — **Fin du MVP**

- [ ] Le service tourne en continu sept jours sans intervention
- [ ] Redémarrage automatique vérifié après panne et après redémarrage serveur
- [ ] Restauration réellement effectuée depuis une sauvegarde
- [ ] Alertes reçues lors d'incidents provoqués
- [ ] Les treize points de la recette 22.1 du cahier sont satisfaits
- [ ] Documentation d'exploitation utilisable par un tiers

---

## Phase 6 — Recherche et backtesting

Parallélisable avec les phases 2 à 5 dès que TASK-013 fournit des données. Exécutée hors du serveur de production.

### TASK-060 — Jeux de données historiques versionnés

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-013 · **Couvre :** F-025
- **Actions :** télécharger l'historique disponible par symbole, contrôler doublons et trous, figer des jeux de données immuables et identifiés, séparer les données brutes des indicateurs calculés.
- **Critères d'acceptation :**
  - [ ] chaque jeu porte un identifiant, une période, une source et une empreinte
  - [ ] les données brutes ne sont jamais modifiées
  - [ ] les trous sont recensés et documentés, pas comblés silencieusement

### TASK-061 — Harnais de backtest

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** XL · **Dépendances :** TASK-031, TASK-041, TASK-060, TASK-004 · **Couvre :** F-025
- **Objectif :** simuler l'exécution en réutilisant l'interface de stratégie et le paquet analytique de production. Aucune réimplémentation de stratégie n'est autorisée.
- **Actions :**
  1. implémenter la boucle de simulation respectant la sémantique du contrat retenue en TASK-004 ;
  2. gérer stop-loss, objectifs, sorties partielles, trailing, positions simultanées et limites horaires, dans la mesure où le contrat les autorise ;
  3. interdire structurellement la lecture de données futures ;
  4. produire les opérations dans le format exact consommé par `analytics`.
- **Critères d'acceptation :**
  - [ ] un test prouve l'impossibilité de lire une donnée postérieure à l'instant simulé
  - [ ] un même jeu d'opérations passé à `analytics` donne les mêmes chiffres qu'en production
  - [ ] deux exécutions identiques donnent un résultat identique, conformément à ENF-008

### TASK-062 — Modélisation des coûts

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-061 · **Couvre :** EF-026
- **Actions :** appliquer le spread observé, un modèle de slippage, les commissions et un retard d'exécution simulé ; permettre de majorer volontairement ces coûts pour les tests de robustesse.
- **Critères d'acceptation :**
  - [ ] le résultat avec et sans coûts est comparable dans un même rapport
  - [ ] une majoration des coûts dégrade le résultat de façon cohérente

### TASK-063 — Protocole anti-surapprentissage

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-062 · **Couvre :** F-025, risque R-02
- **Objectif :** rendre le surapprentissage difficile par construction, et non par bonne volonté.
- **Actions :**
  1. séparer les données en entraînement, validation et hors échantillon, ce dernier étant scellé et inaccessible aux outils d'optimisation ;
  2. implémenter l'analyse glissante ;
  3. implémenter la perturbation des paramètres et la simulation de Monte-Carlo ;
  4. implémenter l'analyse par période et par régime de marché ;
  5. produire un indicateur de stabilité, et non un simple profit.
- **Critères d'acceptation :**
  - [ ] toute tentative d'optimisation touchant au jeu hors échantillon échoue techniquement
  - [ ] une stratégie volontairement sur-ajustée est correctement signalée comme fragile par le protocole, ce qui valide le protocole lui-même

### TASK-064 — Campagnes de recherche par marché

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** XL · **Dépendances :** TASK-063
- **Skills :** `dispatching-parallel-agents` et `subagent-driven-development` pour explorer plusieurs hypothèses en parallèle, chaque sous-agent ne recevant qu'un jeu de données et un manifeste
- **Actions :** explorer le comportement de chaque marché, formuler des hypothèses, les tester, comparer sur les mêmes indicateurs, et sélectionner sur la robustesse plutôt que sur le profit brut.
- **Attention particulière au périmètre 1.1 :** l'or et la crypto n'ont ni la même volatilité, ni la même structure de tendance, ni le même comportement de week-end. Aucune stratégie n'est transférable de l'un à l'autre, conformément à RM-003. La crypto cotant en continu, la notion de bougie journalière et de séance y est différente, ce qui affecte les indicateurs dépendant d'une clôture de séance.
- **Critères d'acceptation :**
  - [ ] pour chaque marché retenu, un rapport de backtest reproductible existe
  - [ ] la sélection est justifiée par la stabilité hors échantillon, pas par le profit net
  - [ ] les deux à quatre cryptomonnaies définitives sont arrêtées, ce qui clôt Q-07, avec vérification que les paires retenues ne sont pas fortement corrélées entre elles
  - [ ] la corrélation entre l'or et chaque crypto retenue est mesurée, afin de ne pas cumuler involontairement le même risque sur plusieurs positions simultanées

### TASK-065 — Critères de validation et promotion

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** S · **Dépendances :** TASK-064 · **Couvre :** RM-016
- **Actions :** fixer les seuils chiffrés d'acceptation, traduire chaque stratégie retenue en manifeste au format de production, et enregistrer la décision de promotion.
- **Critères d'acceptation :**
  - [ ] les seuils sont écrits avant de regarder les résultats finaux, afin d'éviter de les ajuster après coup
  - [ ] chaque manifeste produit se charge dans l'agent sans modification de code

### QUALITY GATE — Phase 6

- [ ] Impossibilité de lire des données futures, prouvée par test
- [ ] Parité des chiffres entre backtest et production, prouvée sur un jeu commun
- [ ] Jeu hors échantillon resté scellé pendant toute l'optimisation
- [ ] Rapports de backtest reproductibles à l'identique
- [ ] Sélection justifiée par la robustesse, décision consignée

---

## Phase 7 — Paper trading

### TASK-070 — Exécuteur simulé

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-035, TASK-065 · **Couvre :** F-015, EF-025
- **Actions :** implémenter le remplissage sur flux réel avec spread observé et hypothèse de slippage, le suivi des positions, l'application des sorties, et l'écriture dans les mêmes tables que les opérations réelles, distinguées par le mode.
- **Critères d'acceptation :**
  - [ ] les opérations simulées sont analysées par le même code que les réelles
  - [ ] aucun appel réseau d'exécution n'est émis en mode paper, vérifié par test

### TASK-071 — Campagne de paper trading

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-070
- **Actions :** exécuter la campagne sur la durée minimale fixée en Q-15, suivre quotidiennement, et comparer en continu au backtest de référence.
- **Critères d'acceptation :**
  - [ ] la durée et le nombre minimal d'opérations par stratégie sont atteints
  - [ ] l'écart entre backtest et paper trading est mesuré et expliqué
  - [ ] une stratégie dont l'écart est inexpliqué n'est pas promue

### QUALITY GATE — Phase 7 — **Fin de la V1**

- [ ] Durée et volume minimaux atteints
- [ ] Écart backtest contre paper mesuré et documenté par stratégie
- [ ] Aucune anomalie d'exécution non expliquée
- [ ] Décision de promotion écrite et signée par l'opérateur

---

## Phase 8 — Exécution sur compte de démonstration

### TASK-080 — Bascule vers PostgreSQL

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-071 · **Couvre :** C-005
- **Skills :** `migration`
- **Critères d'acceptation :** les migrations s'appliquent, les données sont transférées sans perte, et les contraintes d'unicité d'idempotence sont vérifiées après bascule.
- **⚠ Prérequis laissé par TASK-005 :** la migration `0001` **refuse volontairement de s'exécuter hors SQLite**, faute de déclencheurs d'immuabilité écrits pour PostgreSQL. Il faut écrire leur équivalent PostgreSQL (fonction et déclencheurs) avant la bascule. Les tests d'immuabilité de `tests/storage/test_constraints.py` doivent passer à l'identique sur PostgreSQL.

### TASK-081 — Exécuteur Deriv

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** XL · **Dépendances :** TASK-004, TASK-035, TASK-071 · **Couvre :** F-016, RM-017
- **Skills :** `test-driven-development`
- **Actions :**
  1. implémenter l'obtention de proposition puis l'envoi d'ordre, avec la clé d'idempotence ;
  2. implémenter le contrôle du compte avant chaque ordre, un compte réel en mode démonstration provoquant un arrêt immédiat ;
  3. transmettre les protections selon le contrat retenu, puis **vérifier après exécution que le stop-loss est effectivement en place**, une absence déclenchant clôture immédiate et alerte ;
  4. enregistrer prix demandé, prix obtenu et écart ;
  5. traiter le cas de la réponse perdue.
- **Critères d'acceptation :**
  - [ ] la protection est confirmée présente après exécution, pas seulement envoyée
  - [ ] une réponse perdue ne produit jamais de second ordre
  - [ ] une incohérence de compte arrête le composant

### TASK-082 — Suivi et clôture des positions

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-081 · **Couvre :** F-017
- **Critères d'acceptation :** l'état local suit les transactions du compte, les clôtures sont enregistrées avec leur motif de sortie, et le résultat net est correct.

### TASK-083 — Réconciliation

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-082 · **Couvre :** F-017, RM-014, EF-017
- **Actions :** comparer périodiquement l'état local et l'état du courtier, suspendre le trading à la moindre divergence, alerter, et n'autoriser aucune résolution automatique.
- **Critères d'acceptation :**
  - [ ] une divergence injectée volontairement suspend le trading et alerte
  - [ ] aucune correction automatique n'est appliquée

### TASK-084 — Tests de reprise

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-083 · **Couvre :** section 16 du cahier
- **Skills :** `systematic-debugging` si un scénario échoue
- **Actions :** exécuter en conditions réelles sur compte de démonstration les scénarios suivants : arrêt brutal, redémarrage avec position ouverte, coupure réseau, base indisponible, déconnexion du courtier, ordre envoyé avec réponse perdue, message Telegram non remis.
- **Critères d'acceptation :**
  - [ ] chaque scénario est exécuté, son résultat consigné
  - [ ] aucun doublon, aucun état incohérent conservé
  - [ ] chaque écart observé donne lieu à une correction puis à une réexécution du scénario

### TASK-085 — Validation des limites et de l'arrêt d'urgence en conditions réelles

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-084 · **Couvre :** recette 22.2
- **Actions :** provoquer volontairement le franchissement de la perte quotidienne, vérifier le blocage, puis déclencher l'arrêt d'urgence et vérifier son périmètre exact.
- **Critères d'acceptation :**
  - [ ] le franchissement bloque effectivement les nouveaux ordres
  - [ ] l'arrêt d'urgence n'a fermé aucune position sans activation explicite de cette option
  - [ ] les deux procédures sont documentées à partir de l'expérience réelle

### QUALITY GATE — Phase 8 — **Fin de la V2**

- [ ] Les sept points de la recette 22.2 du cahier sont satisfaits
- [ ] Tous les scénarios de reprise exécutés sans doublon ni incohérence
- [ ] Réconciliation sans écart sur une période continue
- [ ] Limites et arrêt d'urgence validés en conditions réelles
- [ ] Revue de sécurité effectuée

---

## Phase 9 — Production réelle, optionnelle

### TASK-090 — Vérification juridique et contractuelle

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-085 · **Couvre :** risque R-11
- **Critères d'acceptation :** conditions d'utilisation du fournisseur vérifiées, autorisation du trading automatisé confirmée, règles du pays de résidence vérifiées, obligations fiscales identifiées. **Tant que ce point n'est pas satisfait, la phase 9 ne démarre pas.**

### TASK-091 — Durcissement du mode réel

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** M · **Dépendances :** TASK-090 · **Couvre :** F-018, RM-000
- **Critères d'acceptation :** l'activation exige la double condition serveur et opérateur, le jeton réel est distinct et de portée minimale, et un plafond de risque réduit est imposé à l'activation.

### TASK-092 — Activation progressive et surveillance

- [ ] Statut : TODO
- **Priorité :** P0 · **Complexité :** L · **Dépendances :** TASK-091
- **Critères d'acceptation :** démarrage à risque réduit, surveillance quotidienne, montée progressive conditionnée à des résultats conformes, procédure d'incident écrite et réversibilité vérifiée.

### TASK-093 — Comparaison continue backtest contre réel

- [ ] Statut : TODO
- **Priorité :** P1 · **Complexité :** M · **Dépendances :** TASK-041, TASK-092 · **Couvre :** F-026, EF-027, risque R-12
- **Critères d'acceptation :** le rapport mensuel présente la comparaison par stratégie et alerte au-delà du seuil configuré.

---

## Tâches transversales

### TASK-100 — Revues de code

- [ ] Statut : récurrent · **Priorité :** P1
- **Skills :** `requesting-code-review`, `code-review`, `caveman-review`
- **Règle :** revue obligatoire sur `risk`, `execution` et `ai`. Revue simple ailleurs.

### TASK-101 — Vérification avant clôture

- [ ] Statut : récurrent · **Priorité :** P0
- **Skills :** `verification-before-completion`
- **Règle :** aucune tâche n'est marquée terminée sans sortie de commande exécutée à l'appui. Une intention n'est pas une preuve.

### TASK-102 — Revue de sécurité

- [ ] Statut : jalons · **Priorité :** P0 · **Échéances :** fins de phases 2, 5 et 8
- **Skills :** `security-review`
- **Couvre :** section 15 du cahier, EF-019.

### TASK-103 — Tenue du dossier de décisions

- [ ] Statut : récurrent · **Priorité :** P1
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

- [ ] le comportement attendu est implémenté et couvert par des tests écrits avant le code lorsque la tâche porte un calcul ;
- [ ] les tests passent, sortie de commande à l'appui ;
- [ ] analyse statique et typage passent ;
- [ ] le test d'architecture passe ;
- [ ] tous les critères d'acceptation de la tâche sont cochés, chacun vérifié par une exécution réelle et non par lecture du code ;
- [ ] aucun secret n'est introduit ;
- [ ] les décisions prises sont consignées ;
- [ ] la documentation concernée est à jour ;
- [ ] le contrôle qualité de la phase reste satisfait.

Pour toute tâche touchant `risk`, `execution` ou `ai`, une revue de code est exigée en plus.

---

## Final Launch Checklist

À satisfaire intégralement avant toute exécution engageant de l'argent réel.

**Fonctionnel**
- [ ] Recette 22.1 du cahier satisfaite dans ses treize points
- [ ] Recette 22.2 du cahier satisfaite dans ses sept points
- [ ] Chaque exigence P0 de la section 8.1 du cahier est vérifiée

**Risque**
- [ ] Chaque contrôle de risque testé individuellement
- [ ] Perte quotidienne maximale déclenchée volontairement et bloquante
- [ ] Arrêt d'urgence déclenché en conditions réelles, périmètre conforme
- [ ] Stop-loss confirmé présent après exécution, et non seulement envoyé
- [ ] Taille de position vérifiée conforme sur au moins dix opérations réelles

**Données et intégrité**
- [ ] Aucun doublon de signal ni d'ordre sur toute la période de démonstration
- [ ] Réconciliation sans écart sur une période continue
- [ ] Piste d'audit complète, un trade ancien restituable intégralement

**Validation des stratégies**
- [ ] Chaque stratégie active a passé backtest hors échantillon, paper trading puis compte de démonstration
- [ ] Écart entre backtest et réel mesuré et expliqué
- [ ] Décision de promotion écrite pour chacune

**Sécurité**
- [ ] Aucun secret dans le dépôt ni dans les journaux
- [ ] Jeton de portée minimale, sans droit de paiement
- [ ] Jetons distincts entre démonstration et réel
- [ ] Liste blanche Telegram vérifiée, mode réel inaccessible depuis Telegram seul
- [ ] Revue de sécurité de la phase 8 effectuée

**Exploitation**
- [ ] Service en continu depuis au moins trente jours sans incident non traité
- [ ] Redémarrage automatique vérifié
- [ ] Restauration réellement effectuée depuis une sauvegarde
- [ ] Alertes reçues lors d'incidents provoqués
- [ ] Procédures d'incident, de restauration et d'arrêt d'urgence écrites et testées

**Conformité**
- [ ] Conditions d'utilisation du fournisseur vérifiées
- [ ] Trading automatisé autorisé sur le compte
- [ ] Règles du pays de résidence vérifiées
- [ ] Obligations fiscales identifiées

**Décision**
- [ ] Autorisation explicite de l'opérateur enregistrée, avec le plafond de risque initial retenu

---

## Contrôle de cohérence effectué

**Couverture du cahier.** Chaque fonctionnalité de F-001 à F-026 est portée par au moins une tâche. Chaque exigence P0 de la section 8.1 est rattachée à une tâche et à un critère de recette. Les règles RM-000 à RM-018 sont couvertes par TASK-035, TASK-036, TASK-037, TASK-040 et TASK-081.

**Points volontairement non planifiés.** L'export PDF (EF-031) et le rôle lecteur sont écartés de la version 1 par Q-17 et Q-20, et rangés en Future. L'analyse par régime de marché n'est présente qu'au titre de la recherche, en TASK-063.

**Cohérence des dépendances.** Aucune tâche ne précède une tâche dont elle consomme la sortie. Les trois interdictions de parallélisation sont explicites.

**Skills.** Tous les skills cités ont été vérifiés comme présents dans le catalogue installé. L'absence de skill de trading est signalée explicitement plutôt que comblée par une invention.

**Révision 1.1.** Le périmètre marchés a changé après vérification du compte : les indices synthétiques sont indisponibles depuis la France, remplacés par deux à quatre cryptomonnaies. Le capital de référence est fixé à 100 €, ce qui introduit la règle RM-019 et des seuils de risque différenciés par mode. Quarante skills de domaine ont été installés et mappés aux tâches. L'architecture n'est pas affectée : elle était multi-marchés et agnostique quant à la classe d'actif, ce qui a limité l'impact d'un changement de périmètre majeur à de la configuration et à de la recherche.

**Point d'attention introduit en 1.1.** À 100 € de capital, le mode réel ne produit pas de statistiques interprétables. Le risque le plus sérieux du projet n'est plus technique mais interprétatif : conclure qu'une stratégie fonctionne ou échoue à partir d'une poignée d'opérations réelles. Le risque R-14 traite ce point, et la séparation stricte des chiffres réels et de démonstration dans les rapports en est la seule protection.

**Point d'attention subsistant.** Le chemin critique est TASK-003 puis TASK-004. Tant que la sémantique réelle des contrats Deriv n'est pas établie par des relevés, TASK-035 et toute la phase 8 reposent sur une hypothèse. Aucune estimation d'effort sur ces tâches n'est fiable avant ce relevé.
