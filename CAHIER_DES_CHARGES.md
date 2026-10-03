# Cahier des charges — Agent de signaux de trading Deriv

**Version :** 1.2
**Date :** 2026-10-01
**Statut :** Spécification de référence — stratégies non définies, en attente des backtests
**Propriétaire :** Daniel (opérateur unique), résident fiscal France
**Type de projet :** Agent logiciel autonome 24/7, sans interface graphique (Telegram est l'unique interface utilisateur)

### Journal des révisions

| Version | Changement | Motif |
|---|---|---|
| 1.0 | Version initiale | — |
| 1.2 | **Accès au courtier modifié : MetaTrader 5 au lieu de l'API WebSocket Deriv.** C-003 et Q-01 tranchées en faveur de l'option C. Ajout de C-010. Hébergement Windows au lieu de Linux. Sections 10.1, 12.1 et 17, F-001, F-016 et RM-017 réécrites | L'API Deriv n'est pas accessible à un résident français : la création de jeton renvoie « indisponible dans votre pays », et la liste des pays de l'API ne cite la France que pour MT5. Constat du 2026-10-03 |
| 1.1 | **Périmètre marchés modifié :** les indices synthétiques Deriv sont retirés, remplacés par deux à quatre cryptomonnaies en contrat pour différence. Capital de référence fixé à 100 €. Règles de risque RM-005 à RM-007 recalibrées. Ajout de la contrainte C-008 et de la règle RM-019 | Vérification du compte réel : les indices synthétiques ne sont pas accessibles à un résident français, l'entité européenne de Deriv ne les proposant pas. Le risque R-11 du présent document s'est matérialisé avant tout développement |

---

## Légende des statuts d'information

Chaque affirmation de ce document porte un statut. Ne jamais traiter une proposition comme une exigence validée.

| Marqueur | Signification |
|---|---|
| `[CONFIRMÉ]` | Fourni explicitement par le propriétaire du projet |
| `[DÉDUIT]` | Conséquence logique d'un élément confirmé |
| `[PROPOSITION]` | Recommandation de l'équipe de conception, modifiable |
| `[À CONFIRMER]` | Décision manquante, bloquante ou non bloquante (précisé au cas par cas) |
| `[CONTRAINTE]` | Imposé par un tiers (API, réglementation, marché) |

---

## 1. Résumé exécutif

Le projet consiste à construire un agent logiciel fonctionnant en continu sur un serveur distant, qui surveille un panier restreint de marchés Deriv (l'or `frxXAUUSD` et deux à quatre cryptomonnaies en contrat pour différence), applique une stratégie propre à chaque marché, et transmet les signaux d'entrée et de sortie à l'opérateur via un bot Telegram. `[CONFIRMÉ]`

Le choix des cryptomonnaies remplace celui des indices synthétiques, inaccessibles depuis la France. Il préserve la propriété recherchée à l'origine : un marché ouvert en continu, y compris le week-end, période durant laquelle l'or est fermé. L'agent dispose ainsi en permanence d'au moins un marché actif. `[DÉDUIT]`

Le système est conçu en trois couches strictement séparées : une couche **stratégie** déterministe qui produit des signaux candidats, une couche **intelligence artificielle** qui explique et peut filtrer ces signaux sans jamais en créer, et une couche **gestion du risque** qui dispose du dernier mot et peut refuser toute opération. `[CONFIRMÉ]`

L'exécution automatique des ordres est prévue mais désactivée par défaut. Son activation est conditionnée à la validation successive d'un backtest hors échantillon, d'une campagne de paper trading, puis d'une campagne sur compte de démonstration Deriv. `[CONFIRMÉ]`

La recherche de stratégies et le backtesting constituent un atelier séparé, hors du processus de production. L'agent embarque néanmoins la bibliothèque de calcul des indicateurs de performance et l'interface de stratégie, afin qu'une stratégie testée et une stratégie exécutée produisent des chiffres comparables. `[PROPOSITION]` — voir la contradiction C-001.

Aucune rentabilité n'est garantie. Le système est un outil d'analyse, d'automatisation et de contrôle du risque. `[CONFIRMÉ]`

---

## 2. Contexte et vision

### 2.1 Problème

L'opérateur souhaite exploiter un petit nombre de marchés avec une discipline systématique, sans devoir surveiller les graphiques en permanence ni laisser son poste de travail allumé. La surveillance manuelle est incompatible avec des marchés qui cotent 24 heures sur 24, ce qui est le cas des cryptomonnaies retenues. `[DÉDUIT]`

### 2.2 Vision

Une plateforme progressive et réversible, où chaque étape doit être validée avant d'autoriser la suivante :

1. collecte et stockage fiables des données Deriv ;
2. génération de signaux et notification Telegram ;
3. recherche et backtesting de stratégies hors production ;
4. validation hors échantillon ;
5. paper trading en temps réel ;
6. exécution automatique sur compte de démonstration ;
7. exécution réelle optionnelle, à risque réduit, sous surveillance renforcée.

`[CONFIRMÉ]`

### 2.3 Principe directeur

L'agent ne modifie jamais sa propre stratégie en production. Toute nouvelle version de stratégie est testée, validée, puis activée explicitement par l'opérateur. `[CONFIRMÉ]`

La sélection d'une stratégie ne se fait pas sur le profit historique le plus élevé, mais sur la robustesse : stabilité hors échantillon, résistance aux coûts de transaction et au slippage, stabilité par période. `[CONFIRMÉ]`

---

## 3. Périmètre

### 3.1 Inclus

| Élément | Statut |
|---|---|
| Or, symbole Deriv `frxXAUUSD` | `[CONFIRMÉ]` |
| Deux à quatre cryptomonnaies en contrat pour différence, à sélectionner | `[CONFIRMÉ]` / sélection `[À CONFIRMER]`, voir Q-07 |
| Connexion unique au terminal MetaTrader 5 de Deriv, source des données et de l'exécution | `[CONFIRMÉ]` en version 1.2, voir C-010 |
| Génération de signaux et notification Telegram | `[CONFIRMÉ]` |
| Commandes de pilotage et d'arrêt d'urgence via Telegram | `[CONFIRMÉ]` |
| Couche de gestion du risque indépendante et prioritaire | `[CONFIRMÉ]` |
| Journalisation complète et piste d'audit | `[CONFIRMÉ]` |
| Calcul des indicateurs de performance et rapports automatiques | `[CONFIRMÉ]` |
| Paper trading avant toute exécution | `[CONFIRMÉ]` |
| Exécution automatique sur compte de démonstration | `[CONFIRMÉ]` |
| Exécution réelle, activation séparée et explicite | `[CONFIRMÉ]`, optionnelle |
| Atelier de recherche et de backtesting, hors production | `[CONFIRMÉ]` |

### 3.2 Exclu de la version 1

| Élément | Statut |
|---|---|
| Marchés autres que l'or et les cryptomonnaies retenues | `[CONFIRMÉ]` |
| **Indices synthétiques Deriv** | **Retirés du périmètre en version 1.1** — indisponibles pour un résident français, voir C-008. Réintégrables uniquement si la résidence fiscale ou l'entité du compte change |
| Analyse de l'ensemble des marchés disponibles | `[CONFIRMÉ]` |
| Moteur de backtest exécuté à l'intérieur du processus de production | `[CONFIRMÉ]` — voir C-001 |
| Lecture visuelle de TradingView par un modèle | `[CONFIRMÉ]` |
| Trading haute fréquence | `[CONFIRMÉ]` |
| Modèle d'IA comme unique moteur de décision | `[CONFIRMÉ]` |
| Auto-modification des stratégies par l'agent | `[CONFIRMÉ]` |
| Entraînement d'un modèle propriétaire | `[CONFIRMÉ]` |
| Gestion de fonds de tiers | `[CONFIRMÉ]` |
| Retraits ou transferts automatiques | `[CONFIRMÉ]` |
| Interface web ou application mobile | `[DÉDUIT]` — Telegram couvre le besoin |

---

## 4. Contradictions détectées

Les points suivants opposent deux informations fournies. Aucun ne doit être tranché implicitement par le développeur.

### C-001 — Backtesting intégré ou externe

**Information A :** le cahier initial exige un moteur de backtesting complet parmi les livrables, avec import d'historique, simulation d'ordres, coûts, statistiques et export.
**Information B :** le périmètre Deriv exclut explicitement « le backtesting intégré à l'agent lui-même », le travail étant mené séparément avec Claude Code.
**Conflit :** un moteur de backtest est exigé comme livrable mais exclu du produit.
**Impact :** si les deux univers sont développés séparément, la même stratégie sera implémentée deux fois, et les indicateurs de performance du backtest ne seront pas comparables à ceux de la production. Le critère « comparaison backtest contre réel » deviendrait invérifiable.
**Résolution retenue `[CONFIRMÉ]` :** code partagé, processus séparés. Le dépôt contient un paquet `strategies` définissant l'interface et les stratégies, un paquet `analytics` calculant les indicateurs de performance, et un paquet `backtest` contenant le harnais de simulation. Le processus de production importe `strategies` et `analytics` mais **n'importe ni ne démarre jamais `backtest`**. Le backtest est un outil de ligne de commande exécuté à la demande, de préférence sur une machine distincte.

**Double motif.** D'abord la ressource : le serveur cible dispose de deux cœurs virtuels et de quatre gigaoctets de mémoire, tandis qu'un backtest charge plusieurs années de données et sature le processeur. Exécuté dans le même processus, il pourrait ralentir ou interrompre l'agent **alors qu'une position est ouverte**. Ensuite la cohérence : une séparation totale conduirait à réimplémenter chaque stratégie et chaque indicateur deux fois, et les chiffres du backtest cesseraient d'être comparables à ceux de la production, ce qui rendrait invérifiable l'exigence EF-027.

**Contrôle associé :** le test d'architecture de TASK-002 vérifie qu'aucun module chargé en production n'importe `backtest`, au même titre qu'il vérifie que seul `risk` atteint `execution`.
**Décision requise de :** aucune, résolue.

### C-002 — Pouvoir de décision de la couche IA

**Information A :** le module de décision IA « confirme ou rejette un signal candidat ».
**Information B :** l'IA « ne devra pas avoir le droit de contourner la gestion du risque », ne doit pas être un moteur de décision, et en cas d'indisponibilité « les règles déterministes doivent continuer à fonctionner ».
**Conflit :** un droit de confirmation accorde à l'IA un pouvoir de décision positif sur l'ouverture d'une position, ce que le principe de séparation interdit.
**Impact :** un signal serait non reproductible et non auditable si un modèle non déterministe pouvait l'autoriser. En cas d'indisponibilité du modèle, le comportement serait ambigu.
**Résolution retenue `[CONFIRMÉ]` :** l'IA reçoit un droit de veto asymétrique. Elle ne peut que dégrader une décision, jamais l'améliorer. Concrètement, elle peut marquer un signal candidat comme rejeté ou abaisser un niveau de confiance, elle ne peut jamais transformer un signal absent en signal présent, ni relever une taille de position, ni desserrer un stop.

**Motif du principe :** un modèle de langage n'est pas reproductible. Un signal qu'il aurait autorisé ne pourrait pas être rejoué ni audité a posteriori, ce qui viole l'exigence de traçabilité F-020. Le veto asymétrique préserve la reproductibilité : l'ensemble des signaux réellement émis reste un sous-ensemble de ce que les règles déterministes ont produit.

**Trois états possibles du filtre, déclarés par stratégie via le paramètre `ai_filter` :**

| État | Comportement du verdict | Comportement en cas de panne du modèle |
|---|---|---|
| `shadow` | Le verdict est **calculé, journalisé, mais non appliqué**. Le signal part quel que soit l'avis du modèle | Le signal part, avec une explication de repli générée localement |
| `advisory` | Le verdict est appliqué, mais son rejet est signalé comme non bloquant et reste visible | Le signal part, marqué comme dégradé |
| `required` | Le verdict est appliqué et un rejet bloque le signal | Le signal est refusé, comportement de défaut fermé |

**Valeur par défaut retenue : `shadow` pour toute stratégie nouvellement déployée. `[CONFIRMÉ]`**

**Motif du mode observateur.** Aucune donnée ne permet aujourd'hui d'affirmer que le filtre par modèle améliore les résultats. Le rendre bloquant dès le départ rendrait cette question définitivement non mesurable, puisque les signaux bloqués ne seraient jamais observés. En mode `shadow`, le verdict est enregistré sur chaque signal sans être appliqué, ce qui permet, après un échantillon suffisant, de calculer la performance qu'aurait produite le filtre et de la comparer à la performance réellement obtenue, **sur les mêmes opérations**. Le passage de `shadow` à `advisory` puis à `required` devient alors une décision fondée sur une mesure, et non sur une intuition.

**Condition de promotion du filtre `[PROPOSITION]` :** au moins cent signaux évalués, et une amélioration du facteur de profit et du drawdown maximal qui résiste à l'exclusion des cinq meilleures opérations. Ce dernier critère évite de promouvoir un filtre dont l'apport tient à quelques coups de chance.
**Décision requise de :** aucune, résolue. Structure TASK-037 et introduit TASK-044.

### C-003 — Type de contrat Deriv, stop-loss et taille de position `[CONTRAINTE]`

**Information A :** la spécification décrit des positions classiques avec stop-loss obligatoire, take-profit, ratio risque/rendement, taille de position calculée en pourcentage du capital, trailing stop et sorties partielles.
**Information B :** l'exécution passe par l'API Deriv (`buy`), qui négocie des contrats et non des positions au comptant. Selon le type de contrat, un stop-loss au sens classique peut ne pas exister.
**Conflit :** les règles de risque du cahier supposent une sémantique de position qui dépend entièrement du type de contrat retenu, lequel n'est pas décidé.
**Impact :** majeur et structurant. Ce choix détermine le calcul de la taille de position, la faisabilité du stop-loss obligatoire, la définition même du ratio risque/rendement, la forme des règles de sortie, et la nature des données de backtest. Une erreur ici invalide le moteur de risque et le backtest.
**Options `[PROPOSITION]` :**

| Option | Sémantique | Stop-loss | Conséquence |
|---|---|---|---|
| A — Contrats à multiplicateur | Position directionnelle avec effet de levier, mise en devise du compte | Stop-loss et take-profit natifs, transmis à l'ordre | Sémantique la plus proche du cahier. Option recommandée, sous réserve de disponibilité sur les symboles retenus |
| B — Contrats à durée fixe (hausse/baisse) | Pari binaire sur une durée | Aucun stop-loss ; le risque est la mise, connue d'avance | Le ratio risque/rendement, le trailing stop et les sorties partielles perdent leur sens. Exigerait la réécriture des règles RM-004 à RM-008 |
| C — Compte Deriv MT5 | CFD classique, lots, stop-loss et take-profit | Natifs | **Retenue en version 1.2.** Sémantique idéale. L'objection de la version 1.0, selon laquelle les données et l'exécution passeraient par deux canaux, tombe : sans l'API WebSocket, données et ordres passent tous deux par le terminal MT5 |

**Décision retenue le 2026-10-03 `[CONFIRMÉ]` : option C, compte Deriv MT5.** L'option A est indisponible pour un résident français (C-010) et l'option B interdite aux particuliers de l'Union (C-008). Reste à mesurer en TASK-003 les spécifications de contrat de chaque symbole (taille de contrat, lot minimal, pas de lot, valeur du point, marge), indispensables à la formule de taille de position de TASK-004.
**Vérification préalable obligatoire :** interroger l'API Deriv (`active_symbols`, `contracts_for`) pour établir, symbole par symbole et pour le type de compte réellement détenu, quels types de contrats sont disponibles et lesquels acceptent des paramètres de stop-loss et de take-profit. Cette vérification est l'objet de TASK-003.

### C-004 — Langage d'implémentation

**Information A :** la recherche et le backtesting utilisent Python, avec pandas et une bibliothèque de backtest.
**Information B :** l'agent de production est décrit comme « Node.js/Python », sans arbitrage.
**Conflit :** deux langages possibles pour un système qui doit partager le code de stratégie et de calcul des indicateurs entre recherche et production.
**Impact :** un agent en Node.js imposerait de réimplémenter chaque stratégie et chaque indicateur dans les deux langages, ce qui réintroduit le risque d'écart décrit en C-001.
**Résolution retenue `[CONFIRMÉ]` :** Python 3.12 pour l'ensemble, agent de production comme atelier de recherche. Justification : partage du code de stratégie et d'indicateurs, écosystème d'analyse mature, et absence de besoin de concurrence extrême, la charge visée étant de trois à cinq symboles sur des bougies.
**Décision requise de :** aucune, résolue.

### C-005 — Moteur de base de données

**Information A :** le cahier initial impose PostgreSQL.
**Information B :** la spécification Deriv évoque « une base légère, SQLite ou Postgres ».
**Impact :** faible si l'accès aux données est abstrait dès le départ.
**Résolution proposée `[PROPOSITION]` :** SQLAlchemy et Alembic dès la première ligne, SQLite en mode WAL pour le développement et la phase de signaux, bascule vers PostgreSQL avant la phase d'exécution sur compte de démonstration. La bascule est alors une variable d'environnement et une exécution de migrations.
**Décision requise de :** non bloquante, mais la bascule doit être planifiée avant TASK-080.

### C-006 — Fréquence des rapports

**Information A :** le cahier initial exige des rapports quotidiens, hebdomadaires et mensuels.
**Information B :** la spécification Deriv laisse le choix ouvert entre quotidien et hebdomadaire.
**Résolution proposée `[PROPOSITION]` :** implémenter les trois périodicités dans un générateur unique paramétré par une fenêtre temporelle, et n'activer que celles retenues par configuration. Le coût marginal des deux périodicités supplémentaires est alors négligeable.
**Décision requise de :** non bloquante.

### C-007 — Mode paper trading

**Information A :** le cahier initial définit quatre modes de fonctionnement, dont un mode paper trading obligatoire avant toute exécution.
**Information B :** la spécification Deriv ne décrit que deux modes, signal et exécution automatique.
**Résolution proposée `[PROPOSITION]` :** conserver les quatre modes du cahier initial. Le paper trading est la seule manière de mesurer la performance d'une stratégie sur des données futures sans exposition, et c'est un prérequis explicite du cahier initial. Le supprimer reviendrait à passer du backtest au compte de démonstration sans étape intermédiaire mesurée.
**Décision requise de :** non bloquante, mais structure la roadmap (Phase 7).

### C-008 — Résidence française et périmètre produit `[CONTRAINTE]` — **résolue en version 1.1**

**Information A :** le périmètre initial repose sur trois à cinq indices synthétiques Deriv, qui constituent la majorité des marchés surveillés.
**Information B :** l'opérateur est résident français. Un compte Deriv ouvert depuis la France relève de l'entité européenne du groupe, soumise au droit de l'Union.
**Constat vérifié le 2026-10-01 :** les indices synthétiques ne sont pas accessibles sur le compte de l'opérateur. `[CONFIRMÉ]`
**Conséquences :**

- les indices synthétiques sont un produit propriétaire de Deriv, généré algorithmiquement, et non un marché réel. **Aucun autre courtier ne peut les fournir.** Changer de courtier ne résout pas ce point, contrairement à ce qui vaudrait pour un actif ordinaire ;
- les options binaires, c'est-à-dire les contrats à durée fixe de type hausse ou baisse, sont interdites aux particuliers dans l'Union européenne. **L'option B de C-003 est donc écartée définitivement**, non pour des raisons techniques mais réglementaires `[À VÉRIFIER auprès du fournisseur, mais l'hypothèse de travail est l'interdiction]` ;
- le levier applicable à l'or est plafonné réglementairement, ce qui augmente la marge requise et interagit avec la contrainte de capital décrite en C-009.

**Résolution retenue `[CONFIRMÉ]` :** le périmètre devient l'or plus deux à quatre cryptomonnaies en contrat pour différence. Le critère de substitution n'est pas la ressemblance statistique avec les synthétiques, qu'aucun actif réel ne reproduit, mais la propriété opérationnelle recherchée : **un marché ouvert en continu**, afin que l'agent ne soit pas inactif du vendredi soir au dimanche soir.
**Conséquence sur l'architecture :** aucune. Le moteur est multi-marchés et agnostique quant à la classe d'actif. Seules la configuration des marchés et la recherche de stratégies sont affectées.
**Conséquence sur la recherche :** significative. Les cryptomonnaies présentent une volatilité, une structure de tendance et un comportement de week-end différents de ceux de l'or. Aucune stratégie n'est transférable de l'un à l'autre sans validation propre, conformément à RM-003.

### C-009 — Capital de référence contre tailles minimales du courtier `[CONTRAINTE]`

**Information A :** les règles de risque prévoient un risque par opération exprimé en pourcentage du capital, avec une valeur de référence de 0,5 %.
**Information B :** le capital de référence est fixé à 100 €. `[CONFIRMÉ]`
**Conflit :** 0,5 % de 100 € représente 0,50 € de risque par opération. Ce montant est inférieur au risque minimal imposé par la taille de position la plus petite négociable, quel que soit l'instrument. Sur l'or en contrat pour différence, la marge requise pour la position minimale dépasse vraisemblablement le capital total.
**Impact :** la règle RM-005 est mathématiquement inapplicable en l'état. Sans correction, le moteur de risque refuserait systématiquement toute opération, ce qui est un comportement correct mais inutile.
**Résolution retenue `[CONFIRMÉ]` :** conserver le capital de référence à 100 € et recalibrer les règles de risque, en acceptant explicitement les deux conséquences suivantes.

1. **Le risque par opération passe de 0,5 % à une fourchette de 2 à 5 %**, imposée par le plancher du courtier et non choisie. Voir RM-005 révisée.
2. **La significativité statistique est perdue.** Avec une perte quotidienne maximale de 5 €, deux opérations perdantes clôturent la journée. Le nombre d'opérations réalisables par mois est trop faible pour qu'un taux de réussite, une espérance ou un facteur de profit mesurés en réel soient interprétables. `[CONTRAINTE]`

**Conséquence méthodologique majeure :** la validation d'une stratégie ne peut pas provenir du compte réel à ce niveau de capital. Elle provient du backtest, du paper trading et du compte de démonstration, où la taille n'est pas contrainte. Le compte réel à 100 € sert à vérifier que la mécanique d'exécution fonctionne, **pas à mesurer une performance**. Ces deux objectifs ne doivent jamais être confondus dans les rapports.

**Règle nouvelle introduite :** RM-019, éligibilité d'un instrument au mode réel.

### C-010 — API Deriv inaccessible depuis la France `[CONTRAINTE]` — **résolue en version 1.2**

**Information A :** l'architecture des versions 1.0 et 1.1 repose sur l'API WebSocket de Deriv, qui fournirait à la fois les données et l'exécution.
**Information B :** constat du 2026-10-03 — la création d'un jeton d'API depuis le compte de l'opérateur renvoie « indisponible dans votre pays ». La liste des pays publiée par Deriv pour son API ne cite la France que dans la section MetaTrader 5. La nouvelle API Deriv ne documente par ailleurs que des options, interdites aux particuliers de l'Union. `[CONFIRMÉ]` pour le message, `[DÉDUIT]` pour la cause, vérification auprès du support Deriv recommandée.
**Résolution retenue `[CONFIRMÉ]` :** accès au courtier par **MetaTrader 5**, au moyen du paquet Python officiel `MetaTrader5` piloté par un terminal MT5 connecté au compte.

**Conséquences :**

| Domaine | Avant | Après |
|---|---|---|
| Données et exécution | API WebSocket | Terminal MT5, **une seule source pour les deux** |
| Sémantique d'ordre | Contrat à déterminer (C-003) | Lots, stop-loss et take-profit natifs, conformes aux règles RM-004 à RM-008 |
| Hébergement | Serveur Linux, environ 5 € par mois | **Windows obligatoire** : le paquet `MetaTrader5` et le terminal ne fonctionnent que sous Windows. Développement et démonstration sur le poste de l'opérateur, serveur Windows en production. Coût `[À CONFIRMER]` |
| Flux temps réel | Abonnement poussé | **Interrogation périodique** : le paquet n'offre pas d'abonnement. Suffisant pour des stratégies évaluées à la clôture de bougie |
| Concurrence | `asyncio` natif | Appels MT5 **bloquants et non réentrants**, exécutés dans un fil dédié unique |
| Horodatage | Temps universel | **Le terminal date les bougies à l'heure du serveur du courtier**, qui n'est en général pas UTC. Le décalage doit être mesuré en TASK-003 et converti à l'entrée, faute de quoi l'invariant « tout en UTC » serait violé en silence |
| Noms de symboles | `frxXAUUSD` et similaires | Noms propres au serveur MT5 de Deriv, `[À CONFIRMER]` en TASK-003 |

**Ce qui ne change pas :** indicateurs, interface de stratégie, point d'entrée `evaluate()`, configuration, contrôle du risque, Telegram, rapports. Seuls les connecteurs `data` et `execution` sont concernés, ce qui confirme l'intérêt de leur isolement.

---

## 5. Acteurs, rôles et permissions

Le système ne comporte pas de gestion de comptes utilisateur. L'identité est l'identifiant numérique Telegram, comparé à une liste blanche. `[PROPOSITION]`

| Rôle | Objectif | Permissions | Données accessibles |
|---|---|---|---|
| `OWNER` | Piloter l'agent | Toutes les commandes, y compris les commandes sensibles et l'arrêt d'urgence | Toutes |
| `VIEWER` (optionnel) | Consulter sans agir | Commandes de lecture uniquement : statut, marchés, positions, performance, rapports | Lecture seule, sans identifiants de compte |
| Non listé | Aucun | Toute commande est refusée, journalisée, et non confirmée à l'émetteur | Aucune |

Composants système, qui ne sont pas des rôles utilisateur mais dont les droits sont contraints :

| Composant | Droits accordés | Droits explicitement refusés |
|---|---|---|
| Couche stratégie | Émettre un signal candidat | Passer un ordre, écrire dans la table des ordres |
| Couche IA | Rejeter un candidat, abaisser une confiance, rédiger un texte | Créer un signal, modifier une taille, modifier un stop, modifier une limite de risque, activer un mode, écrire en base autrement que dans sa propre table de traces |
| Couche risque | Refuser ou réduire, calculer la taille, autoriser l'envoi | Créer un signal |
| Exécuteur | Envoyer, modifier et clôturer des ordres | Opérations de paiement ou de retrait, changement de compte |

---

## 6. Modes de fonctionnement

Quatre modes, mutuellement exclusifs, persistés en base et modifiables par commande Telegram. `[CONFIRMÉ]`

| Mode | Signaux calculés | Message Telegram | Ordres | Exposition |
|---|---|---|---|---|
| `OBSERVATION` | Oui, journalisés | Non, hors alertes système | Non | Aucune |
| `SIGNAL` | Oui | Oui | Non | Décision manuelle de l'opérateur |
| `PAPER` | Oui | Oui | Simulés en interne | Aucune |
| `DEMO` | Oui | Oui | Réels sur compte de démonstration Deriv | Aucune |
| `LIVE` | Oui | Oui | Réels sur compte réel | Réelle |

**RM-000 :** le mode par défaut au démarrage et après toute réinstallation est `SIGNAL`. Le mode `LIVE` ne peut pas être activé par une commande Telegram seule ; il exige en plus une variable d'environnement serveur explicite. `[PROPOSITION]` — cette double condition rend impossible un passage en réel par une simple compromission du bot.

---

## 7. Fonctionnalités

Les fonctionnalités critiques sont spécifiées intégralement. Les autres sont décrites de façon compacte, avec leurs critères d'acceptation.

### F-001 — Ingestion des données de marché en temps réel

**Description :** maintenir une connexion unique au terminal MetaTrader 5 et en lire les ticks et bougies des symboles autorisés. `[révisé en 1.2, voir C-010]`
**Acteurs :** système, API Deriv.
**Préconditions :** jeton d'API valide en variable d'environnement, liste blanche de symboles chargée.
**Déclencheur :** démarrage du processus, ou reconnexion.
**Fonctionnement :** le client initialise le terminal MT5 avec le compte, le serveur et le mot de passe configurés, vérifie le type de compte, mesure le décalage entre l'heure du serveur du courtier et l'heure universelle, puis sélectionne uniquement les symboles de la liste blanche. Les bougies et ticks sont lus par interrogation périodique, depuis un fil dédié, car les appels du paquet `MetaTrader5` sont bloquants. Chaque donnée est convertie en temps universel à l'entrée, associée à sa source, et transmise au module de normalisation.
**Données utilisées :** ticks, bougies, heure serveur, état des symboles.
**Règles métier :** RM-001, RM-002.
**Cas nominal :** les ticks arrivent, les bougies sont construites, l'horodatage de dernière donnée par symbole est mis à jour.
**Cas d'erreur :** échec d'authentification, symbole refusé par l'API, dépassement de quota de requêtes, message malformé. Chaque cas est journalisé avec son code d'erreur d'origine, sans interruption des autres symboles.
**Cas limites :** reprise après une coupure longue, tick reçu avec un horodatage antérieur au précédent, symbole temporairement suspendu par le fournisseur.
**Critères d'acceptation :**
- la connexion s'établit et s'authentifie avec un jeton valide ;
- aucun abonnement n'est ouvert pour un symbole absent de la liste blanche ;
- un symbole refusé n'empêche pas les autres de fonctionner ;
- l'écart entre l'heure serveur Deriv et l'heure locale est mesuré, journalisé, et déclenche une alerte au-delà d'un seuil configurable.

### F-002 — Normalisation, agrégation et contrôle qualité des données

**Description :** transformer le flux brut en bougies exploitables par unité de temps, et garantir qu'aucune décision n'est prise sur des données douteuses.
**Fonctionnement :** les ticks sont agrégés en bougies par unité de temps configurée. Les bougies fournies directement par l'API sont préférées lorsqu'elles existent, l'agrégation locale servant de complément et de contrôle croisé. Chaque série porte un état de santé : fraîcheur de la dernière donnée, présence de trous, doublons détectés.
**Règles métier :** RM-002.
**Critères d'acceptation :**
- une bougie n'est réputée close qu'après réception d'une donnée appartenant à la période suivante, ou après expiration d'un délai de garde ;
- un trou dans la série est détecté et journalisé, et marque la série comme dégradée ;
- un doublon est rejeté sans altérer la bougie existante ;
- une série dégradée ou périmée empêche l'émission de tout signal sur ce symbole, ce comportement étant couvert par un test automatisé.

### F-003 — Résilience de la connexion

**Description :** détecter les coupures et rétablir le service sans intervention.
**Fonctionnement :** surveillance par battement de cœur et par absence de données. À la coupure, tentatives de reconnexion avec délai progressif et plafonné, puis restauration de tous les abonnements, puis rattrapage de l'historique manquant.
**Critères d'acceptation :**
- une coupure simulée est détectée en moins d'un délai configurable ;
- les abonnements sont restaurés à l'identique après reconnexion ;
- aucun signal n'est émis entre la détection de la coupure et la restauration d'une série saine ;
- une coupure dépassant un seuil configurable déclenche une alerte Telegram et suspend le trading, conformément à RM-013.

### F-004 — Liste blanche des marchés et configuration par marché

**Description :** aucun symbole n'est analysé s'il n'est pas déclaré dans la configuration. `[CONFIRMÉ]`
**Fonctionnement :** un fichier de configuration déclare pour chaque marché le symbole technique exact, son état d'activation, la stratégie et la version associées, les unités de temps requises, et les éventuels paramètres de risque spécifiques. Le chargement échoue explicitement si un symbole est inconnu de l'API ou si une stratégie référencée n'existe pas.
**Critères d'acceptation :**
- un symbole absent de la configuration n'est jamais souscrit ni analysé ;
- une configuration invalide empêche le démarrage avec un message d'erreur désignant la ligne fautive ;
- la désactivation d'un marché par commande Telegram est persistée et survit à un redémarrage.

### F-005 — Connaissance des horaires de marché

**Description :** distinguer les cryptomonnaies, qui cotent en continu, de l'or, dont le marché ferme le week-end. `[CONFIRMÉ]`
**Fonctionnement :** interrogation périodique des horaires de négociation publiés par l'API pour chaque symbole, mise en cache, et exposition d'un état de marché consultable par les autres modules.
**Critères d'acceptation :**
- aucun signal n'est émis sur un symbole dont le marché est fermé ;
- la réouverture est détectée sans redémarrage ;
- l'état du marché figure dans le message de signal et dans la réponse à la commande de statut.

### F-006 — Moteur d'indicateurs

**Description :** bibliothèque de calcul d'indicateurs techniques, déterministe et testée unitairement.
**Fonctionnement :** fonctions pures prenant une série de bougies et des paramètres, retournant une série de valeurs. Aucun accès réseau, aucun état global, aucune dépendance à l'heure courante.
**Critères d'acceptation :**
- chaque indicateur est couvert par un test comparant sa sortie à des valeurs de référence calculées indépendamment ;
- le calcul est identique en backtest et en production, la même fonction étant appelée ;
- une série trop courte pour l'indicateur retourne une absence de valeur explicite, jamais une valeur approchée.

### F-007 — Moteur de stratégies interchangeables

**Description :** permettre de brancher une stratégie validée par la recherche sans modifier le code du moteur. `[CONFIRMÉ]`
**Acteurs :** opérateur, système.
**Préconditions :** stratégie validée par le processus d'adoption (section 9).
**Déclencheur :** dépôt ou modification d'un fichier de configuration de stratégie.
**Fonctionnement :** une stratégie se compose d'un manifeste de configuration et d'un module de calcul. Le manifeste déclare au minimum l'identifiant, la version, l'état, les symboles autorisés, les unités de temps, la quantité minimale de données requise, les paramètres, les paramètres de risque et les périodes d'interdiction. Toutes les stratégies exposent la même interface, de sorte que chaque marché fonctionne indépendamment.
**Règles métier :** RM-003, RM-016.
**Cas d'erreur :** manifeste invalide, version inconnue, symbole non autorisé pour cette stratégie, données insuffisantes.
**Critères d'acceptation :**
- une stratégie déclarée pour l'or ne peut pas être appliquée à une paire crypto sans modification explicite de son manifeste ;
- un manifeste invalide est refusé au chargement, l'ancienne configuration restant active, et l'incident est notifié ;
- le rechargement d'une configuration ne nécessite pas de redéploiement complet ;
- **limite explicite `[CONTRAINTE]` :** un changement de paramètres se fait par configuration seule, mais un changement de logique exige un nouveau module de code, testé et déployé. Cette limite contredit l'affirmation « pas de code à toucher pour changer une stratégie », qui n'est vraie que pour les changements de paramètres. Elle doit être comprise avant toute planification de la recherche.

### F-008 — Versionnement des stratégies

**Description :** conserver l'historique complet des versions et des paramètres, pour que chaque trade passé reste explicable.
**Critères d'acceptation :**
- toute modification de paramètres crée une nouvelle version enregistrée, l'ancienne n'étant jamais écrasée ;
- chaque signal référence l'identifiant de version exact qui l'a produit ;
- il est possible de retrouver, pour un trade clos depuis plusieurs mois, les paramètres exacts en vigueur au moment de son ouverture.

### F-009 — Génération d'un signal candidat

**Description :** produire, à la clôture d'une bougie, un signal candidat lorsque les conditions d'entrée sont réunies.
**Déclencheur :** clôture d'une bougie sur une unité de temps surveillée, données saines, marché ouvert, stratégie active.
**Fonctionnement :** le moteur appelle la stratégie avec les données requises. La stratégie retourne une absence de signal, ou un signal candidat comportant le sens, le prix observé, la zone d'entrée, le stop-loss, les objectifs, et les valeurs d'indicateurs ayant motivé la décision. Ces valeurs sont persistées pour l'audit.
**Règles métier :** RM-001, RM-002, RM-003, RM-009.
**Cas d'erreur :** exception dans le module de stratégie. Le signal est abandonné, l'incident journalisé et notifié, et la stratégie est mise en quarantaine après un nombre configurable d'échecs consécutifs.
**Critères d'acceptation :**
- au plus un signal candidat par stratégie, par symbole et par bougie ;
- les valeurs d'indicateurs ayant produit le signal sont enregistrées et consultables ;
- une exception dans une stratégie n'interrompt pas le traitement des autres marchés.

### F-010 — Couche d'analyse par intelligence artificielle

**Description :** expliquer un signal en langage naturel et, le cas échéant, le filtrer. `[CONFIRMÉ]`
**Déclencheur :** production d'un signal candidat uniquement. L'appel n'est jamais effectué à chaque tick. `[CONFIRMÉ]`
**Fonctionnement :** le contexte transmis au modèle est strictement borné : symbole, unité de temps, stratégie et version, valeurs d'indicateurs, niveaux proposés, état du marché. Le modèle retourne une réponse structurée comportant une décision de filtrage, un motif et un texte explicatif. La requête et la réponse sont persistées intégralement pour l'audit.
**Règles métier :** RM-010, RM-011.
**Cas d'erreur :** délai dépassé, indisponibilité du service, réponse non conforme au schéma attendu, budget dépassé. Le comportement est déterminé par le paramètre `ai_filter` de la stratégie, conformément à la résolution de C-002, qui définit les trois états `shadow`, `advisory` et `required`.
**Critères d'acceptation :**
- en mode `shadow`, le verdict du modèle est enregistré et n'a **aucun** effet sur l'émission du signal, ce qui est vérifié par un test soumettant un rejet et constatant que le signal part quand même ;
- aucun appel au modèle n'est déclenché en l'absence de signal candidat ;
- une réponse du modèle ne peut jamais créer un signal, augmenter une taille, ni desserrer un stop, ce qui est vérifié par un test dédié soumettant une réponse malveillante ;
- un délai dépassé n'allonge pas le temps de traitement au-delà d'un plafond configurable ;
- le coût cumulé des appels est mesuré et consultable ;
- en mode `advisory`, une panne du modèle produit un signal accompagné d'une explication de repli générée localement et d'un indicateur de dégradation visible dans le message.

### F-011 — Gestionnaire de risque

**Description :** couche de contrôle finale, qui dispose du dernier mot et peut refuser une opération même recommandée par la stratégie et par l'IA. `[CONFIRMÉ]`
**Déclencheur :** tout signal candidat ayant franchi la couche IA, et toute demande d'ordre.
**Fonctionnement :** application séquentielle de l'ensemble des contrôles de la section 8, calcul de la taille de position, puis émission d'une décision d'autorisation, de réduction ou de refus. Chaque décision, y compris chaque refus, est persistée avec son motif.
**Règles métier :** RM-004 à RM-015.
**Critères d'acceptation :**
- un signal dépourvu de stop-loss valide est refusé en modes `DEMO` et `LIVE` ;
- une erreur de calcul de taille provoque un refus, jamais une valeur de repli ;
- le franchissement de la perte quotidienne maximale bloque tout nouvel ordre jusqu'à la remise à zéro ;
- les pertes évitées par les refus sont comptabilisées et figurent au rapport quotidien ;
- chaque contrôle est couvert par un test unitaire indépendant.

### F-012 — Prévention des doublons et idempotence

**Description :** empêcher qu'un même signal produise plusieurs ordres. `[CONFIRMÉ]`
**Fonctionnement :** chaque signal porte une clé d'idempotence déterministe, dérivée du symbole, de l'identifiant de stratégie, de l'unité de temps et de l'horodatage de la bougie. Chaque ordre porte une clé dérivée du signal. Une contrainte d'unicité en base garantit la propriété même en cas de concurrence ou de redémarrage.
**Critères d'acceptation :**
- deux exécutions du moteur sur la même bougie ne produisent qu'un signal ;
- un redémarrage au milieu d'un envoi d'ordre ne produit pas de second ordre, ce qui est vérifié par un test de reprise ;
- un double appui sur un bouton Telegram ne produit qu'une action ;
- une réponse de courtier perdue est réconciliée sans duplication.

### F-013 — Notification Telegram d'un signal

**Description :** transmettre le signal validé à l'opérateur. `[CONFIRMÉ]`
**Fonctionnement :** composition d'un message contenant l'identifiant unique, le marché, le sens, le prix observé, la zone d'entrée, le stop-loss, les objectifs, le ratio risque/rendement estimé, l'unité de temps, la stratégie et sa version, l'heure de génération, l'heure d'expiration, le niveau de confiance le cas échéant, la justification, l'état du marché et le mode en cours.
**Cas d'erreur :** indisponibilité de Telegram. Le message est mis en file et réémis, sans bloquer le moteur.
**Critères d'acceptation :**
- tous les champs listés sont présents ;
- le mode en cours est affiché de manière non ambiguë ;
- une indisponibilité de Telegram ne bloque ni le moteur ni l'enregistrement en base ;
- le délai entre la validation par la couche risque et l'envoi reste sous un plafond mesuré.

### F-014 — Commandes Telegram et contrôle d'accès

**Description :** piloter l'agent depuis Telegram, avec une liste blanche d'identifiants. `[CONFIRMÉ]`
**Critères d'acceptation :**
- une commande d'un identifiant non autorisé est refusée, journalisée, et ne révèle aucune information ;
- les commandes sensibles exigent une confirmation explicite avant exécution ;
- le nombre de tentatives est limité par identifiant ;
- l'historique des commandes est consultable.

### F-015 — Paper trading

**Description :** simuler des positions en temps réel sur le flux de production, sans exposition. `[CONFIRMÉ]`
**Fonctionnement :** un exécuteur simulé applique les règles de remplissage, le spread observé, une hypothèse de slippage, puis suit les positions ouvertes et applique les sorties.
**Critères d'acceptation :**
- les positions simulées suivent le flux réel et se clôturent sur stop-loss, objectif ou règle de sortie ;
- les résultats sont enregistrés dans les mêmes tables que les trades réels, distingués par le mode ;
- les indicateurs de performance sont calculés par le même code que pour les trades réels.

### F-016 — Exécution des ordres sur compte de démonstration

**Description :** placer des ordres réels sur un compte de démonstration Deriv. `[CONFIRMÉ]`
**Préconditions :** l'ensemble des conditions de la section 10.1 est rempli.
**Fonctionnement :** après validation par la couche risque, vérification préalable de l'ordre auprès du terminal, envoi de l'ordre au marché avec stop-loss et take-profit natifs, vérification du code de retour, puis enregistrement du ticket MT5. La clé d'idempotence est transmise dans le commentaire de l'ordre, afin de pouvoir retrouver un ordre dont la réponse aurait été perdue. `[révisé en 1.2]`
**Règles métier :** RM-004, RM-012, RM-014, RM-017.
**Cas d'erreur :** proposition expirée, prix indisponible, marge insuffisante, rejet par le courtier, réponse perdue.
**Critères d'acceptation :**
- avant tout envoi d'ordre, l'identifiant de compte retourné par l'authentification est vérifié comme correspondant au mode en vigueur, un compte réel en mode `DEMO` provoquant un arrêt immédiat ;
- le stop-loss est confirmé comme présent sur la position après exécution, une absence déclenchant une clôture immédiate et une alerte ;
- le prix demandé, le prix exécuté et l'écart sont enregistrés ;
- une réponse perdue est réconciliée sans duplication.

### F-017 — Suivi et réconciliation des positions

**Description :** maintenir l'état local en accord avec l'état réel du compte. `[CONFIRMÉ]`
**Critères d'acceptation :**
- un abonnement aux transactions du compte tient l'état à jour sans interrogation excessive ;
- une réconciliation périodique compare l'état local à l'état du courtier ;
- toute divergence suspend le trading et alerte l'opérateur, conformément à RM-014.

### F-018 — Exécution réelle

**Description :** identique à F-016, sur compte réel, avec activation séparée. `[CONFIRMÉ]`, optionnelle
**Critères d'acceptation :** ceux de F-016, plus la double condition d'activation de RM-000, plus un plafond de risque réduit imposé à l'activation, plus la validation préalable documentée de l'arrêt d'urgence.

### F-019 — Arrêt d'urgence

**Description :** interrompre immédiatement l'activité. `[CONFIRMÉ]`
**Déclencheur :** commande Telegram, commande serveur, paramètre en base, ou déclenchement automatique par franchissement d'une limite de perte.
**Fonctionnement :** l'état d'arrêt est persisté et évalué avant toute action susceptible d'engager le capital. Le comportement par défaut interdit les nouveaux ordres et annule les ordres en attente selon la configuration. La clôture des positions ouvertes est une option distincte, qui n'est jamais activée implicitement.
**Règles métier :** RM-015.
**Critères d'acceptation :**
- la distinction entre suspension des nouveaux ordres et clôture des positions est explicite dans l'interface et dans la configuration ;
- l'état d'arrêt survit à un redémarrage du processus et du serveur ;
- le déclenchement est notifié immédiatement avec sa cause ;
- l'arrêt est testé de bout en bout avant toute activation du mode réel.

### F-020 — Persistance et piste d'audit

**Description :** conserver l'intégralité des décisions et de leur contexte. `[CONFIRMÉ]`
**Critères d'acceptation :**
- pour toute décision, il est possible de retrouver les données utilisées, les valeurs d'indicateurs, la stratégie, la version, les paramètres, les contrôles de risque appliqués, l'intervention éventuelle de l'IA, la réponse du courtier, les messages Telegram et le résultat final ;
- les enregistrements historiques ne sont jamais écrasés ni modifiés rétroactivement ;
- chaque changement d'état d'un signal est horodaté.

### F-021 — Calcul des indicateurs de performance

**Description :** produire les chiffres de performance, à partir de la base de données uniquement. `[CONFIRMÉ]`
**Critères d'acceptation :**
- les indicateurs de la section 11 sont calculés et consultables par marché, par stratégie, par version, par sens, par unité de temps et par mode ;
- le même code produit les indicateurs en backtest et en production ;
- aucun chiffre du rapport ne provient d'un modèle de langage.

### F-022 — Rapports automatiques

**Description :** envoyer des rapports périodiques sur Telegram. `[CONFIRMÉ]`
**Fonctionnement :** un générateur unique paramétré par une fenêtre temporelle produit les rapports quotidien, hebdomadaire et mensuel, dont le contenu est détaillé en section 11.3. Le commentaire narratif peut être rédigé par l'IA ; les chiffres proviennent exclusivement de la base.
**Critères d'acceptation :**
- le rapport est envoyé à l'heure configurée, y compris après un redémarrage survenu avant l'échéance ;
- un rapport est consultable à la demande par commande ;
- une indisponibilité du modèle de langage n'empêche pas l'envoi du rapport chiffré.

### F-023 — Exports

**Description :** exporter les transactions et les rapports. `[CONFIRMÉ]` pour CSV et JSON, `[À CONFIRMER]` pour PDF.
**Critères d'acceptation :** l'export contient les mêmes chiffres que le rapport, et l'horodatage ainsi que la source de chaque enregistrement sont préservés.

### F-024 — Supervision et alertes

**Description :** surveiller la santé du système. `[CONFIRMÉ]`
**Critères d'acceptation :**
- une alerte Telegram est émise en cas d'arrêt du processus, de perte de connexion, de divergence de réconciliation, de saturation du disque ou d'échec répété d'un composant ;
- les journaux sont structurés et exploitables ;
- l'état des connexions, la latence et les erreurs d'API sont mesurés.

### F-025 — Atelier de recherche et de backtesting

**Description :** environnement séparé de conception et de validation des stratégies. `[CONFIRMÉ]`
**Critères d'acceptation :**
- l'historique est téléchargé, versionné, contrôlé contre les doublons et les trous, et conservé sans modification ;
- le harnais de backtest applique le spread, le slippage, les commissions et un retard d'exécution ;
- le protocole de séparation des données décrit en section 9.3 est appliqué et vérifiable ;
- le rapport de backtest est reproductible à partir du même jeu de données et de la même version de stratégie.

### F-026 — Comparaison entre backtest et réel

**Description :** mesurer l'écart entre les performances attendues et observées. `[CONFIRMÉ]`
**Critères d'acceptation :** le rapport mensuel présente, pour chaque stratégie active, la comparaison des mêmes indicateurs entre le backtest de référence et la production, et signale tout écart au-delà d'un seuil configurable.

---

## 8. Exigences

### 8.1 Exigences fonctionnelles

Priorités : `P0` bloquant, `P1` essentiel, `P2` important, `P3` souhaitable. Les priorités non explicitement fournies par l'opérateur sont indicatives. `[PROPOSITION]`

| ID | Exigence | Prio | Fonctionnalité | Validation |
|---|---|---|---|---|
| EF-001 | Le système fonctionne en continu sans le poste de l'utilisateur | P0 | F-001, F-024 | Le service survit à la déconnexion du poste et à un redémarrage du serveur |
| EF-002 | Seuls les symboles de la liste blanche sont analysés | P0 | F-004 | Test : un symbole hors liste n'est jamais souscrit |
| EF-003 | Une stratégie distincte est affectable à chaque marché | P0 | F-007 | Deux marchés tournent avec deux stratégies différentes |
| EF-004 | Les signaux sont transmis sur Telegram | P0 | F-013 | Réception d'un signal complet en conditions réelles |
| EF-005 | Chaque signal contient entrée, stop, objectifs et version | P0 | F-013 | Contrôle du gabarit de message |
| EF-006 | Les doublons de signaux et d'ordres sont bloqués | P0 | F-012 | Tests de concurrence et de redémarrage |
| EF-007 | Tous les signaux sont enregistrés, y compris les refusés | P0 | F-020 | Requête en base après une session |
| EF-008 | Les indicateurs de performance sont calculés | P0 | F-021 | Comparaison avec un calcul manuel de contrôle |
| EF-009 | Un rapport quotidien est envoyé automatiquement | P0 | F-022 | Réception à l'heure configurée |
| EF-010 | Le système se reconnecte automatiquement | P0 | F-003 | Coupure simulée |
| EF-011 | Les commandes Telegram sont restreintes à une liste blanche | P0 | F-014 | Test avec un identifiant non autorisé |
| EF-012 | Le mode réel est désactivé par défaut | P0 | F-018 | Inspection après installation neuve |
| EF-013 | Un arrêt d'urgence est disponible et testé | P0 | F-019 | Procédure de test documentée et exécutée |
| EF-014 | Aucun ordre n'est émis sur données périmées ou incomplètes | P0 | F-002, F-011 | Test d'injection d'une série dégradée |
| EF-015 | Chaque position en mode réel porte un stop-loss | P0 | F-011, F-016 | Vérification après exécution |
| EF-016 | La taille de position respecte le risque maximal configuré | P0 | F-011 | Tests unitaires du calcul de taille |
| EF-017 | L'état local est réconcilié avec le compte | P0 | F-017 | Divergence injectée, suspension constatée |
| EF-018 | L'IA ne peut ni créer un signal ni assouplir une contrainte | P0 | F-010 | Test avec une réponse de modèle malveillante |
| EF-019 | Les secrets ne figurent jamais dans le code source | P0 | Section 12 | Analyse du dépôt en intégration continue |
| EF-020 | L'or n'est pas négocié hors de ses horaires de marché | P1 | F-005 | Test sur une fenêtre de fermeture |
| EF-021 | Une nouvelle configuration de stratégie est déployable sans redéploiement complet | P1 | F-007 | Rechargement observé en fonctionnement |
| EF-022 | Les versions de stratégie sont historisées | P1 | F-008 | Restitution d'un trade ancien |
| EF-023 | Un rapport hebdomadaire et un rapport mensuel sont disponibles | P1 | F-022 | Génération sur données existantes |
| EF-024 | Les performances sont ventilées par marché, stratégie, version et mode | P1 | F-021 | Contrôle des agrégations |
| EF-025 | Le paper trading est réalisable avant toute exécution | P1 | F-015 | Campagne menée et mesurée |
| EF-026 | Le backtest applique spread, slippage, commissions et latence | P1 | F-025 | Comparaison avec et sans coûts |
| EF-027 | La comparaison backtest contre réel est produite | P2 | F-026 | Présence au rapport mensuel |
| EF-028 | Les données sont exportables en CSV et JSON | P2 | F-023 | Export contrôlé |
| EF-029 | Un marché, une stratégie ou l'exécution sont désactivables immédiatement | P1 | F-014, F-019 | Commandes testées |
| EF-030 | Le coût des appels au modèle est mesuré et plafonné | P2 | F-010 | Compteur consultable |
| EF-031 | Export PDF des rapports | P3 | F-023 | `[À CONFIRMER]` |

### 8.2 Exigences non fonctionnelles

| ID | Exigence | Prio | Cible | Validation |
|---|---|---|---|---|
| ENF-001 | Disponibilité du service | P0 | Redémarrage automatique après panne, objectif indicatif de 99,5 % hors maintenance `[PROPOSITION]` | Mesure de disponibilité sur trente jours |
| ENF-002 | Latence de traitement d'une donnée | P1 | Moins d'une seconde entre la clôture de bougie et la décision `[CONFIRMÉ]` | Mesure instrumentée |
| ENF-003 | Latence de notification | P1 | Quelques secondes entre validation et envoi `[CONFIRMÉ]` | Mesure instrumentée |
| ENF-004 | Isolation de la lenteur du modèle | P0 | Un appel lent ne bloque pas le moteur `[CONFIRMÉ]` | Test avec un modèle simulé lent |
| ENF-005 | Résilience | P0 | Les scénarios de la section 13.4 sont couverts | Tests de reprise |
| ENF-006 | Observabilité | P1 | Journaux structurés, métriques, état des connexions | Inspection |
| ENF-007 | Sauvegarde | P0 | Sauvegarde quotidienne chiffrée, restauration testée | Restauration réalisée au moins une fois |
| ENF-008 | Reproductibilité | P1 | Un backtest rejoué donne le même résultat | Double exécution |
| ENF-009 | Maintenabilité | P2 | Analyse statique, typage et tests en intégration continue | Chaîne verte |
| ENF-010 | Coût d'infrastructure | P2 | Serveur de l'ordre de 4 à 5 euros par mois `[CONFIRMÉ]` | Facture |

---

## 9. Règles métier

### RM-001 — Fraîcheur des données
**Condition :** une décision est envisagée sur un symbole.
**Comportement attendu :** si l'âge de la dernière donnée dépasse le seuil configuré pour ce symbole, aucune décision n'est prise.
**Exception :** aucune.

### RM-002 — Intégrité de la série
**Condition :** la série présente un trou, un doublon non résolu ou une incohérence d'horodatage.
**Comportement attendu :** la série est marquée dégradée, les signaux sont suspendus pour ce symbole, et l'incident est notifié.
**Exception :** en mode `OBSERVATION`, le signal théorique peut être journalisé avec une marque de dégradation, sans être envoyé.

### RM-003 — Compatibilité stratégie et marché
**Condition :** une stratégie est associée à un symbole.
**Comportement attendu :** l'association n'est acceptée que si le symbole figure dans les symboles autorisés du manifeste de la stratégie.
**Exception :** aucune. Une extension à un nouveau marché exige une modification explicite du manifeste et une nouvelle validation.

### RM-004 — Stop-loss obligatoire `[définitive en 1.2, TASK-004]`
**Condition :** un ordre est envisagé en mode `DEMO` ou `LIVE`.
**Comportement attendu :**
- l'ordre est refusé si aucun stop-loss n'est défini ;
- l'ordre est refusé si la distance entre le prix d'entrée et le stop est inférieure à la distance minimale du courtier, soit `trade_stops_level × point`. **Le stop n'est jamais élargi automatiquement** : l'élargir changerait le risque décidé par la stratégie ;
- le stop-loss et le take-profit sont transmis **dans l'ordre lui-même**, nativement ;
- après exécution, le stop présent sur la position est relu. S'il est absent ou s'écarte de plus d'un tick de la valeur demandée, la position est fermée immédiatement et une alerte est émise (F-016).

**Exception :** aucune. Le stop-loss natif a été vérifié en conditions réelles sur le compte démo le 2026-10-04.

### Formule de taille de position `[définitive en 1.2, TASK-004]`

**Entrées :**

| Symbole | Sens | Unité | Source |
|---|---|---|---|
| `E` | Capital de calcul | EUR | Fonds propres du compte en `PAPER` et `DEMO` ; en `LIVE`, le **plus petit** des fonds propres et du capital de référence de 100 €, pour ne jamais dimensionner sur plus que le capital déclaré `[PROPOSITION]` |
| `r` | Risque par opération | fraction | RM-005, selon le mode |
| `L1` | Perte pour **1 lot entier** si le stop est touché | EUR | Calculateur de profit du terminal (`order_calc_profit`), appelé sur 1 lot pour éviter l'arrondi au centime d'un petit volume |
| `L1'` | Même perte, calcul indépendant | EUR | `taille_de_contrat × |entrée − stop| × taux`, où `taux` convertit la devise de profit en euros, lu sur le symbole `EURUSD` du terminal ; 1 si le profit est déjà en euros |
| `M1` | Marge pour 1 lot | EUR | Calculateur de marge du terminal (`order_calc_margin`) |
| `F` | Marge libre du compte | EUR | Terminal |
| `f` | Part maximale de la marge libre engagée par une position | fraction | 0,5 `[PROPOSITION]` |
| `vmin`, `pas`, `vmax` | Lot minimal, pas de lot, lot maximal | lot | Spécification du symbole |

**Calcul :**
1. **Contrôle croisé :** si `|L1 − L1'| / L1'` dépasse **2 %**, l'ordre est refusé. Deux calculs indépendants qui divergent signalent une conversion faussée ou une donnée périmée, et une erreur de calcul doit bloquer l'ordre (section 13.2 du cahier initial).
2. Volume permis par le risque : `V_risque = E × r / L1`.
3. Volume permis par la marge : `V_marge = F × f / M1`.
4. `V_brut = min(V_risque, V_marge, vmax, taille maximale configurée)`.
5. **Arrondi toujours vers le bas** au pas de lot, en arithmétique décimale exacte : `V = plancher(V_brut / pas) × pas`.
6. Si `V < vmin`, l'ordre est refusé, avec le motif « taille inférieure au lot minimal ».

**Propriété garantie :** le risque réalisé `V × L1` ne dépasse jamais `E × r`, puisque l'arrondi se fait vers le bas.

**Ce qui est interdit :** utiliser `trade_tick_value` pour calculer un risque. Mesuré le 2026-10-03, il n'est exact que pour l'or, faux de 11 à 15 % sur la crypto, et faux de 89 % sur certains indices.

**Exemples de référence**, tirés du relevé de TASK-003 (1 USD = 0,888786 EUR). Ils serviront de cas de test à TASK-035.

| | Exemple 1 — or, démonstration | Exemple 2 — or, réel | Exemple 3 — BTC, démonstration |
|---|---|---|---|
| `E`, `r` | 5 497,74 €, 0,5 % → 27,49 € | 100 €, 2 % → 2,00 € | 5 497,74 €, 0,5 % → 27,49 € |
| Distance du stop | 12,3073 $ | 12,3073 $ | 104,1262 $ |
| `L1'` / `L1` | 1 093,86 € / 1 094,00 € (écart 0,01 %) | idem | 92,55 € / 92,00 € (écart 0,59 %) |
| `M1` | 18 393 € | 18 393 € | 37 634 € |
| `V_risque` / `V_marge` | 0,02513 / 0,14945 | 0,00183 / 0,00272 | 0,29703 / **0,07304** |
| Volume final | **0,02 lot** | 0,00 < 0,01 → **refus** | **0,07 lot**, limité par la marge |
| Risque réalisé | 21,88 € (0,40 %) | — | 6,48 € (0,12 %) |
| Marge engagée | 367,86 € | — | 2 634,38 € |

**Lecture de l'exemple 3 :** avec un levier de 1:2, une position BTC est bornée par la marge bien avant le risque. Même en démonstration avec plus de 5 000 €, le risque réel par opération sur le BTC sera d'environ 0,1 % au lieu de 0,5 %. C'est mécanique et voulu ; les rapports devront l'indiquer pour ne pas sous-interpréter les résultats du BTC.

### RM-005 — Risque par opération `[révisée en 1.1, voir C-009]`
**Condition :** calcul de la taille de position.
**Comportement attendu :** la perte encourue si le stop-loss est atteint ne dépasse pas le pourcentage configuré du capital de référence.
**Valeurs selon le mode :**

| Mode | Capital de référence | Risque par opération | Motif |
|---|---|---|---|
| `PAPER`, `DEMO` | Capital du compte de démonstration | 0,5 % | Valeur cible, non contrainte par les minimums du courtier. C'est cette valeur qui sert à la validation des stratégies |
| `LIVE` | 100 € `[CONFIRMÉ]` | 2 % par défaut, 5 % maximum absolu | Plancher imposé par la taille minimale négociable, pas un choix de gestion |

**Exception :** aucune. En cas d'impossibilité de calcul, l'ordre est refusé.
**Note d'interprétation obligatoire :** les résultats obtenus en mode `LIVE` avec un risque de 2 à 5 % ne sont pas comparables à ceux obtenus en `PAPER` ou `DEMO` avec un risque de 0,5 %. Toute comparaison doit se faire en multiples de risque, jamais en pourcentage de capital.

### RM-006 — Perte quotidienne maximale `[révisée en 1.1]`
**Condition :** la perte réalisée et latente cumulée du jour atteint le seuil configuré. Valeur par défaut : 2 % en `PAPER` et `DEMO`, 5 % en `LIVE`, soit 5 € au capital de référence.
**Comportement attendu :** aucun nouvel ordre n'est accepté jusqu'à la remise à zéro quotidienne. Une notification est émise.
**Exception :** la gestion des positions déjà ouvertes se poursuit.
**Conséquence assumée :** en mode `LIVE` à 100 €, une à deux opérations perdantes suffisent à clôturer la journée. Ce comportement est voulu et ne constitue pas un défaut.

### RM-007 — Perte hebdomadaire maximale et drawdown `[révisée en 1.1]`
**Condition :** la perte hebdomadaire ou le drawdown maximal atteint le seuil configuré. Valeurs par défaut : 6 % et 10 % en `PAPER` et `DEMO` ; 10 % et 20 % en `LIVE`, soit 10 € et 20 € au capital de référence.
**Comportement attendu :** suspension des nouveaux ordres et notification exigeant une décision de l'opérateur.
**Motif de l'écart entre modes :** un seuil de 10 % appliqué à 100 € serait franchi par deux opérations perdantes consécutives à 5 % de risque, ce qui suspendrait le système en permanence sans signaler quoi que ce soit d'anormal. Le seuil réel doit rester cohérent avec le risque par opération effectivement applicable.

### RM-019 — Éligibilité d'un instrument au mode réel `[nouvelle en 1.1, voir C-009]`
**Condition :** un instrument est candidat à l'activation en mode `LIVE`.
**Comportement attendu :** le système calcule, à partir de la taille minimale négociable et de la distance de stop-loss typique de la stratégie, le **risque minimal incompressible** que cet instrument impose. Si ce risque minimal dépasse le risque maximal par opération autorisé en mode `LIVE`, l'instrument est déclaré inéligible au mode réel. Il demeure pleinement actif en `OBSERVATION`, `SIGNAL`, `PAPER` et `DEMO`.
**Exception :** aucune. L'inéligibilité ne peut pas être contournée en augmentant le risque autorisé au-delà du plafond absolu de 5 %.
**Motif :** cette règle transforme une impossibilité arithmétique en refus explicite et traçable. Sans elle, l'agent tenterait des ordres systématiquement rejetés par le courtier, ou pire, ouvrirait des positions dont le risque réel dépasserait largement l'intention.
**Notification :** l'inéligibilité d'un instrument est signalée à l'opérateur au démarrage et rappelée dans la réponse à la commande de statut, avec le montant de capital qui la lèverait.

### RM-008 — Nombre et taille des positions
**Condition :** un ordre est envisagé.
**Comportement attendu :** l'ordre est refusé si le nombre maximal de positions ouvertes, le nombre maximal d'opérations quotidiennes, la taille maximale d'une position ou l'exposition totale seraient dépassés. Valeurs par défaut proposées : deux positions simultanées, une position par marché `[PROPOSITION]`. La taille d'une position est en outre plafonnée par la formule de taille, qui borne la marge engagée à la moitié de la marge libre. Le compte autorisant la couverture, plusieurs positions sur un même symbole seraient techniquement possibles : **une position par marché** reste la règle.

### RM-009 — Unicité par bougie
**Condition :** un signal candidat est produit.
**Comportement attendu :** si un signal existe déjà pour la même stratégie, le même symbole et la même bougie, le nouveau est ignoré.

### RM-010 — Périmètre d'action de l'IA
**Condition :** la couche IA retourne une réponse.
**Comportement attendu :** seuls un rejet, un abaissement de confiance et un texte explicatif sont pris en compte. Toute autre instruction est ignorée et journalisée comme tentative de dépassement de périmètre.
**Exception :** aucune.

### RM-011 — Indisponibilité de l'IA
**Condition :** la couche IA est indisponible ou dépasse son délai.
**Comportement attendu :** déterminé par le paramètre `ai_filter` de la stratégie. En `required`, le signal est refusé. En `advisory`, le signal est émis avec une explication de repli et une marque de dégradation.

### RM-012 — Qualité d'exécution
**Condition :** un ordre est envisagé.
**Comportement attendu :** l'ordre est refusé si le spread observé dépasse le maximum configuré, si la marge est insuffisante, ou si le prix proposé s'écarte du prix attendu au-delà du slippage maximal accepté. **La vérification préalable de l'ordre par le terminal ne suffit pas** : elle a approuvé un ordre que le serveur a ensuite refusé pour raison de juridiction. Seul le code retour de l'envoi fait foi.

### RM-013 — Perte de connexion
**Condition :** la connexion au fournisseur est perdue au-delà du seuil configuré.
**Comportement attendu :** suspension du trading, notification, et interdiction de reprise avant restauration d'une série saine.

### RM-014 — Divergence d'état
**Condition :** la réconciliation détecte un écart entre l'état local et l'état du compte.
**Comportement attendu :** suspension immédiate du trading, notification détaillée, et reprise uniquement après intervention de l'opérateur.
**Exception :** aucune. La résolution automatique d'une divergence est interdite.

### RM-015 — Arrêt d'urgence
**Condition :** un arrêt d'urgence est actif.
**Comportement attendu :** aucun nouvel ordre n'est accepté, quelle que soit la source de la demande. Les ordres en attente sont traités selon la configuration. Les positions ouvertes ne sont clôturées que si cette option a été explicitement activée.

### RM-016 — Promotion d'une stratégie
**Condition :** une stratégie change d'état, par exemple de candidate à active en compte de démonstration.
**Comportement attendu :** le changement exige une action explicite de l'opérateur, est horodaté, et enregistre l'identité du décideur ainsi que les résultats de validation invoqués.
**Exception :** aucune. Aucune promotion automatique.

### RM-017 — Cohérence du compte et du mode
**Condition :** un ordre est envisagé.
**Comportement attendu :** le type de compte déclaré par le terminal MT5 (démonstration ou réel) est comparé au mode en vigueur, de même que le numéro de compte, comparé à celui de la configuration. Toute incohérence, notamment un compte réel alors que le mode est `DEMO`, provoque un arrêt immédiat du composant d'exécution et une alerte de gravité maximale. `[révisé en 1.2]`

### RM-018 — Cycle de vie d'un signal
**Condition :** un signal existe.
**Comportement attendu :** il évolue uniquement selon les transitions autorisées entre les états suivants : candidat, rejeté par le risque, validé, envoyé, expiré, accepté, ignoré, ordre envoyé, ordre accepté, ordre rejeté, position ouverte, partiellement clôturée, clôturée, annulée, erreur. Chaque transition est horodatée et persistée.

---

## 10. Architecture

### 10.1 Stack retenue `[PROPOSITION]`

| Couche | Choix | Justification |
|---|---|---|
| Langage | Python 3.12 | Partage du code de stratégie et d'indicateurs entre recherche et production, voir C-004 |
| Concurrence | `asyncio`, avec un fil dédié unique pour MT5 | Les tâches périodiques sont asynchrones ; les appels MT5, bloquants et non réentrants, passent tous par ce fil |
| Accès courtier | Paquet Python officiel `MetaTrader5` et terminal MT5 | Seul accès disponible depuis la France (C-010). Données et exécution par la même source |
| Données | pandas | Écosystème d'analyse, cohérence avec l'atelier de recherche |
| Persistance | SQLAlchemy et Alembic, SQLite puis PostgreSQL | Voir C-005 |
| Validation | pydantic | Validation des manifestes, de la configuration et des réponses du modèle |
| Planification | APScheduler | Rapports et tâches périodiques dans le même processus |
| Bot | Bibliothèque Telegram Bot asynchrone | Cohérence avec `asyncio` |
| Modèle de langage | API Claude | `[CONFIRMÉ]` |
| Exécution | Service Windows ou tâche planifiée, avec redémarrage automatique de l'agent et du terminal MT5 | `[révisé en 1.2]` : systemd et PM2 sont propres à Linux |
| Conteneurisation | Aucune pour l'agent de production | `[révisé en 1.2]` : le terminal MT5 exige une session Windows, incompatible avec un conteneur. Reproductibilité assurée par `uv.lock` |
| Qualité | ruff, mypy, pytest | Déterminisme et maintenabilité du moteur de risque |

### 10.2 Découpage en paquets

Le découpage sépare ce qui doit tourner en production de ce qui ne doit pas y tourner, et isole les composants dont la défaillance ne doit pas contaminer le reste.

| Paquet | Responsabilité | Chargé en production |
|---|---|---|
| `core` | Types communs, horloge, identifiants, erreurs | Oui |
| `config` | Chargement et validation de la configuration et des secrets | Oui |
| `data` | Client Deriv, normalisation, agrégation, contrôle qualité, horaires | Oui |
| `indicators` | Calculs purs | Oui |
| `strategies` | Interface, chargeur de manifestes, modules de stratégie | Oui |
| `ai` | Appel au modèle, schéma de réponse, garde-fous, mesure de coût | Oui |
| `risk` | Contrôles, calcul de taille, décisions, arrêt d'urgence | Oui |
| `execution` | Exécuteur simulé, exécuteur Deriv, réconciliation | Oui |
| `notify` | Bot Telegram, gabarits de messages, file de réémission | Oui |
| `storage` | Modèles de données, dépôts, migrations | Oui |
| `analytics` | Indicateurs de performance, agrégations | Oui |
| `reporting` | Rapports périodiques, exports | Oui |
| `backtest` | Harnais de simulation, protocoles de validation | **Non** — outil de ligne de commande, voir C-001 |
| `research` | Téléchargement d'historique, exploration, optimisation | **Non** |

### 10.3 Flux de décision

```
Terminal MT5 (interrogé depuis un fil dédié)
   │  ticks / bougies, convertis en UTC
   ▼
data  ── contrôle qualité ──► série dégradée ──► aucun signal (RM-001, RM-002)
   │  série saine, bougie close
   ▼
strategies ── aucun signal ──► fin
   │  signal candidat + valeurs d'indicateurs
   ▼
ai  ── rejet ──► journalisé, fin        (RM-010, RM-011)
   │  explication, confiance éventuelle
   ▼
risk ── refus ──► journalisé, comptabilisé au rapport   (RM-004 à RM-015)
   │  autorisation + taille calculée
   ├──────────────► notify (tous modes hors OBSERVATION)
   └──────────────► execution (modes PAPER, DEMO, LIVE uniquement)
                        │
                        ▼
                   storage ──► analytics ──► reporting
```

**Propriété structurante :** `risk` est la seule frontière entre une intention et un engagement. Aucun autre module ne doit pouvoir appeler `execution`. Cette contrainte est vérifiée par un test d'architecture inspectant les dépendances d'import. `[PROPOSITION]`

### 10.4 Environnements

| Environnement | Données | Compte | Stratégies | Secrets |
|---|---|---|---|---|
| Développement | Rejeu de fichiers et simulateurs | Aucun | Expérimentales | Jetons de test |
| Préproduction | Flux réel | Démonstration | Candidates | Jetons dédiés |
| Production | Flux réel | Démonstration puis réel | Validées uniquement | Jetons dédiés, portée minimale |

Aucun jeton n'est partagé entre environnements. `[CONFIRMÉ]`

---

## 11. Modèle de données

### 11.1 Entités

| Entité | Rôle | Points d'attention |
|---|---|---|
| `users` | Identifiants Telegram autorisés et rôle | Liste blanche |
| `accounts` | Comptes de trading connus, type démonstration ou réel, devise | Sert au contrôle RM-017 |
| `markets` | Symbole, état d'activation, unités de temps, stratégie associée | Reflète la liste blanche |
| `candles` | Bougies par symbole et unité de temps | Clé unique sur symbole, unité de temps et horodatage d'ouverture |
| `ticks` | Ticks bruts, conservation limitée | Volume important, politique de purge |
| `strategies` | Identité d'une stratégie | — |
| `strategy_versions` | Version, paramètres, état, date d'activation | Jamais écrasée |
| `signals` | Signal et son contexte complet | Clé d'idempotence unique |
| `signal_events` | Transitions du cycle de vie | Horodatées, immuables |
| `indicator_snapshots` | Valeurs d'indicateurs au moment du signal | Nécessaire à l'auditabilité |
| `ai_calls` | Requête, réponse, coût, latence, décision | Conservation intégrale |
| `risk_decisions` | Contrôle appliqué, résultat, motif, taille calculée | Y compris les refus |
| `orders` | Ordre envoyé, clé d'idempotence, identifiant courtier | Contrainte d'unicité |
| `executions` | Prix demandé, prix obtenu, écart, horodatage | Mesure du slippage |
| `positions` | Position ouverte, protections, état | Réconciliée |
| `trades` | Opération close, résultat net, motif de sortie, mode | Base des indicateurs |
| `reports` | Rapports produits | Conservation durable |
| `system_events` | Connexions, incidents, arrêts, changements de mode | Piste d'audit |
| `audit_log` | Commandes reçues, décisions de l'opérateur | Qui, quand, quoi |

### 11.2 Contraintes structurantes

- unicité sur la clé d'idempotence des signaux et des ordres, c'est la garantie technique de F-012 ;
- unicité sur symbole, unité de temps et horodatage d'ouverture pour les bougies ;
- interdiction de modification des lignes de `signal_events`, `executions`, `trades` et `audit_log` après écriture ;
- index sur les colonnes d'horodatage et de symbole, les requêtes de rapport étant temporelles ;
- toutes les dates sont stockées en temps universel coordonné, la conversion locale n'intervenant qu'à l'affichage.

### 11.3 Conservation

| Donnée | Conservation proposée `[PROPOSITION]` |
|---|---|
| Ticks bruts | Trente jours, puis purge ou archivage compressé |
| Bougies | Durée longue, sans purge automatique |
| Signaux, ordres, opérations | Permanente |
| Traces d'appels au modèle | Douze mois |
| Journaux techniques | Rotation automatique |
| Rapports | Permanente |
| Sauvegardes | Quotidiennes, plusieurs points de restauration |

---

## 12. Intégrations

### 12.1 Deriv MetaTrader 5 `[CONTRAINTE]` — réécrite en version 1.2

| Point | Élément | Statut |
|---|---|---|
| Accès | Paquet Python officiel `MetaTrader5`, terminal MT5 installé et connecté | `[CONFIRMÉ]`, voir C-010 |
| Système | Windows uniquement, pour le paquet comme pour le terminal | `[CONTRAINTE]` |
| Authentification | Numéro de compte, nom du serveur MT5, mot de passe | `[CONFIRMÉ]` |
| Moindre privilège | **Mot de passe investisseur**, en lecture seule, pour toutes les phases sans exécution ; mot de passe principal uniquement à partir de la phase 8 | `[PROPOSITION]` |
| Entité du compte | Entité européenne, résidence française | `[CONFIRMÉ]`, voir C-008 et C-010 |
| Indices synthétiques | Indisponibles sur ce compte | `[CONFIRMÉ]` le 2026-10-01, voir C-008 |
| Nom du symbole de l'or | `XAUUSD` (profit en dollars) et `XAUEUR` (profit en euros) | `[CONFIRMÉ]` le 2026-10-03, rapport TASK-003 |
| Indices synthétiques sur MT5 | Visibles sur le compte démo, **mais refusés à l'exécution : « Instruments blocked in France »**, retcode 10006 | `[CONFIRMÉ]` le 2026-10-04 par un ordre de test. **Hors périmètre, C-008 confirmée** |
| Exécution crypto | Ordre BTCUSD exécuté avec stop-loss et take-profit natifs, puis refermé | `[CONFIRMÉ]` le 2026-10-04 |
| Fiabilité de `order_check` | Répond favorablement à un ordre que le serveur refusera pour raison de juridiction | `[CONTRAINTE]` : seul le code retour de `order_send` fait foi |
| Symboles crypto | Deux à quatre, à sélectionner | `[À CONFIRMER]` — liste établie à partir des symboles réellement visibles sur le compte, et non d'une documentation générale. Critères en Q-07 |
| Spécifications de contrat | Taille de contrat, lot minimal, pas de lot, marge, risque minimal | `[CONFIRMÉ]` en démonstration le 2026-10-03, voir `docs/reports/2026-10-03-mt5-capabilities.md`. **La valeur du tick fournie par MT5 n'est fiable que pour l'or** : le risque se calcule avec le calculateur de profit du terminal |
| Historique | Bougies et ticks lus depuis le serveur du courtier | `[CONFIRMÉ]` : H1 depuis 2011 pour l'or et le BTC ; M15 au-delà du plafond de 100 000 bougies du terminal ; ticks crypto depuis janvier 2025 seulement |
| Plafond de bougies du terminal | Toute demande de 100 000 bougies ou plus est refusée en bloc | `[CONTRAINTE]` mesurée : demander au plus le plafond moins un, paginer au-delà |
| Horaires de négociation | Sessions par symbole, fournies par le terminal | `[DÉDUIT]` de l'exigence EF-020 |
| Heure serveur | Le terminal date les bougies à l'heure du serveur du courtier | **Serveur de démonstration à l'heure universelle, sans heure d'été** `[CONFIRMÉ]` le 2026-10-03 par mesure directe et par le calendrier de l'or. Serveur réel `[À CONFIRMER]` avant la phase 9 |
| Flux temps réel | Interrogation périodique, pas d'abonnement poussé | `[CONTRAINTE]` |
| Type de compte | Démonstration ou réel, lisible depuis le terminal | `[CONFIRMÉ]`, base du contrôle RM-017 |

**Vérification obligatoire avant tout développement d'exécution :** l'ensemble des points marqués à confirmer ci-dessus fait l'objet d'une tâche de vérification dédiée dont le livrable est un rapport écrit, et non une supposition.

### 12.2 Telegram

Bot dédié, jeton en variable d'environnement, liste blanche d'identifiants, limitation du nombre de tentatives, journalisation des commandes, révocation possible du jeton. `[CONFIRMÉ]`

### 12.3 API Claude

Appel déclenché uniquement à la production d'un signal candidat et à la rédaction des rapports. `[CONFIRMÉ]` Réponse contrainte par un schéma strict, délai maximal configuré, coût mesuré et plafonné, historisation intégrale des échanges. Le choix du modèle et le budget mensuel sont `[À CONFIRMER]`.

---

## 13. Interface utilisateur

Il n'existe pas d'interface graphique. Telegram constitue l'unique surface d'interaction, ce qui en fait un composant d'interface à part entière et non un simple canal de notification. `[DÉDUIT]`

### 13.1 Messages émis

| ID | Message | Déclencheur | Contenu essentiel |
|---|---|---|---|
| M-01 | Signal | Signal validé par la couche risque | Champs listés en F-013 |
| M-02 | Mise à jour de position | Ouverture, modification, clôture | Identifiant, marché, état, résultat |
| M-03 | Refus notable | Refus par la couche risque jugé signifiant | Motif, marché, contrôle déclenché |
| M-04 | Alerte système | Coupure, divergence, crash, saturation | Gravité, cause, action attendue |
| M-05 | Rapport | Échéance ou demande | Contenu de la section 14.3 |
| M-06 | Confirmation d'arrêt d'urgence | Déclenchement | Cause, périmètre exact de l'arrêt |

### 13.2 Commandes

| Commande | Rôle requis | Confirmation | Effet |
|---|---|---|---|
| `/status` | VIEWER | Non | État du système, des connexions, du mode et des marchés |
| `/markets` | VIEWER | Non | Marchés et leur état |
| `/signals` | VIEWER | Non | Derniers signaux |
| `/positions` | VIEWER | Non | Positions ouvertes |
| `/performance` | VIEWER | Non | Indicateurs courants |
| `/report daily` et `/report weekly` | VIEWER | Non | Rapport à la demande |
| `/pause` et `/resume` | OWNER | Oui | Suspension et reprise des signaux |
| `/disable <symbole>` et `/enable <symbole>` | OWNER | Oui | Activation d'un marché |
| `/mode <mode>` | OWNER | Oui | Changement de mode, `LIVE` exclu, voir RM-000 |
| `/close_all` | OWNER | Oui, renforcée | Clôture de toutes les positions |
| `/emergency_stop` | OWNER | Oui | Arrêt d'urgence, sans clôture implicite |
| `/help` | VIEWER | Non | Aide |

### 13.3 Règles d'interface

- toute commande sensible affiche une confirmation explicite décrivant précisément ce qui va se produire, notamment la distinction entre suspension et clôture ;
- le mode en cours est rappelé dans chaque message engageant ;
- la validation manuelle d'un signal depuis Telegram, si elle est activée, affiche une confirmation avant exécution, sauf mode automatique intégral explicitement activé. `[CONFIRMÉ]`

---

## 14. Statistiques et rapports

### 14.1 Axes d'analyse

Global, par marché, par stratégie, par version, par sens, par unité de temps, par jour de semaine, par heure, par mois, par mode. `[CONFIRMÉ]` L'analyse par régime de marché est `[À CONFIRMER]`, classée P3.

### 14.2 Indicateurs

Nombre de signaux, nombre d'opérations, signaux acceptés et refusés, taux de réussite, gains et pertes, profit net, facteur de profit, espérance par opération, gain moyen, perte moyenne, meilleure et pire opération, drawdown maximal, durée du drawdown, séries maximales de gains et de pertes, ratio rendement sur risque réalisé, durée moyenne des positions, slippage moyen, spread moyen, exposition moyenne, comparaison backtest contre réel, erreurs techniques, disponibilité du système. Ratios de Sharpe et de Sortino lorsque le nombre d'opérations les rend significatifs. `[CONFIRMÉ]`

### 14.3 Contenu des rapports

**Quotidien :** solde d'ouverture et de clôture, résultat du jour, positions ouvertes et fermées, taux de réussite, drawdown du jour, pertes évitées par les contrôles de risque, erreurs et anomalies, marchés actifs.

**Hebdomadaire :** performance par marché et par stratégie, évolution du capital, comparaison avec les semaines précédentes, séries de pertes, stabilité des résultats, points de surveillance.

**Mensuel :** synthèse, courbe de capital, drawdown, facteur de profit, espérance, comparaison entre backtest et réel, analyse des périodes de sous-performance, liste des stratégies à maintenir, surveiller ou suspendre, propositions d'expériences pour le mois suivant.

Le commentaire narratif peut être rédigé par le modèle. Les chiffres proviennent exclusivement de la base de données. `[CONFIRMÉ]`

---

## 15. Sécurité

### 15.1 Secrets

Jeton Telegram, jeton d'API Deriv, identifiants de base de données et clé d'API du modèle ne figurent jamais dans le code source. `[CONFIRMÉ]` Ils sont fournis par variables d'environnement ou par un gestionnaire de secrets, avec un jeu distinct par environnement. Un contrôle automatisé de détection de secrets est exécuté en intégration continue et en pré-commit.

### 15.2 Portée du compte de trading

Le principe reste le moindre privilège `[CONFIRMÉ]` ; sa mise en œuvre est révisée en version 1.2 pour MT5 :
- **mot de passe investisseur**, en lecture seule, tant que l'agent n'exécute pas d'ordre, c'est-à-dire jusqu'à la phase 8 ;
- mot de passe principal uniquement pour l'exécution, et jamais celui de l'espace client Deriv, qui permet les retraits ;
- identifiants de démonstration et réels strictement séparés, dans des fichiers d'environnement distincts ;
- procédure de changement immédiat du mot de passe MT5 en cas de fuite.

### 15.3 Telegram

Liste blanche d'identifiants, refus silencieux des inconnus, confirmation des commandes sensibles, journalisation, limitation des tentatives, révocation rapide du bot. `[CONFIRMÉ]`

### 15.4 Serveur

Accès par clé uniquement, pare-feu, mises à jour régulières, services inutiles désactivés, base non exposée publiquement, sauvegardes chiffrées, rotation des journaux, surveillance des connexions. `[CONFIRMÉ]`

### 15.5 Risques propres à l'agent

| Risque | Contrôle |
|---|---|
| Injection de consigne via une réponse du modèle | Réponse contrainte par schéma, périmètre d'action limité au rejet, test dédié avec réponse malveillante (EF-018) |
| Commande Telegram usurpée | Liste blanche, confirmation, journalisation |
| Exécution sur le mauvais compte | Contrôle RM-017 avant chaque ordre |
| Duplication d'ordre après incident | Clés d'idempotence et contraintes d'unicité |
| Fuite de secret dans les journaux | Filtrage systématique des valeurs sensibles avant écriture |

---

## 16. Stratégie de tests

| Catégorie | Périmètre | Moment | Critère |
|---|---|---|---|
| Unitaires | Indicateurs, règles d'entrée et de sortie, calcul de risque et de taille, stop-loss, objectifs, idempotence, indicateurs de performance, sérialisation | À chaque modification | Couverture élevée des paquets `indicators`, `risk` et `analytics`, ces paquets étant ceux dont une erreur coûte de l'argent |
| Intégration | Connexion au flux, reconstruction des bougies, stockage, génération de signal, envoi Telegram, création d'un ordre de démonstration, suivi de position, reconnexion, réconciliation | À chaque fusion | Scénarios exécutés contre un simulateur et, pour les plus critiques, contre le compte de démonstration |
| Architecture | Vérification que seul `risk` peut atteindre `execution` | À chaque modification | Test de dépendances d'import |
| Sécurité | Utilisateur non autorisé, jeton invalide, commande dupliquée, injection de paramètres, secret exposé, tentative de contournement des limites, rotation des clés | Avant chaque passage de phase | Aucun contournement possible |
| Reprise | Arrêt brutal, redémarrage avec position ouverte, coupure réseau, base indisponible, déconnexion du courtier, réponse perdue, message non remis | Avant les phases de démonstration et de production | Aucun doublon, aucun état incohérent conservé |
| Performance | Latence de traitement et de notification, comportement sous modèle lent | Avant la mise en production | Respect de ENF-002 à ENF-004 |
| Non-régression | Rejeu d'un jeu de signaux de référence | À chaque modification du moteur | Résultats identiques |

---

## 17. Déploiement et exploitation

**Révisé en version 1.2.** Le terminal MT5 et le paquet `MetaTrader5` imposent Windows (C-010). Le serveur Linux d'environ 5 € par mois initialement retenu ne convient plus.

- **Développement, exploration et démonstration :** poste Windows de l'opérateur. Aucun coût, mais pas de fonctionnement continu quand le poste est éteint, ce qui reste acceptable jusqu'à la phase 7. `[PROPOSITION]`
- **Fonctionnement continu, à partir de la campagne de paper trading :** serveur privé virtuel Windows. Coût `[À CONFIRMER]`, sensiblement supérieur à celui d'un serveur Linux.
- Supervision par service Windows ou tâche planifiée, avec redémarrage automatique de l'agent **et du terminal MT5**, et reconnexion du terminal au compte après redémarrage. `[PROPOSITION]`
- Pas de conteneurisation de l'agent : le terminal exige une session Windows. `[PROPOSITION]`

Chaîne d'intégration continue : analyse statique, typage, tests, détection de secrets, construction de l'image. `[PROPOSITION]`

Exploitation : sauvegarde quotidienne chiffrée avec restauration testée au moins une fois, rotation des journaux, supervision des ressources, procédure d'incident écrite, procédure de restauration écrite, procédure d'arrêt d'urgence écrite et testée.

Les backtests intensifs sont exécutés sur une machine distincte afin de ne pas perturber le service de production. `[CONFIRMÉ]`

---

## 18. Risques du projet

| ID | Risque | Probabilité | Impact | Niveau | Mitigation |
|---|---|---|---|---|---|
| R-01 | Le type de contrat Deriv ne permet pas la sémantique de risque attendue (C-003) | Moyenne | Très élevé | Critique | Vérification technique préalable et décision documentée avant tout développement du moteur de risque |
| R-02 | Stratégie sur-optimisée, rentable en backtest et perdante en réel | Élevée | Élevé | Critique | Séparation stricte des jeux de données, analyse glissante, simulation de coûts majorés, paper trading obligatoire |
| R-03 | Historique disponible insuffisant pour un backtest significatif | Moyenne | Élevé | Élevé | Vérification de la profondeur disponible dès la phase de cadrage, collecte continue démarrée au plus tôt |
| R-04 | Divergence entre l'état local et le compte | Moyenne | Élevé | Élevé | Réconciliation périodique et suspension automatique, sans résolution automatique |
| R-05 | Ordre dupliqué après incident réseau | Moyenne | Élevé | Élevé | Clés d'idempotence, contraintes d'unicité, tests de reprise |
| R-06 | Panne du fournisseur ou de la connexion | Élevée | Moyen | Élevé | Reconnexion progressive, suspension du trading, alerte |
| R-07 | Explication du modèle non fidèle au signal réel | Élevée | Moyen | Moyen | Chiffres issus de la base uniquement, explication clairement identifiée comme narrative |
| R-08 | Dépassement du budget d'appels au modèle | Moyenne | Faible | Moyen | Appel limité aux signaux candidats, plafond mensuel, compteur |
| R-09 | Compromission du bot Telegram | Faible | Très élevé | Élevé | Liste blanche, confirmations, impossibilité d'activer le mode réel depuis Telegram seul |
| R-10 | Perte de données par absence de sauvegarde vérifiée | Faible | Élevé | Moyen | Restauration testée, plusieurs points de restauration |
| R-11 | Contrainte réglementaire ou contractuelle non vérifiée | ~~Moyenne~~ **Réalisé** | Très élevé | **Matérialisé le 2026-10-01** | Indices synthétiques indisponibles pour un résident français. Périmètre rebâti sur l'or et la crypto, voir C-008. **Risque résiduel :** l'autorisation du trading automatisé sur le compte et les obligations fiscales françaises restent à vérifier avant tout passage en réel, en TASK-090 |
| R-13 | Capital insuffisant pour la taille minimale négociable | **Élevée** | Moyen | Élevé | Règle RM-019 : un instrument dont le risque minimal dépasse le plafond autorisé est déclaré inéligible au mode réel, sans bloquer les autres modes. Mesure des tailles minimales en TASK-003 |
| R-14 | Confusion entre validation de la mécanique et validation de la performance | **Élevée** | Élevé | **Critique** | À 100 €, le mode réel ne peut pas produire de statistiques interprétables (C-009). Les rapports doivent séparer explicitement les chiffres de démonstration et de réel, et ne jamais les agréger. Aucune décision de stratégie ne doit s'appuyer sur les résultats réels à ce niveau de capital |
| R-15 | Dépendance à Windows et au terminal MT5 : terminal fermé, déconnecté, mis à jour ou bloqué par une fenêtre | **Élevée** | Élevé | Élevé | Contrôle de santé du terminal à chaque cycle, suspension du trading dès qu'il ne répond plus, redémarrage automatique de l'agent et du terminal, alerte Telegram. Introduit par C-010 |
| R-16 | Heure du serveur du courtier prise pour l'heure universelle | ~~Moyenne~~ **Faible** — serveur démo mesuré à l'heure universelle le 2026-10-03 | **Très élevé** | Moyen, jusqu'à vérification du serveur réel | Bougies décalées de plusieurs heures, horaires de marché faux, rapports quotidiens coupés au mauvais moment, et parité avec l'historique rompue. Mesure du décalage en TASK-003, conversion à l'entrée unique des données, test dédié, et suivi des changements d'heure d'été du serveur |
| R-17 | Levier crypto plafonné à 1:2 dans l'Union : marge minimale supérieure au capital réel | **Élevée** | Moyen | Élevé | Traité par RM-019 : la crypto peut être déclarée inéligible au mode réel tout en restant pleinement active en démonstration. Mesure des tailles minimales en TASK-003 |
| R-12 | Dérive de performance d'une stratégie en production | Élevée | Moyen | Élevé | Comparaison mensuelle backtest contre réel, seuils d'alerte, procédure de suspension |

---

## 19. Livrables

Code source et dépôt versionné, documentation d'installation, documentation d'exploitation, documentation des stratégies, schéma de base de données, fichiers Docker, scripts de déploiement, tests automatisés, bot Telegram, service de collecte, moteur de stratégies, harnais de backtesting en tant qu'outil séparé, module de statistiques, module de reporting, gestionnaire de risque, exécuteur d'ordres de démonstration puis réel si retenu, procédure de sauvegarde, procédure de restauration, procédure d'arrêt d'urgence, rapport de sécurité, manuel utilisateur. `[CONFIRMÉ]`

---

## 20. Hypothèses et questions ouvertes

### 20.1 Décisions bloquantes

Ces décisions conditionnent le démarrage ou la poursuite du développement.

| ID | Question | Impact si non tranchée | Valeur par défaut proposée |
|---|---|---|---|
| Q-01 | ~~Résolution de C-003, type de contrat Deriv~~ | — | **Résolue le 2026-10-03 :** option C, Deriv MT5, imposée par C-010. Reste la formule de taille, en TASK-004, à partir des spécifications de contrat mesurées en TASK-003 |
| Q-02 | ~~Résolution de C-004, langage~~ | — | **Résolue le 2026-10-01 :** Python 3.12 partout |
| Q-03 | ~~Résolution de C-001, place du backtest~~ | — | **Résolue le 2026-10-01 :** code partagé, processus séparés. `backtest` n'est jamais chargé en production, et le test d'architecture le vérifie |
| Q-04 | ~~Résolution de C-002, pouvoir de l'IA~~ | — | **Résolue le 2026-10-01 :** veto asymétrique, et mode `shadow` par défaut à tout nouveau déploiement. Promotion du filtre conditionnée à une mesure, voir C-002 |
| Q-05 | Identifiants du compte démo MT5 | — | **Résolue le 2026-10-04, avec un écart accepté par l'opérateur :** les identifiants démo sont en place, mais avec le **mot de passe principal**, l'opérateur n'ayant pas pu créer le mot de passe investisseur. Conséquence : l'agent peut passer des ordres sur le compte démo dès maintenant. Risque accepté **pour la démonstration uniquement** ; le contrôle RM-017 du type de compte devient le seul rempart, et les identifiants réels devront être distincts. Énoncé initial : **Action opérateur, révisée en 1.2.** Ouvrir un compte démo Deriv MT5 donnant accès à l'or et à la crypto, installer le terminal MetaTrader 5, puis renseigner dans `.env` le numéro de compte, le nom du serveur et le **mot de passe investisseur**, en lecture seule. L'ancien besoin d'identifiant d'application et de jeton d'API disparaît avec C-010 |
| Q-06 | ~~Type de compte et pays de résidence~~ | — | **Résolue :** résidence française, entité européenne. Indices synthétiques indisponibles, voir C-008 |
| Q-07 | ~~Liste exacte des cryptomonnaies~~ | — | **Résolue le 2026-10-04 par l'opérateur : `BTCUSD` uniquement.** Le périmètre devient l'or (`XAUUSD`) et le BTC. Le BTC offre le meilleur spread mesuré parmi les cryptos (3,5 % de l'ATR M15) et un historique depuis 2011. **Il n'est pas éligible au mode réel avec 100 €** (marge 376 €) |
| Q-22 | Capital nécessaire au mode réel | **Avec 100 €, aucun des deux marchés retenus n'est éligible au réel** : marge de 183,93 € pour l'or, de 376 € pour le BTC (RM-019). La démonstration n'est pas concernée | `[À CONFIRMER]` avant la phase 9 : relever le capital, ou accepter que le mode réel reste inaccessible. **Nouveau, non bloquant avant la phase 9** |
| Q-08 | ~~Capital de référence~~ | — | **Résolue :** 100 €, avec les conséquences documentées en C-009 |
| Q-21 | ~~Taille minimale et distance de stop, par symbole~~ | — | **Résolue le 2026-10-03 par TASK-003.** Or inéligible au réel avec 100 € (marge 183,93 €) ; BTC, ETH et XRP inéligibles (marge) ; SOL, LTC et ADA éligibles, mais LTC et ADA ont un spread de 77 % et 86 % de l'ATR M15 |

### 20.2 Décisions non bloquantes

| ID | Question | Valeur par défaut proposée |
|---|---|---|
| Q-09 | ~~Risque par opération~~ | **Résolue :** 0,5 % en démonstration, 2 à 5 % en réel. Voir RM-005 révisée |
| Q-10 | ~~Perte quotidienne maximale~~ | **Résolue :** 2 % en démonstration, 5 % en réel. Voir RM-006 révisée |
| Q-11 | ~~Perte hebdomadaire et drawdown maximal~~ | **Résolue :** 6 % et 10 % en démonstration, 10 % et 20 % en réel. Voir RM-007 révisée |
| Q-12 | Nombre maximal de positions | Deux au total, une par marché |
| Q-13 | Unités de temps par marché | À déterminer par la recherche, M5 et M15 comme point de départ |
| Q-14 | Validation d'un signal par bouton Telegram | Non en version 1, signal informatif seul |
| Q-15 | Durée du paper trading | Minimum trente jours calendaires et trente opérations par stratégie |
| Q-16 | Fréquence des rapports | Quotidien et hebdomadaire actifs, mensuel disponible |
| Q-17 | Export PDF | Non en version 1 |
| Q-18 | Budget mensuel d'appels au modèle | À fixer, plafond technique obligatoire |
| Q-19 | Bascule vers PostgreSQL | Avant la phase de compte de démonstration |
| Q-20 | Rôle VIEWER | Non en version 1, un seul utilisateur |

---

## 21. Matrice de traçabilité

| Besoin | Exigence | Fonctionnalité | Données | Test |
|---|---|---|---|---|
| Fonctionner sans le poste de l'utilisateur | EF-001 | F-001, F-024 | `system_events` | Reprise après redémarrage du serveur |
| Surveiller uniquement les marchés autorisés | EF-002 | F-004 | `markets` | Unitaire sur le chargeur de configuration |
| Une stratégie par marché | EF-003 | F-007, F-008 | `strategies`, `strategy_versions` | Intégration multi-marchés |
| Recevoir les signaux rapidement | EF-004, EF-005, ENF-003 | F-009, F-013 | `signals` | Intégration Telegram et mesure de latence |
| Ne pas dupliquer les ordres | EF-006 | F-012 | `signals`, `orders` | Concurrence et reprise |
| Tout tracer | EF-007 | F-020 | `signal_events`, `audit_log` | Restitution d'un trade ancien |
| Connaître ses performances | EF-008, EF-024 | F-021 | `trades` | Contrôle croisé manuel |
| Recevoir un rapport quotidien | EF-009, EF-023 | F-022 | `reports` | Génération planifiée |
| Résister aux coupures | EF-010, ENF-005 | F-003 | `system_events` | Coupure simulée |
| Sécuriser le pilotage | EF-011, EF-019 | F-014 | `users`, `audit_log` | Tests de sécurité |
| Ne pas risquer le capital par accident | EF-012, EF-013, EF-015, EF-016, EF-017 | F-011, F-016, F-017, F-018, F-019 | `risk_decisions`, `positions` | Unitaires de risque et tests de reprise |
| Ne pas décider sur données douteuses | EF-014 | F-002 | `candles` | Injection de série dégradée |
| Garder l'IA sous contrôle | EF-018, EF-030 | F-010 | `ai_calls` | Test de réponse malveillante |
| Respecter les horaires de l'or | EF-020 | F-005 | `markets` | Test sur fenêtre de fermeture |
| Brancher une stratégie validée | EF-021, EF-022 | F-007, F-008 | `strategy_versions` | Rechargement en fonctionnement |
| Valider avant d'exposer du capital | EF-025, EF-026, EF-027 | F-015, F-025, F-026 | `trades` | Campagne de paper trading et rapport de backtest |

---

## 22. Critères de recette

### 22.1 Recette du produit minimum viable

1. le service fonctionne sur le serveur sans le poste local ;
2. les marchés autorisés sont configurables et respectés ;
3. les données temps réel sont reçues et les bougies correctement construites ;
4. une stratégie distincte est affectée à chaque marché ;
5. les signaux sont envoyés sur Telegram avec entrée, stop, objectifs et version ;
6. les doublons sont bloqués, y compris après redémarrage ;
7. tous les signaux, y compris refusés, sont enregistrés ;
8. les statistiques sont calculées et un rapport quotidien est envoyé ;
9. le système se reconnecte automatiquement et alerte en cas de coupure prolongée ;
10. les commandes Telegram sont restreintes et journalisées ;
11. le mode réel est désactivé par défaut ;
12. un arrêt d'urgence est disponible et sa procédure a été exécutée avec succès ;
13. les tests automatisés essentiels passent et aucun secret n'est présent dans le dépôt.

`[CONFIRMÉ]`

### 22.2 Recette de la phase automatique

1. un ordre de démonstration est correctement exécuté ;
2. le stop-loss est confirmé présent sur la position ;
3. la taille est conforme au risque configuré ;
4. la réconciliation avec le courtier ne révèle aucun écart ;
5. aucun doublon après redémarrage en cours d'exécution ;
6. les limites quotidiennes ont été déclenchées volontairement et ont bloqué les ordres ;
7. l'arrêt d'urgence a été validé en conditions réelles.

`[CONFIRMÉ]`

### 22.3 Conditions préalables au mode réel

Au-delà de 22.2 : autorisation du trading automatisé vérifiée auprès du fournisseur, conformité au pays de résidence vérifiée, stratégie validée hors échantillon puis en paper trading puis en démonstration, limites de risque configurées et testées, durée minimale de fonctionnement en démonstration atteinte, autorisation explicite de l'opérateur enregistrée, risque initial réduit.

---

## 23. Synthèse

Le projet est réalisable, et son principal facteur de réussite n'est pas la qualité du code mais la discipline du processus de validation. Trois points méritent l'attention avant toute ligne de code : la sémantique réelle des contrats Deriv, qui conditionne l'ensemble du moteur de risque (C-003) ; la frontière exacte du pouvoir accordé au modèle de langage (C-002) ; et le partage du code de stratégie entre recherche et production, qui seul rend la comparaison entre backtest et réel honnête (C-001).

Le document de référence pour l'exécution est `ROADMAP.md`, qui dérive de ce cahier et couvre chaque exigence par au moins une tâche.
