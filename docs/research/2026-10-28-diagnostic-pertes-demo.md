# Diagnostic des pertes de la demo et filtre de tendance (vwap_pullback@1.4.0, BTCUSD)

**Date :** 2026-10-28 · **Base :** production Supabase, lecture seule (SELECT uniquement) ·
**Scripts :** `scripts/analysis/diagnose_demo_losses.py`, `scripts/backtest/measure_trend_agreement.py` ·
**Sorties brutes :** `docs/research/measurements/demo-diagnostic-2026-10-28.json`, `trend-agreement-1030.json`.

Aucune rentabilite n'est garantie ni demontree. Compte DEMO uniquement, parametres de risque inchanges.

## 1. Diagnostic chiffre (22 operations cloturees, 2026-10-13 -> 2026-10-28)

| | n | reussite | PF net | net | R moyen | gain moy. / perte moy. | DD suite | pire serie |
|---|---|---|---|---|---|---|---|---|
| Tout | 22 | 54,5 % | 1,08 | +14,90 EUR | -0,025 | +0,79 R / -1,005 R | 127,8 EUR | 4 |
| BTCUSD (1.2.0) | 14 | 50,0 % | 0,87 | -15,66 EUR | -0,115 | 0,77 R / -1,00 R | 88,2 | 3 |
| XAUUSD (1.1.0) | 8 | 62,5 % | 1,43 | +30,56 EUR | +0,131 | 0,82 R / -1,01 R | 70,9 | 3 |

Equity : pic 5 616 le 16/10, 5 511 au 28/10 (creux -139 EUR intra-periode). La baisse recente est
une serie de stops : 6 pertes sur les 7 dernieres operations avant la derniere (25 -> 28/10).

Causes identifiees, avec le niveau de preuve :

1. **Asymetrie de sortie structurelle (solide).** L'objectif est a 0,8 R, le stop a 1 R : un gain
   rapporte 0,79 R net, une perte coute 1,005 R. Le seuil de rentabilite est ~56-59 % de reussite ;
   la demo fait 54,5 %, le backtest 55 %. L'esperance est donc ~0 a negative par construction, pas par bug.
2. **Pas d'edge global (solide, deja mesure).** Backtest 59 999 bougies : PF net 0,71 (BTC), 0,67 (or).
   Le +14,9 EUR de la demo (PF 1,08) tient a 22 operations : IC a 95 % de l'esperance largement autour de 0.
3. **Stops non trop serres (mesure).** Mediane MAE des perdants 1,36 R, MFE 0,35 R ; aucun des 10 perdants
   n'a atteint 0,8 R. Les perdants ne vont nulle part, ils ne sont pas "sortis trop tot". Confirme les
   balayages de stop deja publies (`vwap-tuning/axe-A-stop.md` : resserrer degrade).
4. **Entrees hors tendance (mesure, voir 2).** Sur 22 operations, seules 1 sont "dans la tendance" 10/30
   (gagnante) ; 2 sont "contre" (2 perdantes) ; 19 neutres (PF 1,27 en demo, mais -0,15 R en backtest).
5. **Couts.** Slippage nul sur 12 fills sur 22, max 11,1 USD (BTC). Le spread BTC (18,4 USD) pese ~8 % du
   risque et rogne les gains (0,72 R au lieu de 0,8 R sur BTC). 29 des 56 refus du moteur de risque sont
   `entry_zone` : le prix bouge hors de la bande de 0,1 ATR avant l'ordre (pas une perte, des occasions).
6. **Sessions / heures / jours (non concluant).** Tokyo : 3/7 gagnantes, -36 EUR ; 00-03 UTC : 1/4, -40 EUR ;
   mardi -40 EUR ; jeudi 4/4. Effectifs de 1 a 7 : du bruit. Les filtres de session et de volatilite ont de
   plus deja ete refutes sur 59 999 bougies (`session-volatility/`).
7. **Bugs de cloture / doublons : aucun trouve.** Somme des PnL des trades = variation du solde ; 22 positions
   fermees ; `exit_reason = 'broker'` pour 21 sur 22 (la table ne distingue pas stop et objectif, reclasse
   par prix de cloture : 12 objectifs, 10 stops). Incidents du 8-9/10 (ordres perdus, commentaire invalide)
   concernent d'anciennes strategies, et une divergence de position le 18/10 a ete reconciliee.

## 2. Ajustement teste : n'entrer que dans le sens de la tendance nommee

