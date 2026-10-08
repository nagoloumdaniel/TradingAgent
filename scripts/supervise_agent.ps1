#Requires -Version 5.1
<#
.SYNOPSIS
    Chef d'orchestre local : terminal MT5, agent, dashboard — et relance de ce qui tombe.

.DESCRIPTION
    L'agent doit tourner 24/7 sur cette machine, sans serveur. Une tâche planifiée sait
    lancer un programme au démarrage ; elle ne sait pas qu'un agent n'a de sens qu'avec un
    terminal MT5 vivant dans la même session, qu'un second agent ouvrirait des ordres en
    double, ni qu'un arrêt à 3 h du matin doit être rattrapé sans intervention.

    Ce script est cette couche, et il est le point d'entrée de l'exploitation locale :

    * mono-instance : un verrou nommé empêche deux superviseurs, et la détection de
      processus empêche deux agents (le sien, ou celui qu'un opérateur a lancé à la main) ;
    * le terminal MT5 est attendu, puis démarré s'il manque — l'agent sait aussi le faire,
      mais le faire ici évite une course au démarrage ;
    * chaque service écrit dans son journal du jour (`logs/<service>-<date>.log`), purgé
      au-delà de `-LogRetentionDays` ;
    * un service qui s'arrête est relancé ; le délai double quand les échecs s'enchaînent,
      jusqu'à `-MaxRestartDelaySeconds`, pour ne pas marteler la machine ni le courtier ;
    * un état lisible est écrit dans `logs/superviseur.json`, que `-Status` lit ;
    * Ctrl+C arrête proprement l'arbre de processus de chaque enfant.

    Aucun secret n'est lu ni écrit ici : l'agent lit `.env`, le terminal se reconnecte avec
    ses propres paramètres enregistrés.

.PARAMETER ProjectRoot
    Racine du dépôt. Défaut : le parent du dossier `scripts`, ou `TRADINGAGENT_ROOT`.

.PARAMETER LogDirectory
    Où écrire les journaux et l'état. Défaut : `<racine>\logs`.

.PARAMETER TerminalPath
    `terminal64.exe`. Défaut : `MT5_TERMINAL_PATH`, puis les emplacements habituels.

.PARAMETER PythonPath
    Interpréteur des services. Défaut : `.venv\Scripts\python.exe`.

.PARAMETER AgentArguments
    Arguments passés à l'interpréteur pour l'agent. Défaut : `-m tradingagent.app`.

.PARAMETER WebArguments
    Arguments du dashboard, utilisé seulement avec `-WithDashboard`.

.PARAMETER WithDashboard
    Supervise aussi le dashboard de lecture seule (`tradingagent-web`, 127.0.0.1:8787).

.PARAMETER NoTerminal
    Ne démarre pas le terminal MT5 ; se contente de signaler son absence.

.PARAMETER IgnoreRunningAgent
    Démarre même si un agent tourne déjà hors supervision. Réservé aux vérifications
    (par exemple `-AgentArguments '-m tradingagent.app --help'`) : en exploitation, deux
    agents ouverts sur le même compte sont un défaut, pas une option.

.PARAMETER Once
    Démarre chaque service une fois, attend sa fin, ne relance pas. Pour vérifier une
    installation sans laisser un processus derrière soi.

.PARAMETER Status
    N'agit pas : affiche l'état des services et sort. Code 0 = tout tourne, 1 = dégradé.

.PARAMETER Stop
    Arrête les services enregistrés dans `logs/superviseur.json`, sans toucher à un
    processus qui ne vient pas de ce dépôt.

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

    [switch]$IgnoreRunningAgent,

    [int]$RestartDelaySeconds = 15,

    [int]$MaxRestartDelaySeconds = 300,

    [int]$MaxRestarts = 0,

    [int]$StableSeconds = 120,

    [int]$TerminalWaitSeconds = 90,

    [int]$LogRetentionDays = 14,

    # Le verrou qui garantit un seul superviseur par session. Il est injectable pour une
    # raison précise : sans cela, les tests ne peuvent pas démarrer le leur pendant que
    # l'exploitation tourne, et ils échouent pour une raison qui n'a rien à voir avec ce
    # qu'ils vérifient.
    [string]$MutexName = 'Local\TradingAgentSupervisor',

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
# Le seul canal qui marche pour arreter ou redemarrer TOUTE la pile : l'agent ecrit un mot,
# ce script le lit a chaque battement. Un fichier, parce que le superviseur n'a pas de base
# de donnees et que l'agent ne peut ni tuer le terminal ni se relancer lui-meme.
$controlFile = Join-Path $LogDirectory 'controle.txt'
$StopOrder = 'arreter'
$RestartOrder = 'redemarrer'
$supervisorLog = Join-Path $LogDirectory 'superviseur.log'
# Le code de sortie d'un redémarrage demandé (RESTART_EXIT_CODE, dans app.py).
$RestartExitCode = 75
$mutexName = $MutexName
$mutex = $null
$ownsMutex = $false
$script:Stopping = $false
$script:StartedAt = (Get-Date).ToUniversalTime()
$script:AgentProcesses = @()
$script:StatusExitCode = 0
$script:Services = @()

function Write-Journal {
    param([string]$Level, [string]$Message, [string]$LogFile)
    $line = "{0} {1,-7} {2}" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'), $Level, $Message
    Write-Output $line
    if (-not [string]::IsNullOrWhiteSpace($LogFile)) {
        # Écriture explicite, sans BOM : `-Encoding UTF8` en ajoute un sous Windows
        # PowerShell 5.1 et pas sous PowerShell 7, donc le même journal n'aurait pas la même
        # tête selon l'hôte qui l'a écrit.
        [System.IO.File]::AppendAllText(
            $LogFile, $line + [System.Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
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
    <#
        Les processus python/uv/cmd qui font tourner ce dépôt, quel que soit leur lanceur.
        Sert à deux choses : refuser un second agent (deux agents = deux fois les ordres), et
        ne jamais tuer un processus étranger au dépôt depuis `-Stop`.
    #>
    param([string]$Needle = 'tradingagent', [int[]]$Exclude = @())
    $found = @()
    foreach ($item in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($Exclude -contains $item.ProcessId) { continue }
        $name = $item.Name
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        if ($name -notmatch '^(python|pythonw|uv|cmd|tradingagent-run|tradingagent-web)') { continue }
        $line = $item.CommandLine
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -notlike "*$Needle*") { continue }
        $found += $item
    }
    return $found
}

function Select-RootProcesses {
    <#
        Un agent lance par `uv run tradingagent-run` est une chaine de quatre processus
        (uv, le lanceur du venv, deux interpreteurs). Les compter tous ferait croire a
        quatre agents, donc a un danger qui n'existe pas. On ne garde que la tete de
        chaque chaine : un processus dont le parent n'est pas lui-meme candidat.
    #>
    param([object[]]$Candidates)
    $ids = @($Candidates | ForEach-Object { $_.ProcessId })
    $roots = @()
    foreach ($candidate in $Candidates) {
        if ($ids -notcontains $candidate.ParentProcessId) { $roots += $candidate }
    }
    return $roots
}

function Get-AgentProcesses {
    param([int[]]$Exclude = @())
    $found = @()
    foreach ($item in (Get-ProjectProcesses -Needle 'tradingagent' -Exclude $Exclude)) {
        if ($item.CommandLine -match 'tradingagent[-.]?(app|run)') { $found += $item }
    }
    return @(Select-RootProcesses -Candidates $found)
}

function Get-DashboardProcesses {
    param([int[]]$Exclude = @())
    # Deux formes selon le lanceur : `tradingagent-web.exe` (script de console) et
    # `-m tradingagent.web.app` (superviseur). Un seul motif en raterait une.
    $found = @()
    foreach ($item in (Get-ProjectProcesses -Needle 'tradingagent' -Exclude $Exclude)) {
        if ($item.CommandLine -match 'tradingagent[-.]?web') { $found += $item }
    }
    return @(Select-RootProcesses -Candidates $found)
}

function Get-ForeignServiceProcesses {
    <#
        Les processus d'un service que ce superviseur n'a pas lancés : un agent ouvert dans
        une console, un dashboard resté d'une session précédente. Les doubler produirait des
        ordres en double, ou deux serveurs web sur le même port.
    #>
    param([string]$Name, [int[]]$Exclude = @())
    if ($Name -eq 'agent') { return @(Get-AgentProcesses -Exclude $Exclude) }
    if ($Name -eq 'dashboard') { return @(Get-DashboardProcesses -Exclude $Exclude) }
    return @()
}

function Get-TerminalProcess {
    $process = Get-Process -Name 'terminal64' -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    return @($process)[0]
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

function Stop-ProcessTree {
    <#
        `Stop-Process` ne tue que le processus visé : le `cmd.exe` qui redirige la sortie
        mourrait en laissant l'agent orphelin, donc vivant, donc hors de tout contrôle.
        `taskkill /T` tue l'arbre.
    #>
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null
}

# --- État -----------------------------------------------------------------------------------

function New-SupervisedService {
    param([string]$Name, [string]$Arguments, [string]$LogFile)
    return [pscustomobject]@{
        name       = $Name
        arguments  = $Arguments
        log        = $LogFile
        process    = $null
        pid        = 0
        restarts   = 0
        fast_fails = 0
        last_exit  = $null
        next_start = (Get-Date).ToUniversalTime()
        started_at = $null
        warned_foreign = $false
    }
}

function Write-State {
    if (-not (Test-Path -LiteralPath $LogDirectory)) { return }
    $services = @()
    foreach ($service in $script:Services) {
        $running = $false
        if ($null -ne $service.process) { $running = -not $service.process.HasExited }
        $startedAt = $null
        if ($null -ne $service.started_at) {
            $startedAt = $service.started_at.ToString('yyyy-MM-ddTHH:mm:ssZ')
        }
        $services += [pscustomobject]@{
            name       = $service.name
            pid        = $service.pid
            running    = $running
            restarts   = $service.restarts
            last_exit  = $service.last_exit
            started_at = $startedAt
            log        = $service.log
        }
    }
    $payload = [pscustomobject]@{
        supervisor_pid = $PID
        started_at     = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssZ')
        updated_at     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        project_root   = $ProjectRoot
        python         = $PythonPath
        log_directory  = $LogDirectory
        services       = $services
    }
    # Sans BOM, pour la même raison : ce fichier est lu par `-Stop` et par des outils JSON.
    $json = $payload | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($stateFile, $json, [System.Text.UTF8Encoding]::new($false))
}

function Read-ExistingState {
    if (-not (Test-Path -LiteralPath $stateFile)) { return $null }
    try {
        return Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        Write-Output "AVERTISSEMENT: état illisible, ignoré : $stateFile"
        return $null
    }
}

# --- Démarrage et arrêt -----------------------------------------------------------------------

function New-ServiceWrapper {
    <#
        Écrit le petit script .cmd qui lance un service et ajoute sa sortie au journal du jour.

        Deux raisons, et la seconde est la vraie. `Start-Process -RedirectStandardOutput`
        écrase le fichier : l'historique disparaîtrait à chaque relance. Et imbriquer la
        commande dans `cmd.exe /c "..."` depuis PowerShell 5.1 ne survit pas au passage des
        arguments — vérifié : le fichier de journal n'était même pas créé. Un fichier .cmd
        écrit noir sur blanc n'a ni ce problème d'échappement ni ce défaut d'être illisible :
        l'opérateur peut l'ouvrir et voir exactement ce qui tourne.
    #>
    param([string]$Name, [string]$Executable, [string]$Arguments, [string]$LogFile)
    $wrapper = Join-Path $LogDirectory ("{0}.cmd" -f $Name)
    $lines = @(
        '@echo off',
        'rem Genere par scripts/supervise_agent.ps1 : ne pas modifier a la main.',
        'rem L''agent lit cette variable pour dire vrai dans sa reponse a /restart.',
        'set TRADINGAGENT_SUPERVISED=1',
        ('set TRADINGAGENT_CONTROL_FILE={0}' -f (Join-Path $LogDirectory 'controle.txt')),
        ('"{0}" {1} >> "{2}" 2>&1' -f $Executable, $Arguments, $LogFile),
        'exit /b %ERRORLEVEL%'
    )
    Set-Content -LiteralPath $wrapper -Value $lines -Encoding ASCII
    return $wrapper
}

function Start-ServiceProcess {
    <#
        Lance un service par son enveloppe et rend la main tout de suite. L'agent détecte un
        flux redirigé et écrit alors des journaux JSON, lisibles par machine.
    #>
    param(
        [string]$Name,
        [string]$Executable,
        [string]$Arguments,
        [string]$LogFile,
        [string]$WorkingDirectory
    )
    $wrapper = New-ServiceWrapper -Name $Name -Executable $Executable -Arguments $Arguments -LogFile $LogFile
    return Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', ('"{0}"' -f $wrapper)) `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru
}

function Test-OurProcess {
    <# Un processus que ce dépôt a lancé : nom attendu et ligne de commande dans la racine. #>
    param($Target)
    if ($null -eq $Target) { return $false }
    $targetPid = [int]$Target.pid
    if ($targetPid -le 0) { return $false }
    $live = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $targetPid) -ErrorAction SilentlyContinue
    if ($null -eq $live) { return $false }
    if ($live.Name -notmatch '^(python|pythonw|uv|cmd|pwsh|powershell|tradingagent-run|tradingagent-web)') { return $false }
    $line = $live.CommandLine
    if ([string]::IsNullOrWhiteSpace($line)) { return $false }
    # L'enveloppe du service vit dans le répertoire des journaux, l'interpréteur dans la
    # racine : les deux marquent un processus comme nôtre, et rien d'autre ne le fait.
    return (($line -like "*$ProjectRoot*") -or ($line -like "*$LogDirectory*"))
}

function Read-ControlOrder {
    <#
        L'ordre laisse par /shutdown ou /restart_all, lu une fois et efface aussitot : un
        ordre qui resterait sur le disque ferait redemarrer l'agent en boucle a chaque
        battement. Un fichier illisible ou vide ne declenche rien.
    #>
    if (-not (Test-Path -LiteralPath $controlFile)) { return $null }
    $order = ''
    try { $order = (Get-Content -LiteralPath $controlFile -Raw -Encoding UTF8).Trim().ToLower() }
    catch { $order = '' }
    Remove-Item -LiteralPath $controlFile -Force -ErrorAction SilentlyContinue
    if ($order -eq $StopOrder -or $order -eq $RestartOrder) { return $order }
    return $null
}

function Stop-Terminal {
    <# Ferme MT5 proprement, puis de force : une fenetre qui demande confirmation bloquerait
       un arret demande a distance. #>
    $terminal = Get-TerminalProcess
    if ($null -eq $terminal) { return }
    Write-Journal 'INFO' ("arret du terminal MT5 (PID {0})" -f $terminal.Id) $supervisorLog
    $terminal.CloseMainWindow() | Out-Null
    $waited = 0
    while (-not $terminal.HasExited -and $waited -lt 5) { Start-Sleep -Seconds 1; $waited++ }
    if (-not $terminal.HasExited) { Stop-Process -Id $terminal.Id -Force -ErrorAction SilentlyContinue }
}

function Start-Terminal {
    <# Demarre MT5 et attend qu'il soit la : l'agent qui demarre avant lui echoue poliment. #>
    if ($NoTerminal) { return }
    if ($null -ne (Get-TerminalProcess)) { return }
    $path = Resolve-TerminalPath
    if ($null -eq $path) {
        Write-Journal 'WARN' "terminal MT5 introuvable : l'agent tentera de le lancer lui-meme" $supervisorLog
        return
    }
    Write-Journal 'INFO' ("demarrage du terminal MT5 : {0}" -f $path) $supervisorLog
    Start-Process -FilePath $path -WorkingDirectory (Split-Path -Parent $path) | Out-Null
    $waited = 0
    while ($null -eq (Get-TerminalProcess) -and $waited -lt $TerminalWaitSeconds) {
        Start-Sleep -Seconds 2
        $waited += 2
    }
}

function Stop-EveryService {
    <#
        Tout ce que ce script a lance, plus le terminal, plus les copies qu'il n'a pas lancees.
        Un agent ou un tableau de bord ouverts dans une console comptent : « tout arreter »
        veut dire tout, pas seulement ce que la supervision a demarre.
    #>
    foreach ($service in $script:Services) {
        if ($null -ne $service.process -and -not $service.process.HasExited) {
            Write-Journal 'INFO' ("arret de {0} (PID {1})" -f $service.name, $service.process.Id) $supervisorLog
            Stop-ProcessTree -ProcessId $service.process.Id
        }
        foreach ($foreign in @(Get-ForeignServiceProcesses -Name $service.name)) {
            Write-Journal 'INFO' ("arret de {0} hors supervision (PID {1})" -f $service.name, $foreign.ProcessId) $supervisorLog
            Stop-ProcessTree -ProcessId $foreign.ProcessId
        }
        $service.process = $null
        $service.pid = 0
        $service.started_at = $null
    }
    Stop-Terminal
}

function Stop-RecordedServices {
    $state = Read-ExistingState
    if ($null -eq $state) {
        Write-Output 'Aucun état enregistré : rien à arrêter.'
        return
    }
    $children = @()
    $supervisors = @()
    foreach ($service in @($state.services)) {
        if ($null -ne $service.pid -and [int]$service.pid -gt 0) {
            $children += [pscustomobject]@{ pid = [int]$service.pid; name = $service.name }
        }
    }
    if ($null -ne $state.supervisor_pid -and [int]$state.supervisor_pid -gt 0) {
        $supervisors += [pscustomobject]@{ pid = [int]$state.supervisor_pid; name = 'superviseur' }
    }

    # Les enfants d'abord : tuer le superviseur avant eux laisserait l'agent orphelin.
    foreach ($target in $children) {
        if (-not (Test-OurProcess $target)) {
            Write-Output ("ignoré (processus étranger ou déjà terminé) : {0} PID {1}" -f $target.name, $target.pid)
            continue
        }
        Stop-ProcessTree -ProcessId $target.pid
        Write-Output ("arrêté : {0} (PID {1})" -f $target.name, $target.pid)
    }
    foreach ($target in $supervisors) {
        if (-not (Test-OurProcess $target)) {
            Write-Output ("ignoré (processus étranger ou déjà terminé) : {0} PID {1}" -f $target.name, $target.pid)
            continue
        }
        Stop-Process -Id $target.pid -Force -ErrorAction SilentlyContinue
        Write-Output ("arrêté : {0} (PID {1})" -f $target.name, $target.pid)
    }
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
    Write-Output 'Terminé.'
}

function Stop-ChildProcesses {
    foreach ($service in $script:Services) {
        if ($null -ne $service.process -and -not $service.process.HasExited) {
            Write-Journal 'INFO' ("arrêt de {0} (PID {1})" -f $service.name, $service.process.Id) $null
            Stop-ProcessTree -ProcessId $service.process.Id
        }
    }
}

# --- Affichage ---------------------------------------------------------------------------------

function Show-Status {
    $script:StatusExitCode = 0
    Write-Output ("Superviseur TradingAgent — {0} UTC" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'))

    $state = Read-ExistingState
    if ($null -ne $state) {
        Write-Output ("  état         : {0} (superviseur PID {1}, mis à jour {2})" -f $stateFile, $state.supervisor_pid, $state.updated_at)
        foreach ($service in @($state.services)) {
            Write-Output ("                 {0} : PID {1}, relances {2}, dernier code {3}" -f `
                    $service.name, $service.pid, $service.restarts, $service.last_exit)
        }
    }
    else {
        Write-Output ("  état         : aucun (pas de {0})" -f $stateFile)
    }

    $terminal = Get-TerminalProcess
    if ($null -ne $terminal) {
        Write-Output ("  [ok]         terminal MT5 : PID {0}" -f $terminal.Id)
    }
    else {
        Write-Output '  [dégrade]    terminal MT5 : absent'
        $script:StatusExitCode = 1
    }

    $agents = @(Get-AgentProcesses)
    if ($agents.Count -eq 1) {
        Write-Output ("  [ok]         agent : PID {0}" -f $agents[0].ProcessId)
    }
    elseif ($agents.Count -eq 0) {
        Write-Output '  [dégrade]    agent : arrêté'
        $script:StatusExitCode = 1
    }
    else {
        Write-Output ("  [dégrade]    agent : {0} instances ({1}) — deux agents ouvriraient des ordres en double" -f `
                $agents.Count, (($agents | ForEach-Object { $_.ProcessId }) -join ', '))
        $script:StatusExitCode = 1
    }

    $dashboards = @(Get-DashboardProcesses)
    if ($dashboards.Count -ge 1) {
        Write-Output ("  [ok]         dashboard : PID {0}" -f $dashboards[0].ProcessId)
    }
    else {
        Write-Output '  [info]       dashboard : arrêté (optionnel, -WithDashboard)'
    }
}

function Show-Plan {
    Write-Output 'MODE SIMULATION : rien ne sera lancé'
    Write-Output ("  racine       : {0}" -f $ProjectRoot)
    Write-Output ("  journaux     : {0}" -f $LogDirectory)
    Write-Output ("  interpréteur : {0}" -f $PythonPath)
    $terminal = Resolve-TerminalPath
    if ($NoTerminal) {
        Write-Output '  terminal     : non supervisé (-NoTerminal)'
    }
    elseif ($null -ne $terminal) {
        Write-Output ("  terminal     : {0}" -f $terminal)
    }
    else {
        Write-Output "  terminal     : introuvable (l'agent tentera de le lancer lui-même)"
    }
    Write-Output ("  agent        : {0} {1} >> {2}" -f $PythonPath, $AgentArguments, (Get-ServiceLog 'agent'))
    if ($WithDashboard) {
        Write-Output ("  dashboard    : {0} {1} >> {2}" -f $PythonPath, $WebArguments, (Get-ServiceLog 'dashboard'))
    }
    $unlimited = ($MaxRestarts -eq 0)
    Write-Output ("  relance      : délai {0} s, plafond {1} s, relances illimitées : {2}" -f `
            $RestartDelaySeconds, $MaxRestartDelaySeconds, $unlimited)
}

function Assert-Installation {
    $missing = @()
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) { $missing += '.env' }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config\agent.yaml'))) { $missing += 'config\agent.yaml' }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config\strategies'))) { $missing += 'config\strategies' }
    if ($missing.Count -gt 0) {
        Fail ("installation incomplète : " + ($missing -join ', ') + '. Voir docs/operations/installation.md.') 2
    }
}

# --- Programme principal -------------------------------------------------------------------------

if ($Stop) {
    Stop-RecordedServices
    exit 0
}

if ($Status) {
    Show-Status
    exit $script:StatusExitCode
}

if ($DryRun) {
    Show-Plan
    exit 0
}

$mutex = New-Object System.Threading.Mutex($false, $mutexName)
try {
    $ownsMutex = $mutex.WaitOne(0)
}
catch [System.Threading.AbandonedMutexException] {
    $ownsMutex = $true
}
if (-not $ownsMutex) {
    Fail "un superviseur tourne déjà pour cette session (verrou $MutexName)." 3
}

$exitCode = 0
try {
    Initialize-LogDirectory
    Assert-Installation

    Write-Journal 'INFO' ("démarrage du superviseur (PID {0}) dans {1}" -f $PID, $ProjectRoot) $supervisorLog
    if ($IgnoreRunningAgent) {
        Write-Journal 'WARN' 'IgnoreRunningAgent : un agent deja en marche ne bloquera pas ce demarrage' $supervisorLog
    }

    $script:Services += New-SupervisedService -Name 'agent' -Arguments $AgentArguments -LogFile (Get-ServiceLog 'agent')
    if ($WithDashboard) {
        $script:Services += New-SupervisedService -Name 'dashboard' -Arguments $WebArguments -LogFile (Get-ServiceLog 'dashboard')
    }
    # L'état existe dès le démarrage, avant même le premier service : sinon un superviseur
    # qui attend le terminal, ou un agent lancé à la main, resterait invisible à `-Stop`.
    Write-State

    # Le terminal d'abord : l'agent s'y connecte, et une connexion ratée coûte un cycle
    # d'attente. Le terminal refuse les instances multiples, donc le lancer deux fois est sans
    # effet — on vérifie quand même avant.
    if (-not $NoTerminal) {
        if ($null -ne (Get-TerminalProcess)) {
            Write-Journal 'INFO' 'terminal MT5 déjà en marche' $supervisorLog
        }
        else {
            $terminalPath = Resolve-TerminalPath
            if ($null -ne $terminalPath) {
                Write-Journal 'INFO' ("démarrage du terminal MT5 : {0}" -f $terminalPath) $supervisorLog
                Start-Process -FilePath $terminalPath -WorkingDirectory (Split-Path -Parent $terminalPath) | Out-Null
                $waited = 0
                while ($null -eq (Get-TerminalProcess) -and $waited -lt $TerminalWaitSeconds) {
                    Start-Sleep -Seconds 2
                    $waited += 2
                }
                if ($null -eq (Get-TerminalProcess)) {
                    Write-Journal 'WARN' "terminal MT5 toujours absent après $TerminalWaitSeconds s : l'agent démarrera quand même" $supervisorLog
                }
            }
            else {
                Write-Journal 'WARN' "terminal MT5 introuvable : l'agent tentera de le lancer lui-même" $supervisorLog
            }
        }
    }

    # Ctrl+C n'est pas intercepté : PowerShell interrompt la boucle et déroule le `finally`,
    # qui tue l'arbre de chaque enfant. Un gestionnaire d'événement .NET serait appelé sur un
    # autre thread, sans runspace PowerShell — un piège pour rien.
    while (-not $script:Stopping) {
        $now = (Get-Date).ToUniversalTime()

        # /shutdown et /restart_all : l'agent ne peut ni tuer le terminal ni se relancer, il
        # ecrit donc un mot ici. Lu une fois, efface aussitot.
        $order = Read-ControlOrder
        if ($order -eq $RestartOrder) {
            Write-Journal 'INFO' 'redemarrage de toute la pile demande par l operateur' $supervisorLog
            Stop-EveryService
            Start-Terminal
            foreach ($service in $script:Services) {
                $service.fast_fails = 0
                $service.restarts = 0
                $service.next_start = (Get-Date).ToUniversalTime()
            }
            Write-State
            continue
        }
        if ($order -eq $StopOrder) {
            Write-Journal 'INFO' 'arret de toute la pile demande par l operateur' $supervisorLog
            Stop-EveryService
            $exitCode = 0
            $script:Stopping = $true
            break
        }

        foreach ($service in $script:Services) {
            $running = $false
            if ($null -ne $service.process) { $running = -not $service.process.HasExited }
            if ($running) { continue }

            if ($null -ne $service.process) {
                # Le service vient de se terminer : noter, compter, décider du délai.
                # ExitTime et ExitCode se lisent AVANT de lâcher l'objet : une fois
                # `$service.process` mis à $null, il n'y a plus rien à interroger.
                $exitedAt = $now
                try { $exitedAt = $service.process.ExitTime.ToUniversalTime() } catch { }
                $service.last_exit = $service.process.ExitCode
                $service.process = $null
                $service.pid = 0
                # Durée de vie réelle, pas la granularité de la boucle : un processus mort
                # en 50 ms ne doit pas être compté comme ayant vécu les 2 s du sondage, sinon
                # la détection des boucles d'échec ne se déclenche jamais.
                $lived = 0
                if ($null -ne $service.started_at) { $lived = ($exitedAt - $service.started_at).TotalSeconds }
                if ($Once) {
                    Write-Journal 'INFO' ("{0} terminé (code {1}) après {2:N0} s" -f $service.name, $service.last_exit, $lived) $supervisorLog
                    Write-State
                    continue
                }
                $service.restarts++
                if ($service.last_exit -eq $RestartExitCode) {
                    # Redémarrage demandé par l'opérateur (`/restart`) : ce n'est pas une
                    # instabilité, donc le délai ne double pas — la réponse Telegram promet une
                    # quinzaine de secondes, et une promesse fausse dès le premier essai ne vaut
                    # rien. Le code vient de `RESTART_EXIT_CODE`, dans `app.py`.
                    $service.fast_fails = 0
                    $delay = $RestartDelaySeconds
                }
                else {
                    if ($lived -lt $StableSeconds) { $service.fast_fails++ } else { $service.fast_fails = 0 }
                    $delay = [int]($RestartDelaySeconds * [Math]::Pow(2, [Math]::Min($service.fast_fails, 5)))
                    $delay = [Math]::Min($delay, $MaxRestartDelaySeconds)
                }
                Write-Journal 'WARN' ("{0} terminé (code {1}) après {2:N0} s : relance dans {3} s" -f `
                        $service.name, $service.last_exit, $lived, $delay) $supervisorLog
                if ($MaxRestarts -gt 0 -and $service.restarts -gt $MaxRestarts) {
                    Write-Journal 'CRITICAL' ("{0} : plafond de {1} relances atteint, abandon" -f $service.name, $MaxRestarts) $supervisorLog
                    $script:Stopping = $true
                    $exitCode = 1
                    break
                }
                $service.next_start = $now.AddSeconds($delay)
                Write-State
                continue
            }

            # -Once : le premier démarrage a lieu, les relances non.
            if ($Once -and $null -ne $service.started_at) { continue }

            # Mono-instance, service par service : un agent lancé hors supervision (console,
            # IDE) ou un dashboard resté d'une session précédente doit être respecté, pas
            # doublé. On attend qu'il disparaisse pour prendre la main.
            if (-not $IgnoreRunningAgent) {
                $foreign = @(Get-ForeignServiceProcesses -Name $service.name)
                if ($foreign.Count -gt 0) {
                    if (-not $service.warned_foreign) {
                        Write-Journal 'WARN' ("{0} déjà en marche hors supervision (PID {1}) : attente avant de superviser" -f $service.name, $foreign[0].ProcessId) $supervisorLog
                        $service.warned_foreign = $true
                        Write-State
                    }
                    continue
                }
                $service.warned_foreign = $false
            }

            if ($now -lt $service.next_start) { continue }

            Write-Journal 'INFO' ("démarrage de {0} : {1} {2}" -f $service.name, $PythonPath, $service.arguments) $supervisorLog
            $service.process = Start-ServiceProcess -Name $service.name -Executable $PythonPath `
                -Arguments $service.arguments -LogFile $service.log -WorkingDirectory $ProjectRoot
            $service.pid = $service.process.Id
            $service.started_at = (Get-Date).ToUniversalTime()
            Write-State
        }

        if ($Once) {
            # Terminé = démarré au moins une fois, et plus de processus vivant. Tester
            # seulement « rien ne tourne » sortait de la boucle avant même le premier
            # démarrage, puis avant d'avoir lu le code de sortie.
            $finished = @($script:Services | Where-Object { $null -ne $_.started_at -and $null -eq $_.process })
            if ($finished.Count -eq $script:Services.Count) { break }
        }

        Start-Sleep -Seconds 2
    }

    if ($Once) {
        foreach ($service in $script:Services) {
            if ($null -ne $service.last_exit) { $exitCode = [int]$service.last_exit }
        }
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
