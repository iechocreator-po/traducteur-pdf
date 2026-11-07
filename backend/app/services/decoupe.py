"""
Découpe d'un gros PDF en sections de N pages (Laboratoire › Découper un PDF). Sans LLM ni OCR.

Une section est un simple PDF, importable dans le lot comme n'importe quel autre : ce service ne traduit
rien et ne connaît pas le moteur de traduction.

Règles :
  - le PDF source n'est jamais modifié ; les sections sont écrites à côté, chacune atomiquement ;
  - nom stable, déduit du découpage : `<base>_section_02_p051-100.pdf`. Une autre taille donne donc d'autres noms
    et ne touche pas à une découpe précédente ; refaire la même découpe est refusé sans `remplacer=True` ;
  - la dernière section prend les pages restantes (elle peut être plus courte) ;
  - une taille qui ne découperait rien (≥ nombre de pages) est refusée : ce serait une simple copie ;
  - les signets tombant dans une section y sont conservés : ils donnent ses chapitres à la traduction ;
  - un échec en route retire les fichiers créés par ce lancement : jamais de découpe trouée présentée comme finie.
"""

from __future__ import annotations

import math
import os
import re

from app.services.persistance import ecrire_binaire_atomique

PAGES_PAR_SECTION_MAX = 100_000
NB_MAX_SECTIONS = 1000     # au-delà, plus personne ne relit la liste : c'est une erreur de saisie


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


def _nom_section(nom_base: str, numero: int, nb_sections: int, debut: int, fin: int, nb_pages: int) -> str:
    # Largeurs fixes pour un même document : l'ordre alphabétique du Finder est l'ordre des pages.
    n, p = max(2, len(str(nb_sections))), len(str(nb_pages))
    return f"{nom_base}_section_{numero:0{n}d}_p{debut:0{p}d}-{fin:0{p}d}.pdf"


def _sections_prevues(chemin_pdf: str, nb_pages: int, pages_par_section: int) -> list[dict]:
    dossier = os.path.dirname(chemin_pdf)
    nom_base = os.path.splitext(os.path.basename(chemin_pdf))[0]
    bornes = bornes_sections(nb_pages, pages_par_section)
    sections = []
    for numero, (debut, fin) in enumerate(bornes, start=1):
        nom = _nom_section(nom_base, numero, len(bornes), debut, fin, nb_pages)
        sections.append({"numero": numero, "page_debut": debut, "page_fin": fin, "pages": fin - debut + 1,
                         "nom": nom, "chemin": os.path.join(dossier, nom)})
    return sections


def _autres_sections(chemin_pdf: str, noms_du_plan: set[str]) -> list[str]:
    """Sections d'une AUTRE découpe du même PDF, à côté de lui : elles restent en place, on le dit."""
    dossier = os.path.dirname(chemin_pdf)
    nom_base = os.path.splitext(os.path.basename(chemin_pdf))[0]
    motif = re.compile(re.escape(nom_base) + r"_section_\d+_p\d+-\d+\.pdf")
    return sorted(n for n in os.listdir(dossier) if motif.fullmatch(n) and n not in noms_du_plan)


def planifier(chemin_pdf: str, pages_par_section: int) -> dict:
    """Ce que ferait la découpe, sans rien écrire : l'aperçu de l'interface et le lancement partagent ce calcul."""
    doc = _ouvrir(chemin_pdf)
    try:
        nb_pages = doc.page_count
    finally:
        doc.close()
    sections = _sections_prevues(chemin_pdf, nb_pages, pages_par_section)
    noms = {s["nom"] for s in sections}
    return {
        "pages": nb_pages,
        "pages_par_section": pages_par_section,
        "nb_sections": len(sections),
        "sections": sections,
        "existants": [s["nom"] for s in sections if os.path.exists(s["chemin"])],
        "autres_decoupes": _autres_sections(chemin_pdf, noms),
    }


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


def decouper(chemin_pdf: str, pages_par_section: int, remplacer: bool = False) -> dict:
    """Écrit les sections à côté de `chemin_pdf` et rend le plan, avec la taille de chaque fichier (`octets`)."""
    plan = planifier(chemin_pdf, pages_par_section)
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
