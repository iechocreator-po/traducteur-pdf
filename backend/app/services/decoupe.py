"""
Découpe d'un gros PDF en sections (Laboratoire › Découper un PDF). Sans LLM ni OCR. Deux modes :
  - « pages » : sections de N pages ;
  - « chapitres » : une section par chapitre détecté (voir `detecter_chapitres`).

Une section est un simple PDF, importable dans le lot comme n'importe quel autre : ce service ne traduit
rien et ne connaît pas le moteur de traduction.

Règles :
  - le PDF source n'est jamais modifié ; les sections sont écrites à côté, chacune atomiquement ;
  - nom stable, déduit du découpage : `<base>_section_02_p051-100.pdf`. Une autre taille donne donc d'autres noms
    et ne touche pas à une découpe précédente ; refaire la même découpe est refusé sans `remplacer=True` ;
  - la dernière section prend les pages restantes (elle peut être plus courte) ;
  - une taille qui ne découperait rien (≥ nombre de pages) est refusée : ce serait une simple copie ;
  - les signets tombant dans une section y sont conservés : ils donnent ses chapitres à la traduction ;
  - un échec en route retire les fichiers créés par ce lancement : jamais de découpe trouée présentée comme finie ;
  - par chapitres : `<base>_chapitre_03_p051-100.pdf`, chaque section porte le titre détecté, et le plan dit d'où
    viennent les chapitres (signets ou typographie) : l'aperçu montre les titres avant que rien ne soit écrit.
"""

from __future__ import annotations

import math
import os
import re
import statistics
from collections import Counter

from app.services.persistance import ecrire_binaire_atomique

PAGES_PAR_SECTION_MAX = 100_000
NB_MAX_SECTIONS = 1000     # au-delà, plus personne ne relit la liste : c'est une erreur de saisie
MODES = ("pages", "chapitres")

# Par chapitres. Un « chapitre » non numéroté de moins de CHAPITRE_PAGES_MIN pages (page de titre, dédicace,
# copyright…) est regroupé avec ses voisins courts, puis rattaché au suivant (au précédent en fin de document) : on veut les vrais
# chapitres, pas un fichier par signet. Un niveau de signets (ou de taille de titre) plus fin n'est retenu que si ses
# sections font en médiane au moins CHAPITRE_MEDIANE_MIN pages : en dessous, ce sont des sous-sections.
CHAPITRE_PAGES_MIN = 3
CHAPITRE_MEDIANE_MIN = 6


class DecoupeInvalide(Exception):
    pass


class SectionsExistantes(DecoupeInvalide):
    """La même découpe existe déjà : la refaire écraserait ces fichiers."""

    def __init__(self, noms: list[str]):
        self.noms = noms
        super().__init__(f"{len(noms)} section(s) de cette découpe existent déjà ({noms[0]}…) : "
                         "cochez « Remplacer » pour les écraser.")


def _ouvrir(chemin_pdf: str):
    import pymupdf
    try:
        doc = pymupdf.open(chemin_pdf)
    except Exception as e:
        raise DecoupeInvalide(f"PDF illisible : {e}")
    if doc.needs_pass:
        doc.close()
        raise DecoupeInvalide("Ce PDF est protégé par un mot de passe : impossible de le découper ici.")
    return doc


def bornes_sections(nb_pages: int, pages_par_section: int) -> list[tuple[int, int]]:
    """Pages (début, fin), de 1 à nb_pages incluses, de chaque section. Lève `DecoupeInvalide` si la taille ne va pas."""
    if isinstance(pages_par_section, bool) or not isinstance(pages_par_section, int) \
            or not 1 <= pages_par_section <= PAGES_PAR_SECTION_MAX:
        raise DecoupeInvalide(f"Pages par section : un entier de 1 à {PAGES_PAR_SECTION_MAX} est attendu.")
    if nb_pages < 2:
        raise DecoupeInvalide("Ce document n'a qu'une page : il n'y a rien à découper.")
    if pages_par_section >= nb_pages:
        raise DecoupeInvalide(f"Le document a {nb_pages} pages : choisissez moins de {nb_pages} pages par section, "
                              "sinon il ne serait pas découpé.")
    nb_sections = math.ceil(nb_pages / pages_par_section)
    if nb_sections > NB_MAX_SECTIONS:
        minimum = math.ceil(nb_pages / NB_MAX_SECTIONS)
        raise DecoupeInvalide(f"Le document a {nb_pages} pages : cela ferait {nb_sections} sections "
                              f"({NB_MAX_SECTIONS} au plus). Choisissez au moins {minimum} pages par section.")
    return [(debut, min(debut + pages_par_section - 1, nb_pages))
            for debut in range(1, nb_pages + 1, pages_par_section)]


