#Requires -Version 5.1
<#
.SYNOPSIS
    Installe l'environnement de développement (poste opérateur, TASK-050).

.DESCRIPTION
    `uv sync --frozen` depuis uv.lock, puis hooks pre-commit. Aucune dépendance n'est
    ajoutée au projet : ce script n'installe que ce que `pyproject.toml` déclare.

.EXAMPLE
    pwsh -File scripts/install_deps.ps1 -WhatIf
    pwsh -File scripts/install_deps.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
param(
    [string]$ProjectRoot = $env:TRADINGAGENT_ROOT
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message, [int]$Code = 1)
    [Console]::Error.WriteLine("ERREUR: $Message")
    exit $Code
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    Fail "uv est requis : winget install --id astral-sh.uv -e" 3
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'uv.lock'))) {
    Fail "uv.lock est introuvable : installation non reproductible." 2
}

if ($PSCmdlet.ShouldProcess($ProjectRoot, 'uv sync --frozen (dépendances verrouillées)')) {
    Push-Location $ProjectRoot
    try {
        & uv sync --frozen
        if ($LASTEXITCODE -ne 0) { Fail "uv sync --frozen a échoué (code $LASTEXITCODE)." 4 }
        Write-Output "Dépendances installées depuis uv.lock."
        & uv run --frozen python --version
        & uv run --frozen ruff --version
        & uv run --frozen mypy --version
    }
    finally { Pop-Location }
}

if ($PSCmdlet.ShouldProcess($ProjectRoot, 'pre-commit install')) {
    Push-Location $ProjectRoot
    try {
        & uv run --frozen pre-commit install
        if ($LASTEXITCODE -ne 0) { Fail "pre-commit install a échoué (code $LASTEXITCODE)." 4 }
        Write-Output "Hooks pre-commit installés."
    }
    finally { Pop-Location }
}

Write-Output "Vérification complète : uv run pytest -q ; uv run ruff check . ; uv run mypy"
exit 0
