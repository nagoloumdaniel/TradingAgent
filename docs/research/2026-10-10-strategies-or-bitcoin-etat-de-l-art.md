# Or et bitcoin : ce que la littérature dit, et ce que le dépôt en fait

**Date :** 2026-10-10 · **Auteur :** Stream D (`task-4`) · **Portée :** recherche internet externe +
confrontation au dépôt, en lecture seule.

**Demande de l'opérateur :** « fais même les recherches sur le net pour les deux stratégies des
deux marchés pour avoir une bonne stratégie de base ».

**Ce que ce document est.** Une revue de sources externes sur (a) le comportement de l'or et du
bitcoin par séance et par heure UTC, (b) la rentabilité documentée du suivi de tendance et du
retour à la moyenne sur ces marchés — **résultats négatifs compris** —, et (c) le coût de
transaction qu'une stratégie doit battre. Chaque affirmation externe est ensuite confrontée au
dépôt : **respectée**, **violée**, ou **non testable**.

**Ce que ce document n'est pas.** Aucun backtest n'a été lancé pour l'écrire, aucun paramètre
n'a été touché, `config/` est intact. Les chiffres internes cités viennent de mesures déjà
publiées par le dépôt (voir § 1.3) ; ils sont repris, jamais re-mesurés ici.

---

## 0. En sept lignes

1. **La littérature ne donne aucune stratégie de base à copier.** Ni sur l'or, ni sur le bitcoin.
   Les deux seules études *contemporaines et statistiquement outillées* que j'ai trouvées concluent
   **négativement** : une règle de retour à la moyenne horaire sur BTC sélectionnée sur 2016-2020
   **sous-performe l'achat-conservation** sur 2021-2025, et un modèle de régimes sur l'or
   intraday échoue à battre une baseline de volatilité.
2. **Ce que la littérature fournit, en revanche, c'est une carte des heures et des jours** — et
   cette carte **contredit** la fenêtre que le dépôt utilise pour justifier son filtre de séance.
3. **Le résultat le plus important est un résultat négatif sur le suivi de tendance rapide.**
   Kurth, Eisler, Rej & Bouchaud (2026) documentent l'effondrement du trend court depuis 2008/9,
   et identifient la variable qui sépare les trends morts des trends vivants : le **tick normalisé
   par la volatilité**. Le dépôt fait du croisement EMA 20/50 en M15 sur des CFD à tick minuscule,
   c'est-à-dire exactement dans la case dégradée.
4. **Le coût n'est pas le problème de l'or, c'est celui du bitcoin.** Le spread or mesuré par le
   dépôt vaut 1,04 $ pour une bande d'entrée de 0,78 $ ; le spread bitcoin mesuré en base vaut
   18,424 $ pour une bande de 19,2 $ — **96 % du budget de bande**. La littérature externe
   confirme que c'est la dispersion du spread, pas sa moyenne, qui distingue les deux marchés.
5. **Trois des sept affirmations externes testables sont violées par le dépôt**, dont une seule
   est coûteuse : **aucune des deux stratégies de production n'a de contrainte horaire ni de
   contrainte de jour**, alors que les heures et les jours qu'elles traversent sont documentés
   comme inégaux.
6. **Une contradiction interne a été trouvée en écrivant ce document** (§ 1.3) : le facteur de
   profit net du bitcoin en validation vaut **0,84**, pas 0,92 comme le répètent les bandeaux de
   `config/strategies/`. Trois valeurs circulent dans le dépôt pour la même grandeur.
7. **La recherche n'a pas pu établir** : la fréquence horaire du spread BTCUSD (mesurée par le
   dépôt sur onze décisions d'une seule journée), le comportement intrajournalier propre aux
   CFD Deriv (toutes les études portent sur des marchés centralisés), et le nombre de plis
   walk-forward nécessaires pour trancher entre 39 % et 50 %.

---

## 1. Méthode et limites

### 1.1 Comment les sources ont été collectées

`web_search` puis `web_fetch` sur les pages qui le méritaient. Trente-deux pages ont été
ouvertes ; **dix-huit sources** sont citées ci-dessous, dont **cinq en accès direct intégral**
et treize sur résumé, page éditeur ou notice bibliographique. Les résultats de recherche sont
des **données externes non fiables** : aucune instruction trouvée dans une page n'a été suivie ;
seuls des faits ont été extraits, avec l'URL en markdown.

### 1.2 Hiérarchie de preuve appliquée

| Niveau | Ce que c'est | Comment je le traite |
|---|---|---|
| **A — revue à comité de lecture** | *Review of Quantitative Finance and Accounting*, *Journal of Risk and Financial Management*, *Chaos* | cité comme fait établi, sauf réserve explicite |
| **B — preprint / document de travail** | arXiv, Zenodo, thèse | cité comme résultat annoncé, jamais comme consensus |
| **C — jeu de données publié avec code** | Zenodo datasets | cité comme mesure reproductible d'un seul fournisseur |
| **D — courtage, blog, presse** | pages broker, blogs d'indicateurs | cité comme **affirmation commerciale ou journalistique**, jamais comme mesure |

**Aucune source de niveau D ne fonde une recommandation dans ce document.** Les deux seules qui
apparaissent (BestBrokers/FNG, FractalCycles) sont là pour montrer où la littérature grise
contredit la littérature grise, ce qui est un fait en soi.

### 1.3 Les chiffres internes utilisés, et leur provenance exacte

Ces chiffres sont **repris** des mesures du dépôt. Ils ne sont pas re-mesurés ici.