def _nom_section(nom_base: str, numero: int, nb_sections: int, debut: int, fin: int, nb_pages: int,
                 prefixe: str = "section") -> str:
    # Largeurs fixes pour un même document : l'ordre alphabétique du Finder est l'ordre des pages.
    n, p = max(2, len(str(nb_sections))), len(str(nb_pages))
    return f"{nom_base}_{prefixe}_{numero:0{n}d}_p{debut:0{p}d}-{fin:0{p}d}.pdf"


def _sections_depuis_bornes(chemin_pdf: str, nb_pages: int, bornes: list[tuple[int, int]],
                            prefixe: str = "section", titres: list[str] | None = None) -> list[dict]:
    dossier = os.path.dirname(chemin_pdf)
    nom_base = os.path.splitext(os.path.basename(chemin_pdf))[0]
    sections = []
    for numero, (debut, fin) in enumerate(bornes, start=1):
        nom = _nom_section(nom_base, numero, len(bornes), debut, fin, nb_pages, prefixe)
        s = {"numero": numero, "page_debut": debut, "page_fin": fin, "pages": fin - debut + 1,
             "nom": nom, "chemin": os.path.join(dossier, nom)}
        if titres is not None:
            s["titre"] = titres[numero - 1]
        sections.append(s)
    return sections


def _sections_prevues(chemin_pdf: str, nb_pages: int, pages_par_section: int) -> list[dict]:
    return _sections_depuis_bornes(chemin_pdf, nb_pages, bornes_sections(nb_pages, pages_par_section))


def _autres_sections(chemin_pdf: str, noms_du_plan: set[str]) -> list[str]:
    """Sections d'une AUTRE découpe du même PDF, à côté de lui : elles restent en place, on le dit."""
    dossier = os.path.dirname(chemin_pdf)
    nom_base = os.path.splitext(os.path.basename(chemin_pdf))[0]
    motif = re.compile(re.escape(nom_base) + r"_(?:section|chapitre)_\d+_p\d+-\d+\.pdf")
    return sorted(n for n in os.listdir(dossier) if motif.fullmatch(n) and n not in noms_du_plan)


# ── Détection des chapitres ──────────────────────────────────────────────────

def _bornes_depuis_debuts(debuts: list[tuple[int, str]], nb_pages: int) -> list[tuple[int, int, list[str]]]:
    """(page, titre) triés → (début, fin, [titres]) couvrant 1..nb_pages. Les pages avant le premier chapitre
    forment un « Début du document ». Deux titres sur la même page : une seule section, le premier titre."""
    uniques: list[tuple[int, str]] = []
    for page, titre in sorted(debuts, key=lambda d: d[0]):
        if not 1 <= page <= nb_pages:
            continue
        if uniques and uniques[-1][0] == page:
            continue
        uniques.append((page, titre))
    if not uniques or uniques[0][0] != 1:
        uniques.insert(0, (1, "Début du document"))
    return [(page, (uniques[i + 1][0] - 1) if i + 1 < len(uniques) else nb_pages, [titre])
            for i, (page, titre) in enumerate(uniques)]


def _fusionner(a, b):
    return (a[0], b[1], a[2] + b[2])


# « Chapter 3 », « Chapitre IV », « II Candlestick… », « 2. Méthode » : un chapitre numéroté est un vrai chapitre,
# même court, et n'est jamais fondu dans un voisin.
_RE_NUMEROTE = re.compile(
    r"^(?:(?:chapitre|chapter|cap[ií]tulo|capitolo|kapitel|partie|part|parte|livre|book)\s+\S+"
    r"|(?:\d{1,3}|[IVXLC]{1,7})(?:[.:)\-–—]|\s)\s*\S)", re.IGNORECASE)


