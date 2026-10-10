# `vwap_pullback@1.3.0` (BTCUSD) devant les neuf portes : l'avantage s'effondre hors échantillon

**Date :** 2026-10-10 · **Candidat :** `vwap_pullback@1.3.0`, BTCUSD seul, M15 ·
**Décision : NON RETENU — 2 portes franchies sur 7 évaluables, aucune promotion, `config/` inchangé.**
**Résultat en une ligne :** PF 1,0275 et +31,56 € sur la validation (170 opérations) deviennent
PF **0,6909** et **−370,13 €** sur le jeu scellé (154 opérations).
**Rapport brut :** `docs/research/validation/2026-10-10-BTCUSD-vwap_pullback@1.3.0.json`

---

## 1. Pourquoi ce candidat existait

Le balayage de géométrie du 2026-10-10 (`2026-10-10-geometrie-de-sortie-balayage.md`) avait
montré que la géométrie de sortie en production était **sixième sur sept** et que l'élargissement
des objectifs portait le bitcoin au-dessus du plancher : sur la fenêtre de validation,
PF net **1,0275** et **+31,56 €** sur 170 opérations, contre **0,6818** et −327,71 € pour la
géométrie alors en place. La progression était monotone (0,68 → 0,71 → 0,91 → 1,03).

Ce balayage ne valait pas validation : le plancher de 1,00 était sa condition d'adoption, pas une
porte du §49. `vwap_pullback@1.3.0` a donc été écrit comme un **manifeste candidat** — objectifs
portés à 2,0 R / 4,0 R, stop inchangé à 1,5 ATR, `max_mode: SIGNAL`, déclaré sur BTCUSD seul —
puis soumis au protocole complet.

**Résultat en une ligne : ce qui tenait sur la fenêtre de validation ne tient pas sur la
suivante.** Le détail est en section 4 et c'est le cœur de ce document.

## 2. Ce qui a été mesuré, et comment

- `scripts/backtest/validate_candidate.py`, qui lit les seuils **figés** de
  `docs/research/thresholds.json` (condensat vérifié : version `campaign-20261007`, PF net
  minimum 1,20, 30 opérations minimum) et ne prend aucune option pour les assouplir.
- Jeu gelé de **59 999 bougies M15** BTCUSD, découpage ancré 60 / 20 / 20 :
  35 999 d'entraînement, 11 999 de validation, 12 001 scellés.
- **Sortie au premier objectif** (`targets_at_first_only`), parce que `runtime/pipeline.py`
  n'envoie que `take_profits[0]` au courtier.
- **235 plis walk-forward**, Monte-Carlo sur 1 000 tirages, stress à 2× coûts, variantes de
  robustesse à ±10 %.
- **Le jeu scellé a été ouvert** (`--confirm-holdout`), sans quoi la porte hors échantillon
  restait non évaluée et aucune promotion n'était possible.

## 3. Le verdict : 2 portes franchies, 5 refusées, 2 non évaluables

| porte | statut | chiffre | seuil |
|---|---|---|---|
| backtest | **franchi** | 549 opérations | 30 |
| costs | **refusé** | PF **1,03** | 1,20 |
| walk_forward | **refusé** | **95 / 235** plis rentables (0,40) | 0,50 |
| out_of_sample | **refusé** | rétention **1,00** | 0,50 |
| monte_carlo | **franchi** | P(profit) **0,56** | 0,50 |
| stress (2× coûts) | **refusé** | PF **0,83** | 1,20 |
| parameter_robustness | **refusé** | dispersion **2,15** | 0,50 |
| paper | non évaluable | — | campagne de 30 jours |
| risk | non évaluable | — | verdicts du moteur de risque réel |

**Correction de sélection multiple :** 1 hypothèse, p = 0,4346, 1 survivant avant correction →
**0 après**. Aucun candidat ne se distingue du nombre d'essais.

## 4. Ce que le jeu scellé a établi : l'avantage s'effondre

**Le candidat mesuré sur les 12 001 bougies qui n'avaient jamais été lues — 2026-06-05 au
2026-10-09 — perd :**

| fenêtre | opérations | PF net | profit net |
|---|---|---|---|
| validation (11 999 barres) | 170 | 1,0275 | **+31,56 €** |
| **hors échantillon (12 001 barres)** | **154** | **0,6909** | **−370,13 €** |

Le motif exact de la porte, lu dans le rapport brut :
`out-of-sample net profit -370.134070206920 is not positive`.

L'avantage trouvé sur une fenêtre de validation de 170 opérations **ne survit pas à la fenêtre
suivante**. Il ne s'affaiblit pas : il change de signe et coûte 370 €. C'est exactement ce qu'un
jeu scellé existe pour révéler, et la raison pour laquelle un plancher franchi sur une seule
fenêtre ne vaut pas validation.

**Une note de lecture, parce que le rapport imprimé induit en erreur.** La figure affichée pour
cette porte était `retention 1.00`, ce qui se lit spontanément comme « l'avantage est intégralement
conservé ». C'est faux, et le mécanisme mérite d'être connu :

    def out_of_sample_retention(in_sample, out_of_sample) -> float:
        gross = float(in_sample.net_profit)
        if gross > 0:
            return float(out_of_sample.net_profit) / gross
        return 1.0 if out_of_sample.net_profit > 0 else 0.0