Hypothese (decouverte du 2026-10-11, `2026-10-11-ou-est-l-avantage.md`) : les operations "dans la
tendance" (EMA 10/30, pente > 0,05 ATR) rendent +0,30 R (BTC) / +0,22 R (or), les "contre" -0,50 / -0,67 R.
Le filtre existant de la regle lisait 20/50 et ne reproduisait pas l'effet.

Code : `VwapPullbackParameters.trend_ema_fast/trend_ema_slow` (optionnels, defaut `None` = ancien
comportement, donc aucun manifeste publie ne change) ; 5 tests ajoutes, ecrits d'abord en rouge.
Candidat unique, zero parametre ajuste : 10 / 30 / 0,05 (valeurs par defaut de `entry_features`).
Regle de decision ecrite avant la mesure dans l'en-tete du script.

| | version | fenetre | n | PF net | net EUR | R moyen |
|---|---|---|---|---|---|---|
| BTCUSD | 1.2.0 | entrainement | 709 | 0,7148 | -999,06 | -0,141 |
| BTCUSD | **1.4.0** | entrainement | 88 | **0,8215** | -71,38 | -0,081 |
| BTCUSD | 1.2.0 | validation | 205 | 0,6818 | -327,71 | -0,160 |
| BTCUSD | **1.4.0** | validation | 35 | **1,3916** | +42,88 | +0,123 |
| BTCUSD | 1.2.0 | train+val | 914 | 0,7073 | -1 326,77 | -0,145 |
| BTCUSD | **1.4.0** | train+val | 123 | **0,9441** | -28,50 | -0,023 |
| BTCUSD | 1.2.0 / 1.4.0 | apres les jeux geles (bougies DB) | 33 / 5 | 1,06 / 0,79 | +7,8 / -4,6 | non concluant (n<10) |
| XAUUSD | 1.1.0 | train / val | 656 / 231 | 0,7229 / 0,5454 | | |
| XAUUSD | candidat | train / val | 75 / 36 | 0,6755 / 0,8068 | -121 / -33 | |

(Les lignes de base reproduisent exactement les mesures publiees : 709/0,7148 et 205/0,6818 ; 656/0,7229 et 231/0,5454.)

Verdict de la regle : **BTCUSD retenu** (PF meilleur en entrainement ET validation, 123 operations >= 60,
meilleur R moyen dans 5 trimestres comparables sur 6, fenetre posterieure non concluante donc ne bloque pas).
**XAUUSD refuse** : le PF d'entrainement baisse (0,7229 -> 0,6755) ; XAU reste sur 1.1.0.

## 3. Decision

- `config/strategies/vwap_pullback@1.4.0.yaml` cree (BTCUSD, **`max_mode: SIGNAL`**). **`config/agent.yaml`
  inchange** : BTCUSD reste sur 1.2.0, XAUUSD sur 1.1.0.
- Pourquoi pas active : la regle gagne sa comparaison, mais le garde-fou du depot (RM-016) refuse
  `max_mode: DEMO` sans banniere "DEROGATION OPERATEUR" ecrite par l'operateur ; un agent ne peut pas
  l'ecrire. Activer en SIGNAL ferait perdre l'execution demo. Pour activer : l'operateur ajoute la
  derogation dans le manifeste 1.4.0, passe `max_mode: DEMO`, et met `vwap_pullback@1.4.0` pour BTCUSD.
- C'est une amelioration relative, pas une rentabilite : PF 0,94 sur train+val, sous 1,00 et sous le seuil
  1,20 du projet. Les neuf portes du §49 ne sont pas rejouees.

## 4. Limites

- La tranche "dans la tendance" a ete trouvee en regardant 164 tranches sur train+validation : ces fenetres
  ne sont pas independantes de la decouverte. Seule la fenetre posterieure l'est, et elle ne compte que 5 operations.
- Le jeu scelle n'a pas ete ouvert : l'ouvrir ecrit dans `holdout_unlocks` de la base de production (interdit ici).
  Prochaine etape recommandee pour l'operateur : `validate_candidate.py --confirm-holdout` sur 1.4.0.
- Cadence : ~0,3 operation/jour sur BTC (123 en ~14 mois) ; 30 operations demo demanderont plusieurs mois.
- Demo : 22 operations, aucune conclusion statistique sur sessions, jours ou sens.
- Replay "apres geles" : le harnais prend tous les signaux (33 operations) alors que le live en a eu 14
  (moteur de risque, entry_zone, cooldowns) : comparaison de tendance, pas de parite.
