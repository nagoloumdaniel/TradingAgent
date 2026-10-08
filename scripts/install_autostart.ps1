#Requires -Version 5.1
<#
.SYNOPSIS
    Installe le démarrage automatique de TradingAgent sur cette machine (TASK-051, sans serveur).

.DESCRIPTION
    Une seule question commande tout : la session Windows démarre-t-elle toute seule ?

    * **Avec les droits administrateur**, l'installeur enregistre une tâche planifiée
      Windows déclenchée à l'ouverture de session de l'opérateur. C'est le mécanisme
      robuste : redémarrage automatique, démarrage différé si la machine était occupée,
      aucune instance concurrente.
    * **Sans droits administrateur** — le cas de ce poste aujourd'hui — il dépose un
      lanceur dans le dossier Démarrage de l'utilisateur
      (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`). Aucun droit spécial,
      désinstallable en supprimant un fichier, mais il ne se déclenche qu'à l'ouverture de
      session.

    Dans les deux cas, ce n'est pas l'agent qui est lancé directement mais
    `scripts/supervise_agent.ps1`, qui surveille le terminal MT5, l'agent et
    éventuellement le dashboard, et relance ce qui tombe.

    **Rien n'est lancé au démarrage de la machine avant qu'une session soit ouverte** :
    le terminal MetaTrader 5 exige une session interactive, donc le démarrage à froid sans
    ouverture de session ne peut pas faire tourner l'agent. Pour qu'une machine redémarre
    seule et travaille sans personne, il faut activer l'ouverture de session automatique de
    Windows — voir `docs/operations/demarrage-automatique.md` § « Démarrer sans personne ».
    Cet installeur ne stocke aucun mot de passe : en écrire un dans une tâche ou dans le
    registre serait un secret de plus hors de `.env`.

.PARAMETER ProjectRoot
    Racine du dépôt. Défaut : le parent du dossier `scripts`.

.PARAMETER TaskName
    Nom de la tâche planifiée. Défaut : `TradingAgent`.

.PARAMETER LogDirectory
    Répertoire des journaux du superviseur (transmis tel quel). Défaut : `<racine>\logs`.

.PARAMETER WithDashboard
    Supervise aussi le dashboard de lecture seule.

.PARAMETER UseStartupFolder
    Force le lanceur du dossier Démarrage, même avec les droits administrateur.

.PARAMETER Remove
    Retire la tâche planifiée et le lanceur du dossier Démarrage.

.PARAMETER Status
    N'installe rien : dit ce qui est installé et ce qui tourne.

.PARAMETER RunNow
    Lance le superviseur tout de suite après l'installation, sans attendre un redémarrage.

.PARAMETER WhatIf
    Affiche ce qui serait fait, sans rien modifier.

.EXAMPLE
    pwsh -File scripts/install_autostart.ps1 -WhatIf
    pwsh -File scripts/install_autostart.ps1
    pwsh -File scripts/install_autostart.ps1 -WithDashboard -RunNow
    pwsh -File scripts/install_autostart.ps1 -Status
    pwsh -File scripts/install_autostart.ps1 -Remove
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$ProjectRoot = $env:TRADINGAGENT_ROOT,

    [string]$TaskName = 'TradingAgent',

    [string]$LogDirectory,

    [switch]$WithDashboard,

    [switch]$UseStartupFolder,

    [switch]$Remove,

    [switch]$Status,

    [switch]$RunNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Output $Message
}

function Fail {
    param([string]$Message, [int]$Code = 1)
    [Console]::Error.WriteLine("ERREUR: $Message")
    exit $Code
}

function Test-Privilege {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Shell {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($null -ne $pwsh) { return $pwsh.Source }
    $windows = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($null -ne $windows) { return $windows.Source }
    return 'powershell.exe'
}

function Get-StartupFolder {
    $candidate = [System.Environment]::GetFolderPath('Startup')
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    }
    return $candidate
}

# --- Contexte ---------------------------------------------------------------------------------

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
if ([string]::IsNullOrWhiteSpace($LogDirectory)) { $LogDirectory = Join-Path $ProjectRoot 'logs' }

$supervisor = Join-Path $ProjectRoot 'scripts\supervise_agent.ps1'
$startupFolder = Get-StartupFolder
$startupLauncher = Join-Path $startupFolder "$TaskName.cmd"
$isAdmin = Test-Privilege
$shell = Get-Shell

function Get-SupervisorArguments {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', ('"{0}"' -f $supervisor))
    if ($WithDashboard) { $arguments += '-WithDashboard' }
    $arguments += @('-LogDirectory', ('"{0}"' -f $LogDirectory))
    return $arguments
}

function Get-TaskState {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) { return $null }
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

# --- État -------------------------------------------------------------------------------------

if ($Status) {
    Write-Output "Démarrage automatique TradingAgent — $ProjectRoot"
    $task = Get-TaskState
    if ($null -ne $task) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Output ("  tâche planifiée : {0} ({1}), dernier résultat {2}" -f $TaskName, $task.State, $info.LastTaskResult)
        foreach ($trigger in @($task.Triggers)) {
            Write-Output ("                    déclencheur : {0}" -f $trigger.CimClass.CimClassName)
        }
    }
    else {
        Write-Output '  tâche planifiée : absente'
    }
    if (Test-Path -LiteralPath $startupLauncher) {
        Write-Output ("  dossier Démarrage : {0}" -f $startupLauncher)
    }
    else {
        Write-Output '  dossier Démarrage : aucun lanceur'
    }
    Write-Output ("  droits administrateur : {0}" -f $isAdmin)
    Write-Output ''
    Write-Output 'État des services (superviseur) :'
    & $shell -NoProfile -ExecutionPolicy Bypass -File $supervisor -Status -LogDirectory $LogDirectory
    exit $LASTEXITCODE
}

# --- Retrait ----------------------------------------------------------------------------------

if ($Remove) {
    $removed = $false
    $task = Get-TaskState
    if ($null -ne $task) {
        if (-not $isAdmin) {
            Write-Output "AVERTISSEMENT : la tâche $TaskName existe mais le retrait exige les droits administrateur."
        }
        elseif ($PSCmdlet.ShouldProcess($TaskName, 'Unregister-ScheduledTask')) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Output "Tâche supprimée : $TaskName"
            $removed = $true
        }
    }
    else {
        Write-Output "Tâche absente : $TaskName"
    }
    if (Test-Path -LiteralPath $startupLauncher) {
        if ($PSCmdlet.ShouldProcess($startupLauncher, 'Remove-Item')) {
            Remove-Item -LiteralPath $startupLauncher -Force
            Write-Output "Lanceur supprimé : $startupLauncher"
            $removed = $true
        }
    }
    else {
        Write-Output 'Lanceur du dossier Démarrage : absent'
    }
    if ($removed) {
        Write-Output ''
        Write-Output "Le superviseur déjà lancé n'est pas arrêté par ce retrait : pwsh -File scripts/supervise_agent.ps1 -Stop"
    }
    exit 0
}

# --- Vérifications avant installation -----------------------------------------------------------

if (-not (Test-Path -LiteralPath $supervisor)) {
    Fail "superviseur introuvable : $supervisor" 2
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) {
    Fail "aucun .env dans $ProjectRoot : copiez .env.example et renseignez-le d'abord." 2
}
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Fail "aucun .venv : lancez scripts/install_windows.ps1 avant d'installer le démarrage automatique." 2
}

$useTask = ($isAdmin -and -not $UseStartupFolder)
if (-not $isAdmin -and -not $UseStartupFolder) {
    Write-Output "Droits administrateur absents : le lanceur du dossier Démarrage sera utilisé."
    Write-Output "Pour la tâche planifiée, relancez ce script depuis un terminal administrateur."
    Write-Output ''
}

# --- Installation -------------------------------------------------------------------------------

if ($useTask) {
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Fail "le module ScheduledTasks est indisponible : ce script exige Windows." 2
    }
    $taskArguments = ((Get-SupervisorArguments) -join ' ')
    $action = New-ScheduledTaskAction -Execute $shell -Argument $taskArguments -WorkingDirectory $ProjectRoot
    # Ouverture de session, pas démarrage : le terminal MT5 refuse la session 0, et un agent
    # sans terminal n'est qu'un processus qui échoue poliment toutes les vingt secondes.
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -Hidden
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

    if ($PSCmdlet.ShouldProcess($TaskName, 'Register-ScheduledTask (ouverture de session)')) {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal `
            -Description "TradingAgent : superviseur (terminal MT5, agent) au demarrage de la session." -Force | Out-Null
        Write-Step "Tâche planifiée enregistrée : $TaskName"
        Write-Step "  Déclencheur : ouverture de session de $env:USERNAME"
        Write-Step "  Action      : $shell $taskArguments"
    }
    if (Test-Path -LiteralPath $startupLauncher) {
        Write-Step "Lanceur du dossier Démarrage également présent : $startupLauncher"
        Write-Step "  (le verrou du superviseur empêche deux instances ; retirez-le si vous voulez une seule voie)"
    }
}
else {
    $launcherLines = @(
        '@echo off',
        'rem Lance TradingAgent a l''ouverture de session.',
        'rem Genere par scripts/install_autostart.ps1 : ne pas modifier a la main.',
        ('start "" /min "{0}" {1}' -f $shell, ((Get-SupervisorArguments) -join ' '))
    )
    if ($PSCmdlet.ShouldProcess($startupLauncher, 'écrire le lanceur de démarrage')) {
        if (-not (Test-Path -LiteralPath $startupFolder)) {
            New-Item -ItemType Directory -Path $startupFolder -Force | Out-Null
        }
        Set-Content -LiteralPath $startupLauncher -Value $launcherLines -Encoding ASCII
        Write-Step "Lanceur installé : $startupLauncher"
        Write-Step "  Il s'exécute à chaque ouverture de session de $env:USERNAME."
    }
    $task = Get-TaskState
    if ($null -ne $task) {
        Write-Step ''
        Write-Step "ATTENTION : la tâche planifiée $TaskName existe déjà et lancera aussi l'agent."
        Write-Step "  Le verrou du superviseur empêche un second agent, mais deux voies de démarrage"
        Write-Step "  brouillent le diagnostic. Retirez l'ancienne :"
        Write-Step "  pwsh -File scripts/register_service.ps1 -Remove   (en administrateur)"
    }
}

Write-Output ''
Write-Output 'Vérification : pwsh -File scripts/install_autostart.ps1 -Status'
Write-Output 'Arrêt propre : pwsh -File scripts/supervise_agent.ps1 -Stop'
Write-Output 'Sans personne devant la machine, voir docs/operations/demarrage-automatique.md'

if ($RunNow) {
    if ($useTask) {
        Write-Output ''
        Write-Step "Démarrage immédiat de la tâche $TaskName"
        Start-ScheduledTask -TaskName $TaskName
    }
    else {
        Write-Output ''
        Write-Step 'Démarrage immédiat du superviseur (fenêtre masquée)'
        Start-Process -FilePath $shell -ArgumentList ((Get-SupervisorArguments) -join ' ') `
            -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
    }
}

exit 0
