# Le spread du courtier : constant sur deux heures sur l'or

**Date :** 2026-10-11 · **Nature :** relevé en lecture seule sur le compte de démonstration ·
**Aucun ordre, aucune écriture en base.**
**Sortie brute :** `docs/research/spread/spread-2026-10-10T2350.jsonl` (160 relevés) et
`spread-summary.json`.

---

## 1. Ce que ce relevé comble

La recherche externe du 2026-10-10 a classé la fréquence horaire du spread comme **un trou
coûteux du projet** : le spread utilisé partout venait de **quelques décisions d'une seule
journée**, et trois autres questions en dépendaient — le choix de la fenêtre horaire, le filtre
de coût, et le choix du marché.

Relevé : **160 échantillons** sur **1,99 heure**, toutes les 45 secondes, des deux côtés du
changement de jour UTC, un samedi soir et un dimanche matin. L'or est fermé à cette période, mais
le courtier publie quand même une cotation, et c'est un fait à noter.

## 2. Les chiffres

| | XAUUSD |
|---|---|
| échantillons | 160 |
| spread médian | **0,7400** |
| p05 | 0,7400 |
| p95 | 0,7400 |
| min / max | 0,7400 / **0,7400** |
| **spread / ATR M15 médian** | **0,1848** |

## 3. Le résultat

**Le spread ne varie pas.** Sur trois heures UTC consécutives (23h, 00h, 01h) et 160 relevés, la
médiane est **identique dans chaque heure** : 0,7400. L'or est **exactement constant** sur toute
la fenêtre — un seul prix, 160 fois.

**Conséquence directe : un filtre horaire sur le coût n'a rien à filtrer ici.** L'hypothèse
« trader seulement quand le spread est étroit » supposait que le spread s'élargit à certaines
heures. Dans cette fenêtre, et pour ce courtier, il ne s'élargit pas. C'est exactement le genre de
question où il fallait mesurer plutôt que lire une source.

**Le rapport à l'ATR est le chiffre qui compte** : **0,1848 ATR M15** sur l'or. Le risque d'une
opération s'exprime en ATR : le spread coûte donc moins d'un cinquième d'un ATR de mouvement avant
que le prix n'ait bougé d'un point.

## 4. Ce que ce relevé ne dit pas, et qui est plus important que ce qu'il dit

**Il ne dit rien des publications macro, et c'est la limite décisive.** Un samedi soir et un
dimanche matin ne contiennent aucune publication américaine. Or c'est autour des publications
macro (NFP, CPI, Fed) que le spread est susceptible de s'élargir, et c'est précisément ce que
quelques heures de week-end ne peuvent pas montrer.

**Il est pris marché fermé.** Le cours de l'or est publié par le courtier alors que le marché
sous-jacent est fermé : un spread parfaitement constant n'est pas représentatif d'une séance
ouverte.

**Il ne dit rien de la stabilité du courtier dans le temps.** Un relevé est un instantané :
deux heures ne prouvent pas que le spread de janvier vaut celui d'octobre.

**Il ne dit rien de l'impact, du slippage ni des requotes**, qui ne se lisent pas dans un tick et
qui sont, pour une stratégie à stop serré, au moins aussi coûteux que le spread affiché.

**Et il porte sur le compte de démonstration.** Un compte réel peut avoir d'autres conditions.

## 5. Ce que cela change pour la suite

**La piste « filtrer les heures chères » est fermée pour l'instant**, faute de variation à
filtrer. Elle ne se rouvrirait que par un relevé couvrant des publications macro et une séance
ouverte — ce qui demande d'attendre un vendredi 12:30 ou 14:00 UTC, et de mesurer à cette
heure-là.

**Un fait de méthode, et il vaut d'être retenu :** le spread/ATR de 0,1848 est utilisable comme
constante de dimensionnement, à condition de ne pas lui prêter une variation horaire qu'il n'a
pas montrée.

---

**Méthode.** `scripts/analysis/collect_spread.py` (relevé) et `scripts/analysis/aggregate_spread.py`
(agrégation, qui refuse de conclure sur une heure à moins de dix points). Le compte est resté en
démonstration, aucun ordre n'a été envoyé, et le terminal a été partagé avec l'agent sans le
perturber.
