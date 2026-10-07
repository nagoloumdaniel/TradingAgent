# Exploitation de TradingAgent

Cette documentation s'adresse à l'opérateur qui installe, surveille et remet en service
l'agent. Elle ne suppose aucune connaissance du code : chaque procédure est copiable.

| Document | Quand l'ouvrir |
|---|---|
| [installation.md](installation.md) | Installer l'agent et le terminal MT5 sur une machine Windows |
| [configuration.md](configuration.md) | Remplir `.env`, comprendre les modes et les variables |
| [exploitation.md](exploitation.md) | Enregistrer le service, surveiller, redémarrer, rotation des journaux |
| [incidents.md](incidents.md) | Réagir à une panne, une déconnexion ou une donnée douteuse |
| [backup-restore.md](backup-restore.md) | Sauvegarder chaque jour et restaurer |
| [arret-urgence.md](arret-urgence.md) | Arrêter toute activité, depuis Telegram ou le serveur |
| [commandes-telegram.md](commandes-telegram.md) | Manuel utilisateur du bot Telegram |
| [ci.md](ci.md) | Chaîne d'intégration continue et blocage de fusion |

## Les trois règles à retenir

1. **L'arrêt d'urgence ne dépend d'aucun composant optionnel.** `uv run tradingagent halt`
   écrit directement en base et fonctionne même si l'agent et le bot sont arrêtés.
2. **Aucune sauvegarde n'est récupérable sans `BACKUP_PASSPHRASE`.** Ce mot de passe vit
   hors du serveur, dans le gestionnaire de mots de passe de l'opérateur.
3. **Aucun secret ne figure dans le dépôt.** Les valeurs sont saisies une seule fois dans
   `.env`, qui n'est jamais versionné.

## État des procédures

| Procédure | État |
|---|---|
| Installation Windows | Script fourni, exécuté en simulation (`-WhatIf`) ; à dérouler sur la machine cible |
| Enregistrement du service et du terminal | Script fourni ; à exécuter avec des droits administrateur |
| Sauvegarde chiffrée | Script fourni ; aller-retour sauvegarde → restauration prouvé sur base SQLite jetable |
| Restauration complète sur machine vierge | **Ouvert** : à exécuter par l'opérateur, procédure écrite dans [backup-restore.md](backup-restore.md) |
| Arrêt d'urgence | Testé le 2026-10-04 ([procédure](../procedures/2026-10-04-emergency-stop.md)) |
| Commandes Telegram | Enregistrées de `/status` à `/mode`, y compris `/report` |

Les fichiers de référence du produit restent [`ROADMAP.md`](../../ROADMAP.md) et
[`CAHIER_DES_CHARGES.md`](../../CAHIER_DES_CHARGES.md).
