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

| Commande | Réponse |
|---|---|
| `/help` | Liste des commandes disponibles |
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
