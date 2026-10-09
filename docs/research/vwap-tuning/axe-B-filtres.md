# Axe B — les filtres de la spec BTCUSD : ce qui est implémenté, ce qui a été mesuré

**Date :** 2026-10-09 · **Statut :** deux filtres implémentés et **désactivés par défaut**, un
troisième implémenté et **structurellement inutilisable**, aucun promu.

---

## 1. Pourquoi ce document

La spec BTCUSD de l'opérateur demande **six filtres**. La règle `vwap_pullback` en implémente
trois : retour au VWAP, momentum EMA, pente. Ce travail en ajoute **au plus deux**, chacun
désactivable, et **mesure** ce qu'ils changent réellement — parce qu'un filtre qui « a l'air
juste » et qui retire des trades gagnants coûte plus cher que le bruit qu'il prétend écarter.

Le filtre de **coût** de la spec n'est pas implémenté ici, et ne doit pas l'être : la décision
est prise, il appartient au moteur de risque, qui seul connaît le spread réel du broker.

Le troisième candidat — **la tendance d'unité de temps supérieure** — a été implémenté, testé,
mesuré, et **rejeté**. C'est le résultat le plus utile de cet axe, il est détaillé au § 5.

## 2. Ce qui est écrit

| Élément | Chemin |
|---|---|
| Filtres | `src/tradingagent/strategies/library/vwap_pullback.py` |
| Tests | `tests/strategies/test_vwap_pullback.py` — **26 tests**, dont 11 sur les filtres |
| Mesure | `scripts/backtest/tune_filter_vwap.py` |
| Résultats bruts | `docs/research/vwap-tuning/axe-B-mesures.json` |

Les trois paramètres ajoutés, et leur état par défaut — c'est-à-dire **la règle livrée** :

| Paramètre | Défaut | Effet du défaut |
|---|---|---|
| `allowed_sessions: tuple[Session, ...]` | `()` | aucune contrainte d'horaire |
| `volume_ratio_min: float \| None` | `None` | aucune contrainte de participation |
| `trend_filter: bool` / `trend_slope_atr: float` | `False` / `0,05` | aucune contrainte de tendance |

`allowed_sessions=()`, `trend_filter=False`, `volume_ratio_min=None` reproduisent **exactement**
la règle mesurée avant cet axe. Un filtre ne se rallume qu'après avoir montré, mesure en main,
qu'il ne retire pas plus qu'il n'apporte.

Aucun indicateur n'a été écrit : `session_at` (séance), `trend_of` (régime), et le volume déjà
présent dans les bougies. Chaque filtre est une **sortie anticipée** de `evaluate` — il ne peut
que refuser un signal, jamais en créer un, ni desserrer un stop (C-002).

## 3. Protocole de mesure

BTCUSD M15, `docs/research/datasets-volume`, **jeu complet : 59 999 bougies**, du
2025-01-21T16:30 au 2026-10-09T01:45 UTC, empreinte
`2f04c3b23c31e4074bf618612b09f6ec4c2b55bcfa90b48d024918ff93578459`.

```
ema_fast=20, ema_slow=50, vwap_period=20, atr_period=14, stop_atr_multiplier=1.5,
first_target_rr=1.5, final_target_rr=3.0, pullback_atr=0.4, entry_zone_atr=0.1,
min_slope_atr=0.005, slope_window=10
partial_exit_fractions=(0.5, 0.5), move_stop_to_breakeven_after_first_target=True
coûts : spread 0,5 bp, slippage 0,2 bp, commission 0,50 € par opération, mode SIGNAL,
max_concurrent_positions=1, risque 10 € par opération
```

**Pourquoi le jeu complet et pas une fenêtre.** C'est la correction de méthode du lead, et elle
est fondée : la même configuration donne **268 trades / PF 0,900** sur les 20 000 dernières
bougies et **301 trades / PF 0,990** sur une autre fenêtre de 20 000. Trois chiffres pour une
seule règle — le découpage décide du résultat. Un filtre choisi sur la fenêtre qui flatte serait
choisi sur du bruit. `--bars 20000` reste disponible pour le vérifier.

