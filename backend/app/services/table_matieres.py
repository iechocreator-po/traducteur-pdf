"""
Pages « table des matières » d'un PDF : repérage et rendu Markdown ligne par ligne.

Pourquoi ce module (bug du 22/11/2026, *Advances in Financial Machine Learning*) : pymupdf4llm
analyse la mise en page d'une table des matières comme du texte courant ou comme un tableau.
Résultat dans le `_converti_*.md`, AVANT toute traduction :

    - **About the Author xxi PREAMBLE 1 1 Financial Machine Learning … 3** 1.1 Motivation, 3 1.2 …
    |**PART**|**1**<br>**DATA ANALYSIS**|**21**|

Dix entrées fusionnées en une puce, un faux tableau coupé au milieu d'un mot (« Fin | ancial »).
La traduction et l'export HTML ne font ensuite que reproduire ce désordre.

La géométrie du PDF, elle, est propre : une entrée = une ligne visuelle, son retrait dit sa
profondeur, le numéro de page est à droite ou après une virgule. On réécrit donc ces pages-là
seulement, depuis `page.get_text("dict")`, en une liste Markdown (une puce par entrée, retrait de
deux espaces par niveau). Toutes les autres pages restent la sortie de pymupdf4llm, inchangée.
"""

from __future__ import annotations

import re

# Titre d'une table des matières, en tête de page ou dans un signet PDF.
_RE_TITRE_TDM = re.compile(
    r"^\s*(table\s+of\s+contents|contents|table\s+des\s+mati[eè]res|sommaire|"
    r"[ií]ndice(\s+general)?|contenido|tabla\s+de\s+contenidos?|inhalt(sverzeichnis)?)\s*$",
    re.IGNORECASE,
)

# Une entrée de table des matières se termine par un numéro de page (arabe ou romain).
_RE_FIN_NUMERO = re.compile(r"(?:,|\s)\s*([0-9]{1,4}|[ivxlcdm]{1,7})\s*$", re.IGNORECASE)
# Numéro de section en tête d'entrée (« 2 », « 2.1 », « 2.1.3 ») : sa profondeur donne le niveau.
_RE_NUMERO_SECTION = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){0,4})\s+\S")
# Folio seul (« ix », « 12 ») : en-tête ou pied de page, jamais une entrée.
_RE_FOLIO = re.compile(r"^\s*([0-9]{1,4}|[ivxlcdm]{1,7})\s*$", re.IGNORECASE)

# Une table des matières dépasse rarement ces bornes ; au-delà, c'est un signet mal placé.
PAGES_TDM_MAX = 40
# Repli sans signet : seuils mesurés sur des pages de TdM réelles (≥ 70 % d'entrées numérotées).
LIGNES_MIN_REPLI = 8
RATIO_MIN_REPLI = 0.7
TOLERANCE_LIGNE = 3.0  # pt : deux fragments à moins de 3 pt de hauteur sont sur la même ligne
TOLERANCE_RETRAIT = 4.0  # pt : deux débuts de ligne à moins de 4 pt ont le même retrait


def _lignes_visuelles(page) -> list[dict]:
    """
    Regroupe les fragments de la page en lignes visuelles (même ordonnée), triées de haut en bas.
    pymupdf découpe souvent une entrée en trois lignes logiques (« 1.1 », « Motivation , 3 », numéro
    aligné à droite) : seul le regroupement par ordonnée reconstitue l'entrée.
    """
    fragments = []
    for bloc in page.get_text("dict").get("blocks", []):
        for ligne in bloc.get("lines", []):
            texte = "".join(s["text"] for s in ligne["spans"]).strip()
            if not texte:
                continue
            gras = all(("Bold" in s["font"]) or (s["flags"] & 16) for s in ligne["spans"] if s["text"].strip())
            x0, y0, x1, y1 = ligne["bbox"]
            fragments.append({"x0": x0, "x1": x1, "y": (y0 + y1) / 2, "texte": texte, "gras": gras})

    fragments.sort(key=lambda f: (f["y"], f["x0"]))
    lignes: list[list[dict]] = []
    for f in fragments:
        if lignes and abs(lignes[-1][0]["y"] - f["y"]) <= TOLERANCE_LIGNE:
            lignes[-1].append(f)
        else:
            lignes.append([f])

    resultat = []
    for groupe in lignes:
        groupe.sort(key=lambda f: f["x0"])
        texte = " ".join(f["texte"] for f in groupe)
        texte = re.sub(r"\s+", " ", texte)
        texte = re.sub(r"\s+,", ",", texte)  # « Motivation , 3 » → « Motivation, 3 »
        resultat.append({
            "x0": groupe[0]["x0"],
            "texte": texte.strip(),
            "gras": all(f["gras"] for f in groupe),
            "fragments": groupe,
        })
    return resultat


def _est_entree(texte: str) -> bool:
    return bool(_RE_FIN_NUMERO.search(texte)) and not _RE_FOLIO.match(texte)


