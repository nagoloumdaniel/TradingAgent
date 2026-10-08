# Compléter le projet à 100 % — ce qui reste, par qui, dans quel ordre

**Date :** 2026-10-08 · **Contexte :** l'agent tourne en DEMO sur le compte de démonstration Deriv,
sans serveur Windows, sur le poste de l'opérateur.

---

## 0. La réponse en une page

**Le code est fini.** Les deux cahiers sont couverts : 14 paquets, 10 pages de dashboard, deux EA
compilés, la chaîne de recherche, les portes de validation, l'arrêt d'urgence, les sauvegardes.
Ce qui reste n'est pas du développement : ce sont **quatre décisions**, **une durée** et **une
machine**. Aucune de ces quatre choses ne peut être faite par un agent.

| Reste | Nature | Durée | Bloque quoi |
|---|---|---|---|
| Signer `docs/legal/2026-10-07-verification-operateur.md` | Décision | 30 min de lecture | Toute exécution réelle (phase 9) |
| Attacher les deux EA à des graphiques MT5 | Geste MT5 | 10 min | Le kill switch local, EF-015 |
| Trancher PAPER contre DEMO | Décision | 5 min | Le compteur des 30 jours (TASK-071) |
| Établir `TEST_DATABASE_URL` | Clé | 10 min | 6 tests PostgreSQL qui se sautent |
| Courir 30 jours en continu | Temps | 30 jours | La recette 22.2, TASK-071 |
| Provisionner le serveur Windows | Infra | — | TASK-051, la production 24/7 |

**Ce qui a été ajouté aujourd'hui** pour que ces lignes puissent avancer :

1. le **démarrage automatique** — terminal, agent et dashboard relancés seuls après un
   redémarrage, sans serveur ([`docs/operations/demarrage-automatique.md`](../operations/demarrage-automatique.md)) ;
2. la **boucle d'amélioration débloquée** : `backtest_runs` était vide, donc la chaîne
   quotidienne s'arrêtait sur « aucune preuve » avant même de comparer quoi que ce soit ;
3. un **défaut de baseline corrigé** : `scripts/backtest/improve.py` comparait encore les
   versions `witness@1.1.0` / `trend_breakout@1.0.0` alors que l'agent exécute `witness@1.1.1`
   et `trend_breakout@1.0.1`.

---

## 1. État vérifié le 2026-10-08

| | Valeur | Comment c'est su |
|---|---|---|
| Mode | DEMO (`TRADING_MODE=DEMO`, `LIVE_TRADING_ENABLED=false`) | `.env` lu |
| Agent | une seule instance, terminal MT5 vivant | processus et lignes de commande |
| Positions / ordres / trades | 0 / 0 / 0 | base interrogée |
| Signaux | 2 (1 notifié, 1 refusé par le contrôle de zone) | table `signals` |
| Bougies | 10 998 lignes XAUUSD + BTCUSD | table `candles` |
| `backtest_runs` | **0 avant aujourd'hui** | table `backtest_runs` |
| `ai_analyses`, `ai_proposals` | 0 | la boucle IA n'a jamais rien eu à analyser |
| Campagne paper | **EN COURS, 0 stratégie suivie** | `scripts/paper_campaign.py` |
| EA | `.ex5` compilés, présents dans le terminal, **pas attachés à un graphique** | `MQL5\Experts\TradingAgent` |
| Tâche planifiée | aucune | `Get-ScheduledTask` |
| Démarrage automatique | **installé aujourd'hui** (dossier Démarrage) | `install_autostart.ps1 -Status` |

---

## 2. Les actions de l'opérateur, dans l'ordre

### 2.1 Signer la conformité — 30 minutes, bloque tout le réel

`docs/legal/2026-10-07-verification-operateur.md` liste quatre points, chacun avec sa source
officielle à consulter : conditions du fournisseur, autorisation du trading automatisé, règles du
pays de résidence, obligations fiscales. **Rien n'est inventé dans ce document** ; c'est pourquoi
il attend une signature. Tant qu'elle manque, la phase 9 ne démarre pas et le mode réel reste
inaccessible (RM-000 exige la double condition).

### 2.2 Attacher les deux EA — 10 minutes, supprime un angle mort

