# Rapport TASK-003 — Capacités réelles de Deriv MT5

**Mesuré le :** 2026-10-03 à 22:01 UTC (un samedi) · **Script :** `scripts/explore_mt5.py`, en lecture seule, sans aucun envoi d'ordre · **Relevé brut :** `2026-10-03-mt5-capabilities.json`, dans ce dossier

**Réserve générale :** toutes les mesures portent sur un **compte de démonstration**. Les spécifications, la disponibilité des produits et l'heure du serveur doivent être revérifiées sur le compte réel avant la phase 9.

## Synthèse

1. **L'or est inéligible au mode réel avec 100 €.** La plus petite position, 0,01 lot soit 1 once, exige **183,93 € de marge** et fait risquer environ **10,94 €** avec un stop typique. Le seul critère de risque à 5 % demanderait environ 219 € de capital, et la marge exige davantage. L'or reste pleinement utilisable en démonstration.
2. **Trois cryptos passent le filtre d'éligibilité de RM-019 : SOL, LTC et ADA.** Mais **LTC et ADA ont un spread égal à 77 % et 86 % de leur ATR M15**, ce qui les rend inexploitables pour une stratégie à court terme. **Seul SOL (15 %) est à la fois éligible et raisonnablement coûteux.** Mesure faite un samedi soir, à refaire en semaine.
3. **L'heure du serveur Deriv-Demo est l'UTC, sans passage à l'heure d'été.** Le risque R-16 est levé pour ce serveur, sous réserve de vérification sur le serveur réel.
4. **La valeur du tick fournie par MT5 n'est pas fiable pour calculer un risque.** Elle n'est exacte que pour l'or. Pour la formule de taille de TASK-004, **le calculateur de profit du terminal fait foi**.
5. **Des indices synthétiques sont présents sur ce compte démo**, contrairement au constat qui les avait retirés du projet (C-008). Les deux mesurés sont inéligibles au mode réel avec 100 €.
6. **L'historique est suffisant pour backtester** : H1 depuis 2011 pour l'or et le BTC, M15 depuis au moins juillet 2022 pour l'or.
7. **Le mot de passe utilisé n'est pas le mot de passe investisseur** : le compte déclare le trading autorisé.

## 1. Compte et terminal

| Élément | Valeur mesurée |
|---|---|
| Type de compte | Démonstration |
| Devise du compte | **EUR** : les risques et les marges se lisent directement en euros |
| Levier du compte | 1:30, conforme au plafond européen ; 1:20 effectif sur l'or, 1:2 sur la crypto |
| Mode de marge | Couverture autorisée : plusieurs positions possibles sur un même symbole |
| Société déclarée | Deriv.com Limited, serveur `Deriv-Demo` |
| Trading autorisé | **Oui** — avec le mot de passe investisseur, MT5 répondrait non |
| Terminal | Build 6235, plafond d'historique réglé à 100 000 bougies |

## 2. Symboles disponibles

244 symboles, répartis en : Forex majeur 14, Forex mineur 24, **Métaux 6**, **Crypto 46**, Énergies 3, Matières premières agricoles 5, Indices boursiers 16, Actions 68, ETF 31, Conversions 8, et des **indices synthétiques** : Volatility 6, Crash/Boom 2, Jump 3, Step 3, DEX 6, Drift Switching 3.

- **Or :** `XAUUSD` (profit en dollars) et `XAUEUR` (profit en euros, donc sans conversion pour ce compte).
- **Crypto :** 46 paires, la plupart cotées en dollars. Six ont été mesurées en détail : BTC, ETH, SOL, XRP, LTC, ADA.
- Les symboles suffixés `.conv` servent aux conversions de devises du terminal et ne sont pas des marchés à trader.

## 3. Spécifications et éligibilité au mode réel

Stop typique retenu : 1,5 × ATR(14) M15, relevé au moment de la mesure, et jamais inférieur à la distance minimale imposée par le courtier. Risque calculé par le terminal. Éligibilité selon RM-019 : risque du lot minimal ≤ 5 € **et** marge du lot minimal ≤ 100 €.

