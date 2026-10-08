#Requires -Version 5.1
<#
.SYNOPSIS
    Chef d'orchestre local : terminal MT5, agent, dashboard — et relance de ce qui tombe.

.DESCRIPTION
    L'agent doit tourner 24/7 sur cette machine, sans serveur. Une tâche planifiée sait
    lancer un programme au démarrage ; elle ne sait pas qu'un agent n'a de sens qu'avec un
    terminal MT5 vivant dans la même session, qu'un second agent ouvrirait des ordres en
    double, ni qu'un arrêt à 3 h du matin doit être rattrapé sans intervention.

    Ce script est cette couche, et il est le seul point d'entrée de l'exploitation locale :

    * mono-instance : un verrou nommé empêche deux superviseurs, et la détection de
      processus empêche deux agents (le sien, ou celui qu'un opérateur a lancé à la main) ;
    * le terminal MT5 est attendu, puis démarré s'il manque — l'agent sait aussi le faire,
      mais le faire ici évite une course au démarrage ;
    * chaque service écrit dans son propre journal du jour (`logs/<service>-<date>.log`),
      purgé au-delà de `-LogRetentionDays` ;
    * un service qui s'arrête est relancé ; le délai double quand les échecs s'enchaînent,
      jusqu'à `-MaxRestartDelaySeconds`, pour ne pas marteler la machine ni le courtier ;
    * un état lisible est écrit dans `logs/superviseur.json`, que `-Status` et
      `check_health.ps1` peuvent lire ;
    * Ctrl+C arrête proprement les enfants lancés par ce script.

    Aucun secret n'est lu ni écrit ici : l'agent lit `.env`, le terminal se reconnecte avec
    ses propres paramètres enregistrés.

.PARAMETER ProjectRoot
    Racine du dépôt. Défaut : le parent du dossier `scripts`, ou `TRADINGAGENT_ROOT`.

.PARAMETER LogDirectory
    Où écrire les journaux et l'état. Défaut : `<racine>\logs`.

.PARAMETER TerminalPath
    `terminal64.exe`. Défaut : `MT5_TERMINAL_PATH`, puis les emplacements d'installation
    habituels.

.PARAMETER PythonPath
    Interpréteur utilisé pour les services. Défaut : `.venv\Scripts\python.exe`.

.PARAMETER AgentArguments
    Arguments passés à l'interpréteur pour l'agent. Défaut : `-m tradingagent.app`.

.PARAMETER WebArguments
    Arguments du dashboard, utilisé seulement avec `-WithDashboard`.

.PARAMETER WithDashboard
    Supervise aussi le dashboard de lecture seule (`tradingagent-web`, 127.0.0.1:8787).

.PARAMETER NoTerminal
    Ne démarre pas le terminal MT5 ; se contente de signaler son absence.

.PARAMETER Once
    Démarre chaque service une fois, attend sa fin, ne relance pas. Pour vérifier une
    installation sans laisser un processus derrière soi.

.PARAMETER Status
    N'agit pas : affiche l'état des services et sort. Code 0 = tout tourne, 1 = dégradé.

.PARAMETER Stop
    Arrête les services enregistrés dans `logs/superviseur.json` (et le superviseur), sans
    toucher à un processus qui ne vient pas de ce dépôt.

.PARAMETER DryRun
    Affiche ce qui serait lancé, sans rien lancer.

.EXAMPLE
    pwsh -File scripts/supervise_agent.ps1 -Status
    pwsh -File scripts/supervise_agent.ps1 -Once
    pwsh -File scripts/supervise_agent.ps1 -WithDashboard
    pwsh -File scripts/supervise_agent.ps1 -Stop
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = $env:TRADINGAGENT_ROOT,

    [string]$LogDirectory,

    [string]$TerminalPath = $env:MT5_TERMINAL_PATH,

    [string]$PythonPath,

    [string]$AgentArguments = '-m tradingagent.app',

    [string]$WebArguments = '-m tradingagent.web.app --host 127.0.0.1 --port 8787',

    [switch]$WithDashboard,

    [switch]$NoTerminal,

    [int]$RestartDelaySeconds = 15,

    [int]$MaxRestartDelaySeconds = 300,

    [int]$MaxRestarts = 0,

    [int]$StableSeconds = 120,

    [int]$TerminalWaitSeconds = 90,

    [int]$LogRetentionDays = 14,

    [switch]$Once,

    [switch]$Status,

    [switch]$Stop,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message, [int]$Code = 1)
    [Console]::Error.WriteLine("ERREUR: $Message")
    exit $Code
}

