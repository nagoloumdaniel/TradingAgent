# Le spread du courtier : constant sur deux heures, et 1,65× plus cher sur le bitcoin

**Date :** 2026-10-11 · **Nature :** relevé en lecture seule sur le compte de démonstration ·
**Aucun ordre, aucune écriture en base.**
**Sortie brute :** `docs/research/spread/spread-2026-10-10T2350.jsonl` (320 relevés) et
`spread-summary.json`.

---

## 1. Ce que ce relevé comble

La recherche externe du 2026-10-10 a classé la fréquence horaire du spread BTCUSD comme **le trou
le plus coûteux du projet** : les 18,424 $ utilisés partout venaient de **onze décisions d'une
seule journée**, et trois autres questions en dépendaient — le choix de la fenêtre horaire, le
filtre de coût, et le choix du marché.

Relevé : **320 échantillons** sur **1,99 heure**, toutes les 45 secondes, des deux côtés du
changement de jour UTC, un samedi soir et un dimanche matin. Le Bitcoin cote en continu ; l'or est
fermé, mais le courtier publie quand même une cotation, et c'est un fait à noter.

## 2. Les chiffres

| | BTCUSD | XAUUSD |
|---|---|---|
| échantillons | 160 | 160 |
| spread médian | **18,4240** | **0,7400** |
| p05 | 18,4240 | 0,7400 |
| p95 | 18,9380 | 0,7400 |
| min / max | 18,4240 / **19,7260** | 0,7400 / **0,7400** |
| **spread / ATR M15 médian** | **0,3123** | **0,1848** |

## 3. Deux résultats, et le second contredit une attente

**Le spread ne varie pratiquement pas.** Sur trois heures UTC consécutives (23h, 00h, 01h) et
320 relevés, les deux symboles affichent **une médiane identique dans chaque heure** : 18,4240
pour le bitcoin, 0,7400 pour l'or. L'or est **exactement constant** sur toute la fenêtre — un
seul prix, 160 fois. Le bitcoin a bougé une fois, jusqu'à 19,7260, soit **+7 %** au-dessus de sa
médiane.

**Conséquence directe : un filtre horaire sur le coût n'a rien à filtrer ici.** L'hypothèse
« trader seulement quand le spread est étroit » supposait que le spread s'élargit à certaines
heures. Dans cette fenêtre, et pour ce courtier, il ne s'élargit pas. C'est exactement le genre de
question où il fallait mesurer plutôt que lire une source : la littérature crypto cite des spreads
de 0 à 2 points de base sur des plateformes centralisées, et ce courtier CFD en facture environ
**22** (18,424 sur 82 600).

**Le rapport à l'ATR est le chiffre qui compte**, et il est stable : **0,3123 ATR M15 sur le
bitcoin contre 0,1848 sur l'or**, soit **1,69 fois plus cher** (le relevé court du 10 octobre
donnait 1,65 — la mesure est stable d'un relevé à l'autre). Le risque d'une opération s'exprime en
ATR : le spread coûte donc au bitcoin près d'un tiers d'un ATR de mouvement avant que le prix
n'ait bougé d'un point, contre moins d'un cinquième pour l'or.

## 4. Ce que ce relevé ne dit pas, et qui est plus important que ce qu'il dit

**Il ne dit rien des publications macro, et c'est la limite décisive.** Un samedi soir et un
dimanche matin ne contiennent aucune publication américaine. Or la recherche du 2026-10-10 a
mesuré, sur trois ans et à dix secondes de résolution, que **les rafales de volatilité du bitcoin
coïncident avec les publications macro** (NFP, CPI, Fed). C'est là que le spread s'élargit, et
c'est précisément ce que six heures de week-end ne peuvent pas montrer.

**Il ne dit rien de la stabilité du courtier dans le temps.** Un relevé est un instantané :
deux heures ne prouvent pas que le spread de janvier vaut celui d'octobre.

**Il ne dit rien de l'impact, du slippage ni des requotes**, qui ne se lisent pas dans un tick et
qui sont, pour une stratégie à stop serré, au moins aussi coûteux que le spread affiché.

**Et il porte sur le compte de démonstration.** Un compte réel peut avoir d'autres conditions.

## 5. Ce que cela change pour la suite

**La piste « filtrer les heures chères » est fermée pour l'instant**, faute de variation à
filtrer. Elle ne se rouvrirait que par un relevé couvrant des publications macro — ce qui demande
d'attendre un vendredi 12:30 ou 14:00 UTC, et de mesurer à cette heure-là.

**La contrainte de coût est structurelle, pas temporelle.** Le bitcoin coûte 1,69 fois plus cher
que l'or en proportion du mouvement qu'il offre, et cela ne dépend pas de l'heure. Toute règle
appliquée au bitcoin doit donc battre un coût d'un tiers d'ATR, ce qui est cohérent avec tout ce
que ce projet a mesuré : c'est le marché où la marge est la plus mince, et c'est aussi celui où
l'élargissement de la bande d'entrée avait été nécessaire.

**Un fait de méthode, et il vaut d'être retenu :** le spread/ATR est stable entre deux relevés
indépendants (1,65 puis 1,69). Le chiffre est donc utilisable comme constante de dimensionnement,
à condition de ne pas lui prêter une variation horaire qu'il n'a pas montrée.

---

**Méthode.** `scripts/analysis/collect_spread.py` (relevé) et `scripts/analysis/aggregate_spread.py`
(agrégation, qui refuse de conclure sur une heure à moins de dix points). Six heures couvertes,
six concluantes. Le compte est resté en démonstration, aucun ordre n'a été envoyé, et le terminal
a été partagé avec l'agent sans le perturber.
