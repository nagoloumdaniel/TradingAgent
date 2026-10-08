# Campagne de trente jours — Q-15, TASK-071, phase 11

Cette procédure couvre la mesure dans le temps qui manque entre « la stratégie tourne » et
« la stratégie est comparable à son backtest ». Elle dure **au moins trente jours** : c'est
une exploitation, pas une vérification ponctuelle.

Le principe est simple : l'agent tourne dans un mode **sans argent réel** — `PAPER`
(remplissages simulés sur le flux réel) ou `DEMO` (remplissages réels sur le compte de
démonstration) — ses opérations sont écrites dans les mêmes tables que les opérations réelles
(colonne `mode`), et `scripts/paper_campaign.py` les lit — sans jamais rien écrire — pour dire
où en est la campagne.

> **Les deux lieux comptent, le réel jamais.** `CAMPAIGN_MODES` vaut `{PAPER, DEMO}` : ce sont
> les deux répétitions, et l'opérateur a demandé que les trente jours puissent courir dans
> l'une ou l'autre. Le mode `LIVE` reste exclu (R-14) — un passage en réel est un objet
> distinct, jugé sur ses propres chiffres. Le rapport nomme les lieux mesurés pour chaque
> stratégie (`mesurée sur : DEMO`) afin qu'un mélange soit visible plutôt que silencieux.

## 1. Les critères figés (Q-15)

Ils sont dans `src/tradingagent/reporting/campaign.py` (constante `PLAN`) et **ne se
réécrivent pas en cours de route** : les élargir après coup serait une promotion par dépit.

| Critère | Cible | Source |
|---|---|---|
| Durée | 30 jours calendaires entre le premier trade de la campagne et la date de lecture | Q-15 |
| Volume | 30 opérations clôturées par stratégie | Q-15 |
| Drawdown | drawdown maximal observé ≤ 50 EUR | 5 % du capital de départ paper (1 000 €, décision D-07), même plafond que RM-005 |

Chaque stratégie (couple **marché / stratégie**) reçoit un verdict :

- **EN COURS** — la durée n'est pas atteinte et rien n'est perdu ; on continue.
- **PRÊT** — les trois critères sont satisfaits ; on peut passer à la comparaison au
  backtest et à l'examen de promotion.
- **ÉCHEC** — le drawdown a franchi le plafond, **ou** les trente jours ont été parcourus
  sans atteindre trente opérations. La campagne ne peut plus conclure : elle s'arrête.

Le verdict global est **ÉCHEC** dès qu'une stratégie échoue : une campagne n'est jamais
« prête à moitié ».

## 2. Lancer l'agent dans un mode compté par la campagne

Dans `.env`, la base doit être celle de l'agent (`DATABASE_URL`). Puis, dans le terminal
du serveur, l'un des deux modes comptés :

```powershell
# PAPER : remplissages simulés, aucun ordre n'est émis
$env:TRADING_MODE = "PAPER"
uv run tradingagent-run

# DEMO : ordres réels sur le compte de démonstration, argent fictif
$env:TRADING_MODE = "DEMO"
uv run tradingagent-run
```

Points de contrôle avant de démarrer :

- `TRADING_MODE` vaut `PAPER` ou `DEMO` — **jamais** `LIVE` : la campagne ne compte que les
  deux lieux sans argent réel (R-14) ;
- `LIVE_TRADING_ENABLED` reste `false` : le mode réel exige de toute façon une confirmation
  opérateur (RM-000) ;
- le terminal MT5 est connecté : le `PaperBroker` y lit les caractéristiques des symboles
  même s'il n'envoie aucun ordre, et le `MT5Broker` y envoie les ordres en DEMO ;
- `uv run tradingagent status` répond, et aucune quarantaine n'est active.

Choisir entre les deux : `DEMO` engage la mécanique complète (ordres, stops, EA, courtier) et
produit l'évidence la plus forte ; `PAPER` n'engage rien et se mesure sur le flux réel. Le
rapport dit lequel a servi — il ne les confond jamais en silence.

Sur une machine de production, l'agent est déjà lancé par le superviseur
([demarrage-automatique.md](demarrage-automatique.md)) : c'est `TRADING_MODE` dans `.env` qui
décide du mode, pas la ligne de commande (voir [exploitation.md](exploitation.md)).

En mode `PAPER`, le compte simulé démarre à **1 000 €** (décision D-07) : ce capital n'est
pas le capital réel de 100 €, et aucun ordre réel n'est émis.

## 3. Suivre l'avancement chaque semaine