# --- Racine, interpréteur, journaux ---------------------------------------------------------

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
if ([string]::IsNullOrWhiteSpace($LogDirectory)) { $LogDirectory = Join-Path $ProjectRoot 'logs' }

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $candidate = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate) {
        $PythonPath = $candidate
    }
    else {
        $found = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $found) {
            Fail "aucun interpréteur : .venv absent et python introuvable. Lancez scripts/install_windows.ps1." 2
        }
        $PythonPath = $found.Source
    }
}

$stateFile = Join-Path $LogDirectory 'superviseur.json'
$mutexName = 'Local\TradingAgentSupervisor'
$mutex = $null
$script:Stopping = $false
$script:StartedAt = (Get-Date).ToUniversalTime()
$script:AgentProcesses = @()

function Write-Journal {
    param([string]$Level, [string]$Message, [string]$LogFile)
    $line = "{0} {1,-7} {2}" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'), $Level, $Message
    Write-Output $line
    if (-not [string]::IsNullOrWhiteSpace($LogFile)) {
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    }
}

function Initialize-LogDirectory {
    if (-not (Test-Path -LiteralPath $LogDirectory)) {
        New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    }
    $limit = (Get-Date).AddDays(-1 * $LogRetentionDays)
    Get-ChildItem -LiteralPath $LogDirectory -Filter '*.log' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $limit } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
}

function Get-ServiceLog {
    param([string]$Name)
    return Join-Path $LogDirectory ("{0}-{1}.log" -f $Name, (Get-Date).ToUniversalTime().ToString('yyyyMMdd'))
}

# --- Processus ------------------------------------------------------------------------------

function Get-ProjectProcesses {
    """Les processus python/uv/cmd qui font tourner ce dépôt, quel que soit leur lanceur.

    Sert à deux choses : refuser un second agent (deux agents = deux fois les ordres), et ne
    jamais tuer un processus étranger au dépôt depuis `-Stop`.
    """
    param([string]$Needle = 'tradingagent', [int[]]$Exclude = @())
    $found = @()
    foreach ($process in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($Exclude -contains $process.ProcessId) { continue }
        $name = $process.Name
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        if ($name -notmatch '^(python|pythonw|uv|cmd|tradingagent-run|tradingagent-web)') { continue }
        $line = $process.CommandLine
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -notlike "*$Needle*") { continue }
        $found += $process
    }
    return $found
}

function Get-AgentProcesses {
    param([int[]]$Exclude = @())
    $found = @()
    foreach ($process in (Get-ProjectProcesses -Needle 'tradingagent' -Exclude $Exclude)) {
        $line = $process.CommandLine
        if ($line -match 'tradingagent[-.]?(app|run)') { $found += $process }
    }
    return $found
}

function Get-DashboardProcesses {
    param([int[]]$Exclude = @())
    $found = @()
    foreach ($process in (Get-ProjectProcesses -Needle 'tradingagent-web' -Exclude $Exclude)) {
        $found += $process
    }
    return $found
}

function Get-TerminalProcess {
    $process = Get-Process -Name 'terminal64' -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    return $process | Select-Object -First 1
}

