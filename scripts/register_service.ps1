#Requires -Version 5.1
<#
.SYNOPSIS
    Enregistre l'agent et le terminal MT5 comme tâches planifiées Windows (TASK-050/051).

.DESCRIPTION
    Crée deux tâches planifiées :
    - `TradingAgent` : lance l'agent au démarrage du serveur, redémarre automatiquement
      après un arrêt brutal (RestartCount illimité, intervalle d'une minute) ;
    - `TradingAgentMT5` : lance le terminal MetaTrader 5 à l'ouverture de session, avec
      redémarrage automatique ; le terminal se reconnecte au compte grâce à ses
      informations de compte sauvegardées (voir install_windows.ps1).

    Aucun identifiant n'est placé sur une ligne de commande ni enregistré dans une
    tâche : le terminal se reconnecte depuis ses propres paramètres, l'agent lit ses
    secrets dans `.env`.

.PARAMETER WhatIf
    Affiche les tâches qui seraient créées ou supprimées, sans toucher au système.

.EXAMPLE
    pwsh -File scripts/register_service.ps1 -WhatIf
    pwsh -File scripts/register_service.ps1
    pwsh -File scripts/register_service.ps1 -Restart
    pwsh -File scripts/register_service.ps1 -Remove -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$ProjectRoot = $env:TRADINGAGENT_ROOT,

    [string]$TaskName = 'TradingAgent',

    [string]$TerminalTaskName = 'TradingAgentMT5',

    [string]$EntryPoint = 'uv run --frozen python -m tradingagent.app',

    [string]$TerminalPath = $env:MT5_TERMINAL_PATH,

    [string]$RunAsUser = "$env:USERDOMAIN\$env:USERNAME",

    [int]$RestartMinutes = 1,

    [switch]$Remove,

    [switch]$Restart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message, [int]$Code = 1)
    [Console]::Error.WriteLine("ERREUR: $Message")
    exit $Code
}

function Get-Shell {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($null -ne $pwsh) { return $pwsh.Source }
    $windows = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($null -ne $windows) { return $windows.Source }
    Fail "aucun interpréteur PowerShell trouvé pour la tâche planifiée." 3
    return 'powershell.exe'
}

function Test-Privilege {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    Fail "le module ScheduledTasks est indisponible : ce script exige Windows." 2
}
if (-not (Test-Privilege)) {
    if ($WhatIfPreference) {
        Write-Output "AVERTISSEMENT : sans droits administrateur, l'enregistrement réel échouera. -WhatIf affiche le plan."
    }
    else {
        Fail "droits administrateur requis pour enregistrer une tâche planifiée." 4
    }
}
if (-not $Remove -and -not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) {
    Write-Output "AVERTISSEMENT : .env est absent ; l'agent ne démarrera pas avant de l'avoir rempli."
}

# --- Suppression ------------------------------------------------------------

if ($Remove) {
    foreach ($name in @($TaskName, $TerminalTaskName)) {
        if ($PSCmdlet.ShouldProcess($name, 'Unregister-ScheduledTask')) {
            $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            if ($null -ne $existing) {
                Unregister-ScheduledTask -TaskName $name -Confirm:$false
                Write-Output "Tâche supprimée : $name"
            }
            else {
                Write-Output "Tâche absente : $name"
            }
        }
    }
    exit 0
}

# --- Redémarrage à chaud ----------------------------------------------------

if ($Restart) {
    foreach ($name in @($TaskName, $TerminalTaskName)) {
        if ($PSCmdlet.ShouldProcess($name, 'Stop-ScheduledTask puis Start-ScheduledTask')) {
            $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            if ($null -eq $existing) { Write-Output "Tâche absente : $name"; continue }
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Start-ScheduledTask -TaskName $name
            Write-Output "Tâche redémarrée : $name"
        }
    }
    exit 0
}

# --- Enregistrement ---------------------------------------------------------

$shell = Get-Shell
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes $RestartMinutes) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$agentArguments = "-NoProfile -ExecutionPolicy Bypass -Command `"Set-Location -LiteralPath '$ProjectRoot'; $EntryPoint`""
$agentAction = New-ScheduledTaskAction -Execute $shell -Argument $agentArguments -WorkingDirectory $ProjectRoot
$agentTrigger = New-ScheduledTaskTrigger -AtStartup
$agentPrincipal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Highest

if ($PSCmdlet.ShouldProcess($TaskName, 'Register-ScheduledTask (démarrage automatique + redémarrage)')) {
    Register-ScheduledTask -TaskName $TaskName -Action $agentAction -Trigger $agentTrigger `
        -Settings $settings -Principal $agentPrincipal `
        -Description "TradingAgent : agent de signaux Deriv, redémarrage automatique." -Force | Out-Null
    Write-Output "Tâche enregistrée : $TaskName"
    Write-Output "  Commande : $shell $agentArguments"
}

if ([string]::IsNullOrWhiteSpace($TerminalPath)) {
    $terminalCandidates = @()
    $programFiles = [System.Environment]::GetEnvironmentVariable('ProgramFiles')
    $programFilesX86 = [System.Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($programFiles) { $terminalCandidates += (Join-Path $programFiles 'MetaTrader 5\terminal64.exe') }
    if ($programFilesX86) { $terminalCandidates += (Join-Path $programFilesX86 'MetaTrader 5\terminal64.exe') }
    foreach ($candidate in $terminalCandidates) {
        if (Test-Path -LiteralPath $candidate) { $TerminalPath = $candidate; break }
    }
}
if ([string]::IsNullOrWhiteSpace($TerminalPath)) {
    Write-Output "Terminal MT5 introuvable : renseignez MT5_TERMINAL_PATH puis relancez pour créer $TerminalTaskName."
}
else {
    $terminalAction = New-ScheduledTaskAction -Execute $TerminalPath -WorkingDirectory (Split-Path -Parent $TerminalPath)
    # Le terminal exige une session interactive : déclenchement à l'ouverture de session.
    $terminalTrigger = New-ScheduledTaskTrigger -AtLogOn -User $RunAsUser
    $terminalSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes $RestartMinutes) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    $terminalPrincipal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType Interactive -RunLevel Highest
    if ($PSCmdlet.ShouldProcess($TerminalTaskName, 'Register-ScheduledTask (terminal MT5 au logon)')) {
        Register-ScheduledTask -TaskName $TerminalTaskName -Action $terminalAction -Trigger $terminalTrigger `
            -Settings $terminalSettings -Principal $terminalPrincipal `
            -Description "Terminal MetaTrader 5 : reconnexion automatique au compte." -Force | Out-Null
        Write-Output "Tâche enregistrée : $TerminalTaskName"
        Write-Output "  Exécutable : $TerminalPath"
    }
}

Write-Output "Contrôle : pwsh -File scripts/check_health.ps1 -CheckTask"
exit 0