Quand l'échantillon d'entrée n'est pas positif, la fonction ne calcule aucun rapport : elle rend
`0.0` si le hors échantillon est positif, `1.0` sinon. **`1.00` est donc ici la valeur rendue quand
rien ne va plus**, pas un signe de robustesse. Le protocole ne s'y trompe pas — la porte refuse sur
le **net négatif**, contrôlé avant la rétention — mais l'affichage, si.

J'ai moi-même commis cette erreur en lisant la sortie, et je la consigne plutôt que de la corriger
en silence : c'est un piège d'affichage réel. **À corriger** — un `None` explicite vaudrait mieux
qu'un `1.00` qui signifie « indéterminé ».

## 5. Pourquoi le reste confirme

**Le coût mange tout.** La porte `stress` rejoue la même fenêtre avec des coûts doublés :
PF **0,83**. Sur le bitcoin, où le spread observé vaut 18,42 $ pour une bande d'entrée de 0,13 ATR,
c'est la contrainte dominante — elle l'était déjà dans le diagnostic du 2026-10-09.

**Il n'est pas robuste aux paramètres.** La dispersion vaut **2,15** pour un seuil de 0,50 : une
perturbation de ±10 % des paramètres fait varier le résultat de plus du double. Un avantage qui
dépend à ce point du réglage exact n'est pas un avantage.

**Il ne tient pas sur la durée.** 95 plis walk-forward rentables sur 235, soit 40 % : moins d'un
pli sur deux gagne, ce qui est le profil d'un tirage à somme nulle.

**Il n'est pas distinguable du hasard.** p = 0,4346 après correction, 0 candidat survivant.

**Et le hors échantillon tranche.** La meilleure lecture disponible de ce candidat n'est pas son
PF de validation de 1,03 : c'est son PF de **0,69** sur la fenêtre qu'aucune sélection n'a
touchée.

## 6. Décision

**Aucune promotion, aucune activation, `config/` inchangé.** `vwap_pullback@1.3.0` reste à
`max_mode: SIGNAL`, donc inexécutable en DÉMO comme en réel ; il est conservé comme trace de la
mesure. La production continue avec `vwap_pullback@1.2.0` sur le bitcoin — la géométrie d'origine,
dont on sait maintenant qu'elle est la sixième sur sept.

C'est une décision, pas une défaite : le protocole a fait exactement son travail. Une géométrie
qui paraissait bonne sur 170 opérations de validation est **détruite** par la fenêtre suivante, et
le jeu scellé a coûté une ouverture pour l'établir. Sans lui, on aurait activé une configuration
qui perd 370 € là où on croyait qu'elle gagnait 31 €.

**Ce que ce candidat laisse derrière lui, et qui reste vrai :** la géométrie en production est
mesurément mauvaise — sixième sur sept, battue sur son propre marché. Le fait que la meilleure
alternative échoue aussi ne rend pas l'actuelle bonne ; cela dit seulement que **le problème n'est
pas dans la géométrie de sortie**.

## 7. Un défaut de traçabilité trouvé en vérifiant

**L'ouverture du jeu scellé ne laisse aucune trace durable.** `--confirm-holdout` a bien ouvert la
fenêtre, et le rapport le dit — mais j'ai cherché la trace en base après coup et il n'y en a
aucune : `audit_log` ne contient pas une seule ligne mentionnant le jeu scellé.

C'est grave pour une ressource **non renouvelable**. `protocol.SealedSet` garde son drapeau
d'ouverture en mémoire : un nouveau `split_dataset` rend un jeu scellé neuf, et rien n'empêche de
l'ouvrir indéfiniment. L'intention du code — « lire un holdout est un acte délibéré qui se
dépense » — n'est donc tenue par rien de persistant. Un opérateur qui ouvre le holdout cent fois
n'en garde aucune trace, et personne ne peut relire après coup combien de fois le seul jeu
vraiment hors échantillon du projet a été consulté.

**À corriger, et je ne l'ai pas fait dans ce lot** : persister chaque ouverture (référence,
empreinte du jeu, fenêtre, auteur, horodatage) et refuser la deuxième ouverture d'un jeu donné,
ou l'exiger explicitement. Sans cela, la porte hors échantillon protège moins qu'elle n'en a
l'air.

## 8. La suite, par ordre de valeur

1. **Fermer le trou d'audit du jeu scellé** (section 7). Il bloque la valeur de tout ce qui suit :
   tant que l'ouverture n'est pas comptée, la porte hors échantillon est déclarative.
2. **Corriger l'affichage de la rétention** (section 4) : ne pas publier `1.00` quand aucun rapport
   n'a été calculé. Un lecteur — moi le premier — en a tiré la conclusion inverse de la vérité.
3. **Le levier n'est pas la géométrie de sortie, et c'est maintenant mesuré deux fois.** Le
   balayage montrait que la géométrie en production est sixième sur sept ; la validation montre que
   la meilleure alternative perd 370 € hors échantillon. Les deux résultats ensemble disent :
   chercher ailleurs. C'est la règle d'entrée qu'il faut revoir, ou le choix du marché — pas un
   paramètre de sortie de plus.
4. **Ne pas relancer ce candidat.** Il a été mesuré, le holdout a été dépensé, le verdict est
   connu. Le remesurer serait payer deux fois pour la même réponse.

---

**Méthode.** `scripts/backtest/validate_candidate.py` (exécution : 569 s). Le jeu scellé a été
ouvert une fois, le 2026-10-10. Aucun ordre n'a été envoyé, aucun jeu gelé n'a été réécrit,
`config/` n'a pas été touché, aucune promotion n'a été émise.
