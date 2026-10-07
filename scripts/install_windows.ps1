#Requires -Version 5.1
<#
.SYNOPSIS
    Installation reproductible de TradingAgent sous Windows (TASK-050).

.DESCRIPTION
    Installe l'agent depuis `uv.lock` (`uv sync --frozen`), prépare le fichier
    d'environnement, vérifie la présence des secrets par leur NOM uniquement, et
    rappelle la procédure de connexion du terminal MetaTrader 5.

    Aucun secret n'est écrit, lu en clair, ni affiché par ce script : il ne fait que
    vérifier la présence et la non-vacuité des variables attendues.

.PARAMETER WhatIf
    Affiche les actions sans les exécuter (support natif de PowerShell).

.EXAMPLE
    pwsh -File scripts/install_windows.ps1 -WhatIf
    pwsh -File scripts/install_windows.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$ProjectRoot = $env:TRADINGAGENT_ROOT,

    [switch]$SkipTerminalCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RequiredVariables = @(
    'MT5_LOGIN',
    'MT5_SERVER',
    'MT5_PASSWORD',
    'TELEGRAM_BOT_TOKEN',
    'TELEGRAM_ALLOWED_USER_IDS',
    'ANTHROPIC_API_KEY',
    'DATABASE_URL'
)

function Fail {
    param([string]$Message, [int]$Code = 1)
    [Console]::Error.WriteLine("ERREUR: $Message")
    exit $Code
}

function Write-Step {
    param([string]$Message)
    Write-Output "== $Message"
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$envFile = Join-Path $ProjectRoot '.env'
$template = Join-Path $ProjectRoot '.env.example'
$lockFile = Join-Path $ProjectRoot 'uv.lock'
$dryRun = [bool]$WhatIfPreference

Write-Step "Installation TradingAgent - $ProjectRoot"
if ($dryRun) { Write-Output "MODE SIMULATION (-WhatIf) : aucune modification." }
Write-Output "  PowerShell      : $($PSVersionTable.PSVersion)"
Write-Output "  Windows         : $([System.Environment]::OSVersion.VersionString)"

# 1. Reproductibilité : le verrou doit exister, sinon l'installation dérive.
if (-not (Test-Path -LiteralPath $lockFile)) {
    Fail "uv.lock est introuvable dans $ProjectRoot : l'installation ne serait pas reproductible." 2
}
Write-Output "  Verrou          : uv.lock présent"

# 2. uv doit être disponible ; on ne télécharge rien sans le demander explicitement.
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    Write-Output "  uv              : ABSENT"
    Write-Output "    Installez-le : winget install --id astral-sh.uv -e"
    Write-Output "    Puis relancez ce script."
    Fail "uv est requis pour une installation reproductible (uv.lock)." 3
}
Write-Output "  uv              : $($uv.Source)"

# 3. Dépendances verrouillées.
if ($PSCmdlet.ShouldProcess($ProjectRoot, 'uv sync --frozen')) {
    Push-Location $ProjectRoot
    try {
        & uv sync --frozen
        if ($LASTEXITCODE -ne 0) { Fail "uv sync --frozen a échoué (code $LASTEXITCODE)." 4 }
    }
    finally { Pop-Location }
    Write-Output "  Dépendances     : installées depuis uv.lock"
}

# 4. Fichier d'environnement : jamais de secret dans le dépôt, jamais créé avec une valeur.
if (-not (Test-Path -LiteralPath $envFile)) {
    if ($PSCmdlet.ShouldProcess($envFile, 'créer à partir de .env.example (sans secret)')) {
        if (Test-Path -LiteralPath $template) {
            Copy-Item -LiteralPath $template -Destination $envFile
            Write-Output "  .env            : créé depuis .env.example, à remplir par l'opérateur"
        }
        else {
            Fail ".env est absent et .env.example est introuvable." 5
        }
    }
}
else {
    Write-Output "  .env            : présent"
}

# 5. Contrôle par NOM : on ne lit et on n'affiche jamais la valeur.
$present = @()
$missing = @()
if (Test-Path -LiteralPath $envFile) {
    $assignments = @{}
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $assignments[$Matches[1]] = $Matches[2].Trim()
        }
    }
    foreach ($name in $RequiredVariables) {
        $value = ''
        if ($assignments.ContainsKey($name)) { $value = $assignments[$name] }
        if (-not [string]::IsNullOrWhiteSpace($value)) { $present += $name } else { $missing += $name }
    }
}
else {
    $missing = $RequiredVariables
}
Write-Output "  Secrets définis : $($present.Count)/$($RequiredVariables.Count)"
if ($missing.Count -gt 0) {
    Write-Output "  À RENSEIGNER (noms seulement) : $($missing -join ', ')"
    Write-Output "    Les valeurs se saisissent dans .env, jamais dans le dépôt ni dans le script (15.1)."
}

# 6. Terminal MetaTrader 5 : présence et procédure de connexion.
$candidates = @()
if ($env:MT5_TERMINAL_PATH) { $candidates += $env:MT5_TERMINAL_PATH }
$programFiles = [System.Environment]::GetEnvironmentVariable('ProgramFiles')
$programFilesX86 = [System.Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
if ($programFiles) { $candidates += (Join-Path $programFiles 'MetaTrader 5\terminal64.exe') }
if ($programFilesX86) { $candidates += (Join-Path $programFilesX86 'MetaTrader 5\terminal64.exe') }
$terminal = $null
foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { $terminal = $candidate; break }
}
if ($terminal) {
    Write-Output "  Terminal MT5    : $terminal"
}
elseif ($SkipTerminalCheck) {
    Write-Output "  Terminal MT5    : non vérifié (-SkipTerminalCheck)"
}
else {
    Write-Output "  Terminal MT5    : introuvable"
    Write-Output "    Installez le terminal depuis votre espace client Deriv, puis renseignez MT5_TERMINAL_PATH."
}

Write-Step "Procédure de connexion du terminal (manuelle, jamais scriptée)"
Write-Output "  1. Ouvrir le terminal MT5 avec le compte de démonstration (ou le compte dédié)."
Write-Output "  2. Fichier > Ouvrir un compte > se connecter au serveur Deriv indiqué par MT5_SERVER."
Write-Output "  3. Cocher « Sauvegarder les informations du compte » : le terminal se reconnecte seul."
Write-Output "  4. Outils > Options > Serveur : cocher « Activer les cotations », décocher les mises à jour automatiques."
Write-Output "  5. Laisser « Algo Trading » désactivé tant que l'agent n'exécute pas d'ordre."

Write-Step "Étapes suivantes"
Write-Output "  1. Remplir .env (voir docs/operations/configuration.md)."
Write-Output "  2. Vérifier la base : uv run tradingagent status"
Write-Output "  3. Enregistrer le service : pwsh -File scripts/register_service.ps1 -WhatIf"
Write-Output "  4. Contrôler la santé : pwsh -File scripts/check_health.ps1"
exit 0
