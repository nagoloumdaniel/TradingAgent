# Décision — plafond de `max_mode` : aucune escalade silencieuse

**Date :** 2026-10-08
**Portée :** `config/strategies/`, `tradingagent.config.strategy_catalog`
**Remplace :** l'invariant « un manifeste livré ne dépasse jamais `max_mode: SIGNAL` »
**Règles invoquées :** RM-016 (promotion explicite, horodatée, motivée), RM-017 (cohérence du
compte et du mode), §14 (échelle d'exposition), §49 (les neuf portes)

## Le fait qui a déclenché la décision

`config/strategies/witness@1.1.1.yaml` et `config/strategies/trend_breakout@1.0.1.yaml`
déclarent `max_mode: DEMO`. Ils ont été créés **délibérément**, à la demande de l'opérateur,
pour répéter le système sur un compte de **démonstration**. L'ancien test
`test_shipped_manifest_never_exceeds_signal_mode` échouait donc (`assert 3 <= 1`).

Ce test mesurait un **proxy**. Ce qu'il protège n'est pas « jamais au-dessus de SIGNAL »,
c'est **jamais au-dessus sans le dire**. Un plafond relevé en silence est le défaut ; un
plafond relevé par une décision écrite, datée et chiffrée est une décision.

## L'invariant, texte exact

> Un manifeste de `config/strategies/` ne peut pas dépasser `max_mode: SIGNAL` **en silence**.
> Au-dessus de `SIGNAL`, son en-tête doit porter un **bandeau de dérogation** :
>
> 1. la mention explicite de la dérogation opérateur — le marqueur `DEROGATION OPERATEUR` ;
> 2. sa **date**, au format ISO `AAAA-MM-JJ` ;
> 3. les **chiffres de validation qui manquent** : le profit factor net mesuré *et* celui
>    exigé, la p-value minimale *et* la ligne de Bonferroni à laquelle elle se compare, et le
>    nombre de survivants après correction du taux de fausses découvertes.
>
> Un manifeste au-dessus de `SIGNAL` **sans** ce bandeau est **refusé au chargement** :
> `load_strategy_catalog` lève `ConfigError` avec la ligne du `max_mode`, et le chargement est
> tout-ou-rien — l'agent ne démarre pas.
>
> `max_mode: LIVE` est une autre question et ne s'obtient **pas** par dérogation. `DEMO` est un
> plafond **borné** : un compte de démonstration, où aucun argent réel n'est en jeu, et
> `mode_rank(DEMO) < mode_rank(LIVE)`. Atteindre `LIVE` exige la **validation complète des neuf
> portes du §49**, chacune nommée dans le même bandeau, avec sa date. Une dérogation est
> précisément une autorisation *sans* validation : elle ne peut donc jamais être le document
> qui ouvre le compte réel (RM-016, RM-017).

## Pourquoi un bandeau, et pas un champ YAML

Le manifeste est validé par `StrategyManifest` avec `extra="forbid"` : un champ `derogation:`
serait une donnée de configuration de plus, qu'un script peut écrire. Le bandeau, lui, est un
commentaire : il ne peut être ajouté que par une main qui écrit une phrase, et il ne peut pas
être généré par accident. La règle lit le bloc de commentaires **au-dessus de la première
clé** (`header_banner`) : une dérogation enfouie sous les paramètres n'est pas un en-tête.

## Ce que ça donne concrètement

La dérogation du 2026-10-08, telle qu'elle figure en tête des deux manifestes concernés :

```
# DEROGATION OPERATEUR du 2026-10-08 : plafond releve de SIGNAL a DEMO, sans validation.
# ...
# Chiffres de validation qui manquent, a la date de la derogation (11 999 bougies M15) :
#   * profit factor net (1x couts) mesure 0,92 (BTC) et 1,04 (or), exige 1,20 ; a 2x couts
#     (porte stress) il retombe a 0,81 (BTC) et 0,90 (or) ;
#   * p-value minimale 0,3497, contre la ligne de Bonferroni 0,016667, soit 21 fois au-dessus ;
#   * 0 survivant sur 6 apres correction du taux de fausses decouvertes.
```

La campagne du 2026-10-08 sur 11 999 bougies M15 a refusé **6 portes sur 9**, la p-value
minimale est **0,3497** (21 fois la ligne de Bonferroni de 0,016667), et **0 candidat sur 6**
survit à la correction de Benjamini-Hochberg. Ces chiffres sont la raison pour laquelle le
plafond s'arrête à `DEMO` : ils sont écrits dans le manifeste pour qu'un lecteur ne puisse pas
confondre un essai de démonstration avec une stratégie validée.

## Tests

`tests/strategies/test_mode_ceiling.py` couvre les deux moitiés :

| Test | Ce qu'il prouve |
|---|---|
| `test_a_manifest_above_signal_without_a_derogation_is_refused` | un fichier **temporaire** à `max_mode: DEMO` sans bandeau est refusé, sur la ligne du `max_mode` |
| `test_the_refusal_names_every_piece_of_the_banner_that_is_missing` | un bandeau qui ne nomme pas ses chiffres est refusé, et le refus dit ce qui manque |
| `test_a_documented_derogation_is_accepted_above_the_ceiling` | une dérogation complète passe (DEMO, PAPER) |
| `test_a_manifest_at_or_below_the_ceiling_needs_no_derogation` | la règle n'exige rien de `SIGNAL` ou en dessous |
| `test_every_shipped_manifest_above_signal_carries_its_derogation` | chaque manifeste livré au-dessus de `SIGNAL` porte le bandeau **et ses chiffres** |
| `test_live_is_refused_when_only_a_derogation_raises_the_ceiling` | `LIVE` + la dérogation DEMO → refusé |
| `test_live_is_refused_when_the_validation_names_only_some_gates` | huit portes sur neuf → refusé, la manquante est nommée |
| `test_live_is_accepted_only_with_the_complete_validation_named` | les neuf portes nommées et datées → accepté |
| `test_demo_is_a_bounded_ceiling_below_live` | la propriété qui protège l'argent réel : `DEMO < LIVE` |