def _est_numerote(titres: list[str]) -> bool:
    return bool(_RE_NUMEROTE.match(titres[0].strip()))


def regrouper_sections_courtes(sections: list[tuple[int, int, list[str]]],
                               minimum: int = CHAPITRE_PAGES_MIN) -> list[tuple[int, int, list[str]]]:
    """Regroupe les suites de sections non numérotées de moins de `minimum` pages ; un groupe encore trop court
    rejoint la section suivante (la précédente s'il est en fin de document)."""
    sortie, attente = [], None
    for s in sections:
        if s[1] - s[0] + 1 >= minimum or _est_numerote(s[2]):
            if attente is not None:
                if attente[1] - attente[0] + 1 >= minimum:
                    sortie.append(attente)
                else:
                    s = _fusionner(attente, s)
                attente = None
            sortie.append(s)
        else:
            attente = s if attente is None else _fusionner(attente, s)
    if attente is not None:
        if attente[1] - attente[0] + 1 >= minimum or not sortie:
            sortie.append(attente)
        else:
            sortie[-1] = _fusionner(sortie[-1], attente)
    return sortie


def _titre_groupe(titres: list[str]) -> str:
    if len(titres) == 1:
        return titres[0]
    if len(titres) == 2:
        return f"{titres[0]} + {titres[1]}"
    return f"{titres[0]} … {titres[-1]} ({len(titres)} parties)"


def _mediane_pages(debuts: list[tuple[int, str]], nb_pages: int) -> float:
    bornes = _bornes_depuis_debuts(debuts, nb_pages)
    return statistics.median(fin - debut + 1 for debut, fin, _ in bornes)


def _choisir_niveau(niveaux: list[list[tuple[int, str]]], nb_pages: int) -> int | None:
    """`niveaux[i]` : débuts de chapitre jusqu'au niveau i (du plus grossier au plus fin). Rend le plus fin qui ajoute
    au moins deux coupes au niveau retenu avant lui (une sous-section isolée ne fait pas un niveau de chapitres) et
    dont les sections font en médiane au moins CHAPITRE_MEDIANE_MIN pages ; sinon le plus grossier qui découpe."""
    retenu, coupes_retenues = None, set()
    for i, debuts in enumerate(niveaux):
        coupes = {p for p, _ in debuts if 1 < p <= nb_pages}
        if not coupes:
            continue                       # ne découpe rien : un seul chapitre, à la page 1
        if retenu is None or (len(coupes - coupes_retenues) >= 2
                              and _mediane_pages(debuts, nb_pages) >= CHAPITRE_MEDIANE_MIN):
            retenu, coupes_retenues = i, coupes
    return retenu


def _chapitres_par_signets(toc: list[list], nb_pages: int) -> list[tuple[int, str]] | None:
    if not toc:
        return None
    profondeurs = sorted({niveau for niveau, _, _ in toc})
    niveaux = [[(page, titre.strip() or "Sans titre") for niveau, titre, page in toc if niveau <= n]
               for n in profondeurs]
    i = _choisir_niveau(niveaux, nb_pages)
    return None if i is None else niveaux[i]


_RE_POINTILLES = re.compile(r"\.{4,}|…{2,}|(?:\. ){4,}")
_RE_MOT_CHAPITRE = re.compile(r"^(chapitre|chapter|cap[ií]tulo|capitolo|kapitel|partie|part|parte|livre|book)\b",
                              re.IGNORECASE)
HAUT_DE_PAGE = 0.40          # un titre de chapitre est dans le haut de sa page
ECART_TITRE = 1.12           # taille d'un titre / taille du corps (14 pt sur un corps de 12 pt)
LIGNES_MAX_TITRE_PAGE = 4    # au-delà, une page pleine de « titres » est une table des matières ou un index


