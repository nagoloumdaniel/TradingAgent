#Requires -Version 5.1
<#
.SYNOPSIS
    Restauration d'une sauvegarde TradingAgent chiffrée (TASK-053, ENF-007).

.DESCRIPTION
    Déchiffre un fichier produit par scripts/backup.ps1 (AES-256-CBC + HMAC-SHA256,
    clé dérivée de BACKUP_PASSPHRASE par PBKDF2-HMAC-SHA256), vérifie le HMAC avant
    toute écriture, puis restaure :
    - PostgreSQL : `pg_restore --clean --if-exists` (identifiants par variables
      d'environnement PostgreSQL) ;
    - SQLite : API de sauvegarde sqlite3 (Python stdlib) vers le fichier cible.

    La restauration est destructive sur une base existante : `-Force` est exigé.

.PARAMETER DryRun
    Vérifie le fichier, le HMAC et affiche le plan sans restaurer.

.EXAMPLE
    pwsh -File scripts/restore.ps1 -BackupFile D:\sauvegardes\tradingagent-20261007T023422Z.dump.enc -Force
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,

    [string]$DatabaseUrl = $env:DATABASE_URL,

    [ValidateSet('auto', 'postgres', 'sqlite')]
    [string]$Provider = 'auto',

    [string]$Passphrase = $env:BACKUP_PASSPHRASE,

    # Which `.env` to fall back on when the environment is silent. Resolved in the body,
    # because `$PSScriptRoot` is empty while the defaults above are evaluated.
    [string]$EnvFile = '',

    # The schema the project owns; mirrors the backup's scope. Supabase also exposes `auth`,
    # `storage` and `realtime`, which it manages itself and which `--clean` must not touch.
    [string]$Schema = 'public',

    [switch]$Force,

    [switch]$Migrate,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Magic = [System.Text.Encoding]::ASCII.GetBytes('TABK1')
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SaltSize = 16
$IvSize = 16
$HmacSize = 32
$Pbkdf2Iterations = 200000

# `.env` is where every other setting of this project lives, but the parameters above could
# only read the process environment: `$PSScriptRoot` is empty while they are evaluated. Now
# that the root is known, the file gets its say — and only when the environment was silent,
# so exporting a variable still overrides the file, as everywhere else in the project.
function Get-DotEnvValue {
    param([string]$Name, [string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $found = ''
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $parts = $trimmed.Split('=', 2)
        if ($parts[0].Trim() -eq $Name) {
            $found = $parts[1].Trim().Trim('"').Trim("'")  # last one wins, like dotenv
        }
    }
    return $found
}

$EnvFile = if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    Join-Path $ProjectRoot '.env'
} else { $EnvFile }
if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    $DatabaseUrl = Get-DotEnvValue 'DATABASE_URL' $EnvFile
}
if ([string]::IsNullOrWhiteSpace($Passphrase)) {
    $Passphrase = Get-DotEnvValue 'BACKUP_PASSPHRASE' $EnvFile
}

function Fail {
    param([string]$Message, [int]$Code = 1)
    [Console]::Error.WriteLine("ERREUR: $Message")
    exit $Code
}

function Get-RedactedUrl {
    param([string]$Url)
    return ($Url -replace '(?i)(://[^/@:]*):[^/@]*@', '$1:***@')
}

function Get-Provider {
    param([string]$Url, [string]$Requested)
    if ($Requested -ne 'auto') { return $Requested }
    if ($Url -match '^(?i)postgres(ql)?(\+[a-z0-9]+)?://') { return 'postgres' }
    if ($Url -match '^(?i)sqlite(\+[a-z0-9]+)?://') { return 'sqlite' }
    Fail "schéma d'URL non reconnu : $(Get-RedactedUrl $Url). Attendu postgresql:// ou sqlite:///" 2
    return 'postgres'
}

