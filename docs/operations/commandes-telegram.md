# Manuel des commandes Telegram

## Accès

- Seuls les identifiants listés dans `TELEGRAM_ALLOWED_USER_IDS` sont entendus.
- La conversation doit être **privée** : un message reçu dans un groupe est journalisé
  mais jamais exécuté.
- Un utilisateur inconnu est **refusé silencieusement** (aucune réponse) et son message est
  journalisé, avec un plafond horaire pour qu'un inconnu ne puisse pas remplir la base.
- Trop de tentatives en une minute : l'expéditeur est temporairement ignoré.

Le cahier des charges (13.2) distingue des rôles `VIEWER` et `OWNER` ; la version actuelle
n'applique qu'**une seule liste blanche**, qui vaut opérateur. Ne mettez donc dans cette
liste que des personnes habilitées à tout commander.

## Convention de confirmation

Toute commande sensible répond d'abord **exactement** ce qu'elle va faire, et attend le mot
`confirmer` en second argument :

```
Vous   : /pause
Le bot : Suspend les nouveaux ordres sur tous les marchés. Les positions ouvertes sont conservées.
         Pour confirmer : /pause confirmer

Vous   : /pause confirmer
Le bot : ... C'est fait. /resume pour reprendre.
```

Rien n'est écrit tant que `confirmer` n'est pas revenu. Chaque exécution est journalisée
avec son auteur.

## Commandes de lecture (sans confirmation)

`/help` ouvre la **palette en boutons** : un bouton par commande. Appuyer sur un bouton ouvre
la fiche de *cette* commande — ce qu'elle fait, un exemple à recopier, et le bouton qui
l'exécute. Rien à retenir, rien à taper.

| Commande | Réponse |
|---|---|
| `/help` | La palette, en boutons groupés par usage |
| `/aide CMD` | La fiche d'une commande (c'est ce qu'ouvre un bouton de la palette) |
| `/status` | Mode courant, état d'arrêt, quarantaines, fraîcheur des données par marché |
| `/markets` | Marchés suivis, actifs ou désactivés, dernière bougie en UTC |
| `/signals` | Derniers signaux générés (marché, unité de temps, sens, état) |
| `/positions` | Positions ouvertes (marché, sens, volume, prix, heure UTC) |
| `/performance` | Trades clôturés, taux de réussite, PnL total et par mode |
| `/report [daily\|weekly\|monthly]` | Rapport à la demande sur la période (quotidien par défaut) |

Exemples de réponses :

```
Mode : SIGNAL (signaux seulement, aucun ordre)
État : en marche, aucun arrêt actif
Marchés :
  frxXAUUSD : activé, dernière bougie 2026-10-07 04:15 UTC (clôturée il y a 6 min)
```

```
Aucun signal enregistré.
```

## Commandes sensibles (confirmation obligatoire)

| Commande | Effet exact | Positions ouvertes |
|---|---|---|
| `/pause confirmer` | Suspend les nouveaux ordres sur tous les marchés | **Conservées** |
| `/resume confirmer` | Reprend l'émission d'ordres après une suspension ou un arrêt d'urgence | Conservées |
| `/disable <SYMBOLE> confirmer` | Désactive un marché : plus aucun signal ni ordre dessus | Conservées |
| `/enable <SYMBOLE> confirmer` | Réactive un marché | Conservées |
| `/close_all confirmer` | **FERME réellement** toutes les positions au marché, puis suspend les ordres | **Fermées** |
| `/emergency_stop confirmer` | Arrêt d'urgence : plus aucun nouvel ordre, quelle que soit la source | **Non fermées** (RM-015) |
| `/restart confirmer` | Redémarre l'agent : arrêt propre à la fin du cycle, puis relance | Conservées |
| `/restart_all confirmer` | Redémarre **tout** : terminal MT5, agent, tableau de bord | Conservées |
| `/shutdown confirmer` | Arrête **tout** : agent, tableau de bord, terminal MT5, superviseur | Conservées, mais plus surveillées |

Exemples :

```
/disable XAUUSD
/disable XAUUSD confirmer
```

```
/close_all
FERME toutes les positions ouvertes au marché, puis suspend les nouveaux ordres.
Les positions seront réellement clôturées.
Pour confirmer : /close_all confirmer
```

> **`/close_all` n'est pas `/pause`.** La première clôture des positions avec de l'argent
> réel engagé ; la seconde se contente d'interdire les nouveaux ordres.

## Redémarrer l'agent

