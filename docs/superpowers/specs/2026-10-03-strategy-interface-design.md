# Interface de stratégie — conception

**Tâche :** TASK-031 · **Statut :** validée par l'opérateur le 2026-10-03 · **Couvre :** F-007, RM-003, RM-016, EF-003

Ce document fixe le contrat entre le moteur et toute stratégie, présente ou future. Il complète `CAHIER_DES_CHARGES.md` et `ROADMAP.md`, qu'il ne répète pas.

## 1. Décisions

| Décision | Choix retenu | Motif |
|---|---|---|
| Périmètre V1 | Entrées seules. Stop-loss et objectifs fixés à l'entrée, sortie quand l'un est touché | Les sorties partielles dépendent du type de contrat, encore ouvert (Q-01). L'interface reste extensible sans casser les stratégies existantes |
| Forme | Fonction sans mémoire, évaluée sur une fenêtre fixe de bougies closes | Reproductibilité, impossibilité de lire le futur, parité exacte entre backtest et production, robustesse au redémarrage |
| Écartée | Objet à mémoire, piloté par événements | État divergent entre backtest et production, perdu au redémarrage |
| Écartée | Règles déclaratives en YAML | Mini-langage à maintenir, expressivité insuffisante (limite de F-007). Possible plus tard, en surcouche |
| Coût accepté | Backtest plus lent : indicateurs recalculés sur la fenêtre à chaque bougie | Optimisation seulement si la recherche le justifie, et sans changer la sémantique de fenêtre |

## 2. Types partagés, paquet `core`

### 2.1 `core/mode.py`

- `TradingMode` est **déplacé** depuis `config/settings.py`, qui l'importe désormais de `core`. Valeurs inchangées : `OBSERVATION`, `SIGNAL`, `PAPER`, `DEMO`, `LIVE`.
- `mode_rank(mode: TradingMode) -> int` : ordre strict `OBSERVATION < SIGNAL < PAPER < DEMO < LIVE`.
- `AiFilter(StrEnum)` : `shadow`, `advisory`, `required`, conformément à C-002.

### 2.2 `core/market.py`

- `Direction(StrEnum)` : `BUY`, `SELL`.
- `Candle`, dataclass gelée : `timeframe: Timeframe`, `open_time: datetime`, `open`, `high`, `low`, `close` en `float`.
  - propriété `close_time = open_time + timeframe.seconds` ;
  - à la construction, refus avec `ValueError` si `open_time` n'a pas de fuseau horaire ou n'est pas en UTC, si une valeur n'est pas finie, ou si `low ≤ min(open, close) ≤ max(open, close) ≤ high` n'est pas respecté ;
  - pas de volume : les bougies Deriv n'en fournissent pas.

### 2.3 `core/signal.py`

- `InvalidSignalError(ValueError)`.
- `SignalCandidate`, dataclass gelée, **décision de la stratégie et rien d'autre** :

| Champ | Type | Règle |
|---|---|---|
| `direction` | `Direction` | — |
| `entry_low`, `entry_high` | `float` | finis, `entry_low ≤ entry_high` |
| `stop_loss` | `float` | fini ; à l'achat `stop_loss < entry_low`, à la vente `stop_loss > entry_high` |
| `take_profits` | `tuple[float, ...]` | au moins un ; finis ; à l'achat tous `> entry_high` et strictement croissants, à la vente tous `< entry_low` et strictement décroissants |
| `reason` | `str` | non vide après suppression des espaces ; sert d'explication de repli quand l'IA est indisponible (RM-011) |
| `indicators` | `Mapping[str, float]` | valeurs finies ; copie immuable ; audit F-009 |

Toute violation lève `InvalidSignalError` **dans le constructeur**. Un `SignalCandidate` incohérent ne peut donc exister nulle part dans le système.