**Ce que ce document ne fait pas.** Aucune promotion, aucune modification de
`config/strategies/`, du registre, ni du harnais. Aucune correction de sélection multiple : ces
variantes sont des **hypothèses nommées**, pas une recherche, et leurs écarts sont commentés
comme tels.

## 4. Ce que la mesure dit sur le jeu complet

À COMPLÉTER

## 5. Le filtre de tendance est incompatible avec cette règle, et c'est démontrable

**La justification économique était plausible** : n'acheter un repli que si l'unité de temps
supérieure monte encore. Le problème n'est pas l'idée, c'est ce que `trend_of` mesure.

`trend_of` lit la pente de la moyenne lente sur **une barre**, normalisée par l'ATR. Or
l'événement que cette règle attend est précisément le moment où le prix **revient** sur le VWAP :
les moyennes convergent, et la pente d'une barre y est nulle ou négative. Exiger « ça pousse
encore » à cet instant précis est une contradiction, pas un filtre.

**Balayage de 105 géométries de repli** (6 à 12 barres de repli, 0,06 à 0,20 de pas, 0 à 2
barres de reprise), sur la même mesure que la règle :

| Géométrie | Pente sur 1 barre | `trend_of` (seuil 0,05) | Règle seule | Règle + filtre |
|---|---|---|---|---|
| Repli nominal (10 × 0,10 + 2 reprises) | **-0,0022** | NEUTRAL | signal | **refusé** |
| 105 géométries balayées | **≤ 0 partout** | NEUTRAL partout | — | **refusé partout** |

**Conséquence : avec son seuil de régime (0,05), ce filtre ne refuse pas les mauvais signaux, il
refuse tous les signaux.** Une seule géométrie a produit une pente positive (+0,00087) — et il a
fallu abaisser le seuil à `1e-9`, la plus petite valeur que le modèle accepte, pour la voir
passer. Le test la fige : il prouve le **mécanisme**, pas l'utilité.

**Décision : filtre laissé dans le code, désactivable, testé — et éteint par défaut.**

## 6. Les tests qui prouvent chaque filtre

TDD : les 11 tests ont été écrits **avant** l'implémentation, et les 11 échouaient d'abord
(`extra_forbidden` sur des paramètres inexistants). Chaque filtre a son test de refus et son test
de laisser-passer.

| Filtre | Refuse | Laisse passer |
|---|---|---|
| Séance | `test_the_session_filter_refuses_a_bar_outside_the_allowed_windows` — 18:00 UTC appartient à New York, pas à l'overlap | `test_the_session_filter_lets_the_declared_session_through` |
| Volume | `test_the_volume_filter_refuses_a_touch_on_a_dried_up_bar` — volume 10 contre une moyenne de 100 | `test_the_volume_filter_lets_a_touch_on_an_active_bar_through` |
| Tendance | `test_the_trend_filter_refuses_a_downtrend_for_a_buy` | `test_the_trend_filter_lets_a_real_uptrend_through` — seuil abaissé à `1e-9` |
| Tous | `test_every_filter_is_off_by_default` — un filtre qui change le comportement sans qu'on l'ait demandé n'est pas un filtre | — |

Deux tests méritent d'être signalés parce qu'ils ont **trouvé** quelque chose :

- `test_the_volume_filter_measures_the_bar_against_its_predecessors_only` : la barre courante est
  exclue de sa propre moyenne. Si elle y entrait, un pic de volume se raboterait lui-même
  d'autant plus qu'il est fort — l'inverse du but.
- `test_the_trend_filter_lets_a_real_uptrend_through` : la rédaction initiale supposait que le
  laisser-passer était le cas normal et le refus l'exception. La mesure a montré l'inverse.

## 7. Reproduire

```bash
uv run python scripts/backtest/tune_filter_vwap.py                 # jeu complet, ~25 min
uv run python scripts/backtest/tune_filter_vwap.py --bars 20000    # la fenêtre, pour comparer
uv run pytest tests/strategies/test_vwap_pullback.py -q
```

Le script confronte automatiquement la mesure sans filtre aux chiffres de référence du lead et
affiche « aligné » ou « DÉSALIGNÉ » avec l'écart : un chiffre qu'on cherche à atteindre finit par
être atteint par un réglage.

## 8. Ce qui reste ouvert

À COMPLÉTER
