# Configuration

Toute la configuration vient de l'environnement. Le fichier `.env` à la racine du dépôt
est lu au démarrage (`Settings(_env_file=".env")`) ; il est **ignoré par git** et ne doit
jamais être copié dans un ticket, un journal ou un message.

> **Un fichier par environnement.** Les identifiants de démonstration et de compte réel
> sont strictement séparés (section 15.2 du cahier). Ne mettez jamais un jeu réel dans le
> fichier de démonstration.

## Diagnostic : commencez ici

```powershell
uv run tradingagent doctor
```

La commande dit, pour chaque clé : si elle est renseignée, à quoi elle sert, et **où la
trouver** quand elle manque. Elle teste aussi la base et le répertoire des EA. Elle
**n'affiche jamais une valeur** : la sortie peut être collée dans un ticket sans risque.
Elle sort en code 1 s'il manque une clé obligatoire, 0 sinon.

C'est la première commande à lancer sur un poste neuf, et la première à relancer quand
l'agent refuse de démarrer.

## Variables

Le modèle à copier est [`.env.example`](../../.env.example).

| Variable | Rôle | Remarque |
|---|---|---|
| `MT5_LOGIN` | Numéro de compte MT5 | Entier positif. **Où :** espace client Deriv → Trader's Hub → carte du compte MT5 |
| `MT5_SERVER` | Serveur du courtier | Doit correspondre à celui du terminal. **Où :** la même carte de compte (ex. `Deriv-Demo`) |
| `MT5_PASSWORD` | Mot de passe **investisseur** (lecture seule) | **Où :** terminal MT5 → Outils → Options → Serveur → « Changez le mot de passe investisseur ». Jamais le mot de passe de l'espace client Deriv ; mot de passe principal uniquement pour l'exécution (phase 8) |
| `MT5_TERMINAL_PATH` | Chemin de `terminal64.exe` | Optionnel si un seul terminal installé |
| `EA_FILES_DIR` | Répertoire d'échange avec les deux Expert Advisors | **Pas** sous `MT5_TERMINAL_PATH` : c'est `%APPDATA%\MetaQuotes\Terminal\<instance>\MQL5\Files\TradingAgent`. Vide = l'agent tourne sans EA, état valide |
| `TELEGRAM_BOT_TOKEN` | Jeton du bot BotFather | **Où :** Telegram → `@BotFather` → `/newbot`. Affiché une seule fois |
| `TELEGRAM_ALLOWED_USER_IDS` | Identifiants Telegram autorisés, séparés par des virgules | **Où :** Telegram → `@userinfobot`. Liste blanche ; les inconnus sont refusés silencieusement |
| `DEEPSEEK_API_KEY` | Clé du fournisseur de modèle attendu sur ce projet | **Où :** `platform.deepseek.com` → API Keys. DeepSeek sert un point d'entrée **compatible Anthropic** : le même client dessert les deux fournisseurs, seul le tarif change |
| `ANTHROPIC_API_KEY` | Clé du modèle Anthropic | **Où :** `console.anthropic.com` → Settings → API Keys. Les deux clés sont **optionnelles** : sans aucune, l'agent tourne, le filtre IA n'est pas câblé et l'AI Lab reste déterministe (RM-011) |
| `AI_PROVIDER` | Quel fournisseur répond : `auto`, `deepseek`, `anthropic` ou `none` | Défaut `auto` : DeepSeek si sa clé est présente, sinon Anthropic, sinon aucun. Un choix explicite **ne retombe pas** sur l'autre fournisseur : une clé manquante est une erreur de configuration, pas une invitation à dépenser ailleurs |
| `DEEPSEEK_MODEL` | Nom du modèle DeepSeek | Défaut `deepseek-flash`. Les fournisseurs retirent des noms de modèles : c'est de la configuration, pas une constante du code |
| `ANTHROPIC_MODEL` | Nom du modèle Anthropic | Défaut `claude-sonnet-4-5` |
| `DATABASE_URL` | URI PostgreSQL (Supabase, pooler session) | **Où :** Supabase → Project Settings → Database → Connection string → *Session pooler*. Obligatoire : aucun repli sur un fichier local |
| `TEST_DATABASE_URL` | Base des tests PostgreSQL (RLS, immuabilité, concurrence) | **Où :** un **second** projet Supabase. ⚠️ Ces tests détruisent toutes les tables : le garde-fou refuse la base de l'agent, et `doctor` dit lequel des deux cas s'applique |

### Les tests et la base de test

