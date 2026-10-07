#Requires -Version 5.1
<#
.SYNOPSIS
    Sauvegarde quotidienne chiffrée de la base TradingAgent (TASK-053, ENF-007).

.DESCRIPTION
    - PostgreSQL : `pg_dump --format=custom` de DATABASE_URL (identifiants passés par
      variables d'environnement PostgreSQL, jamais sur la ligne de commande).
    - SQLite : copie cohérente par l'API de sauvegarde de sqlite3 (Python stdlib).
    - Chiffrement : AES-256-CBC + HMAC-SHA256 (encrypt-then-MAC), clé dérivée du
      mot de passe `BACKUP_PASSPHRASE` par PBKDF2-HMAC-SHA256 (200 000 itérations).
      Format portable, sans dépendance externe (age/gpg non requis).
    - Rotation : seuls les `-Retention` fichiers les plus récents sont conservés.

    Aucun secret n'est écrit dans ce script ni affiché par lui : les valeurs viennent
    uniquement de l'environnement et les URL sont masquées avant impression.

.PARAMETER DryRun
    N'exécute ni dump ni chiffrement : affiche le plan exact. DATABASE_URL reste exigée.

.EXAMPLE
    $env:DATABASE_URL = 'postgresql://...'; $env:BACKUP_PASSPHRASE = '...'
    pwsh -File scripts/backup.ps1 -BackupDir D:\sauvegardes -Retention 14

.EXAMPLE
    pwsh -File scripts/backup.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [ValidateSet('auto', 'postgres', 'sqlite')]
    [string]$Provider = 'auto',

    [string]$DatabaseUrl = $env:DATABASE_URL,

    [string]$BackupDir = $env:BACKUP_DIR,

    [int]$Retention = 14,

    [string]$Passphrase = $env:BACKUP_PASSPHRASE,

    # Which `.env` to fall back on when the environment is silent. Resolved in the body,
    # because `$PSScriptRoot` is empty while the defaults above are evaluated.
    [string]$EnvFile = '',

    # The schema the project owns. Supabase also exposes `auth`, `storage` and `realtime`,
    # which it manages itself and which must not end up in our archive.
    [string]$Schema = 'public',

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# $PSScriptRoot is empty while parameter defaults are evaluated under Windows PowerShell 5.1.
if ([string]::IsNullOrWhiteSpace($BackupDir)) {
    $BackupDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'backups'
}

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
if ([string]::IsNullOrWhiteSpace($BackupDir)) {
    $BackupDir = Get-DotEnvValue 'BACKUP_DIR' $EnvFile
}

function Fail {
    param([string]$Message, [int]$Code = 1)
    [Console]::Error.WriteLine("ERREUR: $Message")
    exit $Code
}

function Get-RedactedUrl {
    param([string]$Url)
    # Never print the password embedded in the URL.
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

function Invoke-SqliteBackup {
    param([string]$Source, [string]$Destination)
    $code = 'import sqlite3, sys;' +
    'src = sqlite3.connect(sys.argv[1]);dst = sqlite3.connect(sys.argv[2]);' +
    'src.backup(dst);dst.close();src.close()'
    $launcher = Get-PythonLauncher
    $executable = $launcher[0]
    $arguments = @()
    if ($launcher.Length -gt 1) { $arguments += $launcher[1..($launcher.Length - 1)] }
    $arguments += @('-c', $code, $Source, $Destination)
    & $executable @arguments
    if ($LASTEXITCODE -ne 0) { Fail "la copie SQLite a échoué (code $LASTEXITCODE)." 4 }
}

function New-KeyMaterial {
    param([string]$Secret, [byte[]]$Salt)
    $derive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes(
        $Secret, $Salt, $Pbkdf2Iterations, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
    try { return $derive.GetBytes(64) } finally { $derive.Dispose() }
}

function Protect-BackupFile {
    param([string]$PlainPath, [string]$CipherPath, [string]$Secret)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $salt = New-Object byte[] $SaltSize
        $iv = New-Object byte[] $IvSize
        $rng.GetBytes($salt)
        $rng.GetBytes($iv)
    }
    finally { $rng.Dispose() }
    $material = New-KeyMaterial -Secret $Secret -Salt $salt
    $aesKey = [byte[]]$material[0..31]
    $macKey = [byte[]]$material[32..63]
    $aes = [System.Security.Cryptography.Aes]::Create()
    try {
        $aes.KeySize = 256
        $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
        $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
        $aes.Key = $aesKey
        $aes.IV = $iv
        $plain = [System.IO.File]::ReadAllBytes($PlainPath)
        $cipher = $aes.CreateEncryptor().TransformFinalBlock($plain, 0, $plain.Length)
    }
    finally { $aes.Dispose() }
    $headerSize = $Magic.Length + $SaltSize + $IvSize
    $payload = New-Object byte[] ($headerSize + $cipher.Length)
    [Array]::Copy($Magic, 0, $payload, 0, $Magic.Length)
    [Array]::Copy($salt, 0, $payload, $Magic.Length, $SaltSize)
    [Array]::Copy($iv, 0, $payload, $Magic.Length + $SaltSize, $IvSize)
    [Array]::Copy($cipher, 0, $payload, $headerSize, $cipher.Length)
    $hmac = New-Object System.Security.Cryptography.HMACSHA256
    try {
        $hmac.Key = $macKey
        $mac = $hmac.ComputeHash($payload)
    }
    finally { $hmac.Dispose() }
    $encrypted = New-Object byte[] ($payload.Length + $HmacSize)
    [Array]::Copy($payload, 0, $encrypted, 0, $payload.Length)
    [Array]::Copy($mac, 0, $encrypted, $payload.Length, $HmacSize)
    [System.IO.File]::WriteAllBytes($CipherPath, $encrypted)
}

# --- Validation -------------------------------------------------------------

if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    Fail "DATABASE_URL est absente. Définissez-la dans l'environnement (voir .env.example) ou passez -DatabaseUrl." 2
}
$resolvedProvider = Get-Provider -Url $DatabaseUrl -Requested $Provider
if (-not $DryRun -and [string]::IsNullOrWhiteSpace($Passphrase)) {
    Fail "BACKUP_PASSPHRASE est absente : le chiffrement est obligatoire (ENF-007)." 2
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$baseName = "tradingagent-$stamp.dump"
$finalPath = Join-Path $BackupDir "$baseName.enc"
$sidecarPath = "$finalPath.sha256"

if ($DryRun) {
    Write-Output "MODE SIMULATION (aucune écriture)"
    Write-Output "  Fournisseur        : $resolvedProvider"
    Write-Output "  Source             : $(Get-RedactedUrl $DatabaseUrl)"
    Write-Output "  Destination        : $finalPath"
    Write-Output "  Rétention          : $Retention fichiers"
    if ([string]::IsNullOrWhiteSpace($Passphrase)) {
        Write-Output "  Chiffrement        : IMPOSSIBLE, BACKUP_PASSPHRASE absente"
    }
    else {
        Write-Output "  Chiffrement        : AES-256-CBC + HMAC-SHA256, PBKDF2-SHA256 ($Pbkdf2Iterations itérations)"
    }
    if ($resolvedProvider -eq 'postgres') {
        Write-Output "  Commande           : pg_dump --format=custom --no-owner --no-privileges --schema=$Schema"
    }
    else {
        Write-Output "  Commande           : sqlite3 backup API via python -c"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("tradingagent-backup-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir -Force | Out-Null
$plainPath = Join-Path $workDir $baseName

try {
    if ($resolvedProvider -eq 'postgres') {
        $pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
        if ($null -eq $pgDump) {
            Fail "pg_dump introuvable dans PATH. Installez les outils client PostgreSQL (voir docs/operations/installation.md)." 3
        }
        $info = Set-PgEnvironment -Url $DatabaseUrl
        try {
            # `--schema=$Schema` and not the whole database: on Supabase the connection also
            # sees `auth`, `storage` and `realtime`, which Supabase owns and manages. Dumping
            # them produced an archive whose `--clean` restore tried to drop and recreate
            # objects belonging to another role — a backup that could not be restored into a
            # fresh Supabase project. The project's own tables all live in `public`.
            & $pgDump.Source --format=custom --no-owner --no-privileges `
                --schema=$Schema --file="$plainPath"
            if ($LASTEXITCODE -ne 0) { Fail "pg_dump a échoué (code $LASTEXITCODE)." 4 }
        }
        finally { Clear-PgEnvironment }
        Write-Output "Dump PostgreSQL terminé : $($info.Database) sur $($info.Host) (schéma $Schema)"
    }
    else {
        $sqlitePath = Get-SqlitePath -Url $DatabaseUrl
        if (-not (Test-Path -LiteralPath $sqlitePath)) {
            Fail "base SQLite introuvable : $sqlitePath" 3
        }
        Invoke-SqliteBackup -Source $sqlitePath -Destination $plainPath
        Write-Output "Copie SQLite terminée : $sqlitePath"
    }

    Protect-BackupFile -PlainPath $plainPath -CipherPath $finalPath -Secret $Passphrase
    $hash = (Get-FileHash -LiteralPath $finalPath -Algorithm SHA256).Hash
    Set-Content -LiteralPath $sidecarPath -Value "$hash  $(Split-Path -Leaf $finalPath)" -Encoding ASCII

    $size = (Get-Item -LiteralPath $finalPath).Length
    Write-Output "Sauvegarde chiffrée : $finalPath"
    Write-Output "  Taille : $size octets  SHA256 : $hash"

    $old = Get-ChildItem -LiteralPath $BackupDir -Filter 'tradingagent-*.dump.enc' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $Retention
    foreach ($item in $old) {
        Remove-Item -LiteralPath $item.FullName -Force
        Remove-Item -LiteralPath "$($item.FullName).sha256" -Force -ErrorAction SilentlyContinue
        Write-Output "Rotation : $($item.Name) supprimé"
    }
    Write-Output "Points de restauration conservés : $(@(Get-ChildItem -LiteralPath $BackupDir -Filter 'tradingagent-*.dump.enc').Count)"
    exit 0
}
finally {
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
}
