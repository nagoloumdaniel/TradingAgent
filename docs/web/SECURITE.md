# Sécurité de l'accès au tableau de bord (§43)

> Le tableau de bord est **en lecture seule** (§34) et écoute par défaut sur `127.0.0.1` :
> seul un processus de la machine peut l'atteindre. `TRADINGAGENT_WEB_TOKEN` ajoute une
> seconde barrière — un jeton d'accès — pour le cas où le port devient joignable au-delà de
> la boucle locale (machine partagée, tunnel, reverse proxy).

## Le jeton

| | |
|---|---|
| Variable | `TRADINGAGENT_WEB_TOKEN` |
| Valeur | jamais dans le dépôt, jamais dans un gabarit, jamais dans une URL, jamais dans un journal |
| Longueur | 16 caractères minimum recommandés ; en dessous, un avertissement est journalisé **sans la valeur** |
| Vide ou absente | protection **désactivée** : le comportement d'origine est strictement conservé |
| Rotation | changer la variable invalide instantanément tous les cookies de session émis |

Un jeton d'exemple, à ne pas réutiliser :

```bash
# Générer une valeur aléatoire (PowerShell)
$env:TRADINGAGENT_WEB_TOKEN = -join ((48..57) + (97..122) | Get-Random -Count 48 | % {[char]$_})
# ou sous un shell POSIX
export TRADINGAGENT_WEB_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

Le jeton est lu **une fois**, à la construction de l'application
(`tradingagent.web.auth.configured_token`). `create_app(..., access_token=...)` permet à un
test ou à un script d'embarquement de le fournir explicitement ; il n'est jamais codé en dur
dans le paquet.

## Portée

Quand la variable est renseignée, **tout** exige le jeton :

- les 8 pages (`/`, `/positions`, `/trades`, `/trades/{id}`, `/strategies`, `/ai-lab`,
  `/risk`, `/system`, `/reports`) ;
- le flux SSE `/events` (positions et alertes en direct) ;
- les exports (`/export/trades.csv`, `/export/trades.json`, `/export/performance.json`,
  `/export/equity.svg`, `/export/reports/{id}.txt`) ;
- la sonde `/healthz`, qui expose l'état de la base et l'arrêt de trading.

Une requête sans justificatif valide reçoit **401** avec un document autonome : pas de
navigation, pas de chiffre, pas d'écho de ce qui a été présenté, pas même le nom du
dashboard. Le corps de la réponse est identique que l'en-tête soit absent, malformé ou
faux — un attaquant n'apprend rien de la forme de son erreur.

## Les deux justificatifs acceptés

1. **`Authorization: Bearer <jeton>`** — le jeton exact, comparé en temps constant
   (`hmac.compare_digest`), insensible à la casse du schéma `Bearer`. C'est le transport des
   scripts, de `curl` et d'un superviseur :

   ```bash
   curl -H "Authorization: Bearer $TRADINGAGENT_WEB_TOKEN" http://127.0.0.1:8787/
   curl -H "Authorization: Bearer $TRADINGAGENT_WEB_TOKEN" http://127.0.0.1:8787/healthz
   ```

2. **Cookie de session `tradingagent_session`** — pour les deux flux qu'un en-tête ne peut
   pas couvrir : une navigation de navigateur et `EventSource` (qui n'accepte aucun en-tête).
   Sa valeur n'est **pas** le jeton mais `HMAC-SHA256(jeton, "tradingagent-web-session-v1")`,
   un justificatif *dérivé* : le cookie ne révèle pas le jeton, et le faire tourner invalide
   toutes les sessions.

   Le cookie se fabrique une fois, sur une requête **déjà autorisée** — en pratique avec un
   justificatif Bearer, pour que le jeton ne passe jamais par une URL :

   ```bash
   curl -c cookies.txt -H "Authorization: Bearer $TRADINGAGENT_WEB_TOKEN" \
        http://127.0.0.1:8787/session      # 303 vers /, avec Set-Cookie
   curl -b cookies.txt http://127.0.0.1:8787/positions
   ```

   Attributs posés : `HttpOnly`, `SameSite=Strict`, `Path=/`. `Secure` n'est **pas** posé,
   faute de TLS (voir ci-dessous) : l'activer priverait le cookie sur `http://127.0.0.1`.

Rien d'autre n'est accepté : pas de paramètre de requête, pas d'en-tête personnalisé, pas de
route de connexion en POST. La table de routage reste **GET uniquement**, protection
activée ou non — le jeton garde la porte, il n'ajoute aucun verbe d'écriture derrière.

## Ce que cela ne protège pas

- **Pas de TLS.** Le jeton et le cookie circulent en clair sur le réseau. Sur un lien non
  fiable, un tiers qui capture le trafic lit le jeton. Le tableau de bord ne fait pas de
  HTTPS, ne gère ni certificat ni redirection.
- **Exposé au-delà de la boucle locale, il faut un reverse proxy** (nginx, Caddy, Traefik)
  qui termine TLS, présente le certificat et transmet la requête. Deux options alors :
  laisser le jeton dans l'en-tête `Authorization` (transmis par le proxy), ou faire
  l'authentification au niveau du proxy et garder `127.0.0.1:8787` en amont, fermé.
- **Pas de gestion d'utilisateurs.** Un seul secret partagé : pas de comptes, pas de rôles,
  pas de révocation individuelle, pas de journal d'accès nominatif. Changer le jeton
  déconnecte tout le monde, y compris soi-même.
- **Pas de limitation de débit ni de verrouillage** après N échecs. Un jeton long et
  aléatoire est la seule défense contre une énumération.
- **Pas de protection contre un accès local.** Un processus de la machine, ou la personne
  devant l'écran, peut lire le fichier `.env` ou la mémoire du processus.
- **Le cookie est un justificatif porteur** : qui le vole peut l'utiliser jusqu'à la
  rotation du jeton. `SameSite=Strict` limite l'origine du vol, pas une compromission de la
  machine.
- **L'écoute par défaut reste `127.0.0.1`.** `--host 0.0.0.0` est une décision d'exposition
  que le jeton atténue sans la rendre sûre ; sans TLS, elle ne devrait pas être prise.

## Vérifications automatiques

`tests/web/test_auth.py` (47 cas) prouve, entre autres :

- sans la variable, toutes les pages répondent 200 comme avant, et `/session` n'existe pas ;
- une variable vide ou blanche **ne** verrouille **pas** le dashboard ;
- avec la variable, chaque point d'entrée — pages, replay, SSE, exports, `/healthz` —
  répond **401** sans jeton et **200** avec ;
- le corps du 401 ne contient ni le titre du dashboard, ni un libellé de navigation, ni une
  seule figure du jeu de données ;
- le jeton n'apparaît ni dans le corps des réponses, ni dans le cookie, ni dans un
  enregistrement de journal, ni dans un en-tête `Location` ;
- un jeton faux, un schéma absent, un en-tête hostile non-ASCII sont refusés proprement
  (401, jamais 500) ;
- un cookie forgé est refusé, et la rotation du jeton invalide le cookie émis ;
- la table de routage reste `GET` uniquement, protection activée.

Le `TestClient` des autres tests est protégé par une fixture `autouse` de
`tests/web/conftest.py` qui retire la variable de l'environnement : un jeton présent dans le
shell du développeur ne transforme jamais une suite verte en suite rouge.
