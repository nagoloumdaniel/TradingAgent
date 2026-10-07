# AI Lab — researcher et analyst

Laboratoire d'analyse de l'agent (cahier v3 §5, §15, §16, §17, §39). Il lit des résultats,
classe des pertes et propose des hypothèses ; il ne décide jamais.

## Invariant non négociable (§39)

L'IA **analyse et propose**, le système de validation décide. En particulier :

- elle ne crée jamais de signal ni d'ordre ;
- elle ne modifie jamais un stop-loss, un objectif, une taille ou un mode d'exécution ;
- elle ne promeut jamais une stratégie : seule la validation (walk-forward, hors
  échantillon, Monte-Carlo, paper) puis l'opérateur le font ;
- aucun module de `ai/` n'écrit dans `signals`, `orders`, `positions` ou `trades`.

Ces garanties sont **structurelles** et testées :

- `lab_store.py` n'insère que dans `ai_analyses` et `ai_proposals` ; `record_proposal`
  écrit `PROPOSED` en dur, donc aucun chemin IA ne peut créer une ligne promue ;
- `tests/ai/test_lab_store.py` lit la source des trois modules et refuse toute mention
  d'une table de trading ;
- `tests/ai/test_analyst.py` et `tests/ai/test_researcher.py` envoient une réponse de
  modèle malveillante (création d'ordre, changement de stop, promotion, nouveau signal)
  et vérifient qu'aucune ligne de trading n'apparaît, que le verdict reste celui calculé
  et que les propositions restent `PROPOSED`.

## Modules

| Fichier | Rôle |
| --- | --- |
| `src/tradingagent/ai/lab_store.py` | Persistance seule : `record_analysis`, `record_proposal`, `decide_proposal`, `recent_analyses`, `open_proposals`. Testable sans réseau. |
| `src/tradingagent/ai/analyst.py` | `TradeAnalyst` : classification déterministe d'une perte (**normale**, **anomalie isolée**, **problème d'exécution**) puis, sur une série, **pattern récurrent** ou **dégradation**, avec les chiffres. |
| `src/tradingagent/ai/researcher.py` | `StrategyResearcher` : hypothèses falsifiables (`hypothesis` + `proposed_change` paramétrique + `falsification` + `evidence`) enregistrées en `proposed`. Ne touche à aucun manifeste. |

## Le modèle est optionnel

`TradeAnalyst` et `StrategyResearcher` acceptent un client LLM (`AiClient`) **facultatif**.
Sans clé API (`client=None`), l'analyse et les propositions déterministes sont produites et
persistées normalement (`model="deterministic"`, `response=None`). Le modèle ne peut que
commenter un verdict déjà calculé : sa réponse est lue contre un schéma strict, tout autre
champ est journalisé comme *tentative de dépassement* et ignoré. Une panne du modèle
n'enlève rien à l'analyse déterministe (`findings["model_error"]`).

## Décider une proposition

`LabStore.decide_proposal(id, status, actor, reason, at)` est l'API du système de
validation ; elle exige un acteur, un motif, un instant UTC, refuse `PROPOSED` comme
décision et refuse de décider deux fois la même proposition. Le researcher ne l'appelle
jamais.

## Vérifier

```bash
uv run pytest -q tests/ai
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