| Symbole | Lot min. | Pas | Contrat | Marge du lot min. | Risque du lot min. | Éligible au réel |
|---|---|---|---|---|---|---|
| XAUUSD | 0,01 | 0,01 | 100 oz | 183,93 € | 10,94 € | **Non** (marge et risque) |
| XAUEUR | 0,01 | 0,01 | 100 oz | 183,93 € | 9,59 € | **Non** (marge et risque) |
| BTCUSD | 0,01 | 0,01 | 1 | 376,34 € | 0,92 € | **Non** (marge) |
| ETHUSD | 0,1 | 0,01 | 1 | 119,33 € | 0,35 € | **Non** (marge) |
| SOLUSD | 0,5 | 0,01 | 1 | 26,58 € | 0,13 € | Oui |
| XRPUSD | 500 | 100 | 1 | 330,44 € | 1,71 € | **Non** (marge) |
| LTCUSD | 1 | 0,01 | 1 | 30,80 € | 0,26 € | Oui |
| ADAUSD | 200 | 1 | 1 | 21,79 € | 0,18 € | Oui |
| Volatility 100 (1s) | 1 | 0,01 | 1 | 184,79 € | 11,56 € | **Non** |
| Volatility 100 | 1 | 0,01 | 1 | 119,00 € | 7,03 € | **Non** |
| Step Index 300 | 0,1 | 0,01 | 10 | 2 422,76 € | 16,32 € | **Non** |
| Step Index 400 | 0,1 | 0,01 | 10 | 1 503,14 € | 25,38 € | **Non** |

Valeurs du relevé final de 22:01 UTC. Les marges et risques varient légèrement d'un relevé à l'autre avec les prix et la volatilité.

Autres caractéristiques communes aux symboles mesurés : trading complet autorisé, **exécution au marché**, remplissage **« fill or kill » uniquement**, stop-loss et take-profit natifs acceptés. Les distances minimales de stop vont de 0,20 $ pour l'or à 20 $ pour le BTC. Les swaps sont négatifs dans les deux sens pour la crypto : **conserver une position d'un jour sur l'autre coûte**.

## 4. Coûts : spread rapporté à l'ATR M15

Spread médian sur les 300 dernières bougies M15, soit environ 75 heures allant du jeudi au samedi.

| Symbole | ATR M15 | Spread médian | Spread / ATR |
|---|---|---|---|
| XAUUSD | 8,20 $ | 0,15 $ | **1,8 %** |
| XAUEUR | 6,39 € | 0,21 € | 3,3 % |
| BTCUSD | 69,42 $ | 2,42 $ | 3,5 % |
| XRPUSD | 0,00256 $ | 0,0001 $ | 3,9 % |
| Volatility 100 (1s) | 8,68 | 0,39 | 4,5 % |
| SOLUSD | 0,199 $ | 0,030 $ | 15,1 % |
| ETHUSD | 2,62 $ | 0,58 $ | 22,2 % |
| LTCUSD | 0,196 $ | 0,150 $ | **76,7 %** |
| ADAUSD | 0,00067 $ | 0,00058 $ | **86,3 %** |

**Lecture :** au-delà de 15 à 20 %, une stratégie M15 doit gagner une fraction importante de l'amplitude moyenne d'une bougie avant de couvrir ses seuls frais d'entrée. LTC et ADA sont donc inexploitables en M15 dans ces conditions. Des unités de temps plus longues réduisent le ratio.

**Réserve :** mesure faite un samedi soir, quand la liquidité crypto est plus faible. **À refaire un jour de semaine avant de choisir les cryptos (Q-07).**

## 5. Historique disponible

Nombre de bougies et première date atteinte. Un astérisque signale que le plafond de 100 000 bougies du terminal a été atteint : l'historique du serveur remonte alors **plus loin**, accessible en relevant le réglage ou en paginant.

