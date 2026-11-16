# Références techniques PSE 2026 — version mobile

Version consultable sur téléphone des *Références techniques nationales* PSE (édition 2026), générée automatiquement à partir du PDF officiel.

**Site : https://drloser123.github.io/referentiel-pse/**

L'objectif est de pouvoir lire les références techniques confortablement sur un petit écran, sans changer ni supprimer un seul mot : page d'accueil avec les sections et les fiches, une fiche par écran, recherche plein texte avec le nombre d'occurrences par fiche.

---

## Vue d'ensemble

```
 PDF officiel (389 pages)
        │
        ▼
 outils/convert.py   ──►  out/pages.json   (HTML de chaque page)
   (PyMuPDF)              out/img/*.webp   (figures, pages de garde)
        │
        ▼
 outils/build.py     ──►  index.html       (site complet, un seul fichier)
   + template.html        img/             (images)
        │
        ▼
 outils/verify.py, hlcheck.py   (contrôles : mots et surlignages identiques au PDF)
        │
        ▼
 git push  ──►  GitHub Pages
```

Le site publié se compose uniquement de **`index.html`** et du dossier **`img/`**. Il n'y a ni serveur, ni base de données, ni framework : c'est une page statique, et toute la logique (navigation, recherche) tourne dans le navigateur.

---

## Le site (`index.html`)

### Contenu

Les 200 entrées (préface, sommaire et 198 fiches) sont stockées dans la page sous forme de balises `<template id="t-05PR01">…</template>`. Le navigateur les lit mais ne les affiche pas. Quand on ouvre une fiche, son template est cloné dans la zone de lecture. Ça permet de garder un seul fichier (environ 1,5 Mo, environ 400 Ko compressé par GitHub) tout en n'affichant qu'une fiche à la fois.

Deux petits tableaux JSON décrivent la structure :

- `SECS` : les 13 sections (titre, pages) ;
- `ITEMS` : les entrées (code de fiche, titre, section, page de début).

### Navigation

La navigation passe par l'ancre de l'URL : `…/referentiel-pse/#05PR01` ouvre directement la fiche 05PR01. Le bouton « retour » du téléphone fonctionne donc naturellement, et on peut partager le lien d'une fiche précise.

### Recherche

1. Chaque caractère est **normalisé** : minuscules, accents retirés, apostrophes et espaces insécables unifiés. La longueur du texte ne change pas, ce qui permet de retrouver la position exacte dans le texte d'origine. « coeur » trouve donc « cœur » et « Cœur ».
2. Sur l'accueil, le texte de toutes les fiches est extrait une fois, puis on compte les occurrences fiche par fiche. La couleur des pastilles suit une échelle logarithmique, du jaune pâle au rouge.
3. Dans une fiche, les occurrences sont entourées de balises `<mark class="hit">` en découpant les nœuds de texte du DOM. La barre du bas permet de passer de l'une à l'autre. Si la fiche ne contient pas le mot, la barre propose de revenir aux fiches qui le contiennent.
4. Le texte contenu dans les images (logigrammes, pages de garde) est présent dans la page mais masqué visuellement, pour que la recherche le trouve quand même.

### Préférences

Taille du texte, sections repliées et affichage des surlignages jaunes sont mémorisés dans le `localStorage` du navigateur, propre à chaque appareil. Rien n'est envoyé nulle part.

### Mise en forme

Le CSS est dans la page. Les couleurs reprennent celles du PDF : bleu pour les procédures (PR), orange pour les techniques (FT), gris pour les apports de connaissances (AC), et jaune pour les passages modifiés en 2026. Seul le mode clair est utilisé. La police (Atkinson Hyperlegible) vient de Google Fonts.

---

## La conversion (`outils/convert.py`)

C'est la partie difficile : un PDF ne contient pas de paragraphes, de listes ni de tableaux, seulement des morceaux de texte positionnés et des traits dessinés. Le script reconstruit la structure à partir de la géométrie, avec [PyMuPDF](https://pymupdf.readthedocs.io/).

Pour chaque page :

1. **Texte caractère par caractère** (`get_text("rawdict")`) : police, taille, couleur, gras et italique, position. Chaque caractère est aussi comparé aux rectangles jaunes (surlignage), aux traits noirs sous la ligne de base (soulignement) et aux fonds colorés.
2. **Lignes visuelles** : les morceaux sont regroupés par hauteur, puis découpés quand il y a un grand écart horizontal (colonnes).
3. **Colonnes** : les zones où deux blocs sont côte à côte sont détectées et lues colonne par colonne.
4. **Classification** de chaque ligne selon sa taille, sa police et sa couleur : titre de fiche, référence, titre de section, puce (glyphes Symbol, Courier « o », Wingdings « § »), note de bas de page, légende.
5. **Paragraphes** : une ligne rejoint la précédente si l'interligne est normal. Elle n'y va pas si la ligne précédente s'arrêtait nettement avant la marge, ce qui signale un retour à la ligne voulu dans le PDF.
6. **Tableaux** : deux détecteurs. `find_tables()` de PyMuPDF, plus un détecteur maison à partir des traits fins, pour les tableaux à filets gris sans bordure extérieure. La grille est simplifiée (seules les colonnes avec un vrai trait vertical sont gardées) puis rendue en `<table>` avec `colspan` et `rowspan`, largeurs de colonnes, bordures, fonds et alignements repris du PDF.
7. **Figures** : les images et les dessins vectoriels proches sont regroupés avec leur légende, puis rendus en WebP. Les pages de garde de chapitre sont rendues entières en image.
8. **Notes** : les appels de note deviennent `<sup>` ou `<sub>` (SpO₂) selon leur position par rapport à la ligne de base. Le filet au-dessus des notes est conservé.

Le résultat est un fichier `out/pages.json`, qui associe à chaque numéro de page son HTML.

## L'assemblage (`outils/build.py`)

`build.py` découpe ces pages en fiches (une fiche commence à chaque référence `[05PR01 / …]`), construit `SECS` et `ITEMS`, puis injecte le tout dans `template.html`.

## Les contrôles

- **`verify.py`** compare, page par page, la liste des mots du PDF et celle du HTML généré. Les 121 330 mots doivent être identiques. Les seules différences tolérées sont des espaces fines dans 2 pages et une ligature cassée dans le PDF : « insuﬄa•ons » s'affiche « insufflations », comme on le voit à l'écran.
- **`hlcheck.py`** vérifie lettre par lettre que le texte surligné en jaune est le même que dans le PDF.
- Les tableaux, figures et encadrés ont en plus été comparés visuellement, côte à côte, avec le PDF.

---

## Régénérer le site

```bash
pip install pymupdf pillow
cd outils
python3 convert.py pse.pdf out          # ~1 min pour 389 pages
python3 verify.py  pse.pdf out/pages.json
python3 build.py   out/pages.json ..     # écrit ../index.html
cp out/img/* ../img/
```

Le PDF des références techniques n'est pas inclus dans ce dépôt.

## Limites connues

- Les grands tableaux gardent les proportions du PDF et se font défiler horizontalement sur téléphone.
- Les encadrés « attention » placés en marge dans le PDF sont affichés entre deux paragraphes.
- Les logigrammes (repérage des nombreuses victimes, rachis) sont des images : on zoome pour lire les petites étiquettes.
