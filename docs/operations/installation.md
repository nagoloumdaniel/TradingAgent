# Installation (TASK-050)

Objectif : une machine Windows vierge reçoit l'agent et le terminal MetaTrader 5 en
suivant uniquement ce document.

> **Pourquoi Windows ?** L'API Deriv est inaccessible depuis la France (contradiction
> C-010, résolue en version 1.2) : l'agent passe par le terminal MetaTrader 5, qui exige
> une session Windows. L'agent n'est donc **pas** conteneurisé.

## 1. Prérequis

| Élément | Vérification | Installation |
|---|---|---|
| Windows 10/11 ou Windows Server | `[System.Environment]::OSVersion.VersionString` | — |
| uv | `uv --version` | `winget install --id astral-sh.uv -e` |
| Git | `git --version` | `winget install --id Git.Git -e` |
| Terminal MetaTrader 5 | `Test-Path 'C:\Program Files\MetaTrader 5\terminal64.exe'` | Espace client Deriv |
| Clés SSH (accès au serveur) | `ssh -V` | Paire de clés dédiée, jamais de mot de passe |

PowerShell 7 (`pwsh`) est recommandé, mais les scripts fonctionnent aussi avec Windows
PowerShell 5.1 (`powershell`), installé d'origine.

## 2. Récupérer le dépôt

```powershell
git clone <url-du-depot> D:\TradingAgent
Set-Location D:\TradingAgent
```

## 3. Simuler, puis installer

```powershell
# Simulation : affiche chaque action sans rien modifier.
pwsh -File scripts/install_windows.ps1 -WhatIf

# Installation réelle.
pwsh -File scripts/install_windows.ps1
```

Le script :

1. vérifie que `uv.lock` est présent — sans lui l'installation ne serait pas reproductible ;
2. exécute `uv sync --frozen` (dépendances exactes du verrou, aucune mise à jour implicite) ;
3. crée `.env` depuis `.env.example` s'il manque — **sans aucune valeur** ;
4. contrôle la présence des variables attendues **par leur nom uniquement** et affiche
   celles qui restent à renseigner ;
5. localise `terminal64.exe` (variable `MT5_TERMINAL_PATH` ou emplacements standards).

Options utiles : `-ProjectRoot <chemin>` si le dépôt n'est pas à l'emplacement courant,
`-SkipTerminalCheck` pour ignorer la recherche du terminal.

## 4. Renseigner les secrets

```powershell
Copy-Item .env.example .env   # si le script ne l'a pas déjà fait
notepad .env
```

Valeurs attendues et signification : voir [configuration.md](configuration.md).
**Ne jamais** écrire un secret dans un script, un test, un ticket ou le dépôt.

## 5. Installer et connecter le terminal MetaTrader 5

L'installation du terminal se fait depuis l'espace client Deriv. La connexion est
**manuelle et jamais scriptée** : un mot de passe sur une ligne de commande serait
visible dans la liste des processus.

1. Lancer le terminal avec le compte destiné à l'agent (démonstration jusqu'à la phase 8).
2. `Fichier` > `Ouvrir un compte` > se connecter au serveur indiqué par `MT5_SERVER`.
3. Cocher **« Sauvegarder les informations du compte »** : le terminal se reconnecte seul
   après un redémarrage, y compris après un arrêt brutal.
4. `Outils` > `Options` > `Serveur` : cocher `Activer les cotations`, décocher la mise à
   jour automatique du terminal pendant les heures de marché.
5. Laisser `Algo Trading` **désactivé** tant que l'agent n'exécute pas d'ordres (phases 8/9).
6. Renseigner `MT5_TERMINAL_PATH` dans `.env` si plusieurs terminaux sont installés.

## 6. Vérifier l'accès à la base

```powershell
uv run tradingagent status
```

Attendu : `TRADING: no halt active`. En cas d'erreur de configuration, le message ne
contient jamais la valeur fautive, seulement le nom de la variable.

## 7. Enregistrer le service

Voir [exploitation.md](exploitation.md) : `pwsh -File scripts/register_service.ps1`.

## 8. Contrôler l'installation

```powershell
pwsh -File scripts/check_health.ps1 -CheckTask -CheckDatabase
```

Attendu : `Santé TradingAgent : OK`. Sinon, lire [incidents.md](incidents.md).

## 9. Poste de développement

```powershell
pwsh -File scripts/install_deps.ps1     # uv sync --frozen + hooks pre-commit
uv run pytest -q                        # suite de tests
```

## Critères d'acceptation de TASK-050

| Critère | État |
|---|---|
| Une machine vierge reçoit l'agent en suivant script + procédure | **À dérouler par l'opérateur** ; le script a été exécuté en simulation |
| Aucun fichier de secret embarqué dans le dépôt ni dans le script | Satisfait : `.env` est ignoré par git, seuls des noms de variables apparaissent dans les scripts |
| Empreinte mémoire au repos compatible avec le serveur cible | **À mesurer** sur le serveur : `pwsh -File scripts/check_health.ps1` et observabilité `ResourceMonitor` |
