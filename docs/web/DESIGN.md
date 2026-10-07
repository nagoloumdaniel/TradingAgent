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
| Clair | au choix | Bascule en haut à droite, mémorisée dans `localStorage` |

L'ordre de priorité est : `?theme=light|dark` dans l'URL, puis le choix mémorisé, puis la
préférence système. Le paramètre d'URL n'est pas un détail de test : il permet de coller un
lien qui porte son thème et d'épingler un écran de supervision sur un thème donné.

Le thème est posé **avant le premier rendu** par un script en ligne dans `<head>` : pas de
clignotement. Les deux palettes sont deux jeux de variables CSS ; aucune règle de mise en
page n'est dupliquée.

## Glassmorphism — où le flou est dépensé, et pourquoi pas partout

Trois surfaces translucides, un seul niveau de flou réel :

| Surface | Traitement |
|---|---|
| En-tête (collant) | `backdrop-filter: blur(20px) saturate(180%)` — c'est là que du contenu défile vraiment derrière |
| Barre latérale | `blur(14px)` |
| Carte héro | `blur(16px) saturate(150%)` — une seule par page |
| Toutes les autres cartes | surface translucide + liseré lumineux, **sans flou** |

La raison est mesurable : une page porte jusqu'à quarante cartes, et quarante flous plein
écran coûtent cher sans rien dire de plus — le flou ne se voit que là où quelque chose
passe derrière. La translucidité, elle, suffit à laisser deviner le filigrane.

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

Captures de référence versionnées dans `docs/web/screenshots/` :
`overview-desktop.png`, `header-logo.png`, `scalping.png`, `system.png`.

## Le logo

`src/tradingagent/web/static/nexagold.png` — glyphe blanc sur fond transparent,
443 × 443, adopté le 2026-10-07. Il vient du projet **NexaGoldAI**
(`apps/web/public/nexagold.png`), dont le dépôt local et le dépôt GitHub ont été
supprimés le même jour à la demande de l'opérateur : cette copie est désormais la
seule qui subsiste.

Il est servi par un montage `/static` local, jamais par un CDN : le tableau de bord
reste consultable hors ligne, comme le reste de l'interface. `StaticFiles` ne répond
qu'à `GET` et `HEAD`, donc ce montage ne dessert pas la promesse de lecture seule —
un test le vérifie (`tests/web/test_read_only.py`), et le chemin est inclus dans le
test empirique qui compte les lignes de toutes les tables avant et après.