Les `XAUUSD_Guardian` et `BTCUSD_Guardian` sont compilés et présents dans
`%APPDATA%\MetaQuotes\Terminal\<instance>\MQL5\Experts\TradingAgent`. Il reste à les **glisser sur
un graphique M15** de chaque marché, à autoriser le trading algorithmique, et à vérifier que l'EA
se plaint dans le journal s'il ne trouve pas le pont. Sans eux, l'agent tourne (l'EA est un filet,
pas une dépendance), mais le **kill switch local** n'existe pas : si le backend se tait, personne
ne ferme. Procédure : [`docs/ea/README.md`](../ea/README.md).

### 2.3 Trancher PAPER contre DEMO — 5 minutes, démarre ou non le compteur des 30 jours

Le script de campagne ne compte **que** le mode PAPER (`reporting/campaign.py`, `CAMPAIGN_MODE`).
L'agent tourne en DEMO : la campagne affiche donc `0 stratégie`, et TASK-071 ne peut pas se
fermer. Les deux mesures ne s'additionnent jamais (R-14).

- **Rester en DEMO** : les remplissages sont réels, chez le courtier. C'est la répétition la plus
  forte de la mécanique — c'est ce qui tourne aujourd'hui, et c'est un choix défendable.
- **Passer en PAPER** (`TRADING_MODE=PAPER`) : le compteur des 30 jours démarre, TASK-071 devient
  fermable, mais les remplissages redeviennent simulés.

Recommandation : garder DEMO pour la mécanique, et ne passer en PAPER que le jour où une candidate
aura franchi les portes et devra être évaluée à l'échelle 30 jours.

### 2.4 Établir `TEST_DATABASE_URL` — 10 minutes, débloque 6 tests

Un second projet Supabase en `_test`. Sans lui, six tests PostgreSQL se sautent proprement : la
branche d'insertion PostgreSQL n'est alors jamais exercée en local (elle l'est en CI).

### 2.5 Laisser tourner 30 jours

C'est le seul critère qui ne s'achète pas. Le démarrage automatique installé aujourd'hui est ce
qui rend ces 30 jours possibles sans surveillance. À relire chaque semaine :
`uv run python scripts/paper_campaign.py`.

### 2.6 Réserver le serveur Windows — plus tard

TASK-051 reste ouverte : provisionner un VPS Windows est une action d'infrastructure. Tant qu'il
n'existe pas, le poste de l'opérateur fait le travail, et
[`demarrage-automatique.md`](../operations/demarrage-automatique.md) § 5 explique comment le faire
redémarrer sans personne (ouverture de session automatique).

---

## 3. Démarrer l'analyse pour améliorer la stratégie

### 3.1 Ce qui existait déjà, et pourquoi ça ne produisait rien

La boucle est câblée de bout en bout : l'agent appelle chaque jour `ai/daily.py`, qui classe les
pertes (`analyst`), détecte une dégradation, déclenche une escalade (`escalation`), cherche une
meilleure version (`improvement_cycle`), la mesure, la compare à la version en place, et **s'arrête
à la porte** : elle propose, elle ne promeut jamais.

Deux raisons pour lesquelles elle n'avait encore rien produit :

1. **`backtest_runs` était vide.** La chaîne exige une mesure enregistrée pour se comparer à
   quelque chose ; sans elle, elle s'arrête sur `skipped_no_evidence` — un arrêt honnête, mais un
   arrêt quand même. C'est `scripts/backtest/improve.py` qui remplit cette table, et il n'avait
   jamais été exécuté sur cette base.
2. **Aucun trade clôturé.** L'analyste classe des pertes : sans perte, il n'a rien à classer. Cela
   ne se règle pas par du code, seulement par du temps de marché.

### 3.2 Le défaut corrigé au passage

`improve.py` portait la liste des versions en place **en dur** (`witness@1.1.0`,
`trend_breakout@1.0.0`) sous un commentaire qui affirmait le contraire — « lu depuis les
manifestes de production, pour que la baseline ne dérive pas ». L'agent exécute `witness@1.1.1`
depuis la dérogation du 2026-10-08 : **chaque « amélioration » aurait été mesurée contre une
version que personne n'exécute**, et enregistrée sous ce nom dans `backtest_runs`.

Corrigé : la référence est lue dans `config/agent.yaml`, le fichier que l'agent charge lui-même.
Un marché que ce fichier ne déclare pas est ignoré en le disant, au lieu d'être deviné. Un test
refuse toute version réintroduite en dur (`tests/backtest/test_improve_incumbent.py`).

### 3.3 Ce que la première analyse a produit

```bash
uv run python scripts/backtest/improve.py     # 2026-10-08 05:05 UTC, 11 999 bougies M15 gelées
```

| Marché | Version en place | Objectif (net après coûts) | Variante retenue | Objectif obtenu | Drawdown | Comparaisons | Candidat écrit |
|---|---|---|---|---|---|---|---|
| BTCUSD | `trend_breakout@1.0.1` | **−42,35 €** | `channel_period +25 %` → 25 | **−33,52 €** | 113,92 € | 1 | `trend_breakout@1.0.2` |
| XAUUSD | `witness@1.1.1` | **−7,09 €** | `ema_slow +25 %` → 62 | **+21,51 €** | 42,66 € | 3 | `witness@1.1.2` |

Deux variantes de l'or ont été **refusées par le garde-fou de drawdown** (`ema_fast ±25 %` :
64,07 € et 112,25 €, au-dessus des 58,65 € autorisés) — le garde-fou fonctionne.

