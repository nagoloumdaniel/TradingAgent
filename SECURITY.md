# Politique de sécurité

## Signaler une vulnérabilité

Ne publiez pas de faille dans une issue. Utilisez le signalement privé de GitHub :
**Security → Report a vulnerability** sur ce dépôt.

Indiquez le commit, les étapes de reproduction et l'impact estimé. Réponse visée sous 7 jours.

## Périmètre

- Le dashboard web est en **lecture seule** et protégé par authentification (voir [`docs/web/SECURITE.md`](docs/web/SECURITE.md)).
- Aucun secret n'est versionné : seul [`.env.example`](.env.example) l'est. Un secret exposé doit être révoqué immédiatement.
- Les tests actifs (injection, charge) ne se font que sur un environnement de test, jamais sur un compte réel.

## Avertissement

Ce logiciel exécute des ordres sur des marchés à effet de levier. Voir [`DISCLAIMER.md`](DISCLAIMER.md).