| Symbole | M1 | M5 | M15 | H1 | H4 | D1 | Ticks |
|---|---|---|---|---|---|---|---|
| XAUUSD | 2026-06-23* | 2025-05-06* | 2022-07-07* | 2011-01-02 (92 339) | 2011-01-02 | 2011-01-02 | depuis 2019-01-02 |
| XAUEUR | 2026-06-23* | 2025-05-06* | 2022-07-07* | 2020-01-01 (39 953) | 2020-01-01 | 2020-01-01 | depuis 2025-01-01 |
| BTCUSD | 2026-07-26* | 2025-10-20* | 2023-11-26* | 2011-03-23 (87 573) | 2011-03-23 | 2011-03-23 | depuis 2025-01-01 |
| ETHUSD | 2026-07-26* | 2025-10-20* | 2023-11-26* | 2015-08-07 (85 980) | 2015-08-07 | 2015-08-07 | depuis 2025-01-01 |
| SOLUSD | 2026-07-26* | 2025-10-20* | 2023-11-26* | 2021-10-12 (43 598) | 2021-10-12 | 2021-10-12 | depuis 2025-01-01 |
| XRPUSD | 2026-07-26* | 2025-10-20* | 2023-11-26* | 2015-02-19 (81 805) | 2015-02-19 | 2015-02-19 | depuis 2025-01-01 |
| LTCUSD | 2026-07-26* | 2025-10-20* | 2023-11-26* | 2011-10-24 (87 373) | 2011-10-24 | 2011-10-24 | depuis 2025-01-01 |
| ADAUSD | 2026-07-26* | 2025-10-20* | 2023-11-26* | 2021-10-12 (43 599) | 2021-10-12 | 2021-10-12 | depuis 2025-01-01 |

**Conclusion pour R-03 :** l'historique suffit largement à un backtest significatif en H1, et à plusieurs années en M15. Le risque R-03 n'est pas remonté. **Les ticks crypto ne remontent qu'à janvier 2025** : un backtest au tick ne serait possible que sur une période courte.

## 6. Heure du serveur

**Mesure directe :** sur un tick BTC frais, l'écart entre l'heure du serveur et l'heure universelle est de **0 seconde**.

**Confirmation par le calendrier de l'or :** l'heure, dans l'horloge du serveur, de la première bougie H1 après chaque fermeture de week-end, sur environ deux ans :

| Mois | Nov. à fév. | Mars | Avr. à oct. |
|---|---|---|---|
| Heure d'ouverture | 23:00 | 22:00 ou 23:00 | 22:00 |

L'or rouvre le dimanche à 18:00, heure de New York, soit 23:00 UTC l'hiver et 22:00 UTC l'été. Si le serveur appliquait lui-même un changement d'heure, cette heure d'ouverture resterait constante sur l'année. Elle bascule au contraire exactement au rythme de l'heure d'été américaine : **le serveur reste à l'heure universelle toute l'année.** La pause quotidienne de l'or commence à 21:00 (304 occurrences) ou à 22:00 (130 occurrences), ce qui est cohérent.

**Conséquence :** R-16 est levé **pour le serveur de démonstration**. Le client de données doit tout de même garder un point unique de conversion et un contrôle au démarrage comparant l'heure d'un tick frais à l'heure universelle, car le serveur réel n'est pas encore vérifié.

## 7. Latence

Lecture d'un tick : 0,02 ms en médiane. Lecture de 300 bougies : 0,03 ms en médiane, 45 ms au pire. Une interrogation chaque seconde est sans coût notable. Ces lectures viennent du cache local du terminal, pas d'un aller-retour réseau.

## 8. Pièges techniques découverts