**Ce qu'il faut lire dans ce tableau, sans se raconter d'histoires :**

- les deux versions en place **perdent de l'argent** dans le backtest. L'« amélioration » de BTC
  est un moindre mal (−42 € → −33 €), et celle de l'or est un passage tout juste positif ;
- le « +403,6 % » affiché pour l'or **ne veut rien dire** : un gain relatif calculé contre une
  référence proche de zéro est un artefact arithmétique. Le chiffre qui compte est l'objectif
  absolu, +21,51 € sur la fenêtre mesurée ;
- le drawdown de la candidate BTC (113,92 €) dépasse le capital de référence réel (100 €). Cette
  candidate n'est pas tradable telle quelle, et elle le restera tant que les portes ne l'auront
  pas dit ;
- ces deux fichiers sont des **candidats**, pas des promotions. Ils vivent sous
  `docs/research/candidates/`, et `write_candidate` refuse d'écrire dans `config/strategies/`.
  La campagne du 2026-10-08 a refusé 6 portes sur 9 sur les versions en place : rien ne dit que
  leurs candidates feront mieux.

**Ce que ça a changé dans la base :** `backtest_runs` est passée de 0 à 2 lignes, avec le jeu de
données, son empreinte, la fenêtre, les coûts et les métriques. La chaîne quotidienne a donc
maintenant une référence à laquelle se comparer — et **un second défaut a été trouvé en
exécutant, pas en relisant** : la chaîne enregistre la candidate acceptée comme nouvelle
référence, alors que `_build_version` ne cherchait le manifeste que dans `config/strategies/`.
Conséquence : la **première** amélioration acceptée condamnait toutes les suivantes à une
`ConfigError`, un marché après l'autre. Corrigé dans `src/tradingagent/app.py`
(`_manifest_of` regarde les deux emplacements, et refuse par son nom une référence introuvable),
avec trois tests dans `tests/test_app.py`.

### 3.4 Ce que ça change pour la suite

La chaîne quotidienne a maintenant de quoi comparer, et elle se déclenchera **d'elle-même** dès
que l'analyste verra un motif d'échec se répéter (par défaut : trois occurrences). Elle produit
alors, au plus, un **manifeste candidat** sous `docs/research/candidates/` — jamais une promotion.
La promotion reste les neuf portes de `registry/gates.py`, puis l'opérateur.

Trois façons d'alimenter cette boucle, par ordre de rendement :

1. **laisser courir** : chaque trade clôturé, chaque perte classée, chaque analyse s'accumule ;
2. **élargir les données** : la campagne a mesuré que les stratégies actuelles ne passent pas les
   portes ; de l'historique plus long et un marché de plus changeraient la précision des mesures,
   pas leur signe ;
3. **chercher une autre famille de stratégies** : `scripts/backtest/discover.py` explore six
   familles et a retenu un candidat sur dix-sept sur l'or réel ; c'est le chemin honnête, et il
   est lent.