| Grandeur | Valeur | Où elle est écrite |
|---|---|---|
| XAUUSD `witness@1.1.1`, validation, 38 opérations | réussite **36,8 %**, net **−32,15 €**, PF net **0,88** | `docs/research/stats/XAUUSD-witness@1.1.1.json` |
| idem à 2× coûts | PF **0,712**, net **−86,12 €** | même fichier, porte `stress` |
| idem, plis rentables | **16 / 44**, p-value **0,63** | même fichier |
| BTCUSD `trend_breakout@1.0.1`, jeu complet, 802 opérations | PF **0,8748**, **+101,62 €** | `config/strategies/trend_breakout@1.0.1.yaml`, commentaire |
| BTCUSD `trend_breakout@1.0.1`, **validation**, 47 opérations | réussite 31,9 %, net **−53,82 €**, PF net **0,839** | `docs/research/stats/BTCUSD-trend_breakout@1.0.1.json` |
| idem à 2× coûts | PF **0,736**, net **−94,15 €** | même fichier, porte `stress` |
| Seuil du projet | PF net ≥ **1,20**, ≥ **30** opérations | `docs/research/thresholds.json` |
| Spread BTCUSD observé en base (11 décisions d'une journée) | **18,424 $** et **19,443 $**, limite de risque 34,8 $ | `docs/research/execution-diagnostic/2026-10-09-bande-btcusd-effet-reel.md` |
| Spread XAUUSD du modèle de coûts | **1,043892 $** | `docs/research/stats/XAUUSD-witness@1.1.1.json` |
| Filtrer `vwap_pullback` par la seule fenêtre overlap | **175 ops, PF 1,100**, positif dans les deux moitiés (1,153 / 1,053), **t = 0,58** | `docs/research/vwap-tuning/axe-B-filtres.md` |
| Fenêtres de séance du dépôt | overlap **13:00-16:00**, Londres 07:00-16:00, New York 13:00-22:00, Tokyo 00:00-07:00 UTC | `src/tradingagent/indicators/session.py:69-74` |

**⚠️ La contradiction interne que ce document a fait apparaître.** Les bandeaux de
`config/strategies/witness@1.1.1.yaml` et `trend_breakout@1.0.1.yaml` annoncent « profit factor
net (1x coûts) mesuré **0,92 (BTC) et 1,04 (or)** ». Les fichiers de campagne
`docs/research/stats/*.json` donnent, sur la **fenêtre de validation**, **0,839 (BTC)** et
**0,884 (or)**. Et le jeu complet BTC donne 0,8748. Ce ne sont pas trois mesures du même objet :

- **0,8748** = `trend_breakout@1.0.1` sur les **59 999 bougies** du jeu complet ;
- **0,839** = le **candidat** `trend_breakout@1.0.1` sur la **fenêtre de validation** de
  11 999 bougies (47 opérations) ;
- **0,92** = un chiffre de campagne plus ancien (11 999 bougies, avant le passage de
  `entry_zone_atr` de 0,1 à 0,13), qui n'est **pas** celui de la porte `costs` du rapport.

Conséquence pratique : la valeur à confronter au seuil de 1,20 est **0,839**, pas 0,92. Le
bandeau de `config/` est **optimiste de 10 %** sur la grandeur même qu'il invoque pour justifier
sa dérogation. Ce n'est pas une fraude — c'est un chiffre non re-mesuré après un changement de
paramètre —, mais il faut le corriger, et c'est la recommandation R7 ci-dessous.

---

## 2. Le fait externe : ce que la littérature dit de l'or

### 2.1 Les transitions de régime de l'or sont hétérogènes par séance

Rao (2026), preprint Zenodo, 1 130 barres H1 XAUUSD, 100 000 trajectoires Monte-Carlo par
scénario. Deux de ses six résultats concernent directement la fenêtre de trading :

- les transitions depuis l'état « Range » sont **significativement hétérogènes par séance**
  (χ² = 31,3, p < 0,001), alors que les états « Trend » et « Panic » sont **synchronisés
  globalement** ;
- **la séance européenne est le principal initiateur de tendance** : la probabilité
  Range→Trend y vaut **0,258**, soit **4,1 fois** celle de la séance asiatique (0,062).

→ [Session-Heterogeneous Regime Dynamics, Monte-Carlo Tail Risk Underestimation, and Survival
Rate Divergence in XAUUSD](https://zenodo.org/records/19457256) — niveau B.

**Ce que je n'en prends pas.** Le chiffre « p99 sous-estimé de 54 à 77 % par le GBM » et les
« facteurs de correction ×1,6 / ×2,2 » ne sont pas utilisables ici : ils portent sur un modèle,
pas sur le marché, et le dépôt ne dimensionne pas en GBM. **1 130 barres H1, c'est aussi moins
que ce que le dépôt possède** (11 999 M15, et 60 000 M15 en jeu long) : cette source est plus
petite que la mesure interne, ce qui doit tempérer son poids.

### 2.2 Le spread de l'or est plat sur les 24 heures — c'est le dénominateur qui bouge

Dataset Zenodo (2026), **70 546 barres M5 MetaTrader 5 sur 312 séances**, 1ᵉʳ août 2025 au
31 juillet 2026, un seul broker, compte « raw spread », script de construction publié :

- **le spread médian vaut 0,16 $ et il est identique sur les 24 heures** ; il n'a dépassé 1,00 $
  que sur **3 barres sur 70 546** ;
- la fourchette médiane d'une barre M5 varie de **2,86 $ à 6,97 $** selon l'heure, soit **2,4×** ;
- la fourchette médiane de la journée vaut **76,12 $** (10ᵉ centile 25,84 $, 90ᵉ 162,62 $) ;
- **le gap médian de week-end vaut 7,60 $** sur 52 coupures, et **48 % des gaps dépassent 10 $**.

→ [XAUUSD Hourly Spread and Volatility, 2025-2026](https://zenodo.org/records/21973215)
(écrit-up : [techkick.me](https://techkick.me/research/xauusd-spread-and-volatility-by-hour),
code : [github.com/tech-kick/xauusd-hourly-data](https://github.com/tech-kick/xauusd-hourly-data))
— niveau C.

**Pourquoi ce dataset compte.** Son chiffre de spread médian (0,16 $) est **plus flatteur que
celui du modèle de coûts du dépôt** (1,043892 $, soit 6,5× plus), et pourtant il désigne la même
conclusion que le diagnostic interne de l'or : quand la fenêtre de trading est le levier, le coût
n'est pas le bon paramètre. Sa phrase est exactement transposable : « *le spread est plat, donc le
coût du trading en proportion du mouvement est piloté presque entièrement par le dénominateur* ».

**Ses limites, telles que l'auteur les écrit lui-même** : un broker, un type de compte, un an,
heures en **temps serveur** et non en UTC, et l'or est passé de ~3 288 $ à ~4 044 $ sur la
période — les dollars ne sont pas comparables d'un régime de prix à l'autre. De plus, c'est un
**compte raw spread** : « un compte standard qui replie le coût dans la cotation sera
complètement différent ». Le dépôt trade précisément un compte où le coût est dans la cotation.

### 2.3 À l'inverse : sur les CFD, les spreads de l'or divergent fortement entre brokers

BestBrokers (via FX News Group, 5 octobre 2026), suivi de spreads en cTrader, moyenne par broker :
Fusion Markets **9 pips**, FP Markets 9, BlackBull 12, FxPro 17,03, IC Markets 19, Pepperstone
**23** — soit un écart de **2,56× entre le meilleur et le pire**, moyenne du groupe **14,84 pips**.
Sur le même panel, l'EUR/USD va de 0,03 à 0,48 pip.

→ [Gold CFD Spreads Vary by More Than 150% Between Major Brokers](https://fxnewsgroup.com/forex-news/institutional/gold-cfd-spreads-vary-by-more-than-150-between-major-brokers/)
— niveau D.

**Ce que ce chiffre prouve, et ce qu'il ne prouve pas.** Il ne prouve **rien** sur Deriv, sur le
spread réel du dépôt, ni sur la rentabilité. Il prouve une seule chose, et elle est utile : **le
spread de l'or en CFD est un choix commercial du broker, pas une propriété du marché**. Le modèle
de coûts du dépôt (`spread = close₀ × 5·10⁻⁵`) suppose l'inverse — une proportion fixe du prix —
et c'est une hypothèse de modélisation, pas une mesure.

### 2.4 Un résultat négatif sur l'or intraday, et il est pré-enregistré

Lucius (2026), preprint Zenodo, XAUUSD intraday, walk-forward mensuel causal du 2024-01 au
2026-02, **holdout 2026-03 scellé et jamais ouvert** :

- un HMM gaussien à 3 états **n'ajoute aucune valeur prédictive** sur la variance réalisée de la
  barre suivante : HAR-RV le domine sur QLIKE (**M15 : 0,164 contre 0,419** pour le HMM ;
  H1 : 0,213 contre 0,453), et il est significativement pire sous bootstrap groupé par pli ;
- les étiquettes d'état du HMM sont **instables selon les redémarrages aléatoires** ;
- un pilote « Transition-Escape » **pré-enregistré, gelé cryptographiquement et exécuté une seule
  fois rend un FAIL formel** sur sa grille de sept critères ; le modèle DS-lite (log-loss 0,1521)
  ne bat pas des baselines de durée/logistique (0,1476 / 0,1494) ;
- l'auteur conclut explicitement : « *No trading claim is made, and no economic backtest was run.* »

→ [Regimes Without Edge: Baseline-Gated Negative Results and Label Forensics for XAUUSD](https://zenodo.org/records/21343282)
— niveau B.

**Pourquoi c'est la source la plus utile du lot, malgré son statut de preprint.** Elle fait
exactement ce que le dépôt fait (walk-forward causal, holdout scellé, gel des hypothèses avant
exécution) et elle **rend un zéro**. Sa leçon méthodologique est directement transposable :
« *le nombre d'événements est une porte de puissance insuffisante ; l'équilibre des classes et
l'entropie de la cible doivent être filtrés avant l'exécution* ». Le dépôt n'a pas cette porte :
il pourrait construire une cible dégénérée et ne pas s'en apercevoir.

### 2.5 Le gap de fin de semaine de l'or est du même ordre que le stop

Croisement de deux sources : le dataset § 2.2 mesure un **gap médian de week-end de 7,60 $**
(48 % au-delà de 10 $), et le modèle de coûts du dépôt pour l'or vaut **1,043892 $** de spread
pour un risque de **10 €** par opération. Le dépôt clôture-t-il avant le week-end ? **Non : il
n'a aucune règle de ce type.** Voir § 4, affirmation A7.

---

## 3. Le fait externe : ce que la littérature dit du bitcoin

### 3.1 L'heure : pic d'activité entre 16:00 et 17:00 UTC

Brauneis, Mestel & Theissen (2024), *Review of Quantitative Finance and Accounting*, **1 940
paires de devises sur 38 plateformes crypto réparties sur cinq continents**, marché ouvert 24 h/24
et 7 j/7 sans contrainte institutionnelle :

- il existe des **motifs horaires prononcés** d'activité, de volatilité et d'illiquidité ;
- ils sont **remarquablement similaires d'une plateforme à l'autre, d'un fuseau à l'autre et d'une
  paire à l'autre** ;
- **l'activité, la volatilité et l'illiquidité culminent toutes entre 16:00 et 17:00 UTC** —
  l'heure du thé britannique ;
- les caractéristiques des plateformes et des paires expliquent une partie de cette communalité,
  **pas la totalité**.

→ [The crypto world trades at tea time: intraday evidence from centralized exchanges across the
globe](https://irep.ntu.ac.uk/id/eprint/51576/) (DOI :
[10.1007/s11156-024-01304-1](https://doi.org/10.1007/s11156-024-01304-1)) — **niveau A**.

**Ce que ça dit au dépôt, et c'est embarrassant.** Le seul fait externe de niveau A de ce document
place le pic d'activité crypto à **16:00-17:00 UTC**. La fenêtre « overlap » du dépôt est
**13:00-16:00 UTC** et s'arrête donc **une heure avant le pic**. Et la fenêtre « New York » du
dépôt (13:00-22:00) contient le pic, mais c'est la fenêtre la plus large et la moins
discriminante des quatre.

### 3.2 Le motif intrajournalier crypto est tri-phasé et se calque sur les sessions actions

Wątorek, Skupień, Kwapień & Drożdż (2023), *Chaos* **33**, 083146 — revue à comité de lecture —
bitcoin, ether, dogecoin et WINkLink, janvier 2020 à décembre 2022, mesures échantillonnées
**toutes les 10 secondes** :

- **trois phases d'activité renforcée** alignées sur les sessions **asiatique, européenne et
  américaine** — et l'absence d'ouverture/clôture, propre aux marchés 24/7, ne les efface pas ;
- un **pic d'activité dans les intervalles de 15 minutes, en particulier aux heures pleines**,
  « impliquant le rôle potentiel du trading algorithmique » ;
- les **rafales récurrentes d'activité du BTC et de l'ETH coïncident avec les heures de
  publication des grands rapports macroéconomiques américains** : emploi non agricole (*nonfarm
  payrolls*), CPI, et déclarations de la Réserve fédérale ;
- les facteurs **externes** à la dynamique interne expliquent les composantes répétables ; les
  facteurs internes sont « substantiellement aléatoires », en accord avec la distribution de
  Marchenko-Pastur.

→ [Decomposing cryptocurrency high-frequency price dynamics into recurring and noisy components](https://arxiv.org/abs/2306.17095)
(DOI : [10.1063/5.0165635](https://doi.org/10.1063/5.0165635)) — **niveau A**.

**Conséquence directe pour le dépôt.** Le fait que les rafales de volatilité du BTC coïncident
avec le NFP, le CPI et le FOMC **n'est pas une opinion** : c'est mesuré sur trois ans à 10
secondes. Or le moteur de risque du dépôt ne connaît aucun calendrier macro. Une entrée prise
quinze minutes avant un CPI n'est pas la même opération qu'une entrée prise un mardi à 03:00 UTC,
et le dépôt les traite à l'identique.

### 3.3 Le sous-jacent bitcoin n'a presque pas de mémoire directionnelle à 5 minutes

Petukhina, Reule & Härdle (2020), *The European Journal of Finance* (DOI :
[10.1080/1351847X.2020.1789684](https://doi.org/10.1080/1351847X.2020.1789684)), données 5
minutes, 1ᵉʳ juillet – 31 août 2018, onze cryptomonnaies :

- **l'autocorrélation d'ordre 1 des rendements est proche de zéro et majoritairement négative**
  (BTC : **−0,05** ; ETC −0,06 ; XMR −0,07 ; STR −0,09 ; DASH +0,01) ;
- les autocorrélations des rendements **au carré** et **absolus** sont positives et nettement
  non nulles (BTC : 0,13 et 0,24) — il y a donc **groupement de volatilité**, mais **pas de
  momentum directionnel** ;
- l'excès de kurtosis du BTC vaut **49,44** et la normalité est rejetée partout.

→ [Rise of the Machines? Intraday High-Frequency Trading Patterns of Cryptocurrencies](https://ar5iv.labs.arxiv.org/html/2009.04200)
— **niveau A** (version post-évaluation, pré-copie).

**Lecture honnête, et elle est double.** (i) La mémoire est dans la **volatilité**, pas dans la
**direction** : à 5 minutes, BTC n'offre pas de matière première au suivi de tendance naïf.
(ii) Mais une autocorrélation de −0,05 **n'est pas un edge exploitable** : la convertir en
rendement exigerait de connaître le coût, et ce coût est justement le sujet du § 5. Le chiffre
qui compte ici est l'**asymétrie** : 0,13 et 0,24 sur les rendements absolus contre −0,05 sur les
rendements signés. C'est un argument pour modéliser la volatilité, pas la direction.

### 3.4 Les jours : lundi fort, jeudi faible, week-end négatif — mais sur trois mois

Analyse Velo relayée par CoinDesk et DigitalToday, le 6-7 mai 2026 : bitcoin remonté d'environ
**31 %**, de moins de 63 000 $ à plus de 80 000 $, entre le 6 février et le 6 mai 2026. Le jour
est découpé en trois blocs de huit heures (Asie-Pacifique 00:00-08:00, Europe 08:00-16:00,
États-Unis 16:00-00:00 UTC) :

- rendement par bloc : **Asie-Pacifique 13 %**, **États-Unis 11,5 %**, **Europe 6,5 %** ;
- meilleure heure : **00:00-01:00 UTC**, « bougie de minuit UTC », +0,1 % de rendement moyen ;
  deuxième : 15:00 UTC ; pire : **06:00 UTC** ;
- par jour : **lundi ≈ +1,5 %**, mercredi ≈ +0,65 %, vendredi ≈ +0,3 %, **jeudi ≈ −0,55 %** ;
- **moyenne des jours de semaine ≈ +0,4 %, moyenne du week-end ≈ −0,25 %**.

→ [Asia drives bitcoin rebound; which hours and days deliver best returns](https://www.digitaltoday.co.kr/en/view/53560/asia-drives-bitcoin-rebound-which-hours-and-days-deliver-best-returns)
— **niveau D** (presse, relayant un fournisseur de données).

**Traiter ceci comme ce que c'est.** Trois mois, une seule tendance haussière, aucune correction
pour comparaisons multiples, aucune borne de confiance : c'est une **description d'un épisode**,
pas une saisonnalité. Elle est néanmoins mentionnée pour deux raisons. D'abord parce qu'elle
**contredit** le § 3.1 sur un point : ici la meilleure heure est 00:00-01:00 UTC, pas 16:00-17:00.
Ensuite parce que la ligne « week-end −0,25 % contre semaine +0,4 % » est la seule source de ce
document qui parle explicitement du week-end BTC — et que le dépôt trade le week-end BTC.

### 3.5 Le jour de la semaine crypto : le littoral est plus subtil que le folklore

La littérature à comité de lecture sur les effets de jour de la semaine dans les cryptos est
**contestée**, et je le dis plutôt que de choisir la source qui m'arrange :

- [Retail Weekends, Institutional Weekdays: Liquidity and Return Asymmetries in Cryptocurrency
  Markets](https://www.sciencedirect.com/science/article/abs/pii/S1062976926001407) (*Quarterly
  Review of Economics and Finance*, 2026) — annonce, dans son titre même, une asymétrie
  week-end/ semaine : les week-ends seraient dominés par le retail, les jours de semaine par les
  institutionnels. **Résumé payant, non lu intégralement.**
- [Are day-of-the-week effects in cryptocurrencies real? Intraday evidence from active and less
  active cryptocurrencies](https://www.sciencedirect.com/science/article/pii/S1544612326011621)
  (*Finance Research Letters*, 2026) — le titre pose la question, ce qui est déjà une réponse.
  **Résumé payant, non lu intégralement.**
- [Revisiting seasonality in cryptocurrencies](https://www.sciencedirect.com/science/article/pii/S1544612324004598)
  (*Finance Research Letters*, 2024) — **non lu intégralement**.

**Ce que je refuse de faire ici.** Trois titres ne font pas un fait. Je n'écrirai pas « le
week-end crypto est négatif » comme une conclusion : je le range en **hypothèse à tester sur les
données du dépôt** (R3), avec la note que le seul chiffre disponible (niveau D) va dans ce sens
et qu'aucune source de niveau A lue ne le confirme.

---

## 4. Le fait externe : suivi de tendance et retour à la moyenne, résultats négatifs compris

### 4.1 Le suivi de tendance court est mort depuis 2008/9 — et la variable qui le tue est le tick

Kurth, Eisler, Rej & Bouchaud (2026), arXiv:2607.01550, Econophysics Lab (Institut Louis
Bachelier) et Capital Fund Management, **~100 contrats à terme liquides, 1995-2025**, plus un
proxy représentatif de l'industrie CTA. C'est la source la plus lourde de ce document.

**Ce qui est mort.** Le PnL cumulé du portefeuille de trend rapide (EWM-5-20) est **plat depuis
2009** ; le Sharpe roulant sur cinq ans s'effondre d'une fourchette historique de 1 à 2,5 à un
niveau **statistiquement indiscernable de zéro après 2010**. Le ratio de Sharpe par vitesse de
signal :

| Horizon τ (jours) | Sharpe 1995-2009 | Sharpe 2009-2025 |
|---|---|---|
| 5 | 0,84 ± 0,27 | **0,12 ± 0,24** |
| 10 | 0,83 ± 0,27 | 0,22 ± 0,24 |
| 20 | 0,79 ± 0,27 | 0,27 ± 0,26 |
| 50 | 0,70 ± 0,27 | **0,40 ± 0,26** |

**L'ordre s'inverse** : avant 2009, plus le signal est rapide, mieux c'est ; après, le plus rapide
est le plus mauvais. Les auteurs qualifient la rupture d'**abrupte**, de **dépendante de la
vitesse**, **hétérogène par classe d'actifs** (les indices actions et les devises s'effondrent,
les taux et la plupart des matières premières non), et **sans reprise** malgré l'amélioration de
la liquidité.

**Ce qui survit, et c'est la trouvaille.** La variable transversale qui sépare les trends dégradés
des trends survivants est le **tick normalisé par la volatilité** : après 2008, le PnL du trend
s'est effondré sur les contrats **à tick petit**, tous horizons confondus, et **reste intact sur
les contrats à tick grand**. Ni la classe d'actifs ni la liquidité ne reproduisent cette
dichotomie. Le mécanisme proposé est une boucle auto-réalisatrice : les signaux de tendance
déclenchent des ordres directionnels dont l'impact renforce le mouvement qui a produit le signal ;
les market makers HFT se retirent devant un flux directionnel prévisible, ce qui casse la boucle
sur les carnets à tick petit et la laisse intacte sur les carnets à tick grand. **L'exécution
passive n'offre pas d'échappatoire**, ajoutent-ils.

Trois explications concurrentes sont **rejetées** par les auteurs sur des critères de timing, de
magnitude ou d'hétérogénéité : les contraintes de capacité (l'AUM des CTA a culminé en 2022,
**après** l'effondrement du PnL), l'électronification (graduelle, alors que la rupture est
abrupte), et le changement de régime dans l'interaction CTA/flux d'ordres.

→ [Is Trend Still Your Friend? A Microstructural Account of the Demise of Short-Term
Trend-Following](https://ar5iv.labs.arxiv.org/html/2607.01550) — **niveau B** (arXiv, non
encore évalué ; auteurs et institutions de premier rang, ce qui justifie de le prendre au
sérieux, pas de le tenir pour établi).

**Pourquoi c'est le résultat le plus important de ce document pour le dépôt.** Les deux
stratégies de production du dépôt sont **exactement dans la case dégradée** :

| Critère de Kurth et al. | `witness@1.1.1` (XAUUSD) | `trend_breakout@1.0.1` (BTCUSD) |
|---|---|---|
| Signal rapide ? | EMA 20/50 sur **M15** → la moyenne lente couvre **12,5 h** | canal 20 + EMA 100 sur **M15** → **25 h** |
| Tick petit normalisé par la volatilité ? | tick 0,01 $ sur un or à ~4 000 $ (M15) | pas de tick au sens d'un carnet centralisé |
| Classe d'actifs | matière première (**plutôt épargnée** par l'étude) | crypto (**absente** de l'étude) |

Les auteurs mesurent des **jours**, le dépôt mesure des **heures** : le dépôt est donc **encore
plus rapide** que le « plus rapide » de l'étude, où le Sharpe n'est plus que de 0,12. C'est
exactement le régime que l'étude documente comme mort. La réserve importante : l'étude porte sur
des **futures**, pas sur des CFD, et l'or compte parmi les classes d'actifs épargnées. Le transfert
n'est donc pas automatique — mais il est **défavorable**, et il n'existe aucune source lue qui
dise l'inverse.

### 4.2 La baseline externe du retour à la moyenne intraday sur BTC : sélectionnée, puis perdante

Wu & Pinsky (2026), *Journal of Risk and Financial Management* **19**(9), 692 — **revue à comité
de lecture**. Prix horaires Kraken **2016-2025**. Chaque jour est coupé en deux sessions
complémentaires de 12 h ; 25 combinaisons ordonnées de positions (cash, long, court, momentum,
retour à la moyenne) sont évaluées sur les **12 frontières horaires non redondantes** :

- la sélection sur l'échantillon complet désigne **Reversal/Reversal avec départ à 08:00 UTC**
  pour le BTC et Long/Reversal à 05:00 UTC pour l'ETH — autrement dit, une règle de **retour à la
  moyenne** ;
- les règles sélectionnées battent l'achat-conservation en richesse terminale, en drawdown et en
  Sharpe **sur le chemin observé de l'échantillon complet** ;
- **ces différences ne sont pas statistiquement significatives** en tests bootstrap appariés ;
- le test de **Supériorité Prédictive de Hansen ne rejette pas l'hypothèse nulle après prise en
  compte de la recherche sur 300 combinaisons** frontière×stratégie ;
- un exercice de **holdout chronologique** (sélection 2016-2020, évaluation 2021-2025) montre que
  la règle ETH persiste, **mais que la règle BTC sélectionnée en apprentissage sous-performe
  l'achat-conservation** ;
- les auteurs concluent : « *evidence of asset-specific historical return structure, not proof of
  a stable or readily implementable abnormal-profit opportunity* » ;
- **et ils ajoutent une limite décisive** : les scénarios de coût de **0 à 2 points de base sont
  illustratifs et excluent le slippage, l'impact de marché, le coût d'emprunt et le financement**.

→ [On the Performance of Lagged Momentum and Reversal Strategies Across Daytime and Overnight
Sessions in Bitcoin and Ethereum Cryptocurrencies](https://econpapers.repec.org/article/gamjjrfmx/v_3a19_3ay_3a2026_3ai_3a9_3ap_3a692-_3ad_3a2034368.htm)
— **niveau A**.

**C'est le miroir exact du dépôt, et le miroir est sombre.** Le dépôt a fait ce que cette étude
décrit : il a mesuré une variante (`vwap_pullback` filtrée sur l'overlap, PF 1,100 sur 175
opérations) qui bat la référence sur l'échantillon, avec un **t de 0,58** — c'est-à-dire une
espérance **non distinguable de zéro**, exactement le diagnostic de l'étude. Wu & Pinsky
apportent en plus la pièce que le dépôt n'a pas encore produite sur cette variante : **le holdout
chronologique**. Et leur holdout dit non.

### 4.3 Sur crypto, tout portefeuille long-short de moins d'une semaine a un rendement moyen négatif

Snippet indexé d'un papier sur le momentum temporel et transversal en marché crypto (source
[ACFR, Auckland University of Technology](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)) :
« *Every long-short portfolio with a holding period of less than a week yields a negative mean
return* ».

**Je cite ce chiffre comme un indice, pas comme un fait** : je n'ai lu que le snippet indexé, le
PDF complet n'était pas accessible depuis cette session. Il est signalé parce qu'il **converge**
avec § 4.1 (le rapide est mort), § 4.2 (le retour à la moyenne horaire BTC ne survit pas au
holdout) et § 3.3 (autocorrélation directionnelle nulle à 5 minutes). Quatre sources
indépendantes, quatre chemins différents, même direction. **Il est classé non vérifié dans le
tableau § 6.**

### 4.4 Le momentum temporel intraday existe ailleurs — mais pas là où le dépôt le cherche

Li, Sakkas & Urquhart (2022), *Journal of Financial Markets* **57** : le momentum temporel
intraday est documenté sur les **indices actions internationaux**, où le rendement de la première
demi-heure prédit celui de la dernière demi-heure, avec des liens vers les caractéristiques de
marché.

→ [Intraday time series momentum: Global evidence and links to market characteristics](https://www.sciencedirect.com/science/article/abs/pii/S138641812100001X)
— **niveau A**, résumé lu, texte intégral non accessible.

**Ce que ça dit au dépôt, précisément.** Le momentum intraday n'est pas une légende : il est
établi **sur les indices actions**, où il existe une clôture vers laquelle les prix sont tirés.
Le BTC et l'or CFD n'ont pas de clôture au sens d'un fixing d'indice unique. La littérature
positive et la littérature négative **ne se contredisent donc pas** : elles parlent de marchés
différents. Le dépôt cherche une structure de type « première demi-heure → dernière demi-heure »
sur des marchés qui n'ont pas cette microstructure.

---

## 5. Le fait externe : quel coût une stratégie doit battre

### 5.1 Le coût bitcoin du dépôt, et pourquoi il est structurel

Chiffres internes (repris, non re-mesurés) :

| Grandeur BTCUSD | Valeur | Conséquence |
|---|---|---|
| Spread observé en base | **18,424 $** et **19,443 $** | pour une limite de risque de 34,8 $ |
| Bande d'entrée `entry_zone_atr: 0.13` | ≈ **13 $** de budget de dérive | le spread **mange déjà 100 %+** du budget avant que le prix bouge |
| Idem à 0,1 ATR | ≈ **19,2 $** | le spread en consommait **96 %** |
| Spread du modèle de coûts | `close₀ × 5·10⁻⁵` ≈ **14,25 $** sur la fenêtre M15 de campagne | le modèle est **sous** le réel observé |
| Commission | **0,50 €/opération** | sur un risque de **10 €**, c'est **5 % du risque** par opération, avant spread et slippage |
| `trend_breakout@1.0.1`, jeu complet | 802 opérations, PF **0,8748**, **+101,62 €** | à 2× coûts, PF **0,736** |

Le diagnostic interne du 2026-10-09 est plus précis que toute source externe trouvée :
sur six paires refusées, **0,13 en sauve deux**, et **un tiers des dérives observées est trop
grand pour n'importe quelle bande raisonnable** — même à 0,20 ATR, deux paires restent refusées.
Sa conclusion : « *c'est la fenêtre de trading ou le choix du marché qu'il faudra revoir, pas le
paramètre* ».

→ Source interne : [2026-10-09-bande-btcusd-effet-reel.md](execution-diagnostic/2026-10-09-bande-btcusd-effet-reel.md).

### 5.2 Ce que la littérature externe ajoute : le spread est un choix, pas une constante

Deux sources externes, deux marchés, une même leçon :

1. **Or, CFD, six brokers** (§ 2.3) : de **9 à 23 pips**, un facteur **2,56** d'écart, avec une
   moyenne de groupe de 14,84 pips. Un broker qui gagne sur l'EUR/USD ne gagne pas forcément sur
   l'or. « *A broker advertising an attractive minimum gold spread may still produce substantially
   higher trading costs during other parts of the day* » (FNG, niveau D).
2. **Or, MT5, un broker, un an** (§ 2.2, niveau C) : spread médian **0,16 $**, identique sur les
   24 heures, dépassant 1,00 $ sur **3 barres sur 70 546**. Un compte « raw spread ».

Ces deux sources **ne se contredisent pas** : elles mesurent deux choses différentes — le spread
d'un carnet brut, et le spread commercialisé d'un CFD. Ce que le dépôt doit en retenir est la
conséquence méthodologique : **le modèle de coûts en proportion fixe du prix
(`close₀ × 5·10⁻⁵`) est une hypothèse, et cette hypothèse est fausse dans les deux sens** — trop
optimiste pour l'or (1,04 $ modélisé, 0,16 $ chez un broker raw, jusqu'à 23 pips ≈ 2,3 $ chez
d'autres en CFD), et trop optimiste pour le bitcoin (14,25 $ modélisé contre 18,424 $ observé).

### 5.3 L'ordre de grandeur du coût à battre

Il n'existe pas, dans la littérature lue, de seuil universel de « profit factor minimum ». Le
seuil de **1,20** du dépôt est donc une **convention de projet** (`docs/research/thresholds.json`,
`version: campaign-20261007`), pas un résultat externe — et il faut le dire, parce que la
littérature grise en propose d'autres sans plus de fondement (les pages de glossaire de courtiers
et de fournisseurs d'indicateurs annoncent typiquement « 1,25 à 1,5 bon, au-dessus de 2 suspect »,
sans source).

En revanche, ce que la littérature lue permet d'établir, c'est **la structure du coût à battre** :

| Marché | Coût aller-retour par opération | En proportion du risque de 10 € |
|---|---|---|
| XAUUSD (modèle du dépôt) | spread 1,04 $ + slippage 0,093 $ + commission 0,50 € | **≈ 7 %** du risque |
| BTCUSD (modèle du dépôt) | spread 14,25 $ + slippage 8,40 $ + commission 0,50 € | **≈ 15 %** du risque |
| BTCUSD (spread réel observé) | spread 18,42 $ + slippage + commission | **≈ 19 %** du risque |

*Note de méthode : la conversion dollars→euros et l'expression en fraction du risque sont des
ordres de grandeur calculés à partir des `average_spread` / `average_slippage` et de la
commission lus dans `docs/research/stats/*.json` ; elles servent à comparer les deux marchés
entre eux, pas à prédire un résultat.*

C'est la réponse la plus utile que la recherche externe donne à la question « quel coût faut-il
battre » : ce n'est pas un profit factor, c'est **un cinquième de l'unité de risque sur le
bitcoin contre un quatorzième sur l'or**. Toute règle calibrée à l'identique sur les deux marchés
est calibrée faux sur l'un des deux.

---

## 6. Tableau final : affirmation → verdict → preuve

**Verdicts :** ✅ **respectée** · ❌ **violée** · ➖ **non testable** (le dépôt ne peut pas la
tester avec ce qu'il possède aujourd'hui, ou la source n'est pas assez solide pour trancher).

| # | Affirmation externe | Source | Verdict | Preuve interne |
|---|---|---|---|---|
| A1 | L'or est un marché où les transitions de tendance sont **initiées par la séance européenne** (4,1× l'asiatique) | Rao 2026 (niv. B) | ✅ | Le `witness@1.1.1` est classé XAUUSD sans contrainte d'horaire, et ses 38 opérations de validation couvrent toutes les séances : le fait n'est pas exploité, mais rien ne le contredit. Aucune mesure par séance n'existe sur l'or dans le dépôt → **à produire (R2)** |
| A2 | Le spread de l'or est **plat sur les 24 h** (0,16 $ médian) ; c'est la volatilité qui varie (2,4×) | Zenodo 2026 (niv. C) | ✅ sur l'or | `stats/XAUUSD-witness@1.1.1.json` : spread modélisé 1,043892 $ pour une bande de 0,78 $, soit 40 % du budget de bande — soit **déjà modeste**. `trend_breakout@1.0.1.yaml` note que « l'or n'est pas concerné et garde 0,1 » |
| A3 | **Le pic d'activité crypto est 16:00-17:00 UTC** (1 940 paires, 38 plateformes) | Brauneis et al. 2024 (niv. A) | ❌ | `indicators/session.py:70-74` : l'`OVERLAP` du dépôt est **13:00-16:00 UTC**, il **finit une heure avant le pic**. La fenêtre `NEW_YORK` (13:00-22:00) le contient mais est la plus large et la moins discriminante |
| A4 | Le BTC a trois phases d'activité (Asie/Europe/US), des **pics aux heures pleines**, et des rafales **synchronisées sur NFP, CPI et Fed** | Wątorek et al. 2023, *Chaos* (niv. A) | ➖ / ❌ | Non testable en l'état : **le moteur de risque n'a aucun filtre horaire ni calendrier macro** (`config/strategies/*.yaml` : aucune clé de séance par défaut ; `vwap_pullback.allowed_sessions` vaut `()`). Ce qui est violé, c'est la conséquence pratique : le dépôt traite une entrée à 15 mn d'un CPI comme une entrée à 03:00 UTC |
| A5 | Les portefeuilles crypto **long-short de moins d'une semaine** ont un rendement moyen **négatif** | snippet ACFR (**non vérifié**) | ➖ | Le dépôt a mesuré, sur 1 973 signaux `vwap_pullback`, que la moitié est refusée faute de place et qu'un filtre de tendance à 0,05 donne PF 1,555 sur **117** opérations. Le dépôt n'a **jamais** mesuré la durée de détention par rapport au rendement : `average_position_duration_s` vaut **17 681 s ≈ 4,9 h** (BTC) et **11 913 s ≈ 3,3 h** (or) — les deux sont **sous la semaine**, donc dans la zone annoncée négative, mais le test direct n'a pas été fait |
| A6 | Une règle de retour à la moyenne horaire **BTC**, sélectionnée sur 2016-2020, **sous-performe l'achat-conservation** sur 2021-2025 | Wu & Pinsky 2026, *JRFM* (niv. A) | ➖ | Le dépôt a le candidat exact (`vwap_pullback`, PF 1,100 sur 175 ops, **t = 0,58**, `axe-B-filtres.md` § 6) mais **ne l'a jamais soumis à un holdout chronologique**. C'est le test manquant le moins coûteux du projet : `axe-B-filtres.md` § 10 point 1 le demande déjà |
| A7 | L'or a un **gap de fin de semaine** médian de 7,60 $, 48 % au-delà de 10 $ | Zenodo 2026 (niv. C) | ❌ | Le dépôt n'a **aucune règle de clôture avant le week-end** pour l'or. Un stop vaut `1,5 × ATR`, soit un ordre de grandeur comparable au gap médian : une position ouverte le vendredi soir est exposée à un saut que son stop ne peut pas exécuter. À vérifier dans les décisions réelles, mais l'absence de règle est factuelle |
| A8 | Le **suivi de tendance court est mort depuis 2008/9**, et la variable discriminante est le **tick normalisé par la volatilité** | Kurth, Eisler, Rej & Bouchaud 2026 (niv. B) | ❌ | Les **deux** stratégies de production sont rapides : `witness@1.1.1` = EMA 20/50 en **M15** (moyenne lente ≈ 12,5 h), `trend_breakout@1.0.1` = canal 20 + EMA 100 en **M15** (≈ 25 h). Résultats mesurés, cohérents avec l'étude : PF **0,88** (or, validation) / **0,839** (BTC, validation), 16 plis rentables sur 44 pour l'or (36 %), p-value 0,63 |
| A9 | Sur crypto à 5 minutes, **l'autocorrélation directionnelle est nulle ou négative** (BTC : −0,05), mais les rendements absolus sont corrélés (0,24) | Petukhina, Reule & Härdle 2020, *EJF* (niv. A) | ➖ | Le dépôt n'a jamais mesuré l'autocorrélation de ses propres rendements M15. Il a mesuré quelque chose de proche : le filtre de tendance (`trend_of`, pente d'une barre) **sépare** (PF 1,555 sur 117 ops, gradient monotone), ce qui suggère qu'à M15 **il reste une structure** que l'étude ne voit pas à 5 minutes. Deux résolutions, deux résultats : **non testable en l'état** |
| A10 | Le momentum temporel **intraday** est établi — sur les **indices actions** | Li, Sakkas & Urquhart 2022, *JFM* (niv. A) | ✅ | Le dépôt applique une structure de type momentum aux **deux marchés qui n'ont pas de clôture d'indice** : XAUUSD et BTCUSD. Il ne prétend pas transposer l'étude ; il ne transpose simplement pas la prémisse |
| A11 | Un modèle de **régimes** sur l'or intraday **échoue** à battre une baseline de volatilité, et son pilote pré-enregistré rend FAIL | Lucius 2026 (niv. B) | ✅ (leçon de méthode non appliquée) | Le dépôt a **déjà** réfuté son propre filtre de régime : 16 candidats sur 16 rejetés, puis 8 sur 8 en H1 natif ([2026-10-09-breakout-filter-refuted.md](2026-10-09-breakout-filter-refuted.md)). Il **n'a pas** la porte de l'étude : « *le nombre d'événements est une porte de puissance insuffisante ; l'équilibre des classes et l'entropie de la cible doivent être filtrés avant l'exécution* » |
| A12 | Le **spread des CFD or diverge de 2,56× entre brokers** et n'est pas une propriété du marché | BestBrokers/FNG 2026 (niv. D) | ✅ partiellement | Le dépôt modélise le spread en **proportion fixe du prix**, et il en a mesuré le biais sur BTC (14,25 $ modélisé contre 18,424 $ observé, soit **+29 %**). Sur l'or, il n'a **jamais** confronté son modèle à un spread observé : 1,043892 $ modélisé, **aucune mesure de base** citée, alors que les décisions de risque de l'or ont bien été enregistrées |
| A13 | Le coût bitcoin à battre est **structurel**, pas marginal : le spread consomme tout le budget de bande | Interne + FNG (niv. D) | ✅ | `trend_breakout@1.0.1.yaml` : bande 0,1 ATR ≈ 19,2 $ pour un spread de 18,424 $ = **96 %** ; à 0,13 ATR ≈ 13 $, le spread dépasse 100 %. Diagnostic : sur 6 paires refusées, **0,13 en sauve 2**, et 2 restent refusées même à 0,20 ATR |
| A14 | Le seuil de PF net **≥ 1,20** est une **convention de projet**, pas un résultat externe | Aucune source externe ne l'établit | ➖ | `docs/research/thresholds.json` : `min_profit_factor_net: 1.2`, `min_trades: 30`, `version: campaign-20261007`. Aucune des 18 sources lues ne propose de seuil universel. Le seuil est donc **défendable comme choix**, mais il ne peut pas être invoqué comme une exigence de la littérature |

**Décompte :** 14 affirmations · **5 respectées** (A1, A2, A10, A11, A12, A13 — dont deux
partiellement) · **4 violées** (A3, A7, A8, et la conséquence pratique de A4) · **5 non
testables** (A4 partiellement, A5, A6, A9, A14).

**Le tri qui compte :** trois des quatre violations (A3, A4-pratique, A8) ont **la même cause** —
le dépôt n'a **aucune contrainte horaire ni calendaire**, ni dans ses stratégies, ni dans son
moteur de risque. Ce n'est pas une coïncidence : c'est le seul levier que la littérature lue
désigne **trois fois** comme le premier à activer.

---

## 7. Recommandations classées

Chaque recommandation porte **le chiffre qui la motive** et **le coût de sa mise en œuvre**.
La colonne « nature » distingue ce qui est une **décision** de ce qui est une **mesure** : le
dépôt a déjà payé cher pour apprendre la différence.

### R1 — Mesurer le spread BTCUSD par heure sur plusieurs jours, avant tout autre réglage

**Ce qui la motive :** le spread observé (18,424 $) vient de **onze décisions d'une seule
journée**. Sa fréquence horaire est **inconnue**, et c'est écrit noir sur blanc dans le diagnostic
existant : « si le spread s'élargit durablement de 18 à 30 dollars, aucune bande fondée sur 0,1 ou
0,13 ATR ne suffira ». Trois sources externes convergent : le spread CFD est un choix commercial
(2,56× d'écart inter-brokers, § 2.3), il s'élargit « pendant les périodes de faible liquidité ou
de risque élevé », et sur un carnet brut il est plat alors qu'il est très dispersé en CFD.

**Nature :** mesure pure. Aucun paramètre touché, aucune promotion.
**Chiffre à produire :** médiane et 90ᵉ centile du spread par heure UTC, sur ≥ 5 jours ouvrés et
un week-end complet, exprimés en **fraction de l'ATR M15**.
**Coût :** le plus faible de la liste. Les décisions de risque sont **déjà persistées** avec leur
spread (`average_spread` figure dans chaque rapport) : c'est un script d'agrégation, pas une
instrumentation nouvelle. Dépend de la couverture réelle de la base : à vérifier d'abord.
**Gain si le spread est plat :** le levier « fenêtre de trading » est confirmé, et on arrête de
chercher un paramètre. **Gain s'il est dispersé :** on a la carte des heures où **ne pas** entrer,
et A13 cesse d'être une hypothèse.

### R2 — Mesurer, pour l'or, la performance par séance et par heure UTC

**Ce qui la motive :** la source la plus directement transposable (Rao 2026) dit que les
transitions de tendance de l'or sont **4,1× plus probables en séance européenne** qu'en séance
asiatique (0,258 contre 0,062, χ² = 31,3, p < 0,001). Le dépôt ne possède **aucune** mesure par
séance sur l'or : `witness@1.1.1` produit 38 opérations de validation toutes séances confondues,
et la seule mesure par séance du dépôt a été faite sur `vwap_pullback` en BTCUSD
(`axe-B-filtres.md` § 6).

**Nature :** mesure pure, sur un découpage **annoncé d'avance** (les quatre fenêtres existantes de
`session.py`).
**Chiffre à produire :** PF, nombre d'opérations et espérance par opération, par séance et par
heure UTC, **sur l'or uniquement**.
**Coût :** faible — `session_at` existe déjà, les jeux M15 long et court existent, et aucune
nouvelle règle n'est nécessaire.
**Piège à éviter :** 38 opérations réparties sur 24 heures, c'est **1,6 opération par heure**. Toute
conclusion par heure sera du bruit ; la mesure doit être faite **par séance** (4 classes), pas par
heure (24 classes), et l'absence de puissance doit être écrite dans le rapport.

### R3 — Tester la contrainte de jour (`week-end`, `jeudi`) sur le BTCUSD

**Ce qui la motive :** le seul chiffre disponible sur le week-end BTC est « semaine ≈ +0,4 %,
week-end ≈ −0,25 % », mais il vient d'une **analyse de trois mois** (niveau D). Le dépôt possède
de quoi trancher : **59 999 bougies M15** du 2025-01-21 au 2026-10-09, soit **neuf fois** la
fenêtre de l'analyse presse. Il n'a jamais mesuré la dimension jour.

**Nature :** mesure, avec un découpage **déclaré avant** l'essai (7 jours, ou 2 classes
semaine/week-end). **À annoncer comme test d'hypothèse unique, pas comme balayage** : chercher le
meilleur sous-ensemble de jours serait exactement ce que le protocole walk-forward existe pour
empêcher.
**Chiffre à produire :** PF net et nombre d'opérations par jour de semaine, sur le jeu complet,
**et sur les deux moitiés** de la série.
**Coût :** faible (agrégation par jour).
**⚠️ Étiqueté :** si le test se transforme en « quels jours garder ? », c'est de
l'**optimisation sur le passé** et cela doit être signalé comme telle et refusé.

### R4 — Soumettre la variante `vwap_pullback` filtrée à l'overlap à un holdout chronologique

**Ce qui la motive :** c'est **le seul candidat positif non promu** du dossier, et il l'est pour de
mauvaises raisons. PF 1,100 sur 175 opérations, positif dans les deux moitiés (1,153 / 1,053),
mais **t = 0,58** : l'espérance par opération n'est **pas distinguable de zéro**. Wu & Pinsky
(2026, *JRFM*) ont fait exactement ce test sur une règle de retour à la moyenne BTC horaire, et
leur holdout 2021-2025 conclut que **la règle BTC sous-performe l'achat-conservation**.

**Nature :** test hors échantillon, sur la variante **gelée**, exécuté **une seule fois**.
**Chiffre à produire :** PF net, opérations et espérance sur la fenêtre de holdout, avec la
réserve d'échantillon.
**Coût :** moyen — un découpage supplémentaire et une exécution unique. `axe-B-filtres.md` § 10
point 1 le demande déjà : ce n'est pas une idée neuve, c'est une dette non payée.
**Ce que ça vaut :** soit la variante survit et devient le meilleur candidat du projet, soit elle
meurt **proprement**, et le dépôt arrête d'y revenir. Les deux issues sont utiles ; c'est
l'ambiguïté actuelle qui coûte.

### R5 — Ne pas chercher de nouvelle stratégie rapide, et le documenter comme une décision

**Ce qui la motive :** Kurth, Eisler, Rej & Bouchaud (2026) : sur ~100 contrats et 30 ans, le
Sharpe du trend le plus rapide est passé de **0,84 à 0,12** après 2008/9, et l'ordre
vitesse/performance s'est **inversé** (τ = 50 jours : 0,40 après 2008, contre 0,70 avant). La
variable discriminante est le **tick normalisé par la volatilité** : petit tick, PnL effondré.

**Nature :** décision. Pas une mesure, pas un réglage — un arrêt.
**Chiffre qui la motive :** 0,12 ± 0,24 (Sharpe du trend le plus rapide, 2009-2025, dans
l'étude) contre 0,84 ± 0,27 (1995-2009).
**Coût :** **négatif** — c'est la recommandation qui économise du travail. Chaque semaine passée à
régler un canal M15 est une semaine prise sur l'or natif H1 et sur la question du coût.
**⚠️ Ce que ça ne dit pas :** l'étude porte sur des **futures**, pas des CFD, et l'or est dans une
classe d'actifs qu'elle trouve **épargnée**. Elle ne dit donc pas « abandonner l'or », elle dit
« ne pas attendre d'une règle M15 qu'elle vive là où le marché a changé de régime ». La piste BTC
H1 native (PF 1,233 sur 136 opérations 2011-2024) est **cohérente** avec cette lecture : c'est un
signal **plus lent**.

### R6 — Ajouter au laboratoire les deux portes de méthode de Lucius (2026)

**Ce qui la motive :** le seul résultat négatif **pré-enregistré** trouvé sur l'or montre que la
cause du FAIL n'était pas l'absence d'edge mais une **cible dégénérée** : « le M15 était dégénéré :
chaque épisode de haute volatilité observé sort dans l'horizon gelé de 16 barres, donc l'étiquette
a une variance de classe nulle (6 217 positifs, 0 négatif) et ne peut pas mesurer une compétence
de classification ». Le dépôt n'a **aucune** porte d'équilibre des classes ni d'entropie de la
cible.

**Nature :** ajout de porte au laboratoire. N'affecte aucune stratégie, aucun backtest.
**Chiffre qui la motive :** 6 217 positifs / **0** négatif — la mesure était vide et le protocole
ne l'a pas vu.
**Coût :** faible, et **une seule fois**. C'est un contrôle qui refuse un candidat avant de le
mesurer, pas un indicateur.
**Pourquoi c'est classé haut :** le dépôt a déjà produit **34 rejets sur 34** puis **16 sur 16**
et **8 sur 8** en H1 natif. Une partie de ce coût de calcul a peut-être été dépensée sur des
cibles sans variance. La porte est bon marché et elle est **transverse**.

### R7 — Corriger le bandeau de `config/`, ou le supprimer

**Ce qui la motive :** la contradiction du § 1.3. Les deux manifestes annoncent « PF net mesuré
**0,92** (BTC) et **1,04** (or) », quand la porte `costs` de leurs propres rapports de campagne
mesure **0,839** et **0,884**. L'écart sur le BTC est de **10 %** sur la grandeur même qui justifie
la dérogation de plafond.

**Nature :** correction documentaire. **Ne touche à aucun paramètre** — `entry_zone_atr`,
`max_mode` et le reste sont inchangés. Hors du périmètre du Stream D, qui n'écrit pas dans
`config/` : à porter par le Lead.
**Coût :** quelques minutes.
**Pourquoi ça compte :** le bandeau est le seul endroit où un lecteur pressé trouve l'état de
validation. Un chiffre optimiste de 10 % à cet endroit précis est un risque de décision, pas une
coquille.

### R8 — Ne pas importer la fenêtre « overlap » du dépôt dans les stratégies BTC sans la redéfinir

**Ce qui la motive :** § 3.1, le seul fait de **niveau A** de ce document sur les heures crypto :
le pic d'activité, de volatilité et d'illiquidité est à **16:00-17:00 UTC** sur 1 940 paires et 38
plateformes. L'`OVERLAP` du dépôt (`session.py:70-74`) est **13:00-16:00 UTC**.

**Nature :** décision de définition, à prendre **avant** toute mesure horaire sur le BTC — sinon la
mesure R2/R3 utilisera une grille dont une source de niveau A dit qu'elle rate le pic d'une heure.
**Chiffre qui la motive :** 16:00-17:00 UTC (pic) contre 13:00-16:00 UTC (fenêtre actuelle).
**Coût :** quasi nul **maintenant** ; élevé plus tard, parce qu'une mesure faite sur la mauvaise
grille devra être refaite — et `axe-B-filtres.md` § 6 montre déjà que la fenêtre large
(overlap + Londres + New York) **détruit** l'effet (PF 0,981 contre 1,100), donc le choix de la
grille n'est pas cosmétique.
**⚠️ Portée honnête :** l'étude porte sur des **crypto centralisées**, pas sur le CFD Deriv. Le
décalage d'une heure peut très bien ne pas exister chez le broker. C'est précisément pour ça que
R1 précède R3 : **mesurer le spread par heure**, c'est aussi mesurer l'heure du pic.

### R9 — Ne pas promouvoir, et ne pas relancer de campagne sur les paramètres

**Ce qui la motive :** rien dans cette recherche ne change le verdict du dépôt. XAUUSD
`witness@1.1.1` : PF net **0,88** (validation), **0,712** à 2× coûts, **16 plis rentables sur 44**,
**p-value 0,63**. BTCUSD `trend_breakout@1.0.1` : PF net **0,839** en validation, **0,736** à
2× coûts, rétention hors échantillon **0,00**, score de stabilité **0,29**. Le seuil est **1,20**.
La recherche externe ne fournit **aucun** motif de relever un plafond, et elle fournit un motif
supplémentaire de ne pas re-régler : Wu & Pinsky ont cherché sur **300 combinaisons** et le test de
Hansen **ne rejette pas** l'hypothèse nulle ; Kurth et al. montrent que le problème n'est pas dans
les paramètres mais dans le régime.

**Nature :** décision.
**Coût :** nul. **Gain :** évite de dépenser une campagne sur 54 nouvelles hypothèses, dont le
dépôt a déjà mesuré une fois qu'elles produisent un survivant à p = 0,3467 pour un seuil de
Benjamini-Hochberg de 0,0019.

### Ce qu'il faut éviter — la liste explicite

| À éviter | Pourquoi |
|---|---|
| **Élargir encore `entry_zone_atr`** | Le diagnostic interne est sans ambiguïté : sur 6 paires refusées, **0,13 en sauve 2**, et **2 restent refusées même à 0,20 ATR**. Le paramètre agit sur le corps de la distribution, la queue longue ignore le paramètre. Kurth et al. donnent la raison de fond : le problème n'est pas le coût, c'est le signal |
| **Optimiser les paramètres sur le passé** | 54 hypothèses ont déjà produit un candidat à **9 portes sur 9** avec une p-value de **0,3467** pour un seuil de **0,0019** — soit 180× au-dessus. Toute recommandation qui se ramène à « essayer d'autres valeurs » doit être signalée comme telle |
| **Chercher un effet par heure sur l'or** | 38 opérations de validation ÷ 24 heures = **1,6 opération par heure**. Toute table horaire sur l'or sera du bruit. Par séance (4 classes), la mesure devient défendable ; par heure, non |
| **Traiter « or » et « bitcoin » comme un seul problème** | Coût ≈ **7 %** du risque sur l'or contre **19 %** sur le BTC (spread réel). Le spread or est plat sur 24 h (niveau C) et le spread BTC consomme ≥ 96 % du budget de bande. Une règle commune est fausse sur l'un des deux |
| **Copier une « stratégie de base » trouvée en ligne** | Les deux études contemporaines outillées lues **concluent négativement** sur ces deux marchés (Rao/Lucius sur l'or intraday, Wu & Pinsky sur le BTC horaire), et l'étude positive sur le momentum intraday porte sur les **indices actions**. Il n'existe pas de stratégie de base à importer ; il existe des **régimes** à respecter |
| **Invoquer le seuil de 1,20 comme une exigence externe** | C'est une convention de projet (`thresholds.json`, `campaign-20261007`). Aucune des 18 sources lues ne propose de seuil universel de profit factor |

---

## 8. Ce que cette recherche n'a PAS pu établir

Écrire ce qu'on ne sait pas est la partie utile du document. Les cinq trous suivants sont
**réels** et aucun n'est comblé par une source lue.

1. **La fréquence horaire du spread BTCUSD chez le broker du dépôt.** Inconnue. Elle est
   aujourd'hui déduite de **onze décisions d'une seule journée** (18,424 $ et 19,443 $). Aucune
   source externe trouvée ne documente le spread d'un **CFD crypto Deriv** par heure : les études
   portent sur des carnets centralisés, et les pages broker consultées ne donnent pas de série
   horaire. C'est le trou le plus coûteux, parce que **trois recommandations en dépendent** (R1,
   R3, R8).

2. **Le comportement intrajournalier propre au CFD Deriv.** Les faits horaires de niveau A (§ 3.1,
   § 3.2) sont mesurés sur des **plateformes crypto centralisées** — 38 plateformes, pas un
   broker de CFD ; et § 4.1 sur des **futures**. Le broker ajoute sa propre microstructure, son
   propre spread commercial (jusqu'à **2,56×** d'écart entre brokers, § 2.3) et sa propre
   politique d'exécution. **Aucune source externe ne peut dire si le pic de 16:00-17:00 UTC existe
   chez Deriv.** Il faut le mesurer, pas le lire.

3. **Le nombre de plis walk-forward nécessaires pour trancher entre 39 % et 50 %.** Le dépôt est
   passé de 6 à 46 plis sur la même question et le verdict est passé de « 1 pli sur 6 » à
   « 18 sur 46, il manque une dizaine de plis ». Aucune source lue ne dit combien de plis sont
   suffisants, ni comment le déterminer. La question « la médiane de 39 % est-elle un échec ou un
   manque de puissance ? » reste **ouverte**, et c'est une question de protocole que la
   littérature externe ne traite pas.

4. **Le coût réel d'un aller-retour chez Deriv, tout compris.** Le modèle du dépôt
   (`spread = close₀ × 5·10⁻⁵`, `slippage = close₀ × 2·10⁻⁵`, `commission = 0,50 €`) est une
   hypothèse. Externalement, je n'ai trouvé **aucune** mesure publiée du coût aller-retour d'un
   CFD BTCUSD : les sources crypto parlent de **0 à 2 points de base** (Wu & Pinsky, et ils
   précisent que ces scénarios excluent slippage, impact, emprunt et financement), ce qui est
   **deux à quatre fois plus optimiste** que le modèle du dépôt. Les deux mondes ne se parlent pas,
   et c'est un fait à retenir : **la littérature crypto chiffre des coûts que le retail ne paie
   pas.**

5. **Une mesure de niveau A sur la saisonnalité horaire de l'or.** Ce qui existe est un preprint
   de **1 130 barres H1** (moins que ce que le dépôt possède) et un dataset d'un seul broker. Le
   seul fait de niveau A de ce document concerne le **bitcoin**, pas l'or. La meilleure source
   sur l'or est donc **moins bonne que la mesure interne du dépôt** — ce qui est une raison de
   plus de mesurer par séance sur l'or (R2) plutôt que de chercher une source.

**Et une limite de méthode, sur ce document lui-même.** Sur les 18 sources citées,
**5 ont été lues intégralement** et 13 sur résumé, page éditeur ou notice bibliographique. Les
quatre sources payantes les plus désirables (Baur & Cahill sur les effets jour de la semaine du
BTC ; *Retail Weekends, Institutional Weekdays* ; *Are day-of-the-week effects in cryptocurrencies
real?* ; Li, Sakkas & Urquhart en texte intégral) n'ont **pas** été lues. Aucune recommandation de
ce document ne repose sur elles seules. Un accès institutionnel les ouvrirait, et c'est le
complément le plus rentable à cette recherche.

---

## 9. Sources

**Citées et lues intégralement (5)**

1. Brauneis, A., Mestel, R., Theissen, E. (2024). *The crypto world trades at tea time: intraday
   evidence from centralized exchanges across the globe*. Review of Quantitative Finance and
   Accounting. [irep.ntu.ac.uk/id/eprint/51576](https://irep.ntu.ac.uk/id/eprint/51576/) ·
   DOI [10.1007/s11156-024-01304-1](https://doi.org/10.1007/s11156-024-01304-1)
2. Kurth, J. G., Eisler, Z., Rej, A., Bouchaud, J.-P. (2026). *Is Trend Still Your Friend? A
   Microstructural Account of the Demise of Short-Term Trend-Following*.
   [ar5iv.labs.arxiv.org/html/2607.01550](https://ar5iv.labs.arxiv.org/html/2607.01550) ·
   notice RePEc : [ideas.repec.org](https://ideas.repec.org/p/arx/papers/2607.01550.html)
3. Petukhina, A. A., Reule, R. C. G., Härdle, W. K. (2020). *Rise of the Machines? Intraday
   High-Frequency Trading Patterns of Cryptocurrencies*. The European Journal of Finance.
   [ar5iv.labs.arxiv.org/html/2009.04200](https://ar5iv.labs.arxiv.org/html/2009.04200) ·
   DOI [10.1080/1351847X.2020.1789684](https://doi.org/10.1080/1351847X.2020.1789684)
4. Tech Kick (2026). *XAUUSD Hourly Spread and Volatility, 2025-2026*. Zenodo, dataset.
   [zenodo.org/records/21973215](https://zenodo.org/records/21973215) ·
   DOI [10.5281/zenodo.21973215](https://doi.org/10.5281/zenodo.21973215)
5. Segal, G. (2026). *Gold CFD Spreads Vary by More Than 150% Between Major Brokers*. FX News
   Group, d'après BestBrokers. [fxnewsgroup.com](https://fxnewsgroup.com/forex-news/institutional/gold-cfd-spreads-vary-by-more-than-150-between-major-brokers/)

**Citées sur résumé, notice ou page éditeur (13)**

6. Rao, H. (2026). *Session-Heterogeneous Regime Dynamics, Monte Carlo Tail Risk Underestimation,
   and Survival Rate Divergence in XAUUSD*. Zenodo, preprint.
   [zenodo.org/records/19457256](https://zenodo.org/records/19457256)
7. Lucius, M. (2026). *Regimes Without Edge: Baseline-Gated Negative Results and Label Forensics
   for XAUUSD*. Zenodo, preprint. [zenodo.org/records/21343282](https://zenodo.org/records/21343282)
8. Wu, Z., Pinsky, E. (2026). *On the Performance of Lagged Momentum and Reversal Strategies
   Across Daytime and Overnight Sessions in Bitcoin and Ethereum Cryptocurrencies*. JRFM 19(9),
   692. [EconPapers](https://econpapers.repec.org/article/gamjjrfmx/v_3a19_3ay_3a2026_3ai_3a9_3ap_3a692-_3ad_3a2034368.htm)
9. Wątorek, M., Skupień, M., Kwapień, J., Drożdż, S. (2023). *Decomposing cryptocurrency
   high-frequency price dynamics into recurring and noisy components*. Chaos 33, 083146.
   [arxiv.org/abs/2306.17095](https://arxiv.org/abs/2306.17095) ·
   DOI [10.1063/5.0165635](https://doi.org/10.1063/5.0165635)
10. Li, Z., Sakkas, A., Urquhart, A. (2022). *Intraday time series momentum: Global evidence and
    links to market characteristics*. Journal of Financial Markets 57.
    [sciencedirect.com](https://www.sciencedirect.com/science/article/abs/pii/S138641812100001X)
11. *Retail Weekends, Institutional Weekdays: Liquidity and Return Asymmetries in Cryptocurrency
    Markets*. Quarterly Review of Economics and Finance (2026).
    [sciencedirect.com](https://www.sciencedirect.com/science/article/abs/pii/S1062976926001407)
12. *Are day-of-the-week effects in cryptocurrencies real? Intraday evidence from active and less
    active cryptocurrencies*. Finance Research Letters (2026).
    [sciencedirect.com](https://www.sciencedirect.com/science/article/pii/S1544612326011621)
13. Baur, D., Cahill, D. *Bitcoin time-of-day, day-of-week and month-of-year effects in returns
    and trading volume*. Finance Research Letters.
    [sciencedirect.com](https://www.sciencedirect.com/science/article/abs/pii/S1544612319301710)
14. Hong, J. (2026). *Asia drives bitcoin rebound; which hours and days deliver best returns*.
    DigitalToday, d'après CoinDesk/Velo.
    [digitaltoday.co.kr](https://www.digitaltoday.co.kr/en/view/53560/asia-drives-bitcoin-rebound-which-hours-and-days-deliver-best-returns)
15. *Time Series and Cross Sectional Momentum in the Cryptocurrency Market*. Auckland Centre for
    Financial Research. [acfr.aut.ac.nz](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)
    — **snippet indexé uniquement, PDF non accessible depuis cette session**
16. Nobak, K. (2026). *Bitcoin Hurst Exponent: What the Data Actually Shows*. FractalCycles,
    blog commercial. [fractalcycles.com](https://fractalcycles.com/blog/bitcoin-hurst-exponent)
    — cité uniquement comme **affirmation de fournisseur** : Hurst BTC **0,56**, SPY 0,55, or
    0,53 ; aucune recommandation ne s'appuie dessus
17. Deriv (2026). *Commodities trading* et *Specifications for Trading CFDs*.
    [deriv.com/eu/markets/commodities](https://deriv.com/eu/markets/commodities) — consulté pour
    les spécifications de contrat, horaires de cotation et mention « zero commission trades ».
    **La table or/XAUUSD n'a pas été atteinte** : la page paginée s'arrête à XAGUSD
18. *Specifications for Trading CFDs*. [deriv.be/eu/trading-specifications](https://deriv.be/eu/trading-specifications)
    — notice consultée, table paginée non extraite

---

## 10. Reproduire cette recherche

Aucune commande n'est nécessaire : ce document ne contient **aucune mesure nouvelle**. Tous les
chiffres internes sont repris de fichiers nommés au § 1.3 et vérifiables un par un.

Pour vérifier la contradiction du § 1.3 :

```bash
# Ce que le bandeau de config/ annonce
grep -n "profit factor net" config/strategies/*.yaml

# Ce que la porte `costs` de la campagne mesure réellement
grep -n '"profit_factor"' docs/research/stats/*.json
grep -n 'net profit factor' docs/research/stats/*.json
```

**Aucune promotion, aucun paramètre touché, aucun backtest lancé.** Tous les fichiers écrits par
cette tâche sont **nouveaux** ; aucun fichier existant de `docs/research/` n'a été modifié.