function Get-SqlitePath {
    param([string]$Url)
    $path = $Url -replace '^(?i)sqlite(\+[a-z0-9]+)?://', ''
    if ($path -match '^/([A-Za-z]:/.*)$') { $path = $Matches[1] }
    elseif ($path -match '^/(.+)$') { $path = $Matches[1] }
    return [System.Uri]::UnescapeDataString($path)
}

function Set-PgEnvironment {
    param([string]$Url)
    $normalized = $Url -replace '^(?i)postgresql\+[a-z0-9]+://', 'postgresql://'
    $normalized = $normalized -replace '^(?i)postgres://', 'postgresql://'
    $uri = [System.Uri]$normalized
    $script:PgPrevious = @{}
    foreach ($name in @('PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD', 'PGDATABASE', 'PGSSLMODE')) {
        $script:PgPrevious[$name] = [System.Environment]::GetEnvironmentVariable($name)
    }
    $env:PGHOST = $uri.Host
    if ($uri.IsDefaultPort) { $env:PGPORT = '5432' } else { $env:PGPORT = [string]$uri.Port }
    $user = ''
    $password = ''
    if ($uri.UserInfo) {
        $pieces = $uri.UserInfo.Split(':', 2)
        $user = [System.Uri]::UnescapeDataString($pieces[0])
        if ($pieces.Length -gt 1) { $password = [System.Uri]::UnescapeDataString($pieces[1]) }
    }
    $env:PGUSER = $user
    $env:PGPASSWORD = $password
    $env:PGDATABASE = [System.Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart('/'))
    if ($uri.Query) {
        foreach ($pair in $uri.Query.TrimStart('?').Split('&')) {
            $kv = $pair.Split('=', 2)
            if ($kv.Length -eq 2 -and $kv[0] -ieq 'sslmode') { $env:PGSSLMODE = $kv[1] }
        }
    }
    return @{ Host = $uri.Host; Database = $env:PGDATABASE; User = $user }
}

function Clear-PgEnvironment {
    foreach ($name in $script:PgPrevious.Keys) {
        if ($null -eq $script:PgPrevious[$name]) {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item "Env:$name" $script:PgPrevious[$name]
        }
    }
}

function Get-PythonLauncher {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $uv) { return @('uv', 'run', '--frozen', '--project', $ProjectRoot, 'python') }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) { return @('python') }
    Fail "ni 'uv' ni 'python' n'est disponible dans PATH." 3
    return @()
}

function Invoke-Python {
    param([string]$Code, [string[]]$Arguments)
    $launcher = Get-PythonLauncher
    $executable = $launcher[0]
    $argumentList = @()
    if ($launcher.Length -gt 1) { $argumentList += $launcher[1..($launcher.Length - 1)] }
    $argumentList += @('-c', $Code)
    $argumentList += $Arguments
    & $executable @argumentList
    if ($LASTEXITCODE -ne 0) { Fail "l'appel Python a échoué (code $LASTEXITCODE)." 4 }
}

