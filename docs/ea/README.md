# Expert Advisors Guardian — compilation et installation

Deux Expert Advisors MQL5, issus du **même include**, exécutent les ordres que le backend
Python autorise, surveillent le compte, protègent les stops et remontent ce qui s'est
passé. Ils ne décident rien : voir [protocole-pont.md](protocole-pont.md) pour le contrat
de fichiers et le comportement exact en cas de perte de contact.

| Fichier | Rôle |
|---|---|
| `mt5/Experts/TradingAgent/TradingAgentGuardian.mqh` | le socle commun : lecture du fichier d'état, exécution, vérification, surveillance, protection, kill switch local, journalisation |
| `mt5/Experts/TradingAgent/BTCUSD_Guardian.mq5` | l'EA du BTC : symbole `BTCUSD`, magic `3031`, fichier `BTCUSD_state.json` |
| `mt5/Experts/TradingAgent/XAUUSD_Guardian.mq5` | l'EA de l'or : symbole `XAUUSD`, magic `3031`, fichier `XAUUSD_state.json` |
| `src/tradingagent/ea/bridge.py` | le côté Python : `publish_state(...)`, `read_reports(...)` |
| `src/tradingagent/ea/health.py` | `ea_health(reports_dir)`, ce que lit le tableau de bord |

Un `.mq5` par symbole ne contient que son identité ; toute la logique est dans l'include.
Ajouter un troisième symbole, c'est copier un `.mq5` et changer trois `#define`.

> **Un Guardian n'ouvre jamais une position de sa propre initiative.** Le seul chemin
> d'envoi d'ordre est la liste `orders[]` du fichier d'état publié par le backend.

## 1. Compiler

### Dans MetaEditor (recommandé pour un poste de travail)

1. Ouvrir MetaEditor (F4 depuis le terminal, ou `MetaEditor64.exe`).
2. `Fichier` > `Ouvrir` et choisir `mt5/Experts/TradingAgent/BTCUSD_Guardian.mq5`.
3. `Compiler` (F7). La fenêtre « Erreurs » doit afficher `0 errors, 0 warnings`.
4. Répéter avec `XAUUSD_Guardian.mq5`.
5. Le `.ex5` est produit à côté du `.mq5`.

### En ligne de commande

```powershell
$editor = "C:\Program Files\MetaTrader 5 Terminal\MetaEditor64.exe"   # à côté de terminal64.exe
$file   = (Resolve-Path mt5/Experts/TradingAgent/BTCUSD_Guardian.mq5).Path
$log    = (Resolve-Path mt5).Path + "\build\BTCUSD_Guardian.log"
& $editor "/compile:$file" "/log:$log"
Get-Content $log -Encoding Unicode | Select-String 'error|warning|Result'
```

`MetaEditor64.exe` accepte aussi `"/inc:<chemin>"` si les includes ne sont pas dans le
dossier du `.mq5` ; ici `#include "TradingAgentGuardian.mqh"` est relatif au fichier
compilé, donc aucun chemin d'inclusion n'est nécessaire.

**Piège mesuré :** MetaEditor renvoie un **code de sortie `1` même quand la compilation
réussit**. C'est la ligne `Result:` du journal qui fait foi, pas `$LASTEXITCODE`.

Compilation de référence, exécutée le 2026-10-07 :

```
D:\Projets\TradingAgent\mt5\Experts\TradingAgent\BTCUSD_Guardian.mq5 : information: compiling ...
D:\Projets\TradingAgent\mt5\Experts\TradingAgent\BTCUSD_Guardian.mq5 : information: including ...\TradingAgentGuardian.mqh
Result: 0 errors, 0 warnings, 8200 ms elapsed, cpu='X64 Regular'

D:\Projets\TradingAgent\mt5\Experts\TradingAgent\XAUUSD_Guardian.mq5 : information: compiling ...
D:\Projets\TradingAgent\mt5\Experts\TradingAgent\XAUUSD_Guardian.mq5 : information: including ...\TradingAgentGuardian.mqh
Result: 0 errors, 0 warnings, 16255 ms elapsed, cpu='X64 Regular'
```

Le temps écoulé dépend de la charge de la machine ; seule la ligne `Result:` compte.

Les journaux sont en UTF-16 et contiennent, entre autres, la liste complète des includes
résolus :

```powershell
Get-Content mt5/build/BTCUSD_Guardian.log -Encoding Unicode | Select-String 'error|warning|Result'
```

Journaux complets : `mt5/build/BTCUSD_Guardian.log` et `mt5/build/XAUUSD_Guardian.log`.

## 2. Installer

```powershell
pwsh -File scripts/install_ea.ps1            # installe et compile
pwsh -File scripts/install_ea.ps1 -WhatIf    # montre ce qui serait fait
```

