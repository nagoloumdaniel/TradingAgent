# Arrêt d'urgence

Objectif : **arrêter toute émission d'ordre** en quelques secondes, et savoir comment
reprendre. Deux voies indépendantes existent ; la voie serveur fonctionne même si l'agent
et le bot Telegram sont arrêtés.

## Voie 1 — Telegram (la plus rapide)

Dans une conversation **privée** avec le bot, avec un compte de la liste blanche :

```
/emergency_stop
```

Le bot répond ce que la commande ferait, sans rien exécuter :

```
ARRÊT D'URGENCE : plus aucun nouvel ordre, quelle que soit la source.
Les positions ouvertes ne sont pas clôturées automatiquement (RM-015).
Pour confirmer : /emergency_stop confirmer
```

```
/emergency_stop confirmer
```

**Aucune position n'est fermée automatiquement** (RM-015). Pour fermer les positions en
plus, utiliser `/close_all` (confirmation renforcée, clôture réelle au marché), puis
`/emergency_stop confirmer`.

## Voie 2 — Serveur (indépendante de Telegram)

```powershell
uv run tradingagent status                      # état courant
uv run tradingagent halt --reason "arret d'urgence: perte de connexion"
uv run tradingagent status                      # doit afficher HALTED
```

L'arrêt est écrit dans la base : il **survit au redémarrage** de l'agent, du bot et de la
machine. Il ne peut être levé que par une action explicite.

Si des positions doivent être fermées, l'ajouter explicitement :

```powershell
uv run tradingagent halt --reason "arret d'urgence: fermeture demandee" --close-positions
```

## Reprendre l'activité

Avant toute reprise, contrôler :

- l'état du compte (positions, solde) dans le terminal MT5 ou Telegram (`/positions`) ;
- la cause de l'arrêt (alertes Telegram, journaux) ;
- la fraîcheur des données (`/markets`) et l'état des connexions (`/status`).

Puis :

```
/resume
/resume confirmer
```

ou, côté serveur :

```powershell
uv run tradingagent resume --reason "controle du compte effectue"
```

Un marché isolé mis en quarantaine se réarme avec :

```powershell
uv run tradingagent rearm --strategy witness@1.0.0 --symbol XAUUSD
```

## Ce qu'un arrêt d'urgence fait — et ne fait pas

| Fait | Ne fait pas |
|---|---|
| Interdit tout nouvel ordre, quelle que soit la source | Ne ferme pas les positions (sauf `--close-positions` / `/close_all`) |
| Survit au redémarrage | Ne liquide pas le compte |
| Est journalisé avec motif, auteur et horodatage UTC | Ne peut pas être levé par simple redémarrage |
| Fonctionne agent et bot éteints (voie serveur) | Ne remplace pas une intervention sur le terminal MT5 |

## Référence

La procédure d'arrêt d'urgence a été testée le 2026-10-04, sur base SQLite jetable puis
sur la base de production :
[procédure d'essai](../procedures/2026-10-04-emergency-stop.md). L'état d'arrêt survit à
un redémarrage ; la reprise le lève ; un état illisible conduit au refus par défaut.