def _ratio_entrees(lignes: list[dict]) -> tuple[int, float]:
    utiles = [ln for ln in lignes if not _RE_FOLIO.match(ln["texte"])]
    if not utiles:
        return 0, 0.0
    n = sum(1 for ln in utiles if _est_entree(ln["texte"]))
    return len(utiles), n / len(utiles)


def pages_table_matieres(doc) -> list[int]:
    """
    Indices (base 0) des pages qui forment la table des matières imprimée, ou [] si aucune.

    1. Signet PDF titré « Contents » / « Table des matières »… : sa page, puis les suivantes jusqu'au signet
       suivant (borné à PAGES_TDM_MAX) tant qu'elles gardent le profil d'une TdM (≥ RATIO_MIN_REPLI d'entrées).
       C'est le cas fiable, celui des livres d'éditeur.
    2. Sinon, repli prudent : une page dont l'une des trois premières lignes est un titre de TdM ET
       dont ≥ 70 % des lignes finissent par un numéro de page ; puis les pages suivantes tant
       qu'elles gardent ce profil. Un document sans titre de TdM n'est jamais touché.
    """
    try:
        toc = doc.get_toc()
    except Exception:
        toc = []
    for i, (_niveau, titre, page) in enumerate(toc or []):
        if not _RE_TITRE_TDM.match(titre or "") or page < 1:
            continue
        suivantes = [p for (_n, _t, p) in toc[i + 1:] if p > page]
        fin = (suivantes[0] - 1) if suivantes else page
        fin = min(fin, page + PAGES_TDM_MAX - 1, doc.page_count)
        # Les pages après celle du signet ne suivent que si elles ont le profil d'une TdM : une préface sans signet
        # entre « Contents » et le chapitre 1 était réécrite en liste, son titre et son chapitre perdus.
        pages = [page - 1]
        for numero in range(page, fin):
            if _ratio_entrees(_lignes_visuelles(doc[numero]))[1] < RATIO_MIN_REPLI:
                break
            pages.append(numero)
        return pages

    pages: list[int] = []
    for numero in range(doc.page_count):
        lignes = _lignes_visuelles(doc[numero])
        total, ratio = _ratio_entrees(lignes)
        profil = total >= LIGNES_MIN_REPLI and ratio >= RATIO_MIN_REPLI
        if pages:
            if profil and len(pages) < PAGES_TDM_MAX:
                pages.append(numero)
                continue
            break
        titre = any(_RE_TITRE_TDM.match(ln["texte"]) for ln in lignes[:3])
        if titre and profil:
            pages.append(numero)
    return pages


def _niveaux_de_retrait(lignes: list[dict]) -> list[float]:
    """Retraits distincts de la page (regroupés à TOLERANCE_RETRAIT près), du plus à gauche au plus à droite."""
    paliers: list[float] = []
    for x in sorted(ln["x0"] for ln in lignes):
        if not paliers or x - paliers[-1] > TOLERANCE_RETRAIT:
            paliers.append(x)
    return paliers


def _texte_entree(ligne: dict) -> str:
    """
    Texte d'une entrée, numéro de page séparé par une virgule (« Titre, 23 »), comme les entrées de
    section le portent déjà. Un numéro aligné à droite (dernier fragment, isolé) reçoit la virgule.
    """
    fragments = ligne["fragments"]
    texte = ligne["texte"]
    if len(fragments) >= 2 and _RE_FOLIO.match(fragments[-1]["texte"]):
        corps = " ".join(f["texte"] for f in fragments[:-1])
        corps = re.sub(r"\s+", " ", re.sub(r"\s+,", ",", corps)).strip().rstrip(",")
        numero = fragments[-1]["texte"].strip()
        if ligne["gras"]:
            return f"**{corps}**, {numero}"
        return f"{corps}, {numero}"
    return f"**{texte}**" if ligne["gras"] else texte