```
/restart
Le bot : /restart — redémarrer l'agent : arrêt propre, puis relance.
         Ce qui s'arrête : la boucle, la collecte et les signaux, une vingtaine de secondes.
         Ce qui continue : le terminal MT5, les positions ouvertes et leurs stops.
         L'agent est supervisé : il revient seul, dans une quinzaine de secondes.
         Pour confirmer : /restart confirmer

/restart confirmer
Le bot : Redémarrage demandé : l'agent s'arrête à la fin du cycle en cours.
```

**C'est le superviseur qui relance, pas la commande.** Telegram ne peut pas tuer le processus
qui le sert : la commande *enregistre* la demande, la boucle la lit au cycle suivant, prévient
l'opérateur et s'arrête proprement ; `scripts/supervise_agent.ps1` redémarre tout service
arrêté (voir [demarrage-automatique.md](demarrage-automatique.md)). L'agent quitte alors avec le
code `75`, que le journal du superviseur distingue d'un plantage.

Si l'agent **n'est pas** supervisé (lancé à la main dans une console), le bot le dit dans la
confirmation : un redémarrage le laissera arrêté, et il indique la commande pour le relancer.
Cette phrase est décidée par la variable `TRADINGAGENT_SUPERVISED`, posée par le superviseur —
jamais par `.env`, où l'opérateur pourrait la rendre fausse.

Un redémarrage **ne touche à aucune position** : les stops et les cibles vivent chez le
courtier et dans les EA, pas dans le processus de l'agent.

## Arrêter ou redémarrer toute la pile

```
/shutdown      # tout arrêter : agent, tableau de bord, terminal MT5, superviseur
/restart_all   # tout redémarrer : terminal MT5, agent, tableau de bord
```

L'agent **ne peut pas exécuter ces deux ordres lui-même** : il ne peut pas tuer le terminal,
et tuer le superviseur qui l'a lancé reviendrait à scier la branche. Il écrit donc un mot
dans le fichier que le superviseur surveille (`logs/controle.txt`, chemin donné par
`TRADINGAGENT_CONTROL_FILE`) ; le superviseur le lit à chaque battement, l'efface aussitôt, et
fait le travail : fermeture de MT5, arrêt ou relance de chaque service.

| | `/restart` | `/restart_all` | `/shutdown` |
|---|---|---|---|
| Agent | redémarré | redémarré | arrêté |
| Tableau de bord | inchangé | redémarré | arrêté |
| Terminal MT5 | inchangé | **redémarré** | **arrêté** |
| Superviseur | inchangé | inchangé | **arrêté** |
| Revient tout seul | oui | oui | **non** |

`/shutdown` est le seul geste sans retour automatique : après lui, il faut relancer
`install_autostart.ps1` à la main, ou redémarrer la machine. La confirmation le dit avant
d'agir.

**Sans superviseur, ces deux commandes refusent.** Elles répondent que personne ne lirait
l'ordre, au lieu de faire croire à un arrêt qui n'aura pas lieu.

## Changer de mode

```
/mode SIGNAL
/mode DEMO
```

Modes acceptés : `OBSERVATION`, `SIGNAL`, `PAPER`, `DEMO`. La commande **enregistre** la
demande ; l'agent l'applique à sa prochaine lecture de l'état.

```
/mode LIVE
Le mode LIVE ne peut pas être activé depuis Telegram seul (RM-000) : il exige en plus la
variable d'environnement serveur LIVE_TRADING_ENABLED=true, posée directement sur la machine.
```

## Rapports à la demande

```
/report            # période quotidienne
/report weekly
/report monthly
```

Sans argument, le rapport quotidien est renvoyé. Une période inconnue répond
`Usage : /report daily|weekly|monthly`. Le rapport est composé à partir de la base : il ne
dépend ni du modèle de langage ni de l'agent en cours d'exécution.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Aucune réponse du bot | Bot arrêté, jeton invalide, ou identifiant hors liste blanche (refus silencieux) |
| « Commande inconnue : /xxx » | Faute de frappe ; `/help` donne la liste |
| La commande répond la confirmation sans agir | Il manque le mot `confirmer` en second argument |
| `/mode` répond « enregistré » sans effet immédiat | Comportement normal : l'agent applique à sa prochaine lecture |
| `/restart confirmer` répond « demandé » mais l'agent ne revient pas | Il n'est pas supervisé : voir [demarrage-automatique.md](demarrage-automatique.md), ou relancer `install_autostart.ps1 -RunNow` |
