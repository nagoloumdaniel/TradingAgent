# Démarrage automatique sur le poste de l'opérateur (sans serveur)

> **Question à laquelle ce document répond.** « Je n'ai pas encore de serveur Windows : comment
> tout redémarre tout seul quand la machine redémarre ? »

Deux scripts, et rien d'autre :

| Script | Rôle |
|---|---|
| [`scripts/supervise_agent.ps1`](../../scripts/supervise_agent.ps1) | Chef d'orchestre : terminal MT5, agent, dashboard. Relance ce qui tombe, écrit les journaux, refuse les doublons |
| [`scripts/install_autostart.ps1`](../../scripts/install_autostart.ps1) | Installe le lancement automatique : tâche planifiée si les droits administrateur sont là, lanceur du dossier Démarrage sinon |

## 1. Ce qui démarre, et dans quel ordre

À l'ouverture de session, `install_autostart.ps1` fait lancer le **superviseur**, qui :

1. prend un **verrou nommé** — un second superviseur s'arrête immédiatement (`Local\TradingAgentSupervisor`) ;
2. démarre le **terminal MT5** s'il n'est pas déjà en marche, et l'attend (90 s par défaut) ;
3. démarre l'**agent** (`.venv\Scripts\python.exe -m tradingagent.app`) ;
4. démarre le **dashboard** si l'installation a été faite avec `-WithDashboard` ;
5. **relance** tout service qui s'arrête : 15 s d'attente, doublée à chaque échec rapproché,
   plafonnée à 5 minutes, sans limite de nombre ;
6. écrit `logs/superviseur.json` (PID, relances, dernier code de sortie) et un journal par
   service et par jour.

## 2. Installer

```powershell
# Simulation : montre ce qui serait installé, ne touche à rien.
pwsh -File scripts/install_autostart.ps1 -WhatIf

# Installation réelle, dashboard compris.
pwsh -File scripts/install_autostart.ps1 -WithDashboard

# La même chose, en lançant tout de suite le superviseur (sans attendre un redémarrage).
pwsh -File scripts/install_autostart.ps1 -WithDashboard -RunNow
```

**Avec les droits administrateur**, l'installeur enregistre une tâche planifiée `TradingAgent`
déclenchée à l'ouverture de session : redémarrage automatique, démarrage différé si la machine
était occupée, aucune instance concurrente.

**Sans les droits administrateur** — l'état de ce poste — il écrit
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TradingAgent.cmd`. Aucun droit spécial,
aucun mot de passe stocké, retrait par simple suppression du fichier.

> **Pourquoi pas « au démarrage de la machine » ?** Le terminal MetaTrader 5 exige une session
> interactive : il ne tourne pas en service de session 0. Un agent démarré sans session
> n'aurait pas de terminal, donc pas de prix, donc rien à faire. Le déclencheur est donc
> l'**ouverture de session**, et le § 5 explique comment faire démarrer la session sans personne.

## 3. Vérifier

```powershell
# Ce qui est installé, et ce qui tourne en ce moment.
pwsh -File scripts/install_autostart.ps1 -Status

# L'état des services seulement.
pwsh -File scripts/supervise_agent.ps1 -Status

# La santé générale de la machine (dépendances, disque, terminal, tâche, base).
pwsh -File scripts/check_health.ps1 -CheckTask -CheckDatabase
```

`-Status` sort en code `1` si un service manque : utilisable tel quel par un superviseur
externe. Un agent détecté **en double** est signalé explicitement — deux agents ouverts sur le
même compte sont un défaut, pas une option.

## 4. Arrêter, redémarrer

```powershell
# Arrêt propre : tue l'arbre de chaque service, puis le superviseur.
pwsh -File scripts/supervise_agent.ps1 -Stop

