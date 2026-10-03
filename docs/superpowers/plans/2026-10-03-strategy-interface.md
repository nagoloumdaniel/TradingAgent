# Interface de stratégie — plan d'implémentation

> **Exécution :** inline, dans la session qui a produit la spécification, avec `executing-plans`. Chaque étape suit le cycle TDD : test rouge, implémentation minimale, test vert, chaîne de pré-commit verte, commit.

**Objectif :** livrer le contrat stratégie ↔ moteur de TASK-031, tel que spécifié.

**Architecture :** types partagés dans `core`, stratégies pures et point d'entrée unique dans `strategies`, chargement des fichiers dans `config` avec le registre injecté.

**Stack :** Python 3.12, pydantic, PyYAML, pytest.

**Spécification :** `docs/superpowers/specs/2026-10-03-strategy-interface-design.md`, abrégée « spec » ci-dessous.

## Contraintes globales

- Paquets purs `indicators` et `strategies` : aucune lecture d'horloge, aucun réseau, aucun aléatoire, aucune entrée-sortie (spec §5).
- Toute date en UTC avec fuseau.
- Aucun secret, aucune valeur fautive dans un message d'erreur de configuration.
- Commit à la fin de chaque étape, préfixe `TASK-031`.

---

### Étape 1 — Types partagés dans `core`

**Fichiers :** créer `core/mode.py`, `core/market.py`, `core/signal.py` ; modifier `config/settings.py`, qui importe `TradingMode` depuis `core.mode` ; tests dans `tests/core/`.

**Interfaces produites :** `TradingMode`, `mode_rank(mode) -> int`, `AiFilter`, `Direction`, `Candle` avec `close_time`, `SignalCandidate`, `InvalidSignalError` (spec §2).

**Cas de test :**
- `mode_rank` strictement croissant dans l'ordre OBSERVATION, SIGNAL, PAPER, DEMO, LIVE ;
- `Candle` : `close_time` calculé ; refus d'une date sans fuseau ou hors UTC, d'une valeur non finie, de `high < low`, d'une ouverture ou d'une clôture hors de `[low, high]` ;
- `SignalCandidate` valide à l'achat et à la vente ; refus de chaque violation de la spec §2.3, avec `InvalidSignalError` ; immuabilité de `indicators`, y compris si le dictionnaire source est modifié après coup ;
- tests de `config/settings.py` toujours verts après le déplacement de `TradingMode`.

**Vérification :** `uv run pytest -q`, puis pré-commit.

### Étape 2 — Règle de pureté resserrée et étendue

**Fichiers :** modifier `tests/test_architecture.py`.

**Changements :** dépendances autorisées par paquet pur, `{"indicators": {"core"}, "strategies": {"core", "indicators"}}` ; retrait de `datetime` des modules interdits ; détection syntaxique de tout appel `.now(`, `.utcnow(` ou `.today(` dans un paquet pur.

**Cas de test synthétiques :**
- interdits : `datetime.now(UTC)` dans `strategies`, `date.today()` dans `indicators`, `import time` dans `strategies`, `from tradingagent.config import settings` dans `strategies`, `from tradingagent.data import feed` dans `strategies` ;
- permis : `from datetime import datetime, timedelta` dans `strategies`, `from tradingagent.indicators.momentum import rsi` dans `strategies`, `datetime.now(UTC)` dans `data`.

### Étape 3 — Manifeste, base de stratégie, registre

**Fichiers :** créer `strategies/manifest.py`, `strategies/base.py`, `strategies/registry.py` ; tests dans `tests/strategies/`.

**Interfaces produites :** `StrategyManifest` avec `ref` et `primary_timeframe`, `parse_ref(ref) -> tuple[str, str]`, `StrategyContext` avec `series`, `opens`, `highs`, `lows` et `closes`, `Strategy[P]`, `build_registry(*classes)`, `REGISTRY` vide (spec §3.1 à §3.3).