Ne figurent **pas** dans `SignalCandidate`, car ajoutés par le moteur : identifiant, clé d'idempotence, stratégie, version, unité de temps, prix observé, heures de génération et d'expiration, ratio risque/rendement. Une stratégie ne peut ainsi ni usurper une identité, ni dater un signal, ni annoncer un ratio sans rapport avec ses niveaux.

## 3. Paquet `strategies`

### 3.1 `strategies/base.py`

```python
class Strategy(ABC, Generic[P]):  # P lié à pydantic.BaseModel
    strategy_id: ClassVar[str]
    parameters_model: ClassVar[type[BaseModel]]

    def __init__(self, parameters: P) -> None: ...
    @property
    def parameters(self) -> P: ...

    @abstractmethod
    def evaluate(self, context: StrategyContext) -> SignalCandidate | None: ...
```

`StrategyContext`, dataclass gelée :
- `symbol: str` ;
- `evaluated_at: datetime` : heure de clôture de la bougie déclenchante, **jamais l'heure murale** ;
- `candles: Mapping[Timeframe, tuple[Candle, ...]]` : bougies closes, de la plus ancienne à la plus récente, exactement `history_bars` par unité de temps déclarée ;
- `series(timeframe)` : lève une `KeyError` explicite si l'unité n'est pas déclarée ;
- `opens(timeframe)`, `highs(timeframe)`, `lows(timeframe)`, `closes(timeframe)` : listes de `float`, prêtes pour le paquet `indicators`.

### 3.2 `strategies/manifest.py`

`StrategyManifest`, modèle pydantic gelé, clés inconnues refusées :

| Champ | Contrainte | Défaut |
|---|---|---|
| `strategy_id` | `^[a-z][a-z0-9_]*$` | — |
| `version` | `^\d+\.\d+\.\d+$` | — |
| `max_mode` | `TradingMode` | — |
| `allowed_symbols` | au moins un, sans doublon | — |
| `timeframes` | au moins une, sans doublon ; **la première déclenche l'évaluation** | — |
| `history_bars` | entier, 1 à 10 000 ; **doit inclure le préchauffage** des indicateurs récursifs | — |
| `expiry_bars` | entier ≥ 1 | 1 |
| `ai_filter` | `AiFilter` | `shadow` |
| `parameters` | dictionnaire libre, validé ensuite par `parameters_model` de la classe | `{}` |

Propriétés : `ref` (`"<strategy_id>@<version>"`) et `primary_timeframe` (`timeframes[0]`).

Exemple, fichier `config/strategies/witness@1.0.0.yaml` :

```yaml
strategy_id: witness
version: 1.0.0
max_mode: SIGNAL
allowed_symbols: [frxXAUUSD]
timeframes: [M15, H1]
history_bars: 300
ai_filter: shadow
parameters:
  ema_fast: 20
  ema_slow: 50
```

### 3.3 `strategies/registry.py`

- `build_registry(*classes) -> Mapping[str, type[Strategy]]` : registre explicite. Lève `ValueError` en cas d'identifiant en double, si `parameters_model` n'est pas gelé, ou **s'il ne refuse pas les clés inconnues** (`extra="forbid"`). Sans ce dernier contrôle, ajouté à l'implémentation, un paramètre mal orthographié dans un manifeste serait ignoré en silence et la valeur par défaut utilisée à sa place.
- `REGISTRY` : registre de production, **vide jusqu'à TASK-033**.
- Aucun chargement dynamique d'une classe désignée par un fichier de configuration : un YAML ne doit jamais décider quel code s'exécute.

### 3.4 `strategies/evaluation.py` — point d'entrée unique

```python
def evaluate(
    strategy: Strategy[Any],
    manifest: StrategyManifest,
    symbol: str,
    candles: Mapping[Timeframe, Sequence[Candle]],
    evaluated_at: datetime,
) -> Outcome: ...
```

**C'est la seule voie d'évaluation d'une stratégie.** Le moteur de production (TASK-034) et le harnais de backtest (TASK-061) l'appellent, et rien d'autre.

