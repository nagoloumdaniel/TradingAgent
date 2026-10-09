# La bande d'entrée BTCUSD : ce que l'élargissement sauve vraiment

**Date :** 2026-10-09 · **Portée :** les 12 signaux BTCUSD refusés sur `entry_zone` (6 paires,
chacune produite deux fois par `@1.0.0` et `@1.0.1` avant le correctif du double signal) ·
**Aucune base modifiée, aucun ordre envoyé.**

**La question.** La bande `entry_zone_atr: 0.1` a été portée à **0,13** sur la foi d'une mesure
de backtest (802 opérations, PF 0,8748, +101,62 € contre 0,8487). Le backtest dit aussi que les
refus passeraient de 368 à 185. **Qu'en est-il sur les signaux réels d'aujourd'hui ?**

**La réponse.** Sur six paires refusées, **0,13 en sauve deux**. Le backtest promettait de
diviser les refus par deux ; le réel en sauve un tiers. Et un tiers des dérives observées est
**trop grand pour n'importe quelle bande raisonnable** : même à 0,20 ATR, deux paires restent
refusées.

| Signal | Heure UTC | Dérive de l'ask | 0,10 | **0,13** | 0,20 |
|---|---|---|---|---|---|
| #13 / #14 | 2026-10-09 04:00 | +1,1 $ sous la bande | non | **OUI** | OUI |
| #15 / #16 | 2026-10-09 04:15 | +0,4 $ sous la bande | non | **OUI** | OUI |
| #2 | 2026-10-08 02:00 | −13,6 $ | non | non | OUI |
| #17 / #18 | 2026-10-09 08:15 | −11,0 $ | non | non | OUI |
| #22 | 2026-10-09 12:00 | −8,7 $ | non | non | OUI |
| #7 | 2026-10-08 15:45 | +28,9 $ | non | non | non |
| #19 / #20 | 2026-10-09 11:45 | +17,8 à +22,8 $ | non | non | non |
| #21 | 2026-10-09 12:00 | −26,1 $ | non | non | non |

**Lecture : trois régimes, pas un.**

1. **Deux paires étaient à un cheveu** : l'ask dépassait la bande de 0,4 et 1,1 dollar, quand le
   spread en vaut 18,4. C'est le cas que l'élargissement vise, et il le règle.
2. **Quatre paires ont dérivé de 9 à 29 dollars**, soit une demi à une bande et demie entières.
   Aucune bande plausible ne les rattrape sans accepter des remplissages que la porte existe
   précisément pour refuser.
3. **Deux paires ont dérivé de plus de 26 dollars**, au-delà de deux fois la largeur actuelle.

**Ce que cela change dans la lecture du chiffre de backtest.** Les « 368 refus » du jeu complet
ne sont pas 368 cas de dérive marginale : ce sont des dérives de toute ampleur, et le
pourcentage qu'une bande plus large rattrape dépend entièrement de leur distribution. Le
backtest mesurait une **moyenne** ; les signaux réels montrent que la distribution a une queue
longue, et qu'élargir la bande agit sur le corps, pas sur la queue.

**Et la porte n'est pas en cause.** Sur ces mêmes signaux, les autres contrôles de risque
passent : le spread valait 18,424 et 19,443 pour une limite de 34,8, et 17 contrôles sur 18
passaient. Ce sont bien des **refus d'entrée sur dérive réelle**, c'est-à-dire la porte qui fait
son travail — un remplissage à 29 dollars du signal n'est pas le trade que la règle a décidé.

**Ce qui reste ouvert, et qui décide de la suite.** La fréquence horaire du spread BTCUSD n'est
pas connue : elle ne vient que de onze décisions d'une seule journée. Si le spread s'élargit
durablement de 18 à 30 dollars, aucune bande fondée sur 0,1 ou 0,13 ATR ne suffira — c'est la
**fenêtre de trading** ou le **choix du marché** qu'il faudra revoir, pas le paramètre.
