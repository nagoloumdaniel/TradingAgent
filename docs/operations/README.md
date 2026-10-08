# Exploitation de TradingAgent

Cette documentation s'adresse à l'opérateur qui installe, surveille et remet en service
l'agent. Elle ne suppose aucune connaissance du code : chaque procédure est copiable.

| Document | Quand l'ouvrir |
|---|---|
| [installation.md](installation.md) | Installer l'agent et le terminal MT5 sur une machine Windows |
| [demarrage-automatique.md](demarrage-automatique.md) | Tout relancer seul au démarrage, sans serveur : superviseur, lanceur, arrêt propre |
| [configuration.md](configuration.md) | Remplir `.env`, comprendre les modes et les variables |
| [exploitation.md](exploitation.md) | Enregistrer le service, surveiller, redémarrer, rotation des journaux |
| [incidents.md](incidents.md) | Réagir à une panne, une déconnexion ou une donnée douteuse |
| [backup-restore.md](backup-restore.md) | Sauvegarder chaque jour et restaurer |
| [arret-urgence.md](arret-urgence.md) | Arrêter toute activité, depuis Telegram ou le serveur |
| [commandes-telegram.md](commandes-telegram.md) | Manuel utilisateur du bot Telegram |
| [campagne-paper.md](campagne-paper.md) | Conduire et mesurer la campagne de paper trading de 30 jours |
| [ci.md](ci.md) | Chaîne d'intégration continue et blocage de fusion |

Les autres documents d'exploitation du système v3 :

| Document | Quand l'ouvrir |
|---|---|
| [../web/README.md](../web/README.md) | Lancer et lire le tableau de bord |
| [../web/DESIGN.md](../web/DESIGN.md) | Comprendre le système de design et ses règles |
| [../ea/README.md](../ea/README.md) | Compiler et installer les deux Expert Advisors |
| [../ea/protocole-pont.md](../ea/protocole-pont.md) | Format des échanges entre l'agent et les EA |
| [../registry/README.md](../registry/README.md) | Cycle de vie et promotion d'une stratégie |
| [../ai_lab/README.md](../ai_lab/README.md) | Ce que fait l'IA, et ce qu'elle ne peut pas faire |
| [../risk/README.md](../risk/README.md) | Les 17 contrôles du moteur de risque |
| [../analytics/README.md](../analytics/README.md) | Statistiques globales et de scalping |
| [../legal/2026-10-07-verification-operateur.md](../legal/2026-10-07-verification-operateur.md) | Vérifications à signer avant tout capital réel |

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
| Démarrage automatique sur le poste | Installé et vérifié le 2026-10-08 ([demarrage-automatique.md](demarrage-automatique.md) § 8) : superviseur, mono-instance, relances, arrêt propre |
| Enregistrement du service et du terminal | Script fourni ; à exécuter avec des droits administrateur |
| Sauvegarde chiffrée | Script fourni ; aller-retour sauvegarde → restauration prouvé sur base SQLite jetable |
| Restauration complète sur machine vierge | **Ouvert** : à exécuter par l'opérateur, procédure écrite dans [backup-restore.md](backup-restore.md) |
| Arrêt d'urgence | Testé le 2026-10-04 ([procédure](../procedures/2026-10-04-emergency-stop.md)) |
| Commandes Telegram | Enregistrées de `/status` à `/mode`, y compris `/report` |
| Campagne de paper trading | Outil de mesure fourni (`scripts/paper_campaign.py`) ; les 30 jours restent à courir |
| Tableau de bord web | Livré, lecture seule ; vérifications de sécurité dans [../reports/2026-10-07-verification-finale.md](../reports/2026-10-07-verification-finale.md) |
| Expert Advisors MT5 | Compilés sans erreur, pont câblé ; installation décrite dans [../ea/README.md](../ea/README.md) |

Les fichiers de référence du produit restent [`ROADMAP.md`](../../ROADMAP.md) et
[`CAHIER_DES_CHARGES.md`](../../CAHIER_DES_CHARGES.md).
