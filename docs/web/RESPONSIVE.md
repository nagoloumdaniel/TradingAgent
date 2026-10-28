# Dashboard responsive (refonte NexaGold, 2026-10-28)

Cette refonte remplace l'identité "bleu froid / pilule flottante" décrite dans `DESIGN.md`
par une identité **NexaGold** : noir chaud et or, Geist / Geist Mono embarquées. Le reste de
`DESIGN.md` (lecture seule, pagination serveur, pages d'erreur sans base, polices locales,
sprite Lucide) reste valable.

## Où vit quoi

| Fichier | Rôle |
|---|---|
| `static/css/app.css` | Tokens (deux thèmes), mise en page, composants. Servi en lecture seule. |
| `static/js/app.js` | Amélioration progressive : thème, feuille « Plus », étiquettes de tableaux, menus déroulants, heure locale. |
| `templates/base.html` | Coquille : barre du haut, `nav#side-nav`, barre basse `nav.tabbar`, `<h1>` de page via `{% block heading %}`. |

Le script de thème (avant premier rendu) reste en ligne dans `<head>` ; tout le reste est externe.
Aucune page ne dépend de JavaScript pour être complète : sans lui, les tableaux défilent dans leur cadre.

## Navigation

| Largeur | Forme |
|---|---|
| < 768 px | Barre basse de 5 cibles de 60 px (Vue, Positions, Trades, Risque, Plus). « Plus » ouvre la liste complète en feuille (Échap, voile ou choix d'une entrée la ferment). Zones sûres iOS respectées. |
| 768 à 1099 px | Rail d'icônes (libellés conservés pour les lecteurs d'écran). |
| ≥ 1100 px | Barre latérale de 232 px, figée. |
| ≥ 1800 px | Contenu jusqu'à 1760 px, corps à 16 px. |

## Tableaux

`app.js` pose `data-label` (texte de l'en-tête) sur chaque cellule. Sous 768 px :
- tableau avec en-tête et 3 colonnes ou plus : **pile de cartes** (`.is-stack`), une carte par ligne ;
- tableau clé/valeur sans en-tête (replay, détail) : `.is-kv`, libellé et valeur côte à côte ;
- les lignes poussées par le flux SSE sont étiquetées par un `MutationObserver`.

Dès 768 px : défilement horizontal dans le cadre (ombres de bord, zone focalisable au clavier
seulement si elle déborde, nommée d'après son titre). La page, elle, ne défile jamais à l'horizontale.

## Accessibilité et états

- Contrastes mesurés (WCAG 2.2 AA) : texte secondaire 5,2 à 7,7 sombre, 4,9 à 6,6 clair ; bouton or 9,4 (sombre) et 6,3 (clair).
- Cibles ≥ 44 px (boutons, champs, liens de navigation, pagination) ; focus visible 2 px or ; lien d'évitement ; un seul `<h1>` par page ; `scope="col|row"` sur tous les `<th>` ; `aria-current`.
- `prefers-reduced-motion`, `forced-colors`, `hover: none` traités.
- Vide, erreur, indisponible : `td.empty`, `.banner.*`, pages 404/500/503 dans la même coquille.

## Fuseaux, formats

Les données et l'affichage restent en **UTC** (invariant du projet). Chaque date est un
`<time datetime="…Z">` ; l'heure locale du lecteur apparaît en info-bulle. `format.py` n'écrit
plus jamais `nan` / `inf` : un chiffre absent ou non fini se lit `n/a`.

## Bugs corrigés pendant la refonte

- Débordement horizontal à 360 px sur `/trades/{id}` (clé d'idempotence, `<pre>` sans retour à la ligne).
- Marché fantôme « None » dans les sélecteurs de `/risk` et `/system` (colonnes `symbol` nulles).
- Cellules `td.empty` dessinées comme des cartes en pointillés ; ligne `TOTAL` non stylée ; liens « Réinitialiser » sans style ni cible tactile ; styles en ligne (`style=`) retirés.

## Vérifier

```bash
uv run pytest tests/web -q
uv run python scripts/preview_dashboard.py --port 8799 --demo
uv run --with playwright python tests/web/browser_audit.py http://127.0.0.1:8799 out/
```

`browser_audit.py` (Chromium headless, hors suite pytest) contrôle 11 pages à 360, 768, 1280 et
1920 px : pas de défilement horizontal, cibles ≥ 44 px sous 1280 px, aucune erreur console,
tableaux empilés sur téléphone ; il enregistre une capture par page. Captures de référence :
`screenshots/responsive-overview-360.png`, `responsive-trades-768.png`, `responsive-overview-1920.png`.