La commande de suivi est en lecture seule : elle ne crée, ne modifie et ne supprime aucune
ligne, et peut donc tourner pendant que l'agent travaille.

```powershell
uv run python scripts/paper_campaign.py                 # rapport texte, prêt pour Telegram
uv run python scripts/paper_campaign.py --json          # sortie JSON pour un superviseur
uv run python scripts/paper_campaign.py --market XAUUSD # un seul marché
uv run python scripts/paper_campaign.py --at 2026-11-06T12:00:00+00:00   # rejouer une date
```

Elle lit `DATABASE_URL` comme `tradingagent status` (variable d'environnement prioritaire,
sinon `.env`). Code de sortie : `0` = lecture réussie, `2` = base non configurée ; le
verdict est dans le texte ou le JSON, jamais dans le code de sortie.

Rythme conseillé pendant les trente jours :

| Moment | Commande | Ce qu'on regarde |
|---|---|---|
| J+0 | `uv run python scripts/paper_campaign.py` | la campagne a bien commencé (`premier trade paper`) |
| Chaque lundi | `uv run python scripts/paper_campaign.py` | jours écoulés, opérations cumulées, drawdown |
| Chaque lundi | `/report weekly` sur Telegram | le rapport habituel, semaine par semaine |
| J+30 | `uv run python scripts/paper_campaign.py` | verdict `PRÊT` ou `ÉCHEC` |

Un critère manquant est toujours nommé avec sa valeur observée et sa cible. Par exemple :

```
XAUUSD / witness@1.0.0 — EN COURS
  jours écoulés depuis le premier trade : 17 / 30 (13 restant(s))
  opérations cumulées : 2 / 30
  résultat net : +15.50 EUR
  drawdown maximal observé : 0.00 EUR (toléré 50.00 EUR)
  premier trade paper : 2026-09-20 00:00 UTC
  critères satisfaits : drawdown maximal
  critères manquants :
    - durée de la campagne (Q-15) : observé 17 jour(s), cible 30 jour(s) calendaires
    - opérations cumulées (Q-15) : observé 2, cible 30 par stratégie
```

## 4. Quand la campagne est `PRÊT`

`PRÊT` ne veut pas dire « promue ». Les deux étapes suivantes restent obligatoires :

1. **Comparer au backtest de référence.** `reporting/comparison.py` mesure l'écart par
   stratégie et par mode sur les mêmes indicateurs `analytics` (résultat net, taux de
   réussite, profit factor, drawdown, R réalisé), avec les seuils `TA_COMPARISON_*`. Un
   écart inexpliqué interdit la promotion (TASK-071, troisième critère d'acceptation).
2. **Appliquer les seuils de promotion de TASK-065** (`docs/research/thresholds.json`), et
   faire signer la décision par l'opérateur (RM-016). Aucun agent ne promeut seul.

## 5. En cas d'`ÉCHEC` — retour à TASK-064, jamais une promotion par dépit

1. **Arrêter la campagne** et le consigner :

   ```powershell
   uv run tradingagent halt --reason "campagne paper: critere Q-15 en echec"
   ```

2. **Ne pas élargir `PLAN`** pour transformer l'échec en réussite : durée, volume et
   drawdown ont été figés avant le premier trade, exactement comme les seuils de TASK-065.
3. **Retourner à TASK-064 — campagnes de recherche par marché.** L'échec est une
   information, pas un incident :
   - *échec sur le drawdown* : le profil de risque de la stratégie ou le dimensionnement
     sont à revoir sur l'historique, avant toute nouvelle campagne ;
   - *échec sur le nombre d'opérations après trente jours* : la stratégie produit trop peu
     de signaux pour être mesurée ; la fréquence est un paramètre de conception, pas un
     détail — une stratégie trop rare ne pourra jamais être comparée à son backtest ;
   - *échec répété* : rouvrir les hypothèses de recherche, pas les seuils.
4. **Aucune promotion, aucun passage en `DEMO` ou `LIVE`** ne se décide depuis ce rapport :
   c'est une mesure, pas une autorisation. La montée progressive du mode réel est décrite
   dans `control/live.py` et exige une activation explicite (RM-000).

## 6. Limites assumées

- Le rapport mesure le **mode `PAPER` uniquement**. Démonstration et réel ne sont jamais
  agrégés au mode papier (R-14) : chaque mode se compare séparément.
- Les jours comptés sont **calendaires** : un week-end où le marché est fermé reste un jour
  de campagne, parce que Q-15 parle de durée, pas de séances.
- La campagne ne remplace ni la comparaison au backtest ni la validation hors échantillon :
  elle les complète.