Préconditions, dont la violation est une **erreur de programmation** et lève `ValueError` sans produire d'`Outcome` :
- `manifest.strategy_id == strategy.strategy_id` ;
- `symbol` appartient à `manifest.allowed_symbols` ;
- `evaluated_at` porte un fuseau UTC ;
- chaque série est triée par `open_time` croissante. **Seule la fenêtre retenue est contrôlée** ; un désordre en dehors n'est pas détecté ici et relève du contrôle qualité de TASK-012.

Déroulé, dans cet ordre exact :
1. pour chaque unité de temps déclarée, recherche par dichotomie de la dernière bougie dont `close_time ≤ evaluated_at`. Le coût est `O(log n)`, pas `O(n)` : un backtest peut passer l'historique complet à chaque appel sans complexité quadratique. Une unité absente du dictionnaire `candles` compte pour zéro bougie ;
2. si moins de `history_bars` bougies sont disponibles pour une unité, retour `INSUFFICIENT_HISTORY` **sans appeler la stratégie**. Ce cas couvre notamment le démarrage, avant que l'historique soit chargé ;
3. découpe des `history_bars` dernières bougies, puis contrôle sur cette seule fenêtre du tri, de l'unité de chaque bougie, et du fait que **la dernière bougie de l'unité principale clôt exactement à `evaluated_at`**. Une violation est une erreur de programmation et lève `ValueError` : le moteur ne doit appeler `evaluate` qu'à la clôture d'une bougie principale effectivement reçue, la détection d'une donnée manquante relevant de TASK-012 en amont ;
4. construction du contexte et appel de `strategy.evaluate`. `InvalidSignalError` donne `INVALID_SIGNAL`, toute autre `Exception` donne `STRATEGY_EXCEPTION` avec le type et le message, et un retour qui n'est ni `None` ni `SignalCandidate` donne `INVALID_SIGNAL`. Les `BaseException` hors `Exception`, comme une interruption clavier, ne sont pas attrapées.

`Outcome`, dataclass gelée : `kind: OutcomeKind`, `candidate: SignalCandidate | None`, `detail: str`.

| `OutcomeKind` | Sens | Compte pour la quarantaine de F-009 |
|---|---|---|
| `SIGNAL` | signal candidat valide | non |
| `NO_SIGNAL` | conditions non réunies | non |
| `INSUFFICIENT_HISTORY` | préchauffage incomplet ; `detail` indique l'unité et `disponible/requis` | non |
| `INVALID_SIGNAL` | signal incohérent ou type de retour erroné | **oui** |
| `STRATEGY_EXCEPTION` | exception levée par la stratégie | **oui** |

La fonction n'écrit aucun journal : elle est pure. La journalisation revient au moteur.

### 3.5 `strategies/contract.py`

`check_strategy_contract(strategy, manifest, symbol, candles, evaluation_times) -> list[str]` renvoie la liste des violations, vide si la stratégie respecte le contrat :
- **déterminisme** : deux évaluations au même instant donnent le même `Outcome` ;
- **indépendance à l'ordre** : évaluer les instants dans l'ordre puis dans l'ordre inverse donne les mêmes résultats ;
- **absence de lecture du futur** : à chaque instant, le résultat est identique avec l'historique complet et avec l'historique tronqué à cet instant.

Toute stratégie ajoutée par la suite doit passer ce contrat dans ses tests.

## 4. Paquet `config`

### 4.1 `config/_yaml.py`

Extraction, depuis `config/agent.py`, de la lecture YAML et de la localisation des problèmes par ligne : `read_yaml(path)`, `render_problems(...)`, `line_of(...)`, `dotted(...)`. Le chargeur de manifestes et le chargeur d'`agent.yaml` les partagent.

### 4.2 `config/strategy_catalog.py`

