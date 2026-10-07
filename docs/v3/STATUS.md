# État du cahier des charges v3 — « AI Scalping Trading System BTCUSD & XAUUSD »

> Section par section, ce qui est livré, où, et ce qui reste. Dernière mise à jour :
> 2026-10-07. Un point n'est coché que s'il est vérifié par exécution ; les lignes
> « reste » disent ce qui dépend de l'opérateur, du temps ou d'un serveur.

## 1 à 6 — Vision, objectifs, périmètre, architecture, rôle de l'IA, stratégie de production

| § | État | Où |
|---|---|---|
| 1 Vision (« l'IA recherche, elle ne décide pas ») | ✅ | `docs/v3/ARCHITECTURE.md`, invariants dans `README.md` |
| 2 Objectifs 1-16 | ✅ sauf 9 et 15 partiels | voir lignes ci-dessous |
| 3 Deux marchés seulement, environnements indépendants | ✅ | `config/agent.yaml`, un registre par (marché, ref) |
| 4 Couches séparées | ✅ | `tests/test_architecture.py` (44 tests) |
| 5 Rôle de l'IA | ✅ | `ai/analyst.py`, `ai/researcher.py`, `ai/daily.py` |
| 6 Statut connu du moteur | ✅ | `app.py` journalise marché, ref, statut, date de promotion au démarrage ; PMODE/`active()` |

**Reste (2, objectif 9)** : l'exécution réelle sur MT5 est livrée et testée en démonstration ;
l'objectif « exécuter automatiquement en production » dépend de TASK-090 et de la campagne.

## 7 à 13 — Recherche, données, backtest, anti-surapprentissage, walk-forward, Monte-Carlo, critères

| § | État | Où |
|---|---|---|
| 7 Plusieurs familles explorées | ✅ | `research/discovery.py` : 6 familles, échelle de causes, 1 retenu sur 17 sur l'or réel |
| 8 Données nettoyées et contrôlées | ✅ | `backtest/datasets.py`, `data/quality.py`; jeu réel XAUUSD M15 (3 999 bougies, 18 trous recensés) |
| 9 Backtest réaliste (frais, spread, slippage, latence) | ✅ | `backtest/harness.py`, `backtest/costs.py` |
| 10 Protection contre le surapprentissage | ✅ | `research/protocol.py` : holdout scellé par jeton, walk-forward, Monte-Carlo, stress |
| 11 Walk-forward | ✅ | idem, fenêtres glissantes |
| 12 Monte-Carlo et stress | ✅ | idem, plus `backtest/costs.stressed()` |
| 13 Critères de sélection multi-métriques | ✅ | `analytics/performance.py`, `registry/gates.py` |

**Reste** : un historique Deriv multi-marchés versionné (aucun jeu crypto réel exploitable ici) ;
contrôle du taux de fausses découvertes en sélection multiple.

## 14 à 17 — Cycle de vie, analyse des pertes, dégradation, boucle d'amélioration

| § | État | Où |
|---|---|---|
| 14 Statuts DISCOVERED→…→LIVE→DEPRECATED, LIVE immuable | ✅ | `core/states.py`, `registry/store.py`, CLI démontrée |
| 15 Analyse d'une perte (5 classes) | ✅ | `ai/analyst.py` |
| 16 Détection de dégradation et hypothèse | ✅ | idem + `ai/researcher.py` |
| 17 Boucle d'amélioration | ✅ | `ai/daily.py`, passage quotidien câblé dans `runtime/loop.py` |

**Reste** : rien sur le code ; la valeur de l'IA dépendra des données accumulées.

## 18 à 22 — EA MQL5, latence, risque, kill switch

| § | État | Où |
|---|---|---|
| 18 Deux EA guardians | ✅ | `mt5/Experts/TradingAgent/`, compilés 0 erreur / 0 avertissement |
| 19 Présence dans le terminal, détection de divergence | ✅ | EA + `ea/bridge.compare_expected`, `execution/reconciliation.py` |
| 20 Latence et slippage mesurés | ✅ | `storage/telemetry.py`, `elapsed_ms` dans chaque événement |
| 21 Risk management (11 points) | ✅ | `risk/checks.py` : **17 contrôles**, dont exposition totale BTC+XAU et slippage |
| 22 Kill switch | ✅ | `control/guardian.py`, arrêt d'urgence persistant, `ExecutionEventKind.STOP_MISSING` |

**Reste (§18)** : installation des deux EA dans le terminal par l'opérateur (procédure écrite).

## 23 à 34 — Dashboard, analytics, source de vérité

| § | État | Où |
|---|---|---|
| 23 Dashboard de monitoring | ✅ | `web/` (10 pages, FastAPI + Jinja2) |
| 24 Page Overview | ✅ | `/` |
| 25 Positions temps réel | ✅ | `/positions` + SSE |
| 26 Historique des trades | ✅ | `/trades` |
| 27 Journal des décisions | ✅ | `/trades/{id}` répond « pourquoi » et « quelle version » |
| 28 Trade Replay | ✅ | `/trades/{id}` : chronologie complète, y compris signal refusé |
| 29 AI Lab dans le dashboard | ✅ | `/ai-lab` |
| 30 Versioning des stratégies | ✅ | `/strategies`, `strategy_registry` |
| 31 Statistiques globales | ✅ | `analytics/performance.py`, page Overview |
| 32 Statistiques de scalping | ✅ | `analytics/scalping.py`, page `/scalping` |
| 33 Analyse par marché | ✅ | Overview et Strategies séparent BTCUSD / XAUUSD |
| 34 Analytics = source de vérité | ✅ | le web ne recalcule rien ; test AST + comptage de lignes |

**Reste** : les « conditions de marché » au moment du signal (bougies, spread) ne sont pas
rattachées en base ; la page ne les invente donc pas.

## 35 à 43 — Base, logs, alertes, contrôle humain, sécurité, temps réel

| § | État | Où |
|---|---|---|
| 35 Entités versionnées et traçables | ✅ | migration `0006` : 7 tables (registre, backtests, validations, analyses, propositions, télémétrie, performances) |
| 36 Logs et observabilité | ✅ | `observability.py`, `system_events`, niveaux INFO→CRITICAL |
| 37 Alertes Telegram | ✅ | ouvertures/clôtures compactes, kill switch, EA hors ligne, hypothèses IA, santé |
| 38 Contrôle humain et audit | ✅ | `audit_log`, commandes sensibles confirmées, pages Risk et System |
| 39 Ce que l'IA ne doit pas faire | ✅ | garanti par test : une réponse malveillante n'écrit rien dans les tables de trading |
| 40 Architecture du code | ✅ | `docs/v3/ARCHITECTURE.md` |
| 41 Stack technique | ✅ avec une déviation documentée | dashboard en Python plutôt que Next.js (raison dans le même document) |
| 42 Temps réel | ✅ | `web/sse.py` |
| 43 Sécurité | ✅ | secrets hors dépôt, masquage, RM-017, jeton d'accès optionnel au dashboard, RLS Supabase |

**Reste (37)** : les alertes réelles dépendent du jeton Telegram de l'opérateur.

## 44 à 53 — Environnements, santé, anomalies, rentabilité, recette

| § | État | Où |
|---|---|---|
| 44 Environnements progressifs | ✅ | `core/mode.py`, `control/live.py`, `reporting/campaign.py` |
| 45 Santé du système | ✅ | `/system` : base, marché, EA, latences, erreurs |
| 46 Gestion des anomalies | ✅ | `control/guardian.py`, réconciliation, kill switch EA local |
| 47 Performance du scalper | ✅ | `storage/scalping.execution_costs`, page `/scalping` |
| 48 Rentabilité = robustesse + contrôle | ✅ | `research/protocol.py`, refus de promotion documenté |
| 49 Critères de promotion | ✅ | `registry/gates.py` : 9 portes nommées, refus explicite |
| 50 Architecture finale | ✅ | `docs/v3/ARCHITECTURE.md` |
| 51 Dashboard final | ✅ | 10 pages |
| 52 Boucle autonome contrôlée | ✅ | recherche → validation → déploiement → suivi → analyse → hypothèse → re-backtest |
| 53 Ordre de développement (12 phases) | ✅ phases 1-11 outillées, 12 conditionnée | voir ci-dessous |

**Reste (53, phases 11-12)** : la campagne de paper trading doit **courir** ses 30 jours ;
la phase 12 (production progressive) est conditionnée à TASK-090, au serveur et à la
signature de conformité.

## Ce qui reste, en une liste

1. **Opérateur, décision** : signer `docs/legal/2026-10-07-verification-operateur.md`.
2. **Opérateur, infrastructure** : provisionner le serveur Windows (TASK-051).
3. **Temps** : faire tourner la campagne paper 30 jours (`scripts/paper_campaign.py` la mesure).
4. **Opérateur, clés** : `TEST_DATABASE_URL`, `BACKUP_PASSPHRASE`, `ANTHROPIC_API_KEY` (optionnelle).
5. **Opérateur, terminal** : installer les deux EA (procédure dans `docs/ea/README.md`).
6. **Fait mais à confirmer par un tiers** : restauration sur machine vierge, validation de la
   documentation d'exploitation.

Aucune de ces lignes n'est un blocage technique : ce sont des décisions, des clés, du temps
ou une machine.
