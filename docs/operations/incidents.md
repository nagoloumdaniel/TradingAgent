# Réagir à un incident

Principe : **d'abord empêcher une décision automatique douteuse, ensuite diagnostiquer.**
En cas de doute, arrêter.

## Réflexe immédiat

| Symptôme | Action immédiate |
|---|---|
| Comportement inexpliqué, perte de contrôle | `uv run tradingagent halt --reason "incident"` ou `/emergency_stop confirmer` |
| Positions à fermer sans attendre | `/close_all confirmer` (clôture réelle au marché) |
| Le bot ne répond plus | Voie serveur ci-dessus, elle ne dépend pas de Telegram |

## 1. Qualifier

```powershell
uv run tradingagent status
pwsh -File scripts/check_health.ps1 -CheckTask -CheckDatabase
```

Relever précisément : heure UTC du début, commandes passées, message d'erreur exact,
alertes Telegram reçues. Ne rien redémarrer avant d'avoir noté cet état.

## 2. Panne du processus

| Contrôle | Commande |
|---|---|
| Tâche planifiée | `Get-ScheduledTaskInfo -TaskName TradingAgent` |
| Processus | `Get-Process python, terminal64 -ErrorAction SilentlyContinue` |
| Journaux | sortie de la tâche et journaux JSON de l'agent |

Une tâche planifiée `TradingAgent` redémarre l'agent seule après un arrêt brutal. Si elle
est en échec :

```powershell
pwsh -File scripts/register_service.ps1 -Restart
pwsh -File scripts/register_service.ps1 -WhatIf   # vérifier la commande enregistrée
```

## 3. Perte de connexion (courrier, terminal, base)

| Cause | Indices | Action |
|---|---|---|
| Terminal MT5 fermé ou déconnecté | `terminal64.exe` absent ; `/status` sans fraîcheur de données | Relancer la tâche `TradingAgentMT5` : `Start-ScheduledTask -TaskName TradingAgentMT5` |
| Base injoignable | `uv run tradingagent status` échoue | Vérifier `DATABASE_URL` et le réseau ; l'agent refuse de trader avec un état illisible |
| API du modèle indisponible | Messages d'erreur IA, aucun signal | L'IA ne peut que **rejeter** un signal (C-002) : sa panne n'autorise aucun ordre et n'en empêche pas un rejet par défaut |

L'agent ne doit **jamais** trader avec une donnée périmée : une série dégradée est
signalée et le marché peut être mis en quarantaine (visible dans `/status`).

## 4. Divergence de position ou de solde

1. Comparer `/positions` avec le terminal MT5.
2. Ne pas « corriger » à la main dans la base.
3. En cas de divergence : arrêt d'urgence, relever les identifiants de position et
   l'heure, puis consigner l'écart.
4. Après correction de la cause, reprendre avec `resume` et surveiller la
   réconciliation au cycle suivant.

Toute décision est persistée avec son motif, refus compris : la piste d'audit permet de
reconstituer l'enchaînement.

## 5. Disque saturé

L'alerte de saturation déclenche un message Telegram (seuil 90 %). Espace libre sous 15 % :
`check_health` passe en dégradé.

1. Identifier les gros fichiers (journaux, dumps de sauvegarde).
2. Purger les journaux anciens et vérifier la rotation.
3. Vérifier que la rotation des sauvegardes (`-Retention`) est bien active.

## 6. Sécurité

- Jeton Telegram compromis : révoquer le bot auprès de BotFather, régénérer le jeton,
  mettre à jour `.env`, redémarrer, vérifier `TELEGRAM_ALLOWED_USER_IDS`.
- Mot de passe MT5 compromis : le changer immédiatement (section 15.2 du cahier).
- Secret exposé dans un journal : considérer la clé comme compromise, la faire tourner, et
  vérifier que le masquage (`***`) est bien actif.
- Utilisateur Telegram inconnu : il est refusé silencieusement et journalisé ; vérifier la
  liste blanche et les tentatives répétées.

## 7. Remise en service

```powershell
uv run tradingagent status                          # aucun arrêt actif, ou lever explicitement
pwsh -File scripts/check_health.ps1 -CheckTask -CheckDatabase
pwsh -File scripts/register_service.ps1 -Restart
```

Puis surveiller le premier cycle complet (données fraîches, signal ou non, notification).
Consigner l'incident : heure, cause, action, résultat.

## Escalade

Escalader au responsable si : divergence de solde non expliquée, ordre inattendu, secret
suspecté compromis, ou arrêt d'urgence non levable.