---

## 4. Le démarrage automatique, en trois commandes

```powershell
# 1. Voir ce qui serait installé.
pwsh -File scripts/install_autostart.ps1 -WhatIf

# 2. Installer (dashboard compris), et lancer tout de suite.
pwsh -File scripts/install_autostart.ps1 -WithDashboard -RunNow

# 3. Vérifier.
pwsh -File scripts/supervise_agent.ps1 -Status
```

Ce qui tourne alors sans intervention : terminal MT5, agent, dashboard. Ce qui est garanti :
un seul agent à la fois (verrou + détection de processus), relance avec délai qui double en cas
d'échecs rapprochés, journaux par service et par jour purgés à 14 jours, arrêt propre qui ne tue
que ses propres processus. Détail et pannes courantes :
[`demarrage-automatique.md`](../operations/demarrage-automatique.md).

---

## 5. Ce qu'il ne faut pas faire

- **Ne lancez pas un second agent** pour « voir ». Deux agents sur le même compte, c'est deux fois
  les ordres possibles ; le superviseur refuse d'en démarrer un second, mais une console ouverte à
  la main contourne cette garde.
- **Ne lancez pas `RUN_MT5_LIVE=1 uv run pytest -m mt5_live` pendant que l'agent tourne** : le test
  construit son courtier avec le magic de production, l'agent peut lire sa position comme une
  divergence, et une divergence arrête l'agent globalement — sans reprise automatique.
- **Ne cumulez pas `register_service.ps1` et `install_autostart.ps1`** : le verrou empêche le
  double agent, mais deux voies de démarrage rendent le diagnostic inutile.
- **Ne touchez pas aux seuils de promotion** (`docs/research/thresholds.json`) pour faire passer une
  candidate : leur empreinte est figée, toute édition ultérieure fait refuser la décision.

---

## 6. L'ordre recommandé pour aujourd'hui

| # | Action | Durée | Effet |
|---|---|---|---|
| 1 | `pwsh -File scripts/install_autostart.ps1 -Status` | 1 min | Confirmer que tout redémarre seul |
| 2 | Attacher les deux EA aux graphiques M15 | 10 min | Kill switch local |
| 3 | Signer le document de conformité | 30 min | Débloque la phase 9 |
| 4 | Trancher PAPER contre DEMO | 5 min | Démarre ou écarte le compteur des 30 jours |
| 5 | Relire et commiter l'arbre de travail | 20 min | Voir l'avertissement ci-dessous |
| 6 | Laisser tourner | 30 jours | Le seul critère qui ne se code pas |

### Avertissement sur le commit `de8f1f5`

Le commit `de8f1f5` (« fix(ea): survive the Windows lock the EA takes while reading our
state », 2026-10-08 06:56) a été écrit **pendant** la rédaction de ces scripts, et il a emporté
`scripts/supervise_agent.ps1` dans son état de brouillon. Vérifié par exécution :

```text
git show HEAD:scripts/supervise_agent.ps1   →  24 091 octets, sans BOM
Parser::ParseFile                            →  12 erreurs de syntaxe (Windows PowerShell 5.1)
```

Autrement dit : **tel qu'il est commité, le superviseur ne se lance pas.** La version corrigée et
vérifiée est dans l'arbre de travail (non commitée), et un test
(`tests/test_autostart_scripts.py::test_every_script_parses_under_the_installed_powershell`)
refuse désormais qu'un script du dépôt parte avec une faute de syntaxe ou d'encodage.

---

## 7. La chaîne qualité, sur l'arbre final

```text
uv run pytest -q              2217 passed, 9 skipped in 289.61s
uv run ruff check .           All checks passed!
uv run ruff format --check .  372 files already formatted
uv run mypy                   Success: no issues found in 315 source files
```

Les 9 tests sautés le sont par construction : 2 exigent `RUN_MT5_LIVE=1`, 1 est un verrou
PostgreSQL, 6 sont réservés à PostgreSQL. La suite comptait 2181 tests au début de la session
précédente ; elle en compte 2217, dont 28 ajoutés aujourd'hui (20 sur la chaîne de démarrage,
5 sur la baseline de l'amélioration, 3 sur la composition de la chaîne).