| Piège | Effet constaté | Règle à appliquer |
|---|---|---|
| **Plafond de bougies du terminal** | Toute demande de 100 000 bougies ou plus, par nombre ou par plage de dates, est refusée en bloc avec `(-2, 'Terminal: Invalid params')`, sans réponse partielle. Deux premières mesures de ce rapport ont ainsi affiché un historique vide | Ne jamais demander plus que le plafond moins un. Paginer par fenêtres pour remonter plus loin (TASK-010, TASK-013, TASK-060) |
| **Valeur du tick non fiable** | Risque calculé avec `trade_tick_value` : exact pour l'or, **faux de 11 à 15 %** pour la crypto et les indices Volatility (valeur en dollars au lieu d'euros), **faux de 89 %** pour les Step Index (taille de contrat ignorée) | Calculer le risque avec le calculateur de profit du terminal, ou, à défaut, avec taille de contrat × écart de prix × taux de conversion (TASK-004) |
| **Remplissage** | Seul le mode « fill or kill » est accepté | Envoyer les ordres en remplissage « fill or kill » (TASK-081) |
| **Cotations intermittentes** | Lors d'une mesure précédente, XRP et LTC n'avaient aucune cotation ; elles sont revenues lors de la mesure finale | Traiter l'absence de cotation comme une série dégradée (RM-002, TASK-012) |
| **Historique téléchargé à la demande** | La première lecture d'un symbole renvoie moins de bougies le temps du téléchargement | Relire jusqu'à stabilisation avant de considérer une série complète |
| **Sessions non exposées** | Le paquet Python ne donne pas les horaires de négociation | Déduire les sessions des données, et ne jamais générer de signal sur un marché sans cotation fraîche (F-005) |

## 9. Conséquences pour la suite

- **TASK-004, formule de taille :** fondée sur le calculateur de profit du terminal. Arrondi au pas de lot vers le bas ; refus sous le lot minimal. Le compte étant en euros, le terminal renvoie directement des montants en euros.
- **RM-019 :** or et BTC, ETH, XRP inéligibles au réel ; SOL, LTC, ADA éligibles, mais seul SOL a un coût acceptable en M15.
- **Q-07, choix des cryptos — à décider par l'opérateur :** il y a une tension entre ce qui convient à la **démonstration**, où BTC et XRP offrent les meilleurs spreads et ETH un long historique, et ce qui est accessible en **réel avec 100 €**, où SOL est le seul bon candidat. Proposition : BTC et ETH pour valider les stratégies en démonstration, plus SOL comme candidat au réel ; décision finale après la mesure des spreads en semaine.
- **Unités de temps :** H1 donne un historique complet depuis 2011 pour l'or et un ratio spread/ATR plus favorable. Il mérite d'être considéré à côté du M15 pour la recherche (Q-13).
- **R-17 confirmé :** le levier crypto de 1:2 rend BTC, ETH et XRP inaccessibles en réel avec 100 €.
- **Indices synthétiques :** présents sur ce compte démo. Reste à savoir s'ils le seraient sur un compte réel européen. Avec 100 €, les deux mesurés sont de toute façon inéligibles au réel.

## 10. Test d'exécution, 2026-10-04

À la demande de l'opérateur, `scripts/order_feasibility_mt5.py` a ouvert la position minimale puis l'a refermée aussitôt. Le script refuse de s'exécuter si le terminal ne déclare pas un compte de démonstration.

| Symbole | Vérification préalable (`order_check`) | Envoi (`order_send`) | Protections natives | Position restante |
|---|---|---|---|---|
| Volatility 100 Index | « Done » | **Refusé : retcode 10006, « Instruments blocked in France »** | — | aucune |
| BTCUSD | « Done » | **Exécuté** : retcode 10009, 0,01 à 84 723,952 | stop-loss et take-profit présents sur la position | aucune, refermée à 84 705,528 |

**Enseignements :**
- **Les indices synthétiques sont visibles mais bloqués à l'exécution pour un résident français.** C-008 vaut aussi pour MT5 : ils restent hors périmètre.
- **`order_check` ne détecte pas un blocage par juridiction.** Il répond favorablement alors que le serveur refusera l'ordre. Seul le code retour de `order_send` fait foi (TASK-081).
- Le chemin d'exécution complet fonctionne pour le BTC : ordre au marché en remplissage « fill or kill », stop-loss et take-profit natifs, fermeture par ordre opposé lié à la position.

## 11. Non mesuré ou à refaire

- Spreads crypto **un jour de semaine**, avant de trancher Q-07.
- Profondeur M1 à M15 au-delà du plafond de 100 000 bougies du terminal.
- Heure, spécifications et produits du **serveur réel**, avant la phase 9.
- Refus d'un ordre avec le mot de passe investisseur (critère de TASK-001), dès que ce mot de passe sera en place.