def _entrees_page(page, titre_markdown: str | None, premiere: bool) -> list[tuple[int | None, str]]:
    """
    Entrées d'une page de table des matières, (niveau, texte) : une par ligne visuelle, niveau tiré de la numérotation ou du
    retrait. Les folios et l’en-tête courant (« CONTENTS » répété en haut des pages suivantes) sont
    retirés ; le titre de la première page est rendu par `titre_markdown` (la ligne de titre que
    pymupdf4llm avait produite, pour ne pas changer le découpage en chapitres) ou en gras.
    """
    lignes = _lignes_visuelles(page)
    contenu = []
    titre_vu = False
    for ln in lignes:
        if _RE_FOLIO.match(ln["texte"]):
            continue
        # En-tête courant « x   CONTENTS » / « CONTENTS   xi » : fragments tous folio ou titre.
        textes = [f["texte"] for f in ln["fragments"]]
        if (len(textes) > 1 and any(_RE_TITRE_TDM.match(t) for t in textes)
                and all(_RE_TITRE_TDM.match(t) or _RE_FOLIO.match(t) for t in textes)):
            continue
        if _RE_TITRE_TDM.match(ln["texte"]):
            if premiere and not titre_vu:
                titre_vu = True
                contenu.append(("titre", ln))
            continue  # en-tête courant des pages suivantes
        contenu.append(("entree", ln))

    # Titre trop long replié sur deux lignes : la première n'a pas de numéro de page, la suite
    # (même graisse, retrait égal ou plus grand) le porte. On recolle avant de numéroter.
    fusion: list[tuple[str, dict]] = []
    for k, ln in contenu:
        if (k == "entree" and fusion and fusion[-1][0] == "entree"
                and not _est_entree(fusion[-1][1]["texte"])
                and ln["gras"] == fusion[-1][1]["gras"]
                and ln["x0"] >= fusion[-1][1]["x0"] - TOLERANCE_RETRAIT):
            prec = fusion[-1][1]
            fusion[-1] = ("entree", {
                "x0": prec["x0"], "gras": prec["gras"],
                "texte": f"{prec['texte']} {ln['texte']}",
                "fragments": prec["fragments"] + ln["fragments"],
            })
            continue
        fusion.append((k, ln))
    contenu = fusion

    entrees = [ln for (k, ln) in contenu if k == "entree"]
    paliers = _niveaux_de_retrait(entrees) if entrees else []

    def palier(x: float) -> int:
        for i, p in enumerate(paliers):
            if x - p <= TOLERANCE_RETRAIT:
                return i
        return max(len(paliers) - 1, 0)

    # Le retrait seul ne suffit pas : une page de suite qui commence par des sous-sections n'a pas
    # de ligne de chapitre, son retrait le plus à gauche serait pris pour le niveau 0. La
    # numérotation (« 2.2.1 » → 2) fait foi quand elle existe ; chaque palier de retrait hérite du
    # niveau des entrées numérotées qui s'y trouvent (« Exercises » au retrait des « 2.x » → 1).
    def profondeur(ln: dict) -> int | None:
        m = _RE_NUMERO_SECTION.match(ln["texte"].lstrip("*"))
        return m.group(1).count(".") if m else None

    niveau_du_palier: dict[int, int] = {}
    for ln in entrees:
        p = profondeur(ln)
        if p is not None:
            niveau_du_palier.setdefault(palier(ln["x0"]), p)

    def niveau(ln: dict) -> int:
        p = profondeur(ln)
        if p is not None:
            return p
        return niveau_du_palier.get(palier(ln["x0"]), palier(ln["x0"]))

    return [(None, titre_markdown or f"**{ln['texte']}**") if k == "titre" else (niveau(ln), _texte_entree(ln))
            for k, ln in contenu]


def rendre_page(page, titre_markdown: str | None, premiere: bool) -> str:
    """Markdown d'UNE page (voir rendre_table, qui est ce que l'extraction utilise)."""
    return _assembler(_entrees_page(page, titre_markdown, premiere))


def rendre_table(doc, pages: list[int], titre_markdown: str | None) -> str:
    """
    Markdown de TOUTE la table des matières, d'un seul tenant (les sauts de page disparaissent).

    ⚠️ Un paragraphe commence à chaque entrée de niveau 0 (chapitre, partie), jamais à un saut de
    page. Le moteur de traduction retire les espaces en tête de paragraphe et de morceau (`strip()` à
    plusieurs endroits) : une page de suite qui commençait par « ␣␣- 2.2 … » perdait son retrait une
    fois traduite (9 entrées sur 337 dans Advances in Financial ML, 22/11/2026). Une entrée de
    niveau 0 n'a pas de retrait à perdre, et un chapitre de TdM tient en quelques centaines de
    caractères : les morceaux restent petits.
    """
    entrees = []
    for rang, numero in enumerate(pages):
        entrees += _entrees_page(doc[numero], titre_markdown, premiere=(rang == 0))
    return _assembler(entrees)


def _assembler(entrees: list[tuple[int | None, str]]) -> str:
    sortie: list[str] = []
    for niveau, texte in entrees:
        if niveau is None:                      # titre de la table
            sortie += [texte, ""]
            continue
        if niveau == 0 and sortie and sortie[-1] != "":
            sortie.append("")                   # nouveau paragraphe : seulement devant un niveau 0
        sortie.append(f"{'  ' * niveau}- {texte}")
    return "\n".join(sortie).rstrip() + "\n\n"


def titre_markdown_de(page_md: str) -> str | None:
    """La ligne de titre « ### Contents » que pymupdf4llm a écrite pour cette page, s'il y en a une."""
    for ligne in page_md.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", ligne.strip())
        if m and _RE_TITRE_TDM.match(re.sub(r"[*_]", "", m.group(2)).strip()):
            return ligne.strip()
    return None
