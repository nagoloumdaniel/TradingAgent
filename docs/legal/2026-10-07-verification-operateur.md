# Fiche de vérification à signer — mode réel (TASK-090)

Document à remplir **par l'opérateur**, en s'appuyant sur les sources officielles listées
dans `2026-10-07-conformite-mode-reel.md`. Aucune valeur juridique n'est présumée ici :
chaque case doit être cochée après consultation de la source, et la citation (URL et date
de consultation) recopiée telle quelle.

- **Opérateur (nom) :** Daniel Nagoloum Talla
- **Contact :** landhack049@gmail.com
- **Date (UTC) :** 2026-10-08
- **Pays de résidence fiscale :** France *(à confirmer par l'opérateur — c'est la seule
  information du dossier que le projet ne peut pas établir lui-même)*
- **Fournisseur / entité contractante :** Deriv.com Limited
- **Type de compte :** ☒ démonstration ☐ réel — compte `201827970`, serveur `Deriv-Demo`,
  devise **EUR**
- **Version du dossier vérifiée :** `2026-10-07-conformite-mode-reel.md`

> **État du remplissage — à lire avant de signer.**
>
> L'en-tête ci-dessus est rempli à partir de faits vérifiables : identité du dépôt Git,
> et ce que le terminal expose réellement (le rapport d'un EA porte
> `company: Deriv.com Limited`, `server: Deriv-Demo`, `currency: EUR`, `demo: true`).
>
> **Les tableaux A à E sont volontairement laissés vierges.** Ce ne sont pas des cases à
> remplir d'après ce que le projet sait, mais des vérifications que l'opérateur doit faire
> lui-même sur les sources officielles, et dont il recopie la citation. Les pré-remplir
> depuis le code reviendrait à faire dire au document ce qu'il n'a pas vérifié — exactement
> ce que la phrase d'attestation interdit.
>
> **La signature est laissée vierge, délibérément.** Elle atteste « avoir consulté soi-même
> les sources citées ». Aucun outil ne peut porter cette affirmation à la place d'une
> personne : une signature apposée par un tiers est un faux, et elle priverait ce document de
> sa seule fonction — forcer un contrôle humain avant que de l'argent réel circule.

## A. Conditions d'utilisation du fournisseur

| # | Vérification | Résultat | Source consultée (URL) | Date | Citation / note |
|---|---|---|---|---|---|
| A1 | Le trading automatisé est autorisé sur le compte utilisé | ☐ oui ☐ non ☐ à revoir | | | |
| A2 | Les instruments prévus sont accessibles depuis le pays de résidence | ☐ oui ☐ non ☐ à revoir | | | |
| A3 | Le pays de résidence n'est pas exclu par le fournisseur | ☐ oui ☐ non ☐ à revoir | | | |
| A4 | L'usage de l'API / du terminal automatique respecte les conditions | ☐ oui ☐ non ☐ à revoir | | | |
| A5 | Aucune mesure de sanctions ou d'embargo ne s'applique | ☐ oui ☐ non ☐ à revoir | | | |

## B. Autorisation du trading automatisé

| # | Vérification | Résultat | Source consultée (URL) | Date | Citation / note |
|---|---|---|---|---|---|
| B1 | Le fournisseur est autorisé pour l'activité proposée | ☐ oui ☐ non ☐ à revoir | | | |
| B2 | L'acteur ne figure sur aucune liste noire de l'AMF | ☐ oui ☐ non ☐ à revoir | | | |
| B3 | Les limites de levier CFD applicables aux particuliers sont respectées | ☐ oui ☐ non ☐ à revoir | | | |
| B4 | L'usage strictement personnel ne relève pas du démarchage | ☐ oui ☐ non ☐ à revoir | | | |

## C. Règles du pays de résidence

| # | Vérification | Résultat | Source consultée (URL) | Date | Citation / note |
|---|---|---|---|---|---|
| C1 | La résidence fiscale déclarée est exacte | ☐ oui ☐ non ☐ à revoir | | | |
| C2 | L'obligation de déclarer le compte (formulaire n° 3916 / 3916-bis) est tranchée | ☐ oui ☐ non ☐ à revoir | | | |
| C3 | Le caractère privé ou professionnel de l'activité est tranché | ☐ oui ☐ non ☐ à revoir | | | |
| C4 | Les transferts vers le courtier ne demandent aucune autorisation | ☐ oui ☐ non ☐ à revoir | | | |
| C5 | Le traitement des données relève d'un usage strictement personnel | ☐ oui ☐ non ☐ à revoir | | | |

## D. Obligations fiscales

| # | Vérification | Résultat | Source consultée (URL) | Date | Citation / note |
|---|---|---|---|---|---|
| D1 | Le régime d'imposition des plus-values est identifié | ☐ oui ☐ non ☐ à revoir | | | |
| D2 | Le traitement des moins-values est identifié | ☐ oui ☐ non ☐ à revoir | | | |
| D3 | Le régime des actifs numériques est identifié | ☐ oui ☐ non ☐ à revoir | | | |
| D4 | Les formulaires et échéances annuels sont connus | ☐ oui ☐ non ☐ à revoir | | | |
| D5 | Les justificatifs à conserver sont listés | ☐ oui ☐ non ☐ à revoir | | | |

## E. Décision

- **Toutes les lignes sont-elles « oui » ?** ☐ oui ☐ non
- **Si non :** le dossier reste **non signé**, `legal_checklist_signed = false`, et l'activation
  en mode réel est refusée par `control/live.py`.
- **Si oui :** la case ci-dessous autorise l'ouverture de TASK-091 (mode réel) — elle ne
  vaut pas autorisation de trading, qui reste une décision d'exécution séparée.

> Je certifie avoir consulté moi-même les sources citées ci-dessus à la date indiquée, et
> que ce document reflète les réponses trouvées, sans interprétation ajoutée.

- **Signature :** ______________________ *(à tracer par l'opérateur — voir la note en tête)*
- **Nom en clair :** Daniel Nagoloum Talla
- **Date et heure (UTC) :** ______________________ *(le jour où les sources ont réellement
  été consultées, pas la date de préparation du document)*

## F. Renvois dans le code

- `tradingagent.control.live.evaluate_activation` — refuse l'activation si
  `legal_checklist_signed` est faux.
- `tradingagent.control.live.LiveController.activate` — persiste l'auteur, le plafond de
  risque et l'horodatage dans `system_events` (append-only).
- `ROADMAP.md` — TASK-090 est un prérequis de TASK-091 ; la phase 9 ne démarre pas avant.
