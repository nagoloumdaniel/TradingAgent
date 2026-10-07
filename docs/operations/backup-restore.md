# Sauvegarde et restauration (TASK-053, ENF-007)

## Ce qui est protégé

La base de données (`DATABASE_URL`) contient les signaux, positions, décisions de risque,
rapports et la piste d'audit : c'est l'actif à sauvegarder. Le code vit dans git. Les
journaux ne sont pas sauvegardés (ils sont volumineux et reproductibles).

## Format et chiffrement

| Propriété | Valeur |
|---|---|
| Chiffrement | AES-256-CBC + HMAC-SHA256 (encrypt-then-MAC) |
| Dérivation de clé | PBKDF2-HMAC-SHA256, 200 000 itérations, sel aléatoire par fichier |
| Portabilité | PowerShell/.NET uniquement : ni `age`, ni `gpg`, ni DPAPI n'est requis |
| Nom de fichier | `tradingagent-<horodatage UTC>.dump.enc` |
| Intégrité | `HMAC` vérifié **avant** toute écriture + sidecar `.sha256` |

Le HMAC est vérifié avant le déchiffrement : un fichier altéré ou un mot de passe erroné
est refusé, jamais restauré à moitié.

> **Le mot de passe est la sauvegarde.** `BACKUP_PASSPHRASE` doit être conservé **hors du
> serveur**, dans le gestionnaire de mots de passe de l'opérateur. Sans lui, aucun fichier
> `.enc` n'est récupérable. Ne le stockez jamais à côté des sauvegardes.

## Sauvegarde manuelle

```powershell
# Variables (jamais dans un script ni dans le dépôt).
$env:DATABASE_URL = '<URI PostgreSQL>'      # ou déjà présente dans l'environnement
$env:BACKUP_PASSPHRASE = '<mot de passe robuste>'

pwsh -File scripts/backup.ps1 -DryRun
pwsh -File scripts/backup.ps1 -BackupDir D:\sauvegardes -Retention 14
```

Le script :

1. refuse de démarrer si `DATABASE_URL` ou (hors simulation) `BACKUP_PASSPHRASE` manque ;
2. pour PostgreSQL : `pg_dump --format=custom --no-owner --no-privileges`, identifiants
   transmis par variables `PG*` — **jamais** sur la ligne de commande ;
3. pour SQLite : copie cohérente par l'API de sauvegarde de `sqlite3` ;
4. chiffre, écrit le sidecar `.sha256`, puis applique la **rotation** : seuls les
   `-Retention` fichiers les plus récents sont conservés (14 par défaut).

`-Provider auto` déduit le type de base de l'URL ; `sqlite:///` sert aux tests et aux
répétitions locales.

## Sauvegarde quotidienne planifiée

Créer la tâche une fois (terminal administrateur) :

```powershell
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -File D:\TradingAgent\scripts\backup.ps1'
$trigger = New-ScheduledTaskTrigger -Daily -At 02:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'TradingAgentBackup' -Action $action `
  -Trigger $trigger -Settings $settings -RunLevel Highest
```

`BACKUP_PASSPHRASE` doit être accessible à cette tâche (variable d'environnement
utilisateur ou machine). Vérifier le lendemain qu'un nouveau fichier est apparu et que la
sortie de la tâche ne contient aucune erreur.

## Restauration

> **Destructif sur une base existante.** `-Force` est obligatoire en PostgreSQL (le
> déchiffrement s'accompagne de `pg_restore --clean --if-exists`) et dès que la base
> SQLite contient déjà des tables.

```powershell
# 1. Choisir le point de restauration (le plus récent, ou un plus ancien).
Get-ChildItem D:\sauvegardes\tradingagent-*.dump.enc | Sort-Object LastWriteTime -Descending

# 2. Simulation : vérifie le fichier et le mot de passe sans rien écrire.
$env:BACKUP_PASSPHRASE = '<mot de passe robuste>'
pwsh -File scripts/restore.ps1 -BackupFile D:\sauvegardes\tradingagent-<horodatage>.dump.enc -DryRun

# 3. Restauration réelle. -Migrate applique ensuite alembic upgrade head.
pwsh -File scripts/restore.ps1 -BackupFile D:\sauvegardes\tradingagent-<horodatage>.dump.enc -Force -Migrate

# 4. Vérifier puis redémarrer l'agent.
uv run tradingagent status
pwsh -File scripts/register_service.ps1 -Restart
pwsh -File scripts/check_health.ps1 -CheckTask -CheckDatabase
```

Codes d'erreur : `2` paramètre manquant (fichier ou variable), `3` outil absent
(`pg_restore`/`pg_dump`), `4` échec de l'outil, `5` fichier illisible ou en-tête invalide,
`6` HMAC invalide (mot de passe faux ou fichier altéré), `7` `-Force` requis.

## Procédure de restauration sur une machine vierge

**État : à exécuter par l'opérateur.** C'est le seul critère qui compte pour TASK-053, et
il n'a pas encore été exécuté sur une machine de production. Ce qui est prouvé à ce jour :
un aller-retour sauvegarde → restauration sur une base SQLite jetable, exécuté par
`tests/test_backup_scripts.py` (les refus — variable manquante, mot de passe faux, fichier
altéré — sont vérifiés par les mêmes tests).

Procédure exacte à dérouler et à consigner :

1. Provisionner une machine Windows vierge et y installer uv et Git.
2. `git clone` puis `pwsh -File scripts/install_windows.ps1`.
3. Renseigner `.env` avec les secrets de l'environnement **démo** d'abord.
4. Copier le fichier `.enc` choisi et son `.sha256` sur la machine.
5. `pwsh -File scripts/restore.ps1 -BackupFile <fichier> -Force -Migrate`.
6. `uv run tradingagent status` puis `pwsh -File scripts/check_health.ps1 -CheckTask -CheckDatabase`.
7. `pwsh -File scripts/register_service.ps1`, provoquer un arrêt brutal, vérifier la reprise.
8. Consigner la date, la personne, le fichier restauré et le résultat.

## Vérifications automatiques

```powershell
uv run pytest -q tests/test_backup_scripts.py
```

Ces tests exécutent réellement les scripts : refus d'une variable manquante, refus d'un
mot de passe faux, refus d'écraser sans `-Force`, et aller-retour complet sur SQLite.
