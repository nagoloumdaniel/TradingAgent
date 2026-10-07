# Architecture v3 — correspondance avec le cahier des charges

> Ce document relie chaque section du cahier « AI Scalping Trading System BTCUSD &
> XAUUSD » au code qui la met en œuvre. Il sert de carte de lecture, pas de spécification :
> la spécification reste le cahier.

## Principe directeur (§4, §54)

Cinq couches, aucune ne contournant les protections d'une autre :

| Couche | Rôle | Où |
|---|---|---|
| Recherche IA | cherche, analyse, **propose** | `research/`, `ai/analyst.py`, `ai/researcher.py` |
| Backtest & validation | mesure et vérifie la robustesse | `backtest/`, `research/protocol.py`, `registry/` |
| Risk engine | limite, refuse, réduit | `risk/` |
| Trading engine | exécute la stratégie **validée** | `signals/`, `runtime/`, `execution/` |
| EA MQL5 | exécute, surveille, protège **dans MT5** | `mt5/Experts/TradingAgent/`, `ea/` |
| Analytics & web | mesure et observe | `analytics/`, `reporting/`, `web/` |

L'IA ne décide jamais d'un trade en production (§5, §39) : elle produit des analyses et des
propositions qui passent obligatoirement par le protocole de validation.

## Correspondance section par section

| § | Sujet | Implémentation |
|---|---|---|
| 3 | Deux marchés seulement | `config/agent.yaml` (XAUUSD, BTCUSD) |
| 5 | Rôle de l'IA | `ai/analyst.py`, `ai/researcher.py` |
| 6, 14 | Statuts de stratégie, LIVE immuable | `core/states.py` (`StrategyStatus`), `registry/store.py` |
| 7, 8 | Recherche multi-familles, données | `backtest/datasets.py`, `research/campaign.py` |
| 9, 10 | Backtest réaliste, anti-surapprentissage | `backtest/harness.py`, `backtest/costs.py`, `research/protocol.py` |
| 11, 12 | Walk-forward, Monte-Carlo, stress | `research/protocol.py` |
| 13, 49 | Critères de sélection, portes de promotion | `registry/gates.py` |
| 15, 16, 17 | Analyse des pertes, dégradation, boucle d'amélioration | `ai/analyst.py`, `ai/researcher.py`, `ai/daily.py` (passage quotidien câblé dans la boucle) |
| 18, 19 | EA guardians | `mt5/Experts/TradingAgent/`, `ea/bridge.py`, `ea/health.py` |
| 20 | Latence et slippage | `storage/telemetry.py`, `runtime/pipeline.py` |
| 21, 22 | Risk management, kill switch | `risk/`, `control/guardian.py`, `control/cli.py` |
| 23 à 34 | Dashboard, analytics, source de vérité | `web/`, `analytics/`, `reporting/` |
| 27, 28 | Journal des décisions, replay d'un trade | `web/` (détail `/trades/{id}`), `storage/models.py` |
| 31, 32 | Statistiques globales et de scalping | `analytics/performance.py`, `analytics/scalping.py` |
| 35 | Entités de base | `storage/models.py`, migration `0006` |
| 36, 37 | Logs, observabilité, alertes | `observability.py`, `notify/` |
| 38, 43 | Contrôle humain, sécurité | `control/`, `config/redaction.py`, `notify/access.py`, `web/` (jeton d'accès) |
| 42 | Temps réel | `web/sse.py` |
| 44 | Environnements progressifs | `core/mode.py`, `control/live.py`, `reporting/campaign.py` |
| 45, 46 | Santé du système, anomalies | `web/` (page system), `control/guardian.py`, `ea/health.py` |
| 47, 48 | Performance du scalper, rentabilité | `analytics/`, `reporting/comparison.py` |
| 50, 52 | Boucle recherche → validation → déploiement | `research/`, `registry/`, `ai/daily.py`, `runtime/` |
| 51 | Dashboard final | `web/views.py`, `web/templates/` |
| 53 | Ordre de développement | phases de `ROADMAP.md` |

## Décisions prises et leurs raisons

- **Dashboard en Python (FastAPI + Jinja2 + SSE)**, et non Next.js/TypeScript : le §41
  présentait une pile « possible ». Une seule pile garde l'ensemble testable par la suite
  pytest existante et déployable sur le même hôte Windows, sans chaîne de build Node.
- **Les EA ne décident pas** (§18) : ils exécutent les ordres autorisés, protègent, et
  remontent. Toute la décision reste dans `runtime/` et `risk/`, ce qui préserve la parité
  backtest/production (C-001).
- **Le LLM est optionnel** : sans clé, l'analyse déterministe et les propositions sortent
  quand même. Une panne du modèle ne bloque jamais le système (§39).
- **Une seule source de chiffres** : `analytics/`, lue par le web comme par les rapports.
  Le dashboard ne recalcule rien (§34).

## Limites connues

- L'authentification du dashboard n'existe pas en v1 : il écoute en local
  (`127.0.0.1:8787`) et ne doit pas être exposé tel quel (§43).
- L'IA n'a pas encore tourné sur un historique réel long : les analyses et propositions
  sont outillées et testées, mais leur valeur dépendra des données accumulées.
- Le pont EA reste fondé sur des fichiers : suffisant pour un poste unique, à remplacer par
  un canal authentifié si l'agent et le terminal sont un jour séparés.