Le script lit `EA_FILES_DIR` et `MT5_TERMINAL_PATH` dans `.env`, copie
`mt5/Experts/TradingAgent` dans `<données terminal>\MQL5\Experts\`, puis **recompile sur
place**. C'est le `.ex5` du dossier de données que le terminal charge : compiler dans le
dépôt ne suffit pas, et un `.ex5` recopié peut être en retard sur ses sources.

Il exige `0 errors` — et se fie à la ligne `Result:` du journal, pas au code de sortie,
puisque MetaEditor rend toujours `1`. Relancer le script est sans danger, et c'est ce qu'il
faut faire après toute modification d'un EA.

> Piège vérifié en écrivant ce script : `EA_FILES_DIR` vaut
> `<données>\MQL5\Files\TradingAgent`, donc la racine du dossier de données est **trois**
> niveaux au-dessus, pas deux. Une version antérieure installait dans
> `<données>\MQL5\MQL5\Experts\`.

### À faire ensuite, dans le terminal

Ces étapes demandent l'interface, elles ne se scriptent pas :

1. **Ouvrir les graphiques.** Un graphique `BTCUSD` et un graphique `XAUUSD` (n'importe
   quelle unité de temps : l'EA ne lit aucune bougie).
2. **Attacher chaque EA au sien.** Glisser `BTCUSD_Guardian` sur `BTCUSD`,
   `XAUUSD_Guardian` sur `XAUUSD`. Un EA **refuse de démarrer** sur le graphique d'un autre
   symbole (`INIT_FAILED`) : c'est volontaire.
3. **Activer Algo Trading** (le bouton de la barre d'outils). Tant qu'il est désactivé, l'EA
   tourne, remonte `trade_allowed: false`, et n'envoie rien. En démonstration, l'activer est
   sans risque : sans état publié par le backend, il n'y a aucun ordre à exécuter.
4. **Vérifier le battement de cœur.** Après quelques secondes :

   ```powershell
   Get-Content "$env:APPDATA\MetaQuotes\Terminal\<instance>\MQL5\Files\TradingAgent\reports\BTCUSD_report.json" |
     ConvertFrom-Json | Select-Object symbol, updated_at, connected, trade_allowed, local_halt
   ```

   Attendu : un `updated_at` de moins de deux secondes, `connected: true`. Le tableau de
   bord le montre aussi, page **Système**.

### Les mêmes étapes à la main

Si vous préférez ne pas utiliser le script : dans MetaEditor, `Fichier` >
`Ouvrir le dossier de données`, copier le dossier `TradingAgent` de `mt5/Experts/` dans
`<données terminal>\MQL5\Experts\`, puis compiler (F7) chacun des deux `.mq5` **depuis ce
dossier**. Le reste — graphiques, attachement, Algo Trading — est identique.

## 3. Le paramètre à connaître : le répertoire du pont

Les EAs n'écrivent que dans le bac à sable du terminal :

```
<données terminal>\MQL5\Files\TradingAgent\
```

Côté Python, ce chemin est un **paramètre** de `publish_state`, `read_reports` et
`ea_health`. Exemple :

```python
from pathlib import Path
from tradingagent.ea import AuthorisedOrder, ExpectedPosition, OrderAction, ea_health, publish_state
from tradingagent.core.market import Direction

BRIDGE = Path.home() / ("AppData/Roaming/MetaQuotes/Terminal/<instance>/MQL5/Files/TradingAgent")

publish_state(
    BRIDGE,
    "BTCUSD",
    magic=3031,
    positions=[
        ExpectedPosition(
            ticket=5001,
            direction=Direction.BUY,
            volume=0.01,
            stop_loss=62000.0,
            comment="ta-0123456789abcdef",
        )
    ],
    orders=[
        AuthorisedOrder(
            order_id="open-1",
            action=OrderAction.OPEN,
            direction=Direction.BUY,
            volume=0.01,
            stop_loss=62000.0,
            comment="ta-0123456789abcdef",
        )
    ],
    max_positions=1,
    max_total_volume=0.05,
)

print(ea_health(BRIDGE / "reports"))
# {'BTCUSD': <EaStatus.ONLINE: 'ONLINE'>, 'XAUUSD': <EaStatus.ONLINE: 'ONLINE'>}
```

Le chemin `<instance>` est une empreinte hexadécimale propre à l'installation. Pour la
trouver :

```powershell
Get-ChildItem "$env:APPDATA\MetaQuotes\Terminal" -Directory |
  Where-Object { Test-Path "$($_.FullName)\MQL5\Files" } |
  Select-Object -ExpandProperty FullName
