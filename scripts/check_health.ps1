#Requires -Version 5.1
<#
.SYNOPSIS
    Contrôle de santé local de l'agent (TASK-054, F-024).

.DESCRIPTION
    Vérifie ce qui est vérifiable depuis la machine : dépendances installées, espace
    disque, présence du terminal MT5, état des tâches planifiées, et (optionnellement)
    l'accès à la base via `uv run tradingagent status`.

    Sortie : 0 = sain, 1 = dégradé, 2 = configuration invalide. Les secrets ne sont
    jamais affichés.

.PARAMETER Json
    Émet le rapport en JSON, pour un superviseur externe.

.EXAMPLE
    pwsh -File scripts/check_health.ps1
    pwsh -File scripts/check_health.ps1 -CheckTask -CheckDatabase -Json
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = $env:TRADINGAGENT_ROOT,

    [string]$DataDir,

    [int]$DiskFreeWarningPercent = 15,

    [switch]$CheckTask,

    [switch]$CheckDatabase,

    [string]$TaskName = 'TradingAgent',

    [switch]$Json,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
if ([string]::IsNullOrWhiteSpace($DataDir)) { $DataDir = $ProjectRoot }

$checks = New-Object System.Collections.ArrayList

function Add-Check {
    param([string]$Name, [string]$Status, [string]$Detail)
    [void]$checks.Add([pscustomobject]@{ name = $Name; status = $Status; detail = $Detail })
}

function Test-Dependencies {
    $lock = Join-Path $ProjectRoot 'uv.lock'
    $venv = Join-Path $ProjectRoot '.venv'
    if ((Test-Path -LiteralPath $lock) -and (Test-Path -LiteralPath $venv)) {
        Add-Check 'dependances' 'ok' 'uv.lock et .venv présents'
    }
    else {
        Add-Check 'dependances' 'degrade' 'uv.lock ou .venv absent : lancez scripts/install_windows.ps1'
    }
}

function Test-Disk {
    $disk = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($DataDir).TrimEnd('\').TrimEnd(':'))
    $free = [double]$disk.Free
    $used = [double]($disk.Used + $disk.Free)
    if ($used -le 0) {
        Add-Check 'disque' 'degrade' 'utilisation du disque illisible'
        return
    }
    $freePercent = 100.0 * $free / $used
    if ($freePercent -lt $DiskFreeWarningPercent) {
        Add-Check 'disque' 'degrade' ("espace libre {0:N1} % sous le seuil {1} %" -f $freePercent, $DiskFreeWarningPercent)
    }
    else {
        Add-Check 'disque' 'ok' ("espace libre {0:N1} %" -f $freePercent)
    }
}

function Test-Terminal {
    $terminal = Get-Process -Name 'terminal64' -ErrorAction SilentlyContinue
    if ($null -ne $terminal) {
        Add-Check 'terminal_mt5' 'ok' ("processus terminal64 actif (PID {0})" -f $terminal.Id)
    }
    else {
        Add-Check 'terminal_mt5' 'degrade' 'terminal64.exe non détecté'
    }
}

function Test-Task {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        Add-Check 'tache_planifiee' 'degrade' 'module ScheduledTasks indisponible'
        return
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Add-Check 'tache_planifiee' 'degrade' "tâche $TaskName absente"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task.State -eq 'Running') {
        Add-Check 'tache_planifiee' 'ok' ("{0} : {1}" -f $TaskName, $task.State)
    }
    else {
        Add-Check 'tache_planifiee' 'degrade' ("{0} : {1} (dernier résultat {2})" -f $TaskName, $task.State, $info.LastTaskResult)
    }
}

function Test-Database {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $uv) {
        Add-Check 'base' 'degrade' 'uv indisponible pour interroger la base'
        return
    }
    Push-Location $ProjectRoot
    try {
        $output = & uv run --frozen tradingagent status 2>&1
        $code = $LASTEXITCODE
    }
    finally { Pop-Location }
    if ($code -eq 0) {
        Add-Check 'base' 'ok' (($output | Select-Object -Last 1) -replace '\s+$', '')
    }
    else {
        Add-Check 'base' 'degrade' "tradingagent status a échoué (code $code)"
    }
}

if ($DryRun) {
    Write-Output "MODE SIMULATION : contrôles qui seraient exécutés"
    Write-Output "  dépendances : uv.lock, .venv"
    Write-Output "  disque      : $DataDir (seuil $DiskFreeWarningPercent % libre)"
    Write-Output "  terminal    : processus terminal64"
    if ($CheckTask) { Write-Output "  tâche       : $TaskName" }
    if ($CheckDatabase) { Write-Output "  base        : uv run --frozen tradingagent status" }
    exit 0
}

Test-Dependencies
Test-Disk
Test-Terminal
if ($CheckTask) { Test-Task }
if ($CheckDatabase) { Test-Database }

$degraded = @($checks | Where-Object { $_.status -ne 'ok' }).Count
$overall = 'OK'
if ($degraded -gt 0) { $overall = 'DEGRADE' }

if ($Json) {
    $report = [pscustomobject]@{
        status    = $overall
        timestamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        checks    = @($checks)
    }
    $report | ConvertTo-Json -Depth 4
}
else {
    Write-Output "Santé TradingAgent : $overall ($((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm')) UTC)"
    foreach ($check in $checks) {
        Write-Output ("  [{0}] {1} : {2}" -f $check.status, $check.name, $check.detail)
    }
}

if ($degraded -gt 0) { exit 1 }
exit 0
