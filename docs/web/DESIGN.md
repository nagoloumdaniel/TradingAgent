# Design du dashboard — décisions et sources

> Installé le 2026-10-07 avec `npx skills add Leonxlnx/taste-skill` (12 skills) et
> `npx skills add emilkowalski/skills` (12 skills), tous deux dans `~/.claude/skills/`.
> Ce document dit ce qui a été retenu, ce qui a été écarté, et pourquoi. Les skills sont
> des avis, pas des ordres : ils visent des sites vitrines, ce dashboard est un outil.

## Ce qui a été retenu

**`minimalist-ui`** — l'école directrice, parce qu'elle décrit un outil et non une vitrine :

| Règle du skill | Application |
|---|---|
| Monochrome froid, couleur rare | Fonds `#0b0d11` / `#14171d` / `#191d24`, un seul accent `#8ab4ff`, sémantique désaturée (vert/rouge/ambre) réservée au sens |
| Bordures 1 px, rayons 8-12 px | `--glass-line: rgba(255,255,255,.07)`, `--radius: 14px` / `--radius-sm: 9px` |
| Interdiction d'Inter/Roboto/Arial par défaut | **Geist** et **Geist Mono**, embarquées (§ « Typographie ») |
| Contraste typographique | Libellés 10,5 px, majuscules, interlettrage 0,1 em ; valeurs 21 px, solde en `clamp(30px, 4.4vw, 42px)` |
| Titres de section discrets | `h2` en 11 px majuscules gris, la hiérarchie est portée par l'espace, pas par la couleur |
| Badges pastel, petits, majuscules | `.badge.ok/.warn/.bad` sur fonds désaturés |
| Pas d'emoji dans le balisage | aucun emoji dans les gabarits |
| Ombres quasi absentes | une seule ombre, très diffusée, pour détacher les cartes du fond |

**`emil-design-eng`** — la discipline d'interaction :

| Règle | Application |
|---|---|
| « Combien de fois l'utilisateur verra-t-il cette animation ? » | Un dashboard se relit des dizaines de fois par jour : **aucune animation d'entrée, aucun effet au scroll**, aucun mouvement décoratif |
| Courbes personnalisées, jamais `linear`/`ease-in` | `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)` sur les deux seules transitions (couleur au survol, 150 ms) |
| Durées sous 300 ms | 150 ms pour les couleurs, 120 ms pour la ligne survolée |
| `transform: scale(0.97)` à l'appui | boutons, liens d'action, entrées de navigation |
| `prefers-reduced-motion` respecté | bloc dédié : les couleurs restent (elles aident à comprendre), le mouvement disparaît |
| Survol au doigt = faux positifs | `@media (hover: none)` neutralise les effets de survol |
| N'animer que `transform` et `opacity` | les transitions ne touchent que `background-color`, `border-color` et `transform` |

**`high-end-visual-design`** — ce qui sert un dashboard :

- hiérarchie par l'échelle (un chiffre dominant) plutôt que par la couleur ;
- grille bento : la carte « Solde » occupe deux colonnes, les autres une ;
- rythme spatial : gouttières de 12 px entre cartes, 28 px entre sections ;
- aucune bordure grise générique sur 1 px sans intention : chaque hairline sépare deux
  surfaces de nature différente.

## Typographie — Geist, embarquée

Le dashboard doit rester consultable **hors ligne** : aucune police n'est téléchargée à
l'exécution. Geist et Geist Mono sont donc **versionnées dans le dépôt**
(`src/tradingagent/web/static/fonts/`, 86 Ko au total) et déclarées en `@font-face` avec
les sous-ensembles `latin` et `latin-ext`.

- Police : **Geist** (Vercel), licence **SIL OFL 1.1** — le texte de licence voyage avec
  les fichiers (`static/fonts/OFL.txt`), comme la licence l'exige.
- Chiffres : **Geist Mono**, `font-variant-numeric: tabular-nums`. C'est la règle qui
  compte le plus ici : une colonne de montants qui ne danse pas quand le flux SSE met la
  page à jour.