def _lignes(page) -> list[tuple[float, float, str]]:
    """(taille, y relatif, texte) de chaque ligne non vide de la page."""
    hauteur = page.rect.height or 1
    lignes = []
    for bloc in page.get_text("dict")["blocks"]:
        for ligne in bloc.get("lines", []):
            spans = [s for s in ligne["spans"] if s["text"].strip()]
            if not spans:
                continue
            texte = " ".join(s["text"].strip() for s in spans)
            lignes.append((round(max(s["size"] for s in spans) * 2) / 2, ligne["bbox"][1] / hauteur, texte))
    return lignes


def _est_titre_plausible(texte: str) -> bool:
    lettres = sum(c.isalpha() for c in texte)
    return lettres >= 3 and lettres >= 0.5 * len(texte.replace(" ", "")) and len(texte) <= 120 \
        and not _RE_POINTILLES.search(texte)


def _chapitres_par_typographie(doc) -> list[tuple[int, str]] | None:
    """Sans signets : parcourt toutes les pages et repère, en haut de page, les lignes nettement plus grandes que le
    corps du texte. Chaque page garde son titre le plus grand ; les tailles de titre servent de niveaux."""
    pages = [_lignes(page) for page in doc]
    poids = Counter()
    for lignes in pages:
        for taille, _, texte in lignes:
            poids[taille] += len(texte)
    if not poids:
        return None                        # aucun texte : PDF scanné sans couche texte
    corps = poids.most_common(1)[0][0]

    titres_par_page: dict[int, tuple[float, str]] = {}
    for numero, lignes in enumerate(pages, start=1):
        candidates = [(t, y, txt) for t, y, txt in lignes if t >= corps * ECART_TITRE and y <= HAUT_DE_PAGE]
        if not candidates:
            continue
        plus_grande = max(t for t, _, _ in candidates)
        du_titre = [(y, txt) for t, y, txt in candidates if t == plus_grande]
        if len(du_titre) > LIGNES_MAX_TITRE_PAGE or any(_RE_POINTILLES.search(txt) for _, txt in du_titre):
            continue
        texte = " ".join(txt for _, txt in sorted(du_titre)).strip()
        if _est_titre_plausible(texte):
            titres_par_page[numero] = (plus_grande, texte)

    # Une taille de titre qui n'apparaît qu'une fois (page de couverture) ne fait pas un niveau à elle seule.
    occurrences = Counter(t for t, _ in titres_par_page.values())
    tailles = sorted((t for t, n in occurrences.items() if n >= 2), reverse=True)
    if not tailles:
        mots = [(p, txt) for p, (_, txt) in titres_par_page.items() if _RE_MOT_CHAPITRE.match(txt)]
        return sorted(mots) if len(mots) >= 2 else None
    niveaux = [sorted((p, txt) for p, (t, txt) in titres_par_page.items() if t >= seuil) for seuil in tailles]
    i = _choisir_niveau(niveaux, doc.page_count)
    return None if i is None else niveaux[i]


def detecter_chapitres(doc) -> tuple[list[tuple[int, int, str]], str]:
    """Chapitres (début, fin, titre) couvrant tout le document, et leur source : « signets » (table des matières
    intégrée au PDF, la plus fiable) ou « typographie » (titres repérés page par page). Lève `DecoupeInvalide` si
    rien n'est trouvé ou s'il n'y aurait qu'une section."""
    nb_pages = doc.page_count
    if nb_pages < 2:
        raise DecoupeInvalide("Ce document n'a qu'une page : il n'y a rien à découper.")
    source, debuts = "signets", _chapitres_par_signets(doc.get_toc(), nb_pages)
    if debuts is None:
        source, debuts = "typographie", _chapitres_par_typographie(doc)
    if debuts is None:
        raise DecoupeInvalide("Aucun chapitre détecté : ce PDF n'a pas de signets et aucun titre ne se détache du "
                              "texte (ou il n'a pas de couche texte, comme un scan). Découpez-le par nombre de pages.")
    sections = regrouper_sections_courtes(_bornes_depuis_debuts(debuts, nb_pages))
    if len(sections) < 2:
        raise DecoupeInvalide("Un seul chapitre détecté : il n'y a rien à découper. Découpez-le par nombre de pages.")
    if len(sections) > NB_MAX_SECTIONS:
        raise DecoupeInvalide(f"{len(sections)} chapitres détectés ({NB_MAX_SECTIONS} au plus) : "
                              "découpez-le par nombre de pages.")
    return [(debut, fin, _titre_groupe(titres)) for debut, fin, titres in sections], source