function New-KeyMaterial {
    param([string]$Secret, [byte[]]$Salt)
    $derive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes(
        $Secret, $Salt, $Pbkdf2Iterations, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
    try { return $derive.GetBytes(64) } finally { $derive.Dispose() }
}

function Compare-Bytes {
    param([byte[]]$Left, [byte[]]$Right)
    if ($Left.Length -ne $Right.Length) { return $false }
    $difference = 0
    for ($index = 0; $index -lt $Left.Length; $index++) {
        $difference = $difference -bor ($Left[$index] -bxor $Right[$index])
    }
    return ($difference -eq 0)
}

function Unprotect-BackupFile {
    param([string]$CipherPath, [string]$PlainPath, [string]$Secret)
    $bytes = [System.IO.File]::ReadAllBytes($CipherPath)
    $headerSize = $Magic.Length + $SaltSize + $IvSize
    $minimum = $headerSize + $HmacSize + 16
    if ($bytes.Length -lt $minimum) { Fail "fichier de sauvegarde tronqué ou illisible : $CipherPath" 5 }
    for ($index = 0; $index -lt $Magic.Length; $index++) {
        if ($bytes[$index] -ne $Magic[$index]) {
            Fail "ce fichier n'est pas une sauvegarde TradingAgent (en-tête invalide) : $CipherPath" 5
        }
    }
    $salt = [byte[]]$bytes[$Magic.Length..($Magic.Length + $SaltSize - 1)]
    $ivOffset = $Magic.Length + $SaltSize
    $iv = [byte[]]$bytes[$ivOffset..($ivOffset + $IvSize - 1)]
    $payloadLength = $bytes.Length - $HmacSize
    $material = New-KeyMaterial -Secret $Secret -Salt $salt
    $aesKey = [byte[]]$material[0..31]
    $macKey = [byte[]]$material[32..63]
    $hmac = New-Object System.Security.Cryptography.HMACSHA256
    try {
        $hmac.Key = $macKey
        $expected = $hmac.ComputeHash($bytes, 0, $payloadLength)
    }
    finally { $hmac.Dispose() }
    $stored = [byte[]]$bytes[$payloadLength..($bytes.Length - 1)]
    if (-not (Compare-Bytes -Left $expected -Right $stored)) {
        Fail "authentification HMAC échouée : mot de passe incorrect ou fichier altéré." 6
    }
    $cipherLength = $payloadLength - $headerSize
    $aes = [System.Security.Cryptography.Aes]::Create()
    try {
        $aes.KeySize = 256
        $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
        $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
        $aes.Key = $aesKey
        $aes.IV = $iv
        $plain = $aes.CreateDecryptor().TransformFinalBlock($bytes, $headerSize, $cipherLength)
    }
    catch [System.Security.Cryptography.CryptographicException] {
        Fail "déchiffrement impossible : fichier corrompu." 6
    }
    finally { $aes.Dispose() }
    [System.IO.File]::WriteAllBytes($PlainPath, $plain)
}

function Test-SqliteNonEmpty {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    if ((Get-Item -LiteralPath $Path).Length -eq 0) { return $false }
    $code = 'import sqlite3, sys;' +
    'connection = sqlite3.connect(sys.argv[1]);' +
    'count = connection.execute(''select count(*) from sqlite_master'').fetchone()[0];' +
    'connection.close();print(count)'
    $launcher = Get-PythonLauncher
    $executable = $launcher[0]
    $argumentList = @()
    if ($launcher.Length -gt 1) { $argumentList += $launcher[1..($launcher.Length - 1)] }
    $argumentList += @('-c', $code, $Path)
    $result = & $executable @argumentList
    if ($LASTEXITCODE -ne 0) { return $true }
    return ([int]($result | Select-Object -Last 1) -gt 0)
}

# --- Validation -------------------------------------------------------------

if (-not (Test-Path -LiteralPath $BackupFile)) {
    Fail "fichier de sauvegarde introuvable : $BackupFile" 2
}
if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    Fail "DATABASE_URL est absente. Définissez-la dans l'environnement (voir .env.example) ou passez -DatabaseUrl." 2
}
if (-not $DryRun -and [string]::IsNullOrWhiteSpace($Passphrase)) {
    Fail "BACKUP_PASSPHRASE est absente : impossible de déchiffrer la sauvegarde." 2
}
$resolvedProvider = Get-Provider -Url $DatabaseUrl -Requested $Provider