**Cas de test :**
- manifeste valide avec ses valeurs par défaut (`expiry_bars=1`, `ai_filter=shadow`, `parameters={}`) ;
- refus : identifiant ou version mal formés, symboles ou unités de temps en double, listes vides, `history_bars` hors de 1 à 10 000, clé inconnue ;
- `parse_ref` sur une référence valide et sur des références mal formées ;
- `StrategyContext.series` lève une `KeyError` explicite pour une unité non déclarée ; `closes` renvoie des `float` dans l'ordre ;
- `build_registry` refuse un identifiant en double et un modèle de paramètres non gelé.

### Étape 4 — Point d'entrée unique `evaluate`

**Fichiers :** créer `strategies/evaluation.py` ; tests dans `tests/strategies/test_evaluation.py`.

**Interfaces produites :** `evaluate(strategy, manifest, symbol, candles, evaluated_at) -> Outcome`, `Outcome`, `OutcomeKind` (spec §3.4).

**Cas de test :**
- `SIGNAL` et `NO_SIGNAL` ;
- la fenêtre reçue par la stratégie contient exactement `history_bars` bougies par unité, toutes closes avant `evaluated_at` ;
- multi-unités : une bougie H1 encore ouverte n'est jamais transmise ;
- `INSUFFICIENT_HISTORY` avec une stratégie espion **jamais appelée**, y compris quand une unité est absente du dictionnaire ;
- `STRATEGY_EXCEPTION` ; `INVALID_SIGNAL` sur `InvalidSignalError` et sur un type de retour erroné ; une interruption clavier n'est pas attrapée ;
- préconditions qui lèvent `ValueError` : identifiant de stratégie différent, symbole non autorisé, `evaluated_at` sans fuseau, fenêtre non triée, bougie d'une autre unité, dernière bougie principale qui ne clôt pas à `evaluated_at` ;
- ajouter des bougies futures ne change pas le résultat ;
- deux stratégies évaluées en alternance sur deux symboles donnent les mêmes résultats qu'isolées (critère de TASK-031).

### Étape 5 — Contrat de stratégie

**Fichiers :** créer `strategies/contract.py` ; tests dans `tests/strategies/test_contract.py`.

**Interfaces produites :** `check_strategy_contract(strategy, manifest, symbol, candles, evaluation_times) -> list[str]` (spec §3.5).

**Cas de test :** une stratégie conforme ne renvoie aucune violation ; une stratégie à état caché, qui compte ses appels, est signalée pour dépendance à l'ordre ; une stratégie non déterministe est signalée pour non-déterminisme.

### Étape 6 — Extraction des utilitaires YAML

**Fichiers :** créer `config/_yaml.py` ; modifier `config/agent.py`, qui l'utilise.

**Nature :** refactorisation sans changement de comportement. Les tests existants de `tests/config/test_agent_config.py` restent verts **sans modification**, et c'est le critère de l'étape.

### Étape 7 — Catalogue de stratégies

**Fichiers :** créer `config/strategy_catalog.py` ; tests dans `tests/config/test_strategy_catalog.py`.

**Interfaces produites :** `LoadedStrategy`, `load_strategy_catalog(directory, registry) -> dict[str, LoadedStrategy]` (spec §4.2).

**Cas de test :** catalogue valide indexé par `ref` ; nom de fichier différent de la référence ; identifiant absent du registre ; paramètre refusé localisé à sa ligne ; tous les problèmes de plusieurs fichiers signalés en une fois ; répertoire vide renvoyant un catalogue vide.

### Étape 8 — Contrôles marché ↔ stratégie dans `agent.yaml`

**Fichiers :** modifier `config/agent.py` et `tests/config/test_agent_config.py`.

**Changements (spec §4.3) :** retrait de `MarketConfig.timeframes` ; `strategies: Mapping[str, StrategyManifest]` remplace `known_strategies` ; ajout de `mode: TradingMode`.

**Cas de test ajoutés :** référence absente du catalogue ; symbole hors `allowed_symbols` (RM-003) ; `max_mode` inférieur au mode de l'agent (RM-016) ; `max_mode` égal ou supérieur accepté ; une clé `timeframes` désormais refusée comme clé inconnue. Les tests existants sont adaptés au nouveau format.

### Étape 9 — Clôture

Mise à jour de `ROADMAP.md` (TASK-031 terminée, écarts constatés), suite complète et pré-commit verts, commit final.