def planifier(chemin_pdf: str, pages_par_section: int | None = None, mode: str = "pages") -> dict:
    """Ce que ferait la découpe, sans rien écrire : l'aperçu de l'interface et le lancement partagent ce calcul."""
    if mode not in MODES:
        raise DecoupeInvalide(f"Mode de découpe inconnu : {mode!r} (attendu : {', '.join(MODES)}).")
    doc = _ouvrir(chemin_pdf)
    try:
        nb_pages = doc.page_count
        if mode == "chapitres":
            chapitres, source = detecter_chapitres(doc)
    finally:
        doc.close()
    plan = {"mode": mode, "pages": nb_pages}
    if mode == "chapitres":
        sections = _sections_depuis_bornes(chemin_pdf, nb_pages, [(d, f) for d, f, _ in chapitres],
                                           prefixe="chapitre", titres=[t for _, _, t in chapitres])
        plan.update({"source_chapitres": source, "pages_par_section": None})
    else:
        sections = _sections_prevues(chemin_pdf, nb_pages, pages_par_section)
        plan["pages_par_section"] = pages_par_section
    noms = {s["nom"] for s in sections}
    plan.update({
        "nb_sections": len(sections),
        "sections": sections,
        "existants": [s["nom"] for s in sections if os.path.exists(s["chemin"])],
        "autres_decoupes": _autres_sections(chemin_pdf, noms),
    })
    return plan


def _signets_section(toc: list[list], debut: int, fin: int) -> list[list]:
    """Signets de la section, pages ramenées à la section. PyMuPDF exige un premier niveau à 1, sans saut de niveau."""
    signets, precedent = [], 0
    for niveau, titre, page in toc:
        if debut <= page <= fin:
            precedent = min(niveau, precedent + 1)
            signets.append([precedent, titre, page - debut + 1])
    return signets


def _contenu_section(source, toc: list[list], debut: int, fin: int) -> bytes:
    import pymupdf
    sortie = pymupdf.open()
    try:
        sortie.insert_pdf(source, from_page=debut - 1, to_page=fin - 1)
        attendu = fin - debut + 1
        if sortie.page_count != attendu:
            raise DecoupeInvalide(f"{sortie.page_count} page(s) copiée(s) sur {attendu}.")
        signets = _signets_section(toc, debut, fin)
        if signets:
            sortie.set_toc(signets)
        return sortie.tobytes(garbage=3, deflate=True)
    finally:
        sortie.close()


def decouper(chemin_pdf: str, pages_par_section: int | None = None, remplacer: bool = False,
             mode: str = "pages") -> dict:
    """Écrit les sections à côté de `chemin_pdf` et rend le plan, avec la taille de chaque fichier (`octets`)."""
    plan = planifier(chemin_pdf, pages_par_section, mode)
    deja_la = set(plan["existants"])
    if deja_la and not remplacer:
        raise SectionsExistantes(plan["existants"])

    crees: list[str] = []
    source = _ouvrir(chemin_pdf)
    try:
        toc = source.get_toc()
        for s in plan["sections"]:
            try:
                contenu = _contenu_section(source, toc, s["page_debut"], s["page_fin"])
                ecrire_binaire_atomique(s["chemin"], contenu)
            except Exception as e:
                for chemin in crees:
                    os.unlink(chemin)
                raise DecoupeInvalide(f"Section {s['numero']} (pages {s['page_debut']} à {s['page_fin']}) : {e}. "
                                      f"{len(crees)} fichier(s) créé(s) par ce lancement retiré(s).")
            s["octets"] = len(contenu)
            if s["nom"] not in deja_la:
                crees.append(s["chemin"])
    finally:
        source.close()
    plan["remplaces"] = len(deja_la)
    return plan