- Repli : `ui-sans-serif, system-ui, "Segoe UI"` — si le fichier manque, la page reste
  lisible, simplement moins belle.

## Deux thèmes

| Thème | Quand | Comment |
|---|---|---|
| Sombre | **défaut** | L'opérateur travaille la nuit : un flash de blanc entre deux vérifications d'une position ouverte est pire qu'un mauvais défaut |
| Clair | au choix | **Un seul bouton**, en haut à droite de la barre flottante, mémorisé dans `localStorage` |

L'ordre de priorité est : `?theme=light|dark` dans l'URL, puis le choix mémorisé, puis la
préférence système. Le paramètre d'URL n'est pas un détail de test : il permet de coller un
lien qui porte son thème et d'épingler un écran de supervision sur un thème donné.

Le thème est posé **avant le premier rendu** par un script en ligne dans `<head>` : pas de
clignotement. Les deux palettes sont deux jeux de variables CSS ; aucune règle de mise en
page n'est dupliquée.

### Le bouton unique (2026-10-08)

Deux boutons côte à côte demandaient de comprendre lequel était *actif* ; un seul bouton qui
bascule demande seulement de cliquer. La lune et le soleil occupent la même pastille de
40 × 32 px et glissent l'un vers l'autre (`transform` + `opacity`, 220 ms, courbe
`--ease-out`) ; `prefers-reduced-motion` ramène la transition à 1 ms, l'état reste lisible
parce qu'il ne dépend pas du mouvement.

Accessibilité : `aria-pressed` vaut **vrai quand le thème clair est actif**, et le nom
accessible (`aria-label`, repris en `title`) dit ce que le clic *fera* — « Activer le thème
clair » / « Activer le thème sombre ». Un lecteur d'écran entend donc à la fois l'état et
l'action, sans avoir à deviner ce que représente l'icône.

## Glassmorphism — où le flou est dépensé, et pourquoi pas partout

| Surface | Traitement |
|---|---|
| Barre de navigation **flottante** | `backdrop-filter: blur(16px) saturate(170%)`, rayon 999 px, **pilule centrée** : `max-width: var(--nav-max)` (1080 px), `margin: 10px auto 0`, `justify-self: center` — mesuré à 1440 px : 180 px de marge de chaque côté, à 800 px : 12 px |
| Barre latérale (carte de verre) | `blur(14px)` |
| Carte héro | `blur(16px) saturate(150%)` — une seule par page |
| Toutes les autres cartes | surface translucide + liseré lumineux, **sans flou** |

La raison est mesurable : c'est la **surface** floutée qui coûte cher au défilement, pas le
flou. Le bandeau pleine largeur d'origine floutait toute la largeur de l'écran sur trois
lignes de haut (marque + sous-titre) ; la barre flottante floute une seule ligne, et le
sous-titre a été supprimé — deux tiers de surface en moins pour le même effet. Une page
porte par ailleurs jusqu'à quarante cartes, et quarante flous plein écran coûteraient cher
sans rien dire de plus : le flou ne se voit que là où quelque chose passe derrière.

Le fond translucide reste opaque à 72 % (`--glass-strong`) : sans `backdrop-filter`
(navigateur ancien, mode économie d'énergie), la barre demeure parfaitement lisible.

### La barre est centrée, pas pleine largeur (2026-10-08)

L'opérateur : « la nav doit être au milieu (ne pas remplir la longueur de l'écran) ». La
barre n'occupe donc plus les deux bords : elle est une **pilule centrée**, de 1080 px au
maximum, tenue à égale distance des deux bords par `margin: auto` et `justify-self: center`.
La mesure est faite sur une capture où la barre est peinte en magenta, parce que
l'alignement ne se lit pas dans le HTML :

| Largeur | Largeur de la pilule | Marge gauche | Marge droite |
|---|---|---|---|
| 1440 px | 1080 px | 180 px | 180 px |
| 800 px (règle `max-width: 880px`) | 776 px | 12 px | 12 px |