if ($DryRun) {
    Write-Output "MODE SIMULATION (aucune écriture)"
    Write-Output "  Sauvegarde         : $BackupFile"
    Write-Output "  Cible              : $(Get-RedactedUrl $DatabaseUrl)"
    Write-Output "  Fournisseur        : $resolvedProvider"
    if ([string]::IsNullOrWhiteSpace($Passphrase)) {
        Write-Output "  Déchiffrement      : IMPOSSIBLE, BACKUP_PASSPHRASE absente"
    }
    else {
        Write-Output "  Déchiffrement      : HMAC-SHA256 vérifié avant écriture, AES-256-CBC"
    }
    if ($resolvedProvider -eq 'postgres') {
        Write-Output "  Commande           : pg_restore --clean --if-exists --no-owner --no-privileges --schema=$Schema"
        Write-Output "  -Force requis      : oui (restauration destructive)"
    }
    else {
        Write-Output "  Commande           : API de sauvegarde sqlite3 vers $(Get-SqlitePath -Url $DatabaseUrl)"
        Write-Output "  -Force requis      : oui si la base cible contient déjà des tables"
    }
    exit 0
}

if ($resolvedProvider -eq 'postgres' -and -not $Force) {
    Fail "la restauration PostgreSQL est destructive (--clean --if-exists) : relancez avec -Force." 7
}
if ($resolvedProvider -eq 'sqlite') {
    $targetPath = Get-SqlitePath -Url $DatabaseUrl
    if ((Test-SqliteNonEmpty -Path $targetPath) -and -not $Force) {
        Fail "la base cible contient déjà des tables : relancez avec -Force pour la remplacer." 7
    }
}

$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("tradingagent-restore-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir -Force | Out-Null
$plainPath = Join-Path $workDir 'restored.dump'

try {
    Write-Output "Vérification du HMAC et déchiffrement..."
    Unprotect-BackupFile -CipherPath $BackupFile -PlainPath $plainPath -Secret $Passphrase
    $plainSize = (Get-Item -LiteralPath $plainPath).Length
    Write-Output "Sauvegarde authentifiée, $plainSize octets déchiffrés."

    if ($resolvedProvider -eq 'postgres') {
        $pgRestore = Get-Command pg_restore -ErrorAction SilentlyContinue
        if ($null -eq $pgRestore) {
            Fail "pg_restore introuvable dans PATH. Installez les outils client PostgreSQL (voir docs/operations/installation.md)." 3
        }
        $info = Set-PgEnvironment -Url $DatabaseUrl
        try {
            # Scoped like the dump: `--clean` must never drop an object Supabase owns. An
            # archive taken before that scoping existed still carries `auth`, `storage` and
            # `realtime`, and this is what keeps it away from managed schemas.
            & $pgRestore.Source --clean --if-exists --no-owner --no-privileges `
                --schema=$Schema --dbname="$($info.Database)" "$plainPath"
            if ($LASTEXITCODE -ne 0) { Fail "pg_restore a échoué (code $LASTEXITCODE)." 4 }
        }
        finally { Clear-PgEnvironment }
        Write-Output "Restauration PostgreSQL terminée : $($info.Database) sur $($info.Host) (schéma $Schema)"
    }
    else {
        $targetPath = Get-SqlitePath -Url $DatabaseUrl
        $targetDir = Split-Path -Parent $targetPath
        if ($targetDir -and -not (Test-Path -LiteralPath $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        }
        $code = 'import sqlite3, sys;' +
        'src = sqlite3.connect(sys.argv[1]);dst = sqlite3.connect(sys.argv[2]);' +
        'src.backup(dst);dst.close();src.close()'
        Invoke-Python -Code $code -Arguments @($plainPath, $targetPath)
        Write-Output "Restauration SQLite terminée : $targetPath"
    }

    if ($Migrate) {
        Write-Output "Application des migrations Alembic (head)..."
        $migrateCode = 'import sys;from tradingagent.storage.migrate import upgrade;upgrade(sys.argv[1])'
        Invoke-Python -Code $migrateCode -Arguments @($DatabaseUrl)
        Write-Output "Migrations appliquées."
    }

    Write-Output "Restauration terminée. Redémarrez l'agent : pwsh -File scripts/register_service.ps1 -Restart"
    exit 0
}
finally {
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
}