function Resolve-TerminalPath {
    if (-not [string]::IsNullOrWhiteSpace($TerminalPath)) {
        if (Test-Path -LiteralPath $TerminalPath) { return $TerminalPath }
        Write-Journal 'WARN' "MT5_TERMINAL_PATH ne pointe sur rien : $TerminalPath" $null
    }
    $candidates = @()
    $programFiles = [System.Environment]::GetEnvironmentVariable('ProgramFiles')
    $programFilesX86 = [System.Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($programFiles) {
        $candidates += (Join-Path $programFiles 'MetaTrader 5 Terminal\terminal64.exe')
        $candidates += (Join-Path $programFiles 'MetaTrader 5\terminal64.exe')
    }
    if ($programFilesX86) {
        $candidates += (Join-Path $programFilesX86 'MetaTrader 5\terminal64.exe')
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

# --- Démarrage des services -----------------------------------------------------------------

function Start-ServiceProcess {
    """Lance un service et rend la main tout de suite.

    La sortie est ajoutée au journal du jour par `cmd.exe` : `Start-Process` écrase le
    fichier de redirection, ce qui perdrait l'historique à chaque relance. L'agent détecte
    un flux redirigé et écrit alors des journaux JSON, lisibles par machine.
    """
    param(
        [string]$Executable,
        [string]$Arguments,
        [string]$LogFile,
        [string]$WorkingDirectory
    )
    $command = '"{0}" {1} >> "{2}" 2>&1' -f $Executable, $Arguments, $LogFile
    $process = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $command) `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru
    return $process
}

# --- État -----------------------------------------------------------------------------------

$script:Services = @()

function New-SupervisedService {
    param([string]$Name, [string]$Arguments, [string]$LogFile)
    return [pscustomobject]@{
        name        = $Name
        arguments   = $Arguments
        log         = $LogFile
        process     = $null
        pid         = 0
        restarts    = 0
        fast_fails  = 0
        last_exit   = $null
        next_start  = (Get-Date).ToUniversalTime()
        started_at  = $null
    }
}

function Write-State {
    $payload = [pscustomobject]@{
        supervisor_pid = $PID
        started_at     = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssZ')
        updated_at     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        project_root   = $ProjectRoot
        python         = $PythonPath
        log_directory  = $LogDirectory
        services       = @($script:Services | ForEach-Object {
                [pscustomobject]@{
                    name        = $_.name
                    pid         = $_.pid
                    running     = [bool]($_.process -and -not $_.process.HasExited)
                    restarts    = $_.restarts
                    last_exit   = $_.last_exit
                    started_at  = if ($_.started_at) { $_.started_at.ToString('yyyy-MM-ddTHH:mm:ssZ') } else { $null }
                    log         = $_.log
                }
            })
    }
    if (-not (Test-Path -LiteralPath $LogDirectory)) { return }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding UTF8
}

# --- Vérifications avant de lancer -----------------------------------------------------------

function Assert-Installation {
    $missing = @()
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) { $missing += '.env' }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config\agent.yaml'))) { $missing += 'config\agent.yaml' }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config\strategies'))) { $missing += 'config\strategies' }
    if ($missing.Count -gt 0) {
        Fail ("installation incomplète : " + ($missing -join ', ') + ". Voir docs/operations/installation.md.") 2
    }
}

function Read-ExistingState {
    if (-not (Test-Path -LiteralPath $stateFile)) { return $null }
    try {
        return Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        Write-Journal 'WARN' "état illisible, ignoré : $stateFile" $null
        return $null
    }
}

# --- Arrêt ------------------------------------------------------------------------------------

function Test-OurProcess {
    """Un processus que ce dépôt a lancé : nom attendu et ligne de commande dans la racine."""
    param($Process)
    if ($null -eq $Process) { return $false }
    $live = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $Process.pid) -ErrorAction SilentlyContinue
    if ($null -eq $live) { return $false }
    if ($live.Name -notmatch '^(python|pythonw|uv|cmd|tradingagent-run|tradingagent-web)') { return $false }
    $line = $live.CommandLine
    if ([string]::IsNullOrWhiteSpace($line)) { return $false }
    return ($line -like "*$ProjectRoot*")
}

function Stop-RecordedServices {
    $state = Read-ExistingState
    if ($null -eq $state) {
        Write-Output 'Aucun état enregistré : rien à arrêter.'
        return
    }
    $targets = @()
    foreach ($service in @($state.services)) {
        if ($null -ne $service.pid -and [int]$service.pid -gt 0) {
            $targets += [pscustomobject]@{ pid = [int]$service.pid; name = $service.name }
        }
    }
    if ($null -ne $state.supervisor_pid -and [int]$state.supervisor_pid -gt 0) {
        $targets += [pscustomobject]@{ pid = [int]$state.supervisor_pid; name = 'superviseur' }
    }
    foreach ($target in $targets) {
        if (-not (Test-OurProcess $target)) {
            Write-Output ("ignoré (processus étranger ou déjà terminé) : {0} PID {1}" -f $target.name, $target.pid)
            continue
        }
        # Les enfants d'abord : tuer le superviseur avant eux laisserait l'agent orphelin.
        if ($target.name -eq 'superviseur') { continue }
        Stop-Process -Id $target.pid -Force -ErrorAction SilentlyContinue
        Write-Output ("arrêté : {0} (PID {1})" -f $target.name, $target.pid)
    }
    foreach ($target in $targets) {
        if ($target.name -ne 'superviseur') { continue }
        if (Test-OurProcess $target) {
            Stop-Process -Id $target.pid -Force -ErrorAction SilentlyContinue
            Write-Output ("arrêté : superviseur (PID {0})" -f $target.pid)
        }
    }
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
    Write-Output 'Terminé.'
}

function Stop-ChildProcesses {
    foreach ($service in $script:Services) {
        if ($null -ne $service.process -and -not $service.process.HasExited) {
            Stop-Process -Id $service.process.Id -Force -ErrorAction SilentlyContinue
            Write-Journal 'INFO' ("arrêt de {0} (PID {1})" -f $service.name, $service.process.Id) $null
        }
    }
}

# --- Affichage --------------------------------------------------------------------------------

function Show-Status {
    $degraded = 0
    Write-Output ("Superviseur TradingAgent — {0} UTC" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'))

    $state = Read-ExistingState
    if ($null -ne $state) {
        Write-Output ("  état       : {0} (superviseur PID {1}, mis à jour {2})" -f $stateFile, $state.supervisor_pid, $state.updated_at)
        foreach ($service in @($state.services)) {
            Write-Output ("               {0} : PID {1}, relances {2}, dernier code {3}" -f `
                    $service.name, $service.pid, $service.restarts, $service.last_exit)
        }
    }
    else {
        Write-Output ("  état       : aucun ({0} absent)" -f $stateFile)
    }

    $terminal = Get-TerminalProcess
    if ($null -ne $terminal) {
        Write-Output ("  [ok]     terminal MT5 : PID {0}" -f $terminal.Id)
    }
    else {
        Write-Output '  [dégrade] terminal MT5 : absent'
        $degraded++
    }

    $agents = @(Get-AgentProcesses)
    if ($agents.Count -eq 1) {
        Write-Output ("  [ok]     agent : PID {0}" -f $agents[0].ProcessId)
    }
    elseif ($agents.Count -eq 0) {
        Write-Output '  [dégrade] agent : arrêté'
        $degraded++
    }
    else {
        Write-Output ("  [dégrade] agent : {0} instances ({1}) — deux agents ouvriraient des ordres en double" -f `
                $agents.Count, (($agents | ForEach-Object { $_.ProcessId }) -join ', '))
        $degraded += 2
    }

    if ($WithDashboard) {
        $dashboards = @(Get-DashboardProcesses)
        if ($dashboards.Count -ge 1) {
            Write-Output ("  [ok]     dashboard : PID {0}" -f $dashboards[0].ProcessId)
        }
        else {
            Write-Output '  [dégrade] dashboard : arrêté'
            $degraded++
        }
    }

    if ($degraded -gt 0) { return 1 }
    return 0
}

function Show-Plan {
    Write-Output 'MODE SIMULATION : rien ne sera lancé'
    Write-Output ("  racine     : {0}" -f $ProjectRoot)
    Write-Output ("  journaux   : {0}" -f $LogDirectory)
    Write-Output ("  interpréteur : {0}" -f $PythonPath)
    $terminal = Resolve-TerminalPath
    if ($NoTerminal) {
        Write-Output '  terminal   : non supervisé (-NoTerminal)'
    }
    elseif ($null -ne $terminal) {
        Write-Output ("  terminal   : {0}" -f $terminal)
    }
    else {
        Write-Output '  terminal   : introuvable (l''agent tentera de le lancer lui-même)'
    }
    Write-Output ("  agent      : {0} {1} >> {2}" -f $PythonPath, $AgentArguments, (Get-ServiceLog 'agent'))
    if ($WithDashboard) {
        Write-Output ("  dashboard  : {0} {1} >> {2}" -f $PythonPath, $WebArguments, (Get-ServiceLog 'dashboard'))
    }
    Write-Output ("  relance    : délai {0} s, plafond {1} s, redémarrage illimité : {2}" -f `
            $RestartDelaySeconds, $MaxRestartDelaySeconds, ($MaxRestarts -eq 0))
}

