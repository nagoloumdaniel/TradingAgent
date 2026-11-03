# Sécurité de l'accès au tableau de bord (§43)

> Le tableau de bord est **en lecture seule** (§34) et n'écoute que sur `127.0.0.1` : seule la
> machine de l'opérateur l'atteint. Un **code à 4 chiffres**, saisi dans le navigateur, le
> protège d'un autre onglet, d'un autre utilisateur de la machine ou d'une personne devant
> l'écran. Il ne protège pas d'un réseau (voir « Ce que cela ne protège pas »).

## Le code

| | |
|---|---|
| Choix | à la **première visite**, la page `/login` demande de choisir le code (saisi deux fois) |
| Stockage | haché (scrypt + sel) dans `data/web_pin.json`, ignoré par git. Aucune variable d'environnement, aucun secret dans le dépôt |
| Format | exactement 4 chiffres ASCII `0-9` |
| Session | cookie valable **30 jours** ; le code est redemandé ensuite |
| Déconnexion | bouton « Déconnexion » dans la barre du haut : le cookie est effacé, le code est redemandé |
| Changer le code | supprimer `data/web_pin.json` : la prochaine visite propose d'en choisir un nouveau |

L'ancien mécanisme `TRADINGAGENT_WEB_TOKEN` (jeton `Authorization: Bearer`) est **supprimé** :
plus de variable, plus de champ dans `Settings`, plus d'entrée dans `config doctor`. Une
valeur résiduelle dans un `.env` est ignorée.

## Le cookie de session

`tradingagent_session` = `<expiration>.<HMAC-SHA256(clé, "tradingagent-web-session-v2|<expiration>")>`.

- Il porte sa propre expiration, signée : allonger la date invalide la signature.
- La clé est aléatoire, stockée à côté du haché, et **tourne à chaque (re)choix du code** :
  supprimer `data/web_pin.json` puis choisir un nouveau code invalide tous les cookies émis.
- Il ne contient jamais le code. Attributs : `HttpOnly`, `SameSite=Strict`, `Path=/`,
  `Max-Age` = 30 jours. `Secure` n'est pas posé, faute de TLS sur `http://127.0.0.1`.
- Comparaison en temps constant (`hmac.compare_digest`) ; un cookie malformé ou non-ASCII est
  un refus, jamais une erreur 500.

## Verrouillage

4 chiffres = 10 000 possibilités : sans frein, elles tomberaient en quelques secondes.
**5 codes faux d'affilée verrouillent la connexion 15 minutes**, y compris pour le bon code
(réponse 429 avec `Retry-After`). Le compteur est global et en mémoire : un seul opérateur
sur une boucle locale, et un compteur par client n'aiderait qu'un attaquant capable de changer
de client. Un code juste remet le compteur à zéro. Redémarrer le processus le remet aussi à
zéro : ce n'est pas une défense contre quelqu'un qui peut relancer le serveur.

## Portée

Sans session valide :

- toute page (`/`, `/positions`, `/trades`, `/trades/{id}`, `/strategies`, `/ai-lab`, `/risk`,
  `/system`, `/reports`, `/calendar`) et tout export répondent **303 vers `/login`** ;
- le flux SSE `/events` répond **401** (un `EventSource` ne suit pas une redirection) ;
- toute autre méthode que GET/HEAD répond **401**.

Restent ouverts : `/login`, `/logout`, `/static/*` (logo, feuille de style de la page de
connexion) et `/healthz`, sonde de vie du superviseur. La page `/login` n'affiche ni navigation
ni chiffre.

Le code voyage dans le **corps** d'un formulaire POST (1 Kio maximum lu), jamais dans une URL ;
il n'est ni journalisé ni renvoyé dans une page. Les seules routes POST sont `/login` et
`/logout` ; elles ne touchent pas à la base de trading. La table de routage reste GET/HEAD
partout ailleurs.

## Exposition réseau

`tradingagent-web --host` n'accepte que la boucle locale (`127.0.0.1`, `::1`, `localhost`).
Tout autre hôte (`0.0.0.0`, une IP de réseau, un nom) fait refuser le démarrage (code 2) avec
un message, sans repli silencieux sur `127.0.0.1`. Il n'y a pas d'option pour passer outre.

## Ce que cela ne protège pas

- **Pas de TLS.** Le code et le cookie circuleraient en clair sur un réseau ; c'est la raison
  du refus d'une écoute hors boucle locale.
- **Pas de protection contre un accès local complet.** Un processus de la machine peut lire
  `data/web_pin.json` (le haché de 4 chiffres se casse hors ligne en un instant) ou supprimer
  le fichier pour réinitialiser le code.
- **Pas de gestion d'utilisateurs** : un seul code, pas de comptes, pas de journal nominatif.
- **Le cookie est un justificatif porteur** jusqu'à son expiration ou jusqu'au prochain choix
  du code.

## Vérifications automatiques

`tests/web/test_auth.py` prouve, entre autres :

- chaque point d'entrée sans session : 303 vers `/login` (401 pour `/events`), sans aucun
  libellé ni chiffre du tableau de bord ;
- première visite : choix du code, refus d'un code invalide ou de deux saisies différentes,
  aucun fichier écrit en cas de refus, code jamais en clair dans le fichier ;
- connexion : cookie `HttpOnly`, `SameSite=Strict`, 30 jours ; expiration à 30 jours ;
  déconnexion qui redemande le code ;
- cookie forgé, tronqué, prolongé ou non-ASCII refusé (jamais de 500) ; nouveau code =
  anciens cookies invalides ;
- verrouillage après 5 échecs, y compris pour le bon code, puis déblocage après 15 minutes ;
- le code n'apparaît ni dans un journal, ni dans une page, ni dans un cookie, ni dans une URL ;
- seules `/login` et `/logout` acceptent POST.

`tests/web/test_exposure.py` couvre le refus d'une écoute hors boucle locale, et
`tests/web/test_web_entry.py` garantit que `main()` passe toujours `PIN_FILE` à `create_app` :
sans cela le tableau de bord démarrerait ouvert sans qu'aucun test ne rougisse.