```python
@dataclass(frozen=True)
class LoadedStrategy:
    manifest: StrategyManifest
    strategy: Strategy[Any]


def load_strategy_catalog(
    directory: Path, registry: Mapping[str, type[Strategy[Any]]]
) -> dict[str, LoadedStrategy]: ...  # clé : manifest.ref
```

Le registre est **passé en paramètre** : `config` ne l'importe pas, c'est la racine de composition qui le fournit.

Contrôles, **tous les problèmes de tous les fichiers signalés en une fois**, chacun avec son fichier et sa ligne :
- schéma du manifeste ;
- nom de fichier différent de `<strategy_id>@<version>.yaml` ;
- `strategy_id` absent du registre ;
- `parameters` refusés par `parameters_model`, localisés sous `parameters.<champ>`.

Chargement tout ou rien. Conserver l'ancienne configuration quand la nouvelle est invalide relève du rechargement à chaud, en TASK-032.

### 4.3 Modifications de `config/agent.py`, issues de TASK-006

- **Retrait de `MarketConfig.timeframes`.** Les unités suivies se déduisent du manifeste de la stratégie du marché : une seule source de vérité.
- `MarketConfig.strategy` contient une référence `id@version`.
- `load_agent_config(path, *, known_symbols, strategies, mode)` : `strategies: Mapping[str, StrategyManifest]` remplace `known_strategies`, et `mode: TradingMode` est ajouté. Contrôles supplémentaires, localisés par ligne :
  - référence `id@version` absente du catalogue ;
  - symbole du marché absent de `allowed_symbols` du manifeste (**RM-003**) ;
  - `max_mode` du manifeste inférieur au mode de l'agent : **refus de démarrer** plutôt que de faire tourner ce marché dans un mode dégradé sans le signaler (**RM-016**).

## 5. Test d'architecture

- `strategies` devient un paquet pur, comme `indicators`. Dépendances autorisées : `core` pour `indicators`, `core` et `indicators` pour `strategies`.
- **Règle resserrée :** `datetime` n'est plus interdit à l'import, car les stratégies manipulent légitimement des dates. Restent interdits dans les paquets purs les modules d'horloge, de réseau, d'aléatoire et d'entrée-sortie (`time`, `socket`, `asyncio`, `random`, `os`, etc.), **et tout appel à une méthode `now`, `utcnow` ou `today`**, détecté par analyse syntaxique.

## 6. Tests et critères d'acceptation

| Critère | Vérification |
|---|---|
| Deux marchés tournent avec deux stratégies différentes sans interférer | deux stratégies de test évaluées en alternance sur deux symboles, résultats identiques à leurs évaluations isolées |
| Une stratégie de l'or est refusée sur une paire crypto | `load_agent_config` signale RM-003 avec la ligne du symbole |
| Un manifeste invalide est refusé avec sa ligne | tests de `load_strategy_catalog` |
| Historique insuffisant : la stratégie n'est pas appelée | stratégie espion qui compte ses appels |
| Aucune lecture du futur | `check_strategy_contract` sur une stratégie de test, plus un test qui ajoute des bougies futures |
| Une stratégie qui lève, ou qui renvoie un signal incohérent, ne fait pas tomber l'évaluation | `STRATEGY_EXCEPTION` et `INVALID_SIGNAL` attendus |
| Les invariants de `Candle` et de `SignalCandidate` sont garantis à la construction | tests unitaires de chaque règle |
| Les stratégies restent pures | test d'architecture, cas synthétiques permis et interdits |
| `max_mode` insuffisant empêche le démarrage | `load_agent_config` en mode `PAPER` avec une stratégie plafonnée à `SIGNAL` |

## 7. Hors périmètre

Quarantaine et persistance des signaux (TASK-034) · rechargement à chaud et maintien de l'ancienne configuration (TASK-032) · stratégie témoin (TASK-033) · gestion de position, sorties partielles, trailing stop (après TASK-004) · périodes d'interdiction et paramètres de risque propres à une stratégie (V2).
