# Design du dashboard — décisions et sources

> Installé le 2026-10-07 avec `npx skills add Leonxlnx/taste-skill` (12 skills) et
> `npx skills add emilkowalski/skills` (12 skills), tous deux dans `~/.claude/skills/`.
> Ce document dit ce qui a été retenu, ce qui a été écarté, et pourquoi. Les skills sont
> des avis, pas des ordres : ils visent des sites vitrines, ce dashboard est un outil.

## Ce qui a été retenu

**`minimalist-ui`** — l'école directrice, parce qu'elle décrit un outil et non une vitrine :

| Règle du skill | Application |
|---|---|
| Monochrome chaud, couleur rare | Fonds `#0f1013` / `#16181d` / `#1b1e24`, un seul accent `#7aa2f7`, sémantique désaturée (vert/rouge/ambre) réservée au sens |
| Bordures 1 px `#EAEAEA`, rayons 8-12 px | `--line: #262a32`, `--radius: 12px` / `--radius-sm: 8px` |
| Interdiction d'Inter/Roboto/Arial par défaut | Pile système avec du caractère : `Segoe UI Variable Text`, et monospace pour tous les chiffres |
| Contraste typographique | Libellés 10,5 px, majuscules, interlettrage 0,09 em ; valeurs 22 px (34 px pour le solde) |
| Titres de section discrets | `h2` en 12 px majuscules gris, la hiérarchie est portée par l'espace, pas par la couleur |
| Badges pastel, petits, majuscules | `.badge.ok/.warn/.bad` sur fonds sombres désaturés |
| Pas d'emoji dans le balisage | aucun emoji dans les gabarits |
| Ombres quasi absentes | aucune ombre portée : la profondeur vient des hairlines et de deux nuances de surface |

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

## Ce qui a été écarté, et pourquoi

| Recommandation | Décision | Raison |
|---|---|---|
| Animations d'entrée au scroll (`translateY` + `blur`, IntersectionObserver) | **écartée** | Le framework d'Emil la refuse lui-même pour ce qui se consulte en permanence. Un opérateur qui rafraîchit 50 fois par jour ne doit pas attendre 800 ms. |
| `py-24` à `py-40` entre sections, `max-w-4xl` | **assouplie** | 24 px de padding vertical, largeur 1340 px : la densité est une fonctionnalité d'un tableau de bord. |
| Z-Axis Cascade, rotations `-2deg`, cartes qui se chevauchent | **écartée** | Empêche la lecture en colonne et les comparaisons ligne à ligne. |
| Boutons en pilule, « button-in-button » | **écartée sur les cartes** | Les pilules sont réservées aux badges de statut ; les contrôles restent à 8 px de rayon. |
| Polices premium téléchargées (Geist, Clash Display) | **écartée** | Aucune dépendance réseau : l'outil s'ouvre hors ligne. La pile système est documentée dans `--font-sans`. |
| Grain, dégradés ambiants, orbes lumineux | **écartée** | Aucun apport d'information ; le bruit dégrade la lisibilité des chiffres. |
| Fond blanc/creme (`minimalist-ui` §4) | **inversée** | L'opérateur travaille la nuit, sur de longues sessions : thème sombre chaud. La discipline de couleur du skill est conservée, pas sa luminance. |

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
uv run python scripts/preview_dashboard.py --port 8799     # base SQLite jetable + fixtures
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" \
  --headless=new --force-device-scale-factor=1 --window-size=1440,1250 \
  --screenshot=overview.png http://127.0.0.1:8799/
```