La marge est **symétrique par construction** : `width: 100%` plus `max-width` pour la borne,
et sous 880 px `width: calc(100% - 24px)` — jamais `width: 100%` *avec* des marges
horizontales, qui déborderait de la largeur du viewport.

## Le logo — deux fichiers, une seule marque (2026-10-08)

Le glyphe est **blanc sur fond transparent** : sur le thème clair, il disparaissait. Il existe
donc deux fichiers, et la feuille de style — jamais un script — décide lequel se dessine :

| Fichier | Thème | Ce qu'il contient |
|---|---|---|
| `static/nexagold.png` | sombre (défaut) | le glyphe blanc d'origine, 443 × 443, inchangé |
| `static/nexagold-dark.png` | clair | le même glyphe, **luminance inversée, canal alpha conservé** |

La dérivation n'est pas une retouche à la main : la luminance de chaque pixel est inversée
(`255 − L`) et l'alpha est recopié tel quel, donc la couverture du dessin est identique au
pixel près — 68 195 pixels opaques, 128 054 pixels transparents, luminance moyenne des pixels
opaques 5,1 contre 249,9 pour l'original. Un test le vérifie sur les fichiers réels
(`tests/web/test_brand.py`, décodeur PNG en bibliothèque standard : pas de Pillow en
dépendance pour mesurer une image). Les **deux** `<img>` sont dans le document et une seule
est affichée, pour que le bon dessin soit là dès le premier rendu.

Le favicon reste le glyphe blanc : il ne suit pas le thème de la page.

## Barre latérale figée, et tiroir sur téléphone

Au-dessus de 880 px, `nav.side` est une carte de verre en `position: sticky` : elle ne défile
pas avec le contenu et reste visible pendant toute la lecture d'une longue table. Son propre
défilement n'apparaît que si ses entrées dépassent la hauteur de l'écran.

En dessous de 880 px, elle devient un **tiroir** : hors du flux, glissé depuis la gauche
(`translateX(-102%)`), avec un voile cliquable. `visibility: hidden` quand il est fermé, pour
qu'il ne reste pas dans l'ordre de tabulation. Trois sorties : `Échap` (qui rend le focus au
bouton), un clic sur le voile, ou le choix d'une entrée. Le bouton d'ouverture porte
`aria-expanded` et `aria-controls`. C'était la correction attendue : une barre figée qui
mange 40 % d'un écran de téléphone est un défaut, pas un choix.

## Barres de défilement : la barre part, le défilement reste

```css
html { scrollbar-width: none; }
html::-webkit-scrollbar { width: 0; height: 0; }
```

Rien ne pose `overflow: hidden` sur le document : la molette, les flèches, `Page haut/bas`,
l'espace, le tactile et les ancres continuent de fonctionner. Masquer la barre ne supprime
jamais le défilement — supprimer le défilement est ce qui rendrait des données
inaccessibles.

Là où une zone défile **réellement** (un tableau plus large que son cadre), la barre est
conservée et stylisée (`scrollbar-width: thin`, pouce `--line-strong`, 8 px) : c'est le seul
indice qu'il reste des colonnes à droite. Un tableau large est en outre rendu focusable par
le script de fin de page (`tabindex="0"`, `role="region"`, nom pris au titre qui le précède)
**dès que** `scrollWidth > clientWidth`, et seulement dans ce cas : la zone se parcourt donc
au clavier, et l'ordre de tabulation n'est pas encombré quand les tableaux tiennent à
l'écran.

## Icônes : une sprite SVG en ligne, créditée

