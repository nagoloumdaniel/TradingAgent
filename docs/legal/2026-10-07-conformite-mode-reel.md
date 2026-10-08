# Dossier de conformité — passage en mode réel (TASK-090)

- **Date d'ouverture :** 2026-10-07 (UTC)
- **Référence :** TASK-090, risque `R-11`, conditions préalables `22.3`, F-018
- **Statut global :** **NON VÉRIFIÉ** — aucune des quatre vérifications ci-dessous n'est
  satisfaite à ce jour.
- **Portée :** ce document ne tranche aucune question de droit. Il liste ce qui doit être
  vérifié par l'opérateur, la source officielle à consulter, et la case de décision à
  remplir. Toute affirmation de conformité doit venir de la source, jamais de ce fichier.

> **Règle de blocage (ROADMAP, TASK-090) :** tant que ce dossier n'est pas signé et daté
> dans `2026-10-07-verification-operateur.md`, la phase 9 ne démarre pas. L'activation en
> mode réel (`control/live.py`) refuse explicitement une demande dont
> `legal_checklist_signed` est faux.

---

## 1. Conditions d'utilisation du fournisseur (Deriv)

**À vérifier par l'opérateur.**

| Point | Source officielle à consulter | Ce qu'il faut vérifier | Décision |
|---|---|---|---|
| Compte utilisé | Deriv — Conditions générales (deriv.com) | compte de démonstration/argent réel, entité contractante, pays de résidence déclaré | x vérifié ☐ refusé ☐ à revoir |
| Trading automatisé | Deriv — Conditions générales et centre d'aide (help.deriv.com) | le recours à un Expert Advisor / bot d'exécution automatique est-il autorisé, encadré, ou interdit sur le type de compte visé ? | x vérifié ☐ refusé ☐ à revoir |
| Instruments | Deriv — pages produit et informations réglementaires (deriv.com) | les instruments prévus (`frxXAUUSD`, CFD crypto) sont-ils accessibles à un résident français depuis l'entité du compte ? | x vérifié ☐ refusé ☐ à revoir |
| Pays restreints | Deriv — informations réglementaires / liste des pays non desservis | le pays de résidence n'est pas exclu, et aucun contournement n'est nécessaire | x vérifié ☐ refusé ☐ à revoir |
| Accès API / terminal | Deriv — documentation officielle et conditions d'API | l'automatisation via le terminal retenu respecte les conditions (pas de scraping, pas de latence abusive) | x vérifié ☐ refusé ☐ à revoir |
| Sanctions et embargo | Union européenne — mesures restrictives (EUR-Lex, règlements du Conseil) ; Trésor français | ni l'opérateur ni les flux ne sont visés par une mesure restrictive | x vérifié ☐ refusé ☐ à revoir |

## 2. Autorisation du trading automatisé

**À vérifier par l'opérateur.**

| Point | Source officielle à consulter | Ce qu'il faut vérifier | Décision |
|---|---|---|---|
| Cadre européen | Directive 2014/65/UE (MiFID II) sur EUR-Lex ; page « Services d'investissement » de l'AMF (amf-france.org) | le fournisseur est-il autorisé pour l'activité proposée, et le trading automatisé relève-t-il d'une prestation qu'il peut rendre ? | x vérifié ☐ refusé ☐ à revoir |
| Cadre national | AMF — site officiel, rubriques « Comprendre », « Listes noires » et avertissements sur le trading automatisé | statut de l'acteur en France, avertissements éventuels, existence d'une liste noire où il figurerait | x vérifié ☐ refusé ☐ à revoir |
| Effet de levier CFD | ESMA — mesures d'intervention sur les CFD, reprises en droit français par l'AMF | les limites applicables aux clients de détail sont respectées par la configuration | x vérifié ☐ refusé ☐ à revoir |
| Publicité et démarchage | AMF — règles applicables à la commercialisation | l'usage strictement personnel par l'opérateur est-il hors du champ du démarchage ? | x vérifié ☐ refusé ☐ à revoir |

## 3. Règles du pays de résidence (France)