# Redémarrage : arrêt, puis relance (le lanceur du dossier Démarrage, ou la tâche).
pwsh -File scripts/install_autostart.ps1 -RunNow
```

`-Stop` ne tue **que** les processus enregistrés dans `logs/superviseur.json`, et seulement
s'ils appartiennent au dépôt : un `python` lancé ailleurs n'est jamais touché.

**Redémarrer à distance** : `/restart confirmer` sur Telegram. La commande enregistre la
demande, l'agent s'arrête proprement à la fin de son cycle (code de sortie `75`, que le journal
distingue d'un plantage) et le superviseur le relance dans la quinzaine de secondes. C'est
utile après une coupure réseau ou une mise en veille : au démarrage, l'agent redemande au
courtier les bougies manquantes et répare le trou dans sa série. Détail de la commande :
[commandes-telegram.md](commandes-telegram.md).

**Arrêter ou redémarrer toute la pile** : `/shutdown` et `/restart_all` — les mêmes commandes,
mais qui vont jusqu'au terminal MT5 et au superviseur. L'agent ne peut pas les exécuter : il
écrit un ordre (`arreter` ou `redemarrer`) dans `logs/controle.txt`, que ce script lit à chaque
battement et efface aussitôt. Un ordre laissé sur le disque ferait redémarrer l'agent en boucle,
c'est pourquoi il est consommé une seule fois. Le chemin est transmis à l'agent par
`TRADINGAGENT_CONTROL_FILE` ; sans superviseur, les deux commandes refusent au lieu de mentir.

| `/shutdown` fait | `/restart_all` fait |
|---|---|
| ferme MT5, arrête le tableau de bord et l'agent, puis quitte lui-même | ferme MT5, arrête tout, redémarre MT5, puis relance les services |

`logs/controle.txt` est un fichier de passage, jamais versionné : il ne vit que le temps d'un
battement.

> **Si l'agent a été lancé à la main** (console, IDE), le superviseur ne le double pas : il le
> détecte, l'annonce dans son journal, et attend qu'il disparaisse pour prendre la main. C'est
> volontaire — mais cela veut dire qu'un agent lancé en console continue de tourner sans être
> supervisé. Pour passer la main : arrêtez la console, le superviseur démarrera l'agent au
> cycle suivant (moins de 20 secondes).

## 5. Démarrer sans personne devant la machine

Une machine qui redémarre seule et travaille sans opérateur a besoin de **deux** choses :
l'ouverture de session automatique, et les scripts du § 2.

L'ouverture de session automatique se règle dans Windows, pas dans ce dépôt :

- `netplwiz` → décocher « Pour utiliser cet ordinateur, l'utilisateur doit entrer un nom
  d'utilisateur et un mot de passe » (le plus simple, aucune trace dans un fichier) ;
- ou l'outil **Autologon** de Sysinternals, qui chiffre le mot de passe dans les secrets LSA.

**Ce que ces scripts ne font pas, et pourquoi :** ils n'écrivent aucun mot de passe. Écrire le
mot de passe de session dans une tâche planifiée ou dans le registre créerait un secret hors de
`.env`, exactement ce que la règle « aucun secret dans le dépôt » interdit. Deux conséquences à
connaître avant de choisir :

- une session ouverte en permanence est une session **non verrouillée** : sur un poste de
  travail, cela vaut la peine d'y réfléchir ;
- si BitLocker est actif avec un code PIN au démarrage, la machine attendra quand même quelqu'un :
  l'ouverture de session automatique ne contourne pas le pré-démarrage.

## 6. Journaux

| Fichier | Contenu |
|---|---|
| `logs/superviseur.log` | Démarrages, arrêts, relances, plafond de relances atteint |
| `logs/superviseur.json` | État courant : PID, relances, dernier code de sortie, chemin des journaux |
| `logs/agent-AAAAMMJJ.log` | Sortie de l'agent, en JSON (le flux est redirigé, donc `app.py` écrit des journaux JSON) |
| `logs/dashboard-AAAAMMJJ.log` | Sortie du dashboard, si installé avec `-WithDashboard` |
| `logs/agent.cmd`, `logs/dashboard.cmd` | L'enveloppe réellement lancée : lisible, modifiable, utile au diagnostic |

Les journaux sont purgés au-delà de **14 jours** à chaque démarrage du superviseur
(`-LogRetentionDays`). `logs/` n'est pas versionné.

## 7. Pannes courantes

| Symptôme | Cause probable | Que faire |
|---|---|---|
| `superviseur.log` répète « relance dans … s » | L'agent échoue au démarrage | Lire la fin de `logs/agent-*.log` : c'est presque toujours `.env` incomplet ou base injoignable |
| `plafond de … relances atteint, abandon` | `-MaxRestarts` fixé, ou boucle d'échec | Corriger la cause, puis relancer le superviseur |
| `terminal MT5 introuvable` | `MT5_TERMINAL_PATH` absent et installation ailleurs | Renseigner `MT5_TERMINAL_PATH` dans `.env` |
| `agent déjà en marche hors supervision` | Un agent tourne en console | L'arrêter : le superviseur prendra la main seul |
| Rien ne démarre après un redémarrage | La tâche n'est pas déclenchée, ou la session n'est pas ouverte | `install_autostart.ps1 -Status`, puis § 5 |

## 8. Ce qui a été vérifié, et comment

Exécuté le 2026-10-08 sur ce poste (`powershell -NoProfile -File …`) :

| Chemin | Résultat |
|---|---|
| `-DryRun` | Plan affiché, rien lancé |
| `-Status` | Terminal, agent et dashboard détectés ; sortie `0` |
| `-Once` avec la commande de production (`python -m tradingagent.app --help`) | Code de sortie propagé (`0`), sortie capturée dans `logs/agent-<date>.log` |
| Code de sortie propagé | Processus sorti en `5` → superviseur sorti en `5`, `last_exit: 5` dans l'état |
| Relance après arrêt brutal | 3 relances puis abandon (`-MaxRestarts 2`), code de sortie `1` |
| Délai qui double | 2 s → 4 s → 8 s → 16 s, plafonné à `-MaxRestartDelaySeconds` |
| Mono-instance agent | Un agent lancé en console : aucun second agent démarré (PID comparés avant/après) |
| Mono-instance dashboard | Un dashboard déjà en écoute : aucune relance en boucle |
| `-Stop` | Arbre de processus tué, superviseur tué, agent de la console intact, état supprimé |
| Lanceur du dossier Démarrage | Exécuté, superviseur démarré, journal et état écrits |

Les tests automatisés correspondants sont dans
[`tests/test_autostart_scripts.py`](../../tests/test_autostart_scripts.py).

## 9. Quand un vrai serveur Windows arrivera

`scripts/register_service.ps1` reste la voie du serveur (TASK-051) : il enregistre l'agent et
le terminal comme tâches planifiées sur une machine dédiée. Sur un poste de travail, préférez
`install_autostart.ps1` : il supervise, il journalise, et il ne demande pas les droits
administrateur. **N'utilisez pas les deux** — le verrou du superviseur empêche le double agent,
mais deux voies de démarrage rendent le diagnostic inutilement confus.