Avec `TEST_DATABASE_URL` renseignée, `uv run pytest` exécute **les tests qui ont besoin
d'un vrai PostgreSQL** — immuabilité des tables append-only, RLS, verrous — soit 29 tests
qui migrent le schéma **une fois par module** et coûtent une dizaine de secondes. La suite
complète tourne alors en trois minutes.

```powershell
uv run pytest -q                      # 1646 tests, ~3 min, PostgreSQL inclus
```

Le reste de la suite (stockage, exécution, registre, web) reste sur SQLite, parce que
chaque test y migrerait le schéma entier — quatre secondes sur le réseau — pour vérifier du
code qui ne dépend pas du moteur. Le faire tourner sur le serveur est possible, mais
explicitement :

```powershell
$env:TRADINGAGENT_TEST_ON_SERVER = '1'
uv run pytest -q tests/registry       # ~6 min, tout le paquet sur Supabase
```

Le garde-fou mérite un mot, parce qu'il a changé. Il exigeait un nom de base finissant par
`_test` ; il vérifie maintenant la vraie question — **cette base est-elle démontrablement
différente de celle de l'agent ?** Un opérateur qui provisionne un second projet Supabase
obtient une base nommée `postgres`, comme tout le monde : la refuser ne protégeait rien et
rendait les tests PostgreSQL inexécutables. Le suffixe `_test` reste accepté, parce que
c'est un signal délibéré.

| `TEST_DATABASE_URL` | `DATABASE_URL` | Verdict |
|---|---|---|
| `..._test` | quelconque | accepté |
| autre nom, hôte différent | `db.production...` | accepté — c'est une autre base |
| même hôte **et** même nom | `db.production...` | **refusé** : ces tests détruisent tout |
| `TRADING_MODE` | `OBSERVATION`, `SIGNAL`, `PAPER` ou `DEMO` | `LIVE` ne peut pas être posé ici seul |
| `LIVE_TRADING_ENABLED` | Moitié serveur de la double condition du mode réel | Rester `false` hors phase 9 |

Variables propres à l'exploitation (lues par les scripts et le tableau de bord, pas par
l'agent) :

| Variable | Utilisée par | Rôle |
|---|---|---|
| `BACKUP_PASSPHRASE` | `scripts/backup.ps1`, `scripts/restore.ps1` | Mot de passe de chiffrement des sauvegardes. **À générer** et à garder hors du serveur |
| `TRADINGAGENT_WEB_TOKEN` | `tradingagent-web` | Protection optionnelle du tableau de bord (§43), 16 caractères minimum. Absente : écoute locale sans authentification |
| `BACKUP_DIR` | `scripts/backup.ps1` | Répertoire des sauvegardes (défaut : `<dépôt>\backups`) |
| `TRADINGAGENT_ROOT` | tous les scripts | Racine du dépôt si elle diffère du dossier des scripts |

Les deux secrets que vous générez vous-même :

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Modes de fonctionnement

| Mode | Ce qui se passe | Argent engagé |
|---|---|---|
| `OBSERVATION` | Aucun signal envoyé, aucun ordre | Non |
| `SIGNAL` | Signaux notifiés, aucun ordre | Non |
| `PAPER` | Ordres simulés | Non |
| `DEMO` | Ordres sur le compte de démonstration | Non |
| `LIVE` | Ordres réels | **Oui** |

Le passage en `LIVE` exige **les deux** conditions simultanées (RM-000) :
`TRADING_MODE=LIVE` **et** `LIVE_TRADING_ENABLED=true`. Si l'une manque, le démarrage est
refusé. `/mode` ne permet pas de passer en `LIVE`.

## Vérifier une configuration

```powershell
# L'agent refuse de démarrer si une variable manque ou est vide.
uv run tradingagent status

# La présence des variables est contrôlée par nom, jamais par valeur.
pwsh -File scripts/install_windows.ps1 -WhatIf
```

En cas d'erreur, le message liste uniquement les variables fautives, par exemple :

```
Invalid environment configuration:
  MT5_SERVER: Field required
  DATABASE_URL: must not be blank
```

## Règles

- **UTC partout.** Les horodatages sont affichés en UTC ; un datetime naïf est une erreur.
- **Aucun secret dans le dépôt.** Le contrôle est automatisé (pre-commit et CI).
- **Moindre privilège.** Mot de passe investisseur en lecture seule jusqu'à la phase 8.
- **Rotation des clés.** En cas de fuite : changer le mot de passe MT5, révoquer le jeton
  Telegram, rotation de la clé Anthropic, puis redémarrer le service.
