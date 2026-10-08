# Recommandations consolidées — état au 2026-10-08

> **Pourquoi ce document.** Les recommandations ont été données au fil des mesures, dans des
> réponses séparées. Certaines n'ont pas été prises en compte, et il est difficile de savoir
> lesquelles. Ce document les rassemble **toutes**, avec pour chacune : ce qui a été mesuré,
> ce qu'il faut décider, et qui agit. Aucune n'est une opinion : chacune cite le chiffre ou le
> fichier qui la fonde.

**Légende du statut :** ✅ fait · ⏳ en attente de décision · 🔒 action opérateur (impossible à
faire depuis l'agent) · ❌ conflit à trancher

---

## 0. Backtest des trois stratégies existantes — toutes perdantes

Mesuré le 2026-10-08 avec le modèle de coûts du dépôt (spread 0,5 bp du prix, slippage
0,2 bp, **commission forfaitaire 0,50 €**), hors coûts puis avec coûts. Script :
`scripts/backtest/measure_production.py`.

### `witness@1.1.1` sur XAUUSD M15 — 60 000 bougies, 2,5 ans

| | Brut | Avec coûts |
|---|---|---|
| Opérations | 977 | 977 |
| Taux de réussite | 32,34 % | 32,34 % |
| Espérance par opération | **−0,410 €** | **−1,195 €** |
| Résultat net | **−401,01 €** | **−1 167,75 €** |
| Profit factor | 0,9405 | **0,8370** |
| Drawdown maximal | 735,68 € | 1 352,40 € |

### `trend_breakout@1.0.1` sur BTCUSD M15 — 60 000 bougies, 21 mois

| | Brut | Avec coûts | Coûts ×2 |
|---|---|---|---|
| Opérations | 1 812 | 1 812 | 1 812 |
| Taux de réussite | 33,39 % | 33,39 % | 33,33 % |
| Espérance par opération | **−0,078 €** | **−0,793 €** | −1,502 € |
| Résultat net | **−142,20 €** | **−1 437,77 €** | −2 721,01 € |
| Profit factor | 0,9884 | **0,8890** | 0,8010 |
| Drawdown maximal | 518,29 € | 1 589,71 € | 2 794,03 € |

### `scalp_triple_filter` sur XAUUSD M1 — 11 999 bougies, 12,8 jours

| | Brut | Avec coûts |
|---|---|---|
| Opérations | 158 | 158 |
| Taux de réussite | 62,03 % | 62,03 % |
| Espérance par opération | +0,487 € | **−0,785 €** |
| Résultat net | **+76,91 €** | **−124,07 €** |
| Profit factor | 1,1267 | **0,8118** |

### Ce que ces trois tableaux disent

**Aucune des trois stratégies existantes n'est rentable, et les deux stratégies dites « de
production » perdent de l'argent même sans coûts.** Le profit factor brut est de 0,94 et 0,99
— autrement dit, sur 2 à 2,5 ans et plus de 900 opérations chacune, elles ne dégagent aucune
espérance avant même de payer le spread.

**La bonne nouvelle, et elle compte :** c'est la **démonstration que les 9 portes ont raison**.
Elles refusaient déjà ces stratégies (`0 retenu sur 34` à chaque campagne), et le plafond de
mode les maintient en `DEMO` par dérogation. Le dépôt ne s'est donc pas trompé : il les a
mesurées, refusées, et documentées. Ce n'est pas un échec du système, c'est le système qui
fonctionne — la seule chose qui manquait était de le **lire**.

**En mode réel, ces stratégies seraient arrêtées en quelques jours :** le frein de perte
quotidienne (2 % de 100 € = 2 €) serait atteint en une à deux opérations sur l'or comme sur le
BTC, et l'agent s'arrêterait. Le risque RM-005 à 2 % par opération, sur un capital de 100 €,
signifie que chaque perte coûte 2 € sur un compte qui en porte 100.

**Aucune promotion n'a été faite.** Les plafonds restent `DEMO` (dérogation datée) et `SIGNAL`.
`config/strategies/` n'a pas été touché.

### Et MAE/MFE dit où est le vrai problème — ce n'est pas le stop

Mesuré le 2026-10-09 sur les mêmes campagnes, `mae_r` et `mfe_r` par opération (TASK-069) :

| | `witness` sur XAUUSD (977 op.) | `trend_breakout` sur BTCUSD (1 812 op.) |
|---|---|---|
| MAE médiane, tous trades | 1,082 R | 1,071 R |
| MFE médiane, tous trades | 1,021 R | 1,013 R |
| MAE médiane des **gagnants** | 0,443 R | 0,402 R |
| MFE médiane des **gagnants** | 2,244 R | 2,229 R |
| **MFE médiane des perdants** | **0,538 R** | **0,538 R** |
| Gagnants ayant failli être stoppés (MAE ≥ 0,5 R) | **141 / 316 (45 %)** | **253 / 605 (42 %)** |

**Trois conclusions, toutes actionnables :**

1. **Le stop n'est probablement pas trop serré.** 45 % des gagnants sont passés à moins d'un
   demi-stop de la sortie perdante. Resserrer le stop supprimerait près de la moitié des
   trades gagnants : le laisser tel quel est ce que la mesure recommande.

2. **Le problème est l'objectif, et il est structurel.** Les gagnants vont en médiane jusqu'à
   **2,244 R** d'excursion favorable alors que l'objectif est à **2,0 R** : il coupe les
   gagnants *avant* leur extension médiane. Symétriquement, les perdants ont vu en médiane
   **+0,538 R** avant de se retourner. Ces règles encaissent donc au moment où il faudrait
   laisser courir, et laissent repartir un perdant depuis +0,5 R. **Un objectif plus lointain
   et un break-even vers +0,5 R sont les deux pistes que ces chiffres désignent** — et non un
   réglage du stop.

3. **MAE et MFE médianes sont presque symétriques** (1,08 contre 1,02) : à l'entrée, ces
   règles ne prédisent pas la direction mieux que le hasard. Leur résultat ne vient que de la
   géométrie 1:2, qui exige 33 % de réussite pour survivre — et elles en font 32 à 33 %.
   **Aucune marge.** C'est l'explication arithmétique du profit factor brut de 0,94 et 0,99, et
   elle dit où chercher : pas dans le réglage des niveaux, mais dans la **sélection des
   entrées**.

---

## 1. Blocages d'exécution

### R-01 · L'arrêt local de l'EA était un verrou à sens unique ✅

**Mesuré le 2026-10-08 sur 60 s :** l'âge de l'état oscillait entre **4 et 16 s** (limite
30 s), le backend publiait (révisions 2579 → 2582), et `local_halt` restait **vrai** avec
`orders_sent: 0`. L'arrêt armé pendant la coupure de 19:46 ne s'est jamais relevé.

**Cause :** `m_halt = false` n'existait qu'à l'initialisation, et `LoadHalt()` n'était appelé
que dans `OnInit`. Rien, dans la boucle, ne pouvait désarmer le verrou.

**Corrigé :** `CheckStateFreshness` lève l'arrêt dès que le battement repasse sous la limite,
**et seulement** si le motif conservé est celui du silence. Divergence (RM-014) et kill switch
restent des verrous d'opérateur. Les deux EA recompilés (0 erreur, 0 avertissement).
Documenté dans CAHIER RM-013/RM-015, `docs/ea/protocole-pont.md` §7, `docs/ea/README.md`.

### R-02 · Faire recharger les EA corrigés 🔒

Les fichiers d'arrêt sont supprimés et les binaires recompilés sont en place, **mais l'EA en
mémoire garde son arrêt** : `LoadHalt` ne tourne qu'au démarrage. Il faut retirer puis
remettre `XAUUSD_Guardian` et `BTCUSD_Guardian` sur leurs graphiques. Sans ce geste, aucun
ordre ne partira, quelle que soit la qualité des stratégies.

### R-03 · `mt5.order_send` muet perdait le motif ✅

Quatre ordres sont morts le 2026-10-08 avec le message « answer lost » et **aucun code**,
alors que `order_check`, deux fonctions plus haut, conservait déjà le sien. `order_send` lève
désormais `TerminalError` avec `last_error()`, et une réponse illisible est refusée au lieu de
s'échapper en `AttributeError`. Le protocole déclare `TradeResult`, plus `TradeResult | None`.

---

## 2. Stratégie or M1 (Supertrend + Stochastic + EMA + ATR)

### R-04 · Le taux de réussite publié ne se reproduit pas ✅

**Mesuré :** 158 opérations, **62,03 %** de réussite contre 87,11 % publié. Profit factor
**1,13 brut**, **0,81 net** pour un seuil de 1,20. **−124,07 €** après coûts du dépôt. La
géométrie du risque, elle, se reproduit (gain moyen = 0,690 × perte moyenne, contre 0,698
déduit de la publication) : c'est bien le **taux de réussite** qui manque.

### R-05 · Le walk-forward n'a pas pu tourner ⏳

`insufficient_data` sur **16 candidats sur 16**. La cause est structurelle : un pli fait
**250–350 bougies**, et la chauffe de l'EMA 50 sur M5 en réclame **250 en M1**. Le protocole
de plis est calibré pour du M15. **Décision attendue :** réduire la période du filtre EMA,
changer le plan de plis, ou renoncer au M1 pour la validation.

### R-06 · L'historique M1 or est trop court pour juger ⏳

**12,8 jours** (11 999 bougies), parce que le courtier ne sert pas de M1 au-delà du
2026-06-23 pour l'or. Les 158 opérations ne sont donc pas 158 observations indépendantes.
**Décision attendue :** valider en M15 (60 000 bougies, 2,5 ans) ou en H1 (14 894 bougies).

### R-07 · La résolution intrabar explique probablement l'écart ⏳

Le harnais tranche **contre** la stratégie quand une bougie touche l'objectif et le stop :
`harness.py:361` — *« a bar touching both the stop and a target stops out first »*. Une règle à
objectif serré (0,7 R) derrière un stop large (3,0 × ATR) est exactement celle dont le
résultat dépend le plus de ce choix. **Test décisif :** rejouer avec des données tick (or
disponible depuis 2019-01-02, BTC depuis 2025-01-01).

---

## 3. Stratégie BTC (VWAP + momentum + pullback)

### R-08 · Le volume manquait, il traverse maintenant la chaîne ✅

`mt5_terminal.rates()` **jetait `tick_volume`** depuis l'origine du projet. `Candle` porte
désormais un `volume` keyword-only ; `None` veut dire « non enregistré », `0.0` veut dire
« aucun échange ». L'agrégation somme un seau complet et rend `None` sur un seau partiel.
Les 8 jeux gelés se relisent avec des empreintes **identiques**.

### R-09 · La base ne stocke pas le volume ⏳

`storage/candles.py` n'a **pas de colonne volume** : l'agent collecte le volume puis le perd
en base. **Un VWAP de production ne verra donc aucun volume.** Deux voies : une colonne
`tick_volume` avec migration, ou un VWAP de production qui lit le flux et non la base.
**C'est le point qui bloque R-10.**

### R-10 · Le VWAP ancré 00:00 UTC reste à écrire ⏳

Décision prise : ancre = journée UTC, exposée en paramètre. Non implémenté.

### R-11 · Quatre écarts entre la spec BTC et le code ❌

1. **Le trailing sur structure est structurellement impossible.** `SignalCandidate` fige les
   niveaux à la décision et une stratégie est une fonction pure sans mémoire
   (`strategies/base.py:53`). Suivre les *higher lows* après l'entrée demande que la position
   se souvienne. `harness.py:74` ne fait qu'un trailing à **distance fixe**. Chantier
   d'architecture, pas paramètre.
2. **`funding` n'existe pas ici : c'est du swap**, et `CostModel` ne le connaît pas. **Aucun
   backtest du dépôt n'a jamais payé de swap.** Angle mort pour une stratégie qui garde des
   positions.
3. **Le filtre de coût va dans `risk`**, pas dans la stratégie (ta décision) : une stratégie
   n'a pas accès au courtier ni au spread courant, et l'invariant « seul `risk` engage du
   capital » doit rester vrai.
4. **Le M1 ne peut pas porter ton backtest** : 8,4 jours d'historique BTC. Le walk-forward
   doit se faire en **H1/M15**, où l'historique remonte à 2011.

---

## 4. Critères de certification IA

### R-12 · Ta grille est déjà celle du dépôt, à 8 critères sur 9 ✅

| Ton critère | Dépôt |
|---|---|
| Profit factor ≥ 1,5 | `min_profit_factor_net = 1,2` (moins strict) |
| Hors-échantillon rentable | `min_out_of_sample_retention = 0,5` |
| Après coûts rentable | portes `costs` **et** `stress` (2× coûts) |
| Monte-Carlo robuste | `min_monte_carlo_probability = 0,5` |
| Drawdown ≤ 10 % | `max_drawdown_eur = 200 €` |
| No martingale / no grid | structurellement impossible |
| Walk-forward | `walk_forward ≥ 50 % de plis` |
| Expectancy > 0 | couvert par le profit factor net |
| **Win rate ≥ 70 %** | **n'existe pas** |

### R-13 · Ne fais pas du win rate le critère principal ⏳

Ta propre stratégie le montre : 87 % de réussite avec un gain moyen à **0,70×** la perte
moyenne. Le win rate décrit la **forme**, pas la solidité. À ajouter comme porte **secondaire
indicative**, jamais comme condition qui pousse à sur-optimiser vers des TP minuscules.

### R-14 · Deux durcissements que je recommande ⏳

- **Monte-Carlo de 1 000 → 10 000 itérations** (ta §22) ;
- **plancher d'expectancy explicite**, aujourd'hui seulement implicite via le profit factor.

---

## 5. Moteur d'analyse IA (ta spécification du 2026-10-08)

### R-15 · Ce qui existe déjà ✅

`Performance` calcule **21 grandeurs** : win rate, profit factor, expectancy, net, gain/perte
moyens, meilleur/pire, drawdown max **et sa durée**, séries de gains/pertes, Sharpe,
Sortino, R:R réalisé, durée moyenne, slippage et spread moyens, échantillon insuffisant
signalé (seuil 30). Les portes et `perturb_parameters` couvrent déjà walk-forward,
Monte-Carlo, robustesse des paramètres et correction de sélection multiple.

### R-16 · Ce qui manque, par ordre d'importance ⏳

| Priorité | Manque | Débloque |
|---|---|---|
| **1** | Régime de marché (tendance/range/breakout, liquidité), session (Asie/Londres/NY/overlap) | tes §2, §15 |
| **2** | Instantané des conditions à l'entrée (ATR, session, régime, distance support/résistance, volume) | tes §5, §12 |
| **3** | MAE / MFE par trade | tes §6, §7 — le seul moyen objectif de régler SL et TP |
| **4** | Structure de prix (HH/HL/LH/LL, BOS, range high/low) | ta §4 |
| **5** | Calmar, Recovery Factor, drawdown moyen, R:R par UT et par heure | ta §1 |

### R-17 · Deux contraintes non négociables sur R-16 ⏳

- **Tout indicateur de contexte doit être causal.** Un régime calculé avec des bougies
  futures produit une analyse brillante et fausse. La tripwire existante doit être étendue.
- **L'IA reste en veto asymétrique (C-002).** Un filtre découvert par l'analyse des pertes
  (ta §12) doit passer par le laboratoire et les 9 portes, jamais s'appliquer directement en
  production.

### R-18 · Ta spec décrit deux niveaux d'analyse 🔒

| Niveau | Où il vit | État |
|---|---|---|
| Par stratégie, par run (§1, §13, §14) | `backtest` / `research` | **déjà en place** |
| Par signal, en direct (§5, §12) | `strategies` → `ai` → `risk` | **chantier neuf** |

Le premier est la bonne échelle pour optimiser. Le second exige d'enregistrer le contexte au
moment du signal. Décision à prendre : quelle priorité entre les deux.

### R-19 · News, corrélations, funding 🔒

Aucune source de données dans le dépôt pour les news (CPI, NFP, FOMC) ni pour DXY/US10Y/S&P.
Je ne peux pas les inventer, et brancher une source externe est une décision qui t'appartient.
Le swap est côté courtier mais absent du modèle de coûts (voir R-11.2).

---

## 6. Données et méthode

### R-20 · Le M1 de l'or ne peut pas porter un backtest long ✅ mesuré

Voir R-06. Le BTC, lui, a **H1 depuis 2011-03-23** (87 573 bougies) et M15 depuis 2023-11-26 :
ton plan walk-forward 2019→2026 **est réalisable sur BTC en H1**, pas en M1.

### R-21 · Une seconde configuration pour le dashboard ✅

`tradingagent-web` exigeait l'emplacement du pont une **seconde fois** alors que l'agent lit
déjà `EA_FILES_DIR`. Résultat observé : *« Aucun rapport EA »* pendant que les deux EA
portaient un arrêt local — **la page qui existe pour révéler un EA arrêté était la seule qui
ne pouvait pas le voir.** Corrigé, avec 8 tests qui manquaient.

### R-22 · Cadence et plafonds de risque ⏳

- `max_trades_per_day: 4` : une stratégie M1 génère ~34 opérations/jour de marché. **~90 % des
  signaux seraient refusés**, et ce sont les 4 premiers qui passent, pas les meilleurs.
- `cooldown_after_losses: 3` / `cooldown_hours: 4` : un gel de 4 h arrête une stratégie M1 en
  pleine séance.
- **Ton §17 dit 0,25–0,5 % de risque par trade ; RM-005 impose 2 à 5 % en réel.** Conflit
  documenté et non tranché : à 100 € de capital, 0,5 % fait 0,50 €, sous le plancher de
  toute position possible avec le lot minimal du courtier.

### R-23 · Faux positifs de la stratégie liquidity sweep ⏳

Ton étude citait 32,7 % de réussite après coûts contre 70–80 % annoncés. C'est le même
schéma que la stratégie M1 or : un chiffre publié qui ne survit pas aux coûts et à la
résolution du moteur. À traiter comme **non vérifié**, pas comme réfuté.

---

## Synthèse : les 6 gestes qui débloquent le plus

| # | Geste | Qui | Pourquoi maintenant |
|---|---|---|---|
| 1 | **R-02** Recharger les deux EA sur leurs graphiques | 🔒 toi | Sans ça, aucun ordre ne part, quoi qu'on mesure |
| 2 | **R-09** Trancher le stockage du volume | ⏳ toi | Bloque le VWAP, donc toute la stratégie BTC |
| 3 | **R-06** Valider en M15/H1 plutôt qu'en M1 | ⏳ toi | 12,8 jours ne peuvent pas juger une stratégie |
| 4 | **R-07** Test tick pour expliquer l'écart de 25 points | ⏳ toi | La seule question qui dise si le 87 % est réel |
| 5 | **R-16** Feature Engine (régime, structure, MAE/MFE) | ⏳ toi | Débloque ton moteur d'analyse IA |
| 6 | **R-14** Monte-Carlo à 10 000 + plancher d'expectancy | ⏳ toi | Petit, borné, durcit la certification |