```

> Relancer périodiquement `publish_state` est **obligatoire** : au-delà de 30 secondes
> sans nouvel état, l'EA déclenche son arrêt local. Republier à chaque cycle de la boucle
> d'exécution suffit, même sans changement.

**Câblage dans l'agent.** `runtime/loop.py::_sync_eas` publie l'état toutes les 20 secondes
(positions attendues du courtier, `kill_switch` = état d'arrêt global) et lit les rapports
pour signaler un EA `OFFLINE`. Il ne s'active que si `EA_FILES_DIR` est renseigné dans
`.env` : sans cette variable, l'agent tourne sans EA, ce qui est un état valide. Une erreur
du pont est journalisée et n'interrompt jamais un cycle : le terminal est un filet de
sécurité, pas une dépendance du moteur.

## 4. Exploiter

| Geste | Où |
|---|---|
| Voir l'état des EAs (tableau de bord) | `ea_health(<bridge>/reports)` → `ONLINE` / `OFFLINE` |
| Voir le détail d'un EA | `read_reports(<bridge>/reports)` → positions réelles, compteurs, arrêt local |
| Relire le journal complet | `read_events(<bridge>/reports, "BTCUSD")` ou `reports/BTCUSD_events.jsonl` |
| Arrêter tous les nouveaux ordres depuis Python | `publish_state(..., kill_switch=True)` — immédiat, réversible |
| Arrêter un EA pour de bon (kill switch local) | créer `control\<SYMBOLE>_halt.txt` avec le motif, ou laisser l'EA le faire lui-même sur divergence |
| **Repartir** après un arrêt local | comprendre la cause, corriger, **supprimer `control\<SYMBOLE>_halt.txt`**, redémarrer l'EA |

Un arrêt local **survit au redémarrage du terminal**. C'est voulu : il ne se lève que par
une action explicite de l'opérateur, comme l'arrêt d'urgence de
[../operations/arret-urgence.md](../operations/arret-urgence.md).

### Défaut corrigé le 2026-10-07 : le champ `data` rendait le rapport invalide

`LogEvent` reçoit un **fragment** d'objet (`"symbol":"BTCUSD","magic":3031`) ou `"{}"` par
défaut. Le fragment était écrit tel quel, sans accolades :

```
"data":"symbol":"BTCUSD","magic":3031,...
```

Ce n'est pas du JSON. `json.loads` échouait sur **tout le fichier**, `read_json` renvoyait
`None`, et `ea_health` annonçait `OFFLINE` en permanence alors que l'EA tournait et écrivait
un battement de cœur toutes les deux secondes. Le symptôme — « il tourne mais il est hors
ligne » — ne désigne pas la cause ; c'eût été un piège durable.

`LogEvent` normalise désormais : vide → `{}`, déjà entre accolades → inchangé, sinon le
fragment est encadré. Aucune modification n'a été nécessaire côté Python, qui attendait déjà
un objet (`EaEvent.data: Mapping`).

**Leçon opérationnelle :** un EA déjà attaché continue d'exécuter le `.ex5` chargé au moment
de l'attachement. Après `scripts/install_ea.ps1`, il faut **détacher et rattacher** l'EA, ou
redémarrer le terminal.

## 5. Dépannage

| Symptôme | Cause probable | Geste |
|---|---|---|
| `ea_health` ne renvoie rien | les EAs ne sont pas attachés, ou le mauvais `<instance>` | vérifier le dossier de données, puis les rapports |
| `ea_health` renvoie `OFFLINE` alors que le rapport vient d'être écrit | rapport **illisible** : `read_json` refuse le fichier entier, donc absent et invalide se ressemblent | ouvrir le `.json` et vérifier qu'il est du JSON valide. C'est le symptôme qu'a produit le défaut `data` corrigé le 2026-10-07 (voir ci-dessous) |
| `ea_health` renvoie `OFFLINE` | battement de cœur périmé (> 15 s) : EA retiré, terminal fermé, ou rapport corrompu | rouvrir le graphique, regarder l'onglet « Experts » |
| `local_halt: true`, `halt_reason: divergence d'etat…` | l'état réel ne correspond plus à l'état attendu (RM-014) | réconcilier à la main, puis supprimer `control\<SYMBOLE>_halt.txt` |
| `local_halt: true`, `halt_reason: backend silencieux…` | plus aucun `publish_state` depuis 30 s | relancer le backend |
| `ORDER_REFUSED` répété avec `stop-loss obligatoire absent` | un ordre `OPEN` a été publié sans `stop_loss` | corriger le backend : RM-004 n'a pas d'exception |
| `ORDER_REFUSED` avec `volume … hors du pas` | volume qui ne respecte pas `SYMBOL_VOLUME_STEP` | arrondir au pas de lot côté Python |
| `EXECUTION_FAILED` avec `retcode 10006` | instrument bloqué par la juridiction | normal en France pour les indices synthétiques : ils sont hors périmètre (C-008) |
| `PROTECTION` en `CRITICAL` | une position s'est retrouvée sans stop, l'EA l'a fermée | vérifier pourquoi le stop n'a pas été accepté (distance minimale du courtier) |
| Compilation : `member function not defined` | une méthode déclarée dans la classe n'est pas définie | MetaEditor indique la ligne : déclarer **et** définir, ou retirer la déclaration |

## 6. Vérifier les EAs sans terminal

Le côté Python du pont est testable sans MetaTrader 5 :

```powershell
uv run pytest -q tests/ea
```

Les tests couvrent l'écriture atomique, un battement de cœur périmé qui rend `OFFLINE`,
un fichier corrompu qui rend `OFFLINE` sans exception, et l'aller-retour
`publish_state` / `read_reports`.
