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

1. **Copier les fichiers dans le terminal.** Dans MetaEditor, `Fichier` >
   `Ouvrir le dossier de données`, puis copier le dossier `TradingAgent` de
   `mt5/Experts/` dans `<données terminal>\MQL5\Experts\`. Recompiler dans MetaEditor une
   fois les fichiers en place : c'est le `.ex5` du dossier de données que le terminal
   charge.
2. **Créer le graphique.** Ouvrir un graphique `BTCUSD` (au moins M1, n'importe quelle
   unité de temps : l'EA ne lit aucune bougie), puis un graphique `XAUUSD`.
3. **Attacher l'EA.** Glisser `BTCUSD_Guardian` sur le graphique `BTCUSD`, cocher
   « Autoriser la modification des paramètres du compte » n'est pas nécessaire : aucun
   réglage n'est fait par l'EA lui-même. L'EA **refuse de démarrer** sur un autre
   graphique que le sien (`INIT_FAILED`) — c'est volontaire.
4. **Activer Algo Trading.** Tant qu'il est désactivé, l'EA tourne, remonte
   `trade_allowed: false`, et n'envoie rien. En démonstration (phase 7), l'activer est
   sans risque : sans état publié par le backend, il n'y a aucun ordre à exécuter.
5. **Vérifier le battement de cœur.** Après quelques secondes :
   ```powershell
   Get-Content "...\MQL5\Files\TradingAgent\reports\BTCUSD_report.json" | ConvertFrom-Json |
     Select-Object symbol, updated_at, connected, trade_allowed, local_halt, applied_revision
   ```
   Attendu : un `updated_at` de moins de deux secondes, `connected: true`.

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

## 5. Dépannage

| Symptôme | Cause probable | Geste |
|---|---|---|
| `ea_health` ne renvoie rien | les EAs ne sont pas attachés, ou le mauvais `<instance>` | vérifier le dossier de données, puis les rapports |
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