Le jeu est **Lucide** (<https://lucide.dev>), licence **ISC** — permissive, et compatible
avec la copie de la géométrie dans le dépôt. Aucun CDN, aucun paquet npm, aucune requête
réseau : la sprite est incluse une fois par page (`templates/_sprite.html`) et chaque icône
est un `<use href="#i-…">`, donc un seul exemplaire de chaque chemin dans le document quel
que soit le nombre d'icônes dessinées. La couleur et l'épaisseur du trait sont héritées du
`<svg class="icon">` appelant (`currentColor`), ce qui permet la même icône en accent dans
l'entrée de navigation active et en gris ailleurs.

Accessibilité : une icône décorative est `aria-hidden="true"` et ne porte rien ; une icône
qui *signifie* quelque chose prend un `label` et devient `role="img" aria-label="…"`
(macro `icon(name, label)` dans `templates/_icons.html`). Les entrées de navigation, elles,
gardent leur texte visible à côté de l'icône : la pastille ne porte jamais seule le sens.

Le crédit figure aussi dans le balisage (commentaire HTML au-dessus de la sprite), comme la
licence OFL voyage avec les polices Geist.

## Pagination, recherche, marché : une primitive, pas neuf copies

`tradingagent/web/paging.py` porte les trois règles que l'opérateur a demandées, une fois
pour toutes les pages qui listent des données :

| Règle | Où elle vit |
|---|---|
| **10 lignes maximum par affichage** | `PAGE_SIZE = 10`, appliqué par un `LIMIT` SQL |
| **Recherche et pagination côté serveur** | `LIMIT`/`OFFSET` + `count()` dans la requête ; le motif de recherche est un paramètre lié, `%` et `_` échappés |
| **Un seul marché à la fois** | `market` est un paramètre de requête lié (`WHERE symbol = :market`) ; `resolve_market` retombe sur le marché par défaut, jamais sur « tous » |

Le gabarit ne décide rien : il rend un `Page` (`rows`, `total`, `page`, `pages`, `showing`,
`empty_message`, `url(n)`). Les macros partagées sont dans `templates/_macros.html` — le
pager, le sélecteur de marché, les listes déroulantes, le champ de recherche — pour que
trois pages ne puissent pas diverger.

Deux points de conception qui comptent :

1. **Le marché par défaut.** `XAUUSD` d'abord, puis `BTCUSD`, puis le premier marché présent
   dans la base. Un `?market=` inconnu (signet périmé) retombe sur ce défaut : un vieux lien
   ne doit jamais être ce qui remet deux instruments dans la même table. La base vide n'a pas
   de marché, donc pas de filtre — il n'y a rien à séparer.
2. **Le flux SSE porte le même filtre.** `EventSource("/events?market=…")` : le compteur de
   positions ouvertes poussé par le flux décrit le marché de la table qu'il surplombe. La
   date du flux passe par `format.precise`, la même que la page : un horodatage ISO
   (`2026-10-07T23:51:32.068457+00:00`) n'apparaît plus à côté d'un `2026-10-07 23:51:32`.

Sur `/trades`, les cartes de synthèse décrivent la **sélection entière** filtrée en SQL, pas
les dix lignes visibles : une carte calculée sur dix lignes sur quatre cents serait un
mensonge. Les montants sont stockés en texte sous SQLite, donc l'agrégation a lieu dans
`analytics` — après le filtre SQL, jamais dans le navigateur.

### Les cinq pages restantes (2026-10-08)

`/scalping`, `/strategies`, `/ai-lab`, `/risk` et `/system` ont reçu le même traitement, par
la même primitive. Une seule fonction compte et borne une table (`queries._slice`) : un
`COUNT` puis un `LIMIT`/`OFFSET`, jamais une lecture complète coupée en Python. Un test
écoute les requêtes réellement exécutées et exige de voir `SELECT COUNT(*)` **et** `LIMIT`
dans le journal de chaque liste (`tests/web/test_remaining_pages.py`) : c'est la seule façon
de prouver la règle plutôt que de la commenter.

| Page | Table(s) | Marché | Recherche | Remarque |
|---|---|---|---|---|
| `/strategies` | `strategy_registry` ∪ les références vues dans les trades | oui (`market`) | oui | l'union est comptée et paginée en SQL ; les performances ne sont calculées que pour les dix références affichées |
| `/ai-lab` | `ai_analyses`, `ai_proposals`, `validation_runs`, `backtest_runs` | oui (les quatre) | oui | **quatre** paramètres de page (`page`, `proposals`, `validations`, `backtests`) : lire les propositions ne déplace pas les analyses |
| `/scalping` | découpes horaires | oui — dérivable, le symbole est porté par chaque trade | non | voir ci-dessous |
| `/risk` | `system_events`, `halt_commands` | oui pour les événements | oui | l'historique des arrêts n'a pas de colonne marché : pagination + recherche seules |
| `/system` | `execution_events`, `system_events`, journal EA | oui pour les deux logs | oui | le journal EA ne vient pas de la base : il est **borné**, pas paginé |

Trois décisions qui méritent d'être écrites :

1. **`/scalping` est une page d'agrégats, pas une liste.** Les découpes viennent de
   `analytics.scalping`, qui a besoin de tout son échantillon : une distribution ne se calcule
   pas sur dix lignes. Le marché, lui, est bien un paramètre lié (`positions.symbol = :market`),
   appliqué aux trades, aux coûts et aux latences. La découpe horaire est la seule qui peut
   dépasser un affichage (24 heures au plus) : elle est donc paginée, et la page dit que les
   autres découpes sont bornées par construction (4 sessions, 7 jours, 5-6 bandes). Il n'y a
   pas de champ de recherche : il n'y a aucun texte à filtrer, et un filtre sur des libellés
   serait un tri côté client — précisément ce que le traitement supprime.
2. **Un marché nullable n'est pas un marché.** `system_events.symbol` vaut `NULL` pour ce qui
   concerne le compte entier — décalage d'horloge, kill switch, changement de mode. Le filtre
   est donc « **ce marché, plus les événements sans marché** » : deux instruments ne partagent
   jamais une table, et l'alerte qui ne concerne aucun instrument reste affichée quel que soit
   le marché choisi. La colonne « Marché » écrit « tous marchés » quand la ligne n'en a pas.
   `execution_events.symbol`, elle, n'est jamais nulle : là, l'égalité simple suffit.
3. **Le journal des EA est borné, pas paginé.** Il est lu dans les rapports JSON du pont, pas
   dans une table : il ne peut donc pas être compté en SQL. La page affiche les **dix**
   événements les plus récents, tous gardiens confondus, et écrit « 10 sur 120 » à côté : une
   troncature silencieuse serait le défaut qu'on remplace.

## Filigranes — de vrais graphiques

Deux graphiques vivent derrière le contenu, dessinés à partir de **données réelles** :

- la **courbe d'équité**, depuis les instantanés de compte (`account_snapshots`) ;
- les **bougies M15**, depuis les bougies stockées (`candles`).

`queries.watermark()` ne fait que de la géométrie d'affichage : elle projette des nombres
déjà en base sur un `viewBox`. Aucun indicateur n'est calculé, aucune figure n'en dérive, et
toutes les pages s'affichent à l'identique sans filigrane — le §34 tient.

Trois garde-fous, tous appris d'un défaut observé à l'écran :

1. **Un seuil d'échantillon.** En dessous de 12 points d'équité ou 24 bougies, on ne
   dessine rien : avec cinq points, la « courbe » n'est qu'une diagonale qui traverse les
   cartes. Un graphique se mérite.
2. **Une intensité très basse** (opacité 0,13 et 0,10) et un masque qui efface le centre.
   Si l'œil s'accroche au filigrane, c'est raté.
3. **Aucune exception ne remonte.** Une base non migrée renvoie un filigrane vide, jamais
   une erreur : c'est ce qui évitait de transformer une page 503 propre en 500.

## Adaptatif

| Largeur | Comportement |
|---|---|
| > 1100 px | Barre latérale verticale, grille de cartes en `auto-fit` |
| 880-1100 px | Marges resserrées |
| < 880 px | Barre latérale en bandeau horizontal défilant, sous-titre masqué |
| < 560 px | Cartes sur deux colonnes minimum de 160 px, marque seule dans l'en-tête |

Toutes les largeurs ont été vérifiées par capture. Deux pièges rencontrés et corrigés :
`grid-template-columns: 1fr` a un plancher implicite de `min-content` (une table
`nowrap` élargissait la page au-delà du viewport), et `grid-column: span 2` sur une grille
retombée à une colonne fabrique une colonne fantôme — remplacé par `1 / -1`.

## Les surfaces d'erreur

Un outil de monitoring qui répond `{"detail":"Internal Server Error"}` n'a rien dit à
l'opérateur. Chaque erreur a donc une page : un code, une phrase, ce que cela **ne veut pas
dire**, et où aller ensuite.

| Code | Gabarit | Ce qu'elle dit |
|---|---|---|
| 400 | `error.html` | Les paramètres de l'adresse sont incomplets ou mal formés |
| 403 | `error.html` | Cette ressource n'est pas accessible depuis cette session |
| **404** | `404.html` | Page introuvable, **l'adresse demandée est rappelée** (échappée, tronquée à 120 caractères) |
| **404 de domaine** | `trade_replay.html` | « Trade inconnu · Aucun signal *n* en base » — plus utile qu'une 404 générique, parce qu'elle dit *quel* identifiant manque |
| **405** | `error.html` | « Lecture seule » : le tableau de bord ne répond qu'à `GET` et `HEAD`. L'en-tête `Allow` est conservé, et les méthodes acceptées sont écrites dans la page |
| **500** | `500.html` | Erreur interne. La trace reste dans le journal, la page ne dit rien des internes |
| **503** | `unavailable.html` | Base injoignable, avec les trois choses à vérifier |
| **401** | `auth.py` | Page autonome : ni navigation, ni chiffre, ni écho de ce qui a été présenté |

Trois règles tiennent l'ensemble :

1. **Une page d'erreur ne lit jamais la base.** C'est la seule façon d'être certain qu'une
   panne de base ne se transforme pas en panne de rendu. Le filigrane y est donc absent —
   par construction, et non par oubli, ce qu'un test vérifie.
2. **Aucune figure ne traverse une erreur.** Le tableau de bord peut afficher un résultat
   net de 16,75 € : la 404 ne doit pas en laisser filtrer un centime.
3. **Un client qui a demandé du JSON reçoit du JSON.** L'en-tête `Accept` décide : un
   navigateur (`text/html`, ou `*/*`) a la page, un script qui demande
   `application/json` garde la réponse machine.

Un gabarit de base ne suppose jamais une clé de contexte : la 503, la 404 et la 401 sont
rendues par des chemins différents de `page()`. C'est exactement le défaut qui avait
transformé une 503 propre en 500 quand le filigrane a été introduit.

## Ce qui a été écarté, et pourquoi

| Recommandation | Décision | Raison |
|---|---|---|
| Animations d'entrée au scroll (`translateY` + `blur`, IntersectionObserver) | **écartée** | Le framework d'Emil la refuse lui-même pour ce qui se consulte en permanence. Un opérateur qui rafraîchit 50 fois par jour ne doit pas attendre 800 ms. |
| `py-24` à `py-40` entre sections, `max-w-4xl` | **assouplie** | 24 px de padding vertical, largeur 1340 px : la densité est une fonctionnalité d'un tableau de bord. |
| Z-Axis Cascade, rotations `-2deg`, cartes qui se chevauchent | **écartée** | Empêche la lecture en colonne et les comparaisons ligne à ligne. |
| Boutons en pilule, « button-in-button » | **écartée sur les cartes** | Les pilules sont réservées aux badges de statut ; les contrôles restent à 8 px de rayon. |
| Polices premium téléchargées (Geist, Clash Display) | **retournée** | La règle visait la *dépendance réseau*, pas la police : Geist est désormais embarquée dans le dépôt et servie localement. L'outil s'ouvre toujours hors ligne. |
| Grain, dégradés ambiants, orbes lumineux | **assouplie** | Deux halos très diffus sont conservés comme source de lumière derrière le verre, à 10-13 % d'opacité. Aucun grain : il dégrade la lisibilité des chiffres. |
| Fond blanc/crème (`minimalist-ui` §4) | **inversée et complétée** | L'opérateur travaille la nuit : le thème sombre reste le défaut. Le thème clair existe maintenant comme choix explicite, et non comme imposition du système. |

## Contraintes d'accessibilité tenues

- `:focus-visible` avec anneau de 2 px sur tous les éléments interactifs ;
- `lang="fr"`, `aria-current="page"` sur l'entrée de navigation active ;
- `prefers-reduced-motion` et `prefers-color-scheme` déclarés ;
- les libellés tronqués passent à deux lignes plutôt que d'être coupés ;
- les tableaux restent lisibles au doigt : défilement horizontal conteneurisé, en-tête collant.

## Correctifs de robustesse trouvés pendant la revue visuelle

La revue par capture d'écran (Edge headless) a mis au jour une classe de défaut que les
tests ne voyaient pas :

- `grid-template-columns: 1fr` a un plancher implicite de `min-content` : une table
  `white-space: nowrap` pouvait élargir la page au-delà du viewport. Corrigé par
  `minmax(0, 1fr)` sur `body`, `.shell` et par `min-width: 0` sur `main` et `nav.side`.
- Les liens n'avaient pas de règle globale : les badges servant de liens apparaissaient
  soulignés. Corrigé par `text-decoration: none` + soulignement au survol.
- Une capture à 390 px de large est **rognée** par Chromium sur Windows (largeur de fenêtre
  minimale) : elle ne prouve pas un débordement. Vérifier la mise en page mobile à 500 px,
  ou émuler un appareil plutôt que de se fier à `--window-size`.

## Reproduire la revue visuelle

```bash
uv run python scripts/preview_dashboard.py --port 8799 --demo    # base jetable + fixtures + historique réaliste
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" \
  --headless=new --force-device-scale-factor=1 --window-size=1440,1250 \
  --screenshot=overview.png "http://127.0.0.1:8799/"
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" \
  --headless=new --force-device-scale-factor=1 --window-size=1440,1250 \
  --screenshot=light.png "http://127.0.0.1:8799/?theme=light"
```

`--demo` ajoute une courbe d'équité et 260 bougies : sans lui, les fixtures de test ne
contiennent que quelques points, ce qui suffit à exercer le code mais pas à juger un
graphique (et sous le seuil, le filigrane ne se dessine pas — c'est voulu).

Captures de référence versionnées dans `docs/web/screenshots/` : `overview-desktop.png`,
`header-logo.png`, `scalping.png`, `system.png`, et pour le passage du 2026-10-08 :
`scalping-2026-10-1440.png`, `strategies-2026-10-1440.png`, `ai-lab-2026-10-1440.png`,
`risk-2026-10-1440.png`, `system-2026-10-1440.png`, `overview-2026-10-light-logo.png`.

## Le logo

`src/tradingagent/web/static/nexagold.png` — glyphe blanc sur fond transparent,
443 × 443, adopté le 2026-10-07. Il vient du projet **NexaGoldAI**
(`apps/web/public/nexagold.png`), dont le dépôt local et le dépôt GitHub ont été
supprimés le même jour à la demande de l'opérateur : cette copie est désormais la
seule qui subsiste. Sa variante pour le thème clair est `nexagold-dark.png` (§ « Le logo —
deux fichiers, une seule marque »).

Il est servi par un montage `/static` local, jamais par un CDN : le tableau de bord
reste consultable hors ligne, comme le reste de l'interface. `StaticFiles` ne répond
qu'à `GET` et `HEAD`, donc ce montage ne dessert pas la promesse de lecture seule —
un test le vérifie (`tests/web/test_read_only.py`), et les **deux** chemins sont inclus dans le
test empirique qui compte les lignes de toutes les tables avant et après.
