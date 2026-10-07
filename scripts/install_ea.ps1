#Requires -Version 5.1
<#
.SYNOPSIS
    Installe et compile les deux Expert Advisors Guardian dans le terminal MetaTrader 5.

.DESCRIPTION
    Copie `mt5/Experts/TradingAgent` dans le dossier de données du terminal, puis recompile
    sur place. C'est le `.ex5` **du dossier de données** que MetaTrader charge : compiler
    dans le dépôt ne suffit pas, et un `.ex5` recopié peut être en retard sur ses sources.

    Le script ne se contente pas de recopier : il exige `0 errors` de MetaEditor, parce que
    « MetaEditor a rendu 1 » n'est pas un échec (il rend toujours 1) et que la seule ligne
    qui fait foi est `Result:` dans le journal.

    Relancer ce script est sans danger : il écrase et recompile.

.PARAMETER WhatIf
    Simule : affiche ce qui serait copié et compilé, sans rien écrire.

.EXAMPLE
    pwsh -File scripts/install_ea.ps1
    pwsh -File scripts/install_ea.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$EnvFile = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvFile)) { $EnvFile = Join-Path $ProjectRoot '.env' }
$SourceDir = Join-Path $ProjectRoot 'mt5\Experts\TradingAgent'
$BuildDir = Join-Path $ProjectRoot 'mt5\build'


function Get-DotEnvValue {
    param([string]$Name, [string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $found = ''
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $parts = $trimmed.Split('=', 2)
        if ($parts[0].Trim() -eq $Name) { $found = $parts[1].Trim().Trim('"').Trim("'") }
    }
    return $found
}

$bridge = Get-DotEnvValue 'EA_FILES_DIR' $EnvFile
if ([string]::IsNullOrWhiteSpace($bridge)) {
    Write-Error "EA_FILES_DIR est vide dans $EnvFile. Renseignez-la : sans elle, l'agent tourne sans EA (etat valide, mais aucun garde-fou)."
    exit 2
}

# EA_FILES_DIR vaut <donnees>\MQL5\Files\TradingAgent : trois niveaux au-dessus se trouve
# la racine du dossier de donnees, pas deux. (`Split-Path -Parent` deux fois donnait
# <donnees>\MQL5, et le script installait dans <donnees>\MQL5\MQL5\Experts.)
$dataFolder = [System.IO.Path]::GetFullPath((Join-Path $bridge '..\..\..'))
if (-not (Test-Path -LiteralPath (Join-Path $dataFolder 'MQL5'))) {
    Write-Error "EA_FILES_DIR ne ressemble pas a <donnees>\MQL5\Files\TradingAgent : $bridge"
    exit 2
}
$target = Join-Path $dataFolder 'MQL5\Experts\TradingAgent'

$terminal = Get-DotEnvValue 'MT5_TERMINAL_PATH' $EnvFile
if ([string]::IsNullOrWhiteSpace($terminal)) {
    $editor = Get-Command MetaEditor64.exe -ErrorAction SilentlyContinue
    if ($null -eq $editor) {
        Write-Error "MT5_TERMINAL_PATH est vide et MetaEditor64.exe est introuvable : impossible de compiler."
        exit 2
    }
    $editorPath = $editor.Source
} else {
    $editorPath = Join-Path (Split-Path -Parent $terminal) 'MetaEditor64.exe'
    if (-not (Test-Path -LiteralPath $editorPath)) {
        Write-Error "MetaEditor64.exe introuvable a cote de $terminal"
        exit 2
    }
}

Write-Output "Sources            : $SourceDir"
Write-Output "Dossier de donnees : $dataFolder"
Write-Output "Destination        : $target"
Write-Output "Compilateur        : $editorPath"

if ($WhatIfPreference) {
    Write-Output ''
    Write-Output 'MODE SIMULATION (aucune ecriture)'
    Get-ChildItem -LiteralPath $SourceDir | ForEach-Object { Write-Output "  copierait $($_.Name)" }
    Get-ChildItem -LiteralPath $SourceDir -Filter *.mq5 | ForEach-Object { Write-Output "  compilerait $($_.Name)" }
    exit 0
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
Copy-Item -Path (Join-Path $SourceDir '*') -Destination $target -Force

$failures = @()
foreach ($file in Get-ChildItem -LiteralPath $target -Filter *.mq5) {
    $symbol = $file.BaseName
    $log = Join-Path $BuildDir "$symbol.log"
    if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force }
    # MetaEditor rend toujours 1 : la ligne `Result:` est la seule qui fasse foi.
    & $editorPath "/compile:$($file.FullName)" "/log:$log" | Out-Null
    if (-not (Test-Path -LiteralPath $log)) {
        $failures += "$symbol : MetaEditor n'a produit aucun journal"
        continue
    }
    $result = Get-Content -LiteralPath $log -Encoding Unicode |
        Select-String -Pattern 'Result:' | Select-Object -Last 1
    if ($null -eq $result) {
        $failures += "$symbol : pas de ligne Result dans le journal"
        continue
    }
    Write-Output "  $symbol : $($result.Line.Trim())"
    if ($result.Line -notmatch '0 errors') { $failures += "$symbol : $($result.Line.Trim())" }
}

if ($failures.Count -gt 0) {
    Write-Error ("compilation en echec : " + ($failures -join ' | '))
    exit 4
}

Write-Output ''
Write-Output 'Expert Advisors installes et compiles.'
Write-Output 'Il reste, dans le terminal :'
Write-Output '  1. ouvrir un graphique BTCUSD et un graphique XAUUSD'
Write-Output '  2. glisser BTCUSD_Guardian sur BTCUSD, XAUUSD_Guardian sur XAUUSD'
Write-Output '  3. activer Algo Trading (le bouton de la barre d''outils)'
Write-Output '  4. verifier : uv run tradingagent-web, page Systeme, ou le rapport dans'
Write-Output "     $bridge\reports"
exit 0