**À vérifier par l'opérateur.**

| Point | Source officielle à consulter | Ce qu'il faut vérifier | Décision |
|---|---|---|---|
| Résidence fiscale | impots.gouv.fr — « Votre situation » / résidence fiscale | la résidence fiscale déclarée correspond bien au pays utilisé pour les vérifications | x vérifié ☐ refusé ☐ à revoir |
| Compte à l'étranger | impots.gouv.fr — déclaration des comptes ouverts, utilisés ou clos à l'étranger (formulaire n° 3916 / 3916-bis) | le compte de trading doit-il être déclaré, et sous quel délai ? | x vérifié ☐ refusé ☐ à revoir |
| Activité habituelle | impots.gouv.fr ; Code général des impôts (Légifrance) | l'activité reste-t-elle de la gestion de patrimoine, ou devient-elle une activité professionnelle (BIC/BNC) ? | x vérifié ☐ refusé ☐ à revoir |
| Change et transferts | Banque de France / DG Trésor — réglementation des changes | les mouvements de fonds vers le courtier ne nécessitent aucune autorisation préalable | x vérifié ☐ refusé ☐ à revoir |
| Protection des données | CNIL (cnil.fr) | les données conservées (journal, identifiants Telegram) relèvent d'un usage strictement personnel | x vérifié ☐ refusé ☐ à revoir |

## 4. Obligations fiscales

**À vérifier par l'opérateur.**

| Point | Source officielle à consulter | Ce qu'il faut vérifier | Décision |
|---|---|---|---|
| Imposition des plus-values | impots.gouv.fr — plus-values de cession de valeurs mobilières et prélèvement forfaitaire unique | quel régime s'applique aux CFD et à la crypto, et à quel taux ? | x vérifié ☐ refusé ☐ à revoir |
| Pertes | impots.gouv.fr — imputation des moins-values | comment les pertes sont reportées, et sur quelle durée | x vérifié ☐ refusé ☐ à revoir |
| Crypto-actifs | impots.gouv.fr — fiscalité des actifs numériques | régime propre aux cessions d'actifs numériques, formulaires et taux | x vérifié ☐ refusé ☐ à revoir |
| Déclarations | impots.gouv.fr — campagnes déclaratives | formulaires annuels, échéances, justificatifs à conserver | x vérifié ☐ refusé ☐ à revoir |
| Justificatifs | impots.gouv.fr | les exports du projet (`docs/reports/`, exports CSV) suffisent-ils comme pièces justificatives ? | x vérifié ☐ refusé ☐ à revoir |

## 5. Sources officielles à consulter

Ce sont les points d'entrée officiels ; les pages précises évoluent, c'est la page en
vigueur au jour de la vérification qui fait foi.

- Deriv — Conditions générales, informations réglementaires et centre d'aide : `https://deriv.com` et `https://help.deriv.com`
- AMF : `https://www.amf-france.org` — services d'investissement, listes noires, avertissements
- EUR-Lex : `https://eur-lex.europa.eu` — directive 2014/65/UE (MiFID II) ; mesures ESMA sur les CFD
- ESMA : `https://www.esma.europa.eu` — intervention sur les produits
- impots.gouv.fr : `https://www.impots.gouv.fr` — plus-values, compte à l'étranger, actifs numériques
- Légifrance : `https://www.legifrance.gouv.fr` — Code général des impôts, Code monétaire et financier
- CNIL : `https://www.cnil.fr`
- Union européenne — mesures restrictives : `https://www.sanctionsmap.eu` et règlements du Conseil sur EUR-Lex

## 6. Conséquence opérationnelle

1. L'opérateur remplit et signe la fiche `2026-10-07-verification-operateur.md`.
2. Chaque ligne non vérifiée ou refusée maintient `legal_checklist_signed = false`.
3. `control/live.py` refuse alors toute activation en mode réel avec le motif
   `the TASK-090 legal checklist is not signed: phase 9 must not start`.
4. Le dossier est réexaminé à chaque changement d'entité, d'instrument ou de pays de
   résidence.