# --- Programme principal ----------------------------------------------------------------------

if ($Stop) {
    Stop-RecordedServices
    exit 0
}

if ($Status) {
    exit (Show-Status)
}

if ($DryRun) {
    Show-Plan
    exit 0
}

$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$ownsMutex = $false
try {
    $ownsMutex = $mutex.WaitOne(0)
}
catch [System.Threading.AbandonedMutexException] {
    $ownsMutex = $true
}
if (-not $ownsMutex) {
    Fail 'un superviseur tourne déjà pour cette session (verrou Local\TradingAgentSupervisor).' 3
}

try {
    Initialize-LogDirectory
    Assert-Installation

    $supervisorLog = Join-Path $LogDirectory 'superviseur.log'
    Write-Journal 'INFO' ("démarrage du superviseur (PID {0}) dans {1}" -f $PID, $ProjectRoot) $supervisorLog

    $script:Services += New-SupervisedService -Name 'agent' -Arguments $AgentArguments -LogFile (Get-ServiceLog 'agent')
    if ($WithDashboard) {
        $script:Services += New-SupervisedService -Name 'dashboard' -Arguments $WebArguments -LogFile (Get-ServiceLog 'dashboard')
    }

    # Le terminal d'abord : l'agent s'y connecte, et une connexion ratée au démarrage coûte
    # un cycle d'attente. Le terminal refuse les instances multiples, donc le lancer deux fois
    # est sans effet — on vérifie quand même avant.
    $terminalPath = Resolve-TerminalPath
    if (-not $NoTerminal) {
        if ($null -eq (Get-TerminalProcess)) {
            if ($null -ne $terminalPath) {
                Write-Journal 'INFO' ("démarrage du terminal MT5 : {0}" -f $terminalPath) $supervisorLog
                Start-Process -FilePath $terminalPath -WorkingDirectory (Split-Path -Parent $terminalPath) | Out-Null
                $waited = 0
                while ($null -eq (Get-TerminalProcess) -and $waited -lt $TerminalWaitSeconds) {
                    Start-Sleep -Seconds 2
                    $waited += 2
                }
                if ($null -eq (Get-TerminalProcess)) {
                    Write-Journal 'WARN' 'terminal MT5 toujours absent après attente : l''agent démarrera quand même' $supervisorLog
                }
            }
            else {
                Write-Journal 'WARN' 'terminal MT5 introuvable : l''agent tentera de le lancer lui-même' $supervisorLog
            }
        }
        else {
            Write-Journal 'INFO' 'terminal MT5 déjà en marche' $supervisorLog
        }
    }

    $cancelHandler = [System.ConsoleCancelEventHandler] {
        param($sender, $eventArgs)
        $eventArgs.Cancel = $true
        $script:Stopping = $true
    }
    try { [Console]::CancelKeyPress += $cancelHandler } catch { }

    $exitCode = 0
    while (-not $script:Stopping) {
        $now = (Get-Date).ToUniversalTime()

        foreach ($service in $script:Services) {
            $running = [bool]($service.process -and -not $service.process.HasExited)
            if ($running) { continue }
            if ($null -ne $service.process) {
                # Le service vient de se terminer : noter, compter, décider du délai.
                $service.last_exit = $service.process.ExitCode
                $lived = 0
                if ($null -ne $service.started_at) { $lived = ($now - $service.started_at).TotalSeconds }
                if ($lived -lt $StableSeconds) { $service.fast_fails++ } else { $service.fast_fails = 0 }
                $service.restarts++
                $delay = $RestartDelaySeconds * [Math]::Pow(2, [Math]::Min($service.fast_fails, 5))
                $delay = [Math]::Min([int]$delay, $MaxRestartDelaySeconds)
                Write-Journal 'WARN' ("{0} terminé (code {1}) après {2:N0} s : relance dans {3} s" -f `
                        $service.name, $service.last_exit, $lived, $delay) $supervisorLog
                if ($MaxRestarts -gt 0 -and $service.restarts -gt $MaxRestarts) {
                    Write-Journal 'CRITICAL' ("{0} : plafond de {1} relances atteint, abandon" -f $service.name, $MaxRestarts) $supervisorLog
                    $script:Stopping = $true
                    $exitCode = 1
                    break
                }
                $service.next_start = $now.AddSeconds($delay)
                $service.process = $null
                $service.pid = 0
                Write-State
                continue
            }

            if ($Once) { continue }

            # Mono-instance : un agent lancé hors supervision (console, IDE) doit être
            # respecté, pas doublé. On attend qu'il disparaisse pour prendre la main.
            if ($service.name -eq 'agent') {
                $foreign = @(Get-AgentProcesses)
                if ($foreign.Count -gt 0) {
                    if (-not $script:AgentProcesses) {
                        Write-Journal 'WARN' ("agent déjà en marche hors supervision (PID {0}) : attente avant de superviser" -f $foreign[0].ProcessId) $supervisorLog
                    }
                    $script:AgentProcesses = $foreign | ForEach-Object { $_.ProcessId }
                    continue
                }
                $script:AgentProcesses = @()
            }

            if ($now -lt $service.next_start) { continue }

            Write-Journal 'INFO' ("démarrage de {0} : {1} {2}" -f $service.name, $PythonPath, $service.arguments) $supervisorLog
            $service.process = Start-ServiceProcess -Executable $PythonPath -Arguments $service.arguments `
                -LogFile $service.log -WorkingDirectory $ProjectRoot
            $service.pid = $service.process.Id
            $service.started_at = (Get-Date).ToUniversalTime()
            Write-State
        }

        if ($Once) {
            $alive = @($script:Services | Where-Object { $_.process -and -not $_.process.HasExited })
            if ($alive.Count -eq 0) {
                foreach ($service in $script:Services) {
                    if ($null -ne $service.process) { $exitCode = $service.process.ExitCode }
                }
                break
            }
        }

        Start-Sleep -Seconds 2
    }

    Write-Journal 'INFO' 'superviseur arrêté' $supervisorLog
    exit $exitCode
}
finally {
    Stop-ChildProcesses
    Write-State
    if ($ownsMutex -and $null -ne $mutex) { [void]$mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
