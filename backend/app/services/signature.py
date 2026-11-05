"""
Signature électronique VISUELLE d'un PDF (Bibliothèque › ✍ Signer), à la manière d'Aperçu ou d'Acrobat
« Remplir et signer » : l'image de la signature (dessinée, tapée ou importée) est apposée à l'endroit
choisi, sur une ou plusieurs pages, avec une mention facultative (« Signé par … le … »).

Ce n'est PAS une signature numérique certifiée (PAdES, certificat X.509) : rien ne prouve
cryptographiquement l'identité du signataire ni l'intégrité du fichier. L'interface le dit.

Règles :
  - le PDF d'origine n'est jamais modifié : la sortie est `<base>_signe.pdf`, à côté ;
  - signer un fichier déjà `_signe.pdf` ajoute la signature à ce même fichier (écriture atomique) ;
  - les coordonnées reçues sont normalisées (0–1) sur la page TELLE QU'AFFICHÉE (rotation comprise).
"""

from __future__ import annotations

import io
import os
import re

TAILLE_MAX_SIGNATURE = 2 * 1024 * 1024   # octets du PNG reçu
COTE_MAX_SIGNATURE = 4000                # pixels
NB_MAX_PLACEMENTS = 50
LARGEUR_APERCU_MAX = 1600


class SignatureInvalide(Exception):
    pass


def _ouvrir(chemin_pdf: str):
    import pymupdf
    try:
        doc = pymupdf.open(chemin_pdf)
    except Exception as e:
        raise SignatureInvalide(f"PDF illisible : {e}")
    if doc.needs_pass:
        doc.close()
        raise SignatureInvalide("Ce PDF est protégé par un mot de passe : impossible de le signer ici.")
    return doc


def chemin_signe(chemin_pdf: str) -> str:
    base, _ = os.path.splitext(chemin_pdf)
    return chemin_pdf if base.endswith("_signe") else f"{base}_signe.pdf"


def pdfs_signables(chemin_traduit: str, chemin_source: str | None) -> list[dict]:
    """
    PDF proposés pour un document de la Bibliothèque : le PDF d'origine, la mise en page traduite
    (si générée) et leurs versions déjà signées. Seuls les fichiers existants sont listés.
    """
    from app.services import reconstruction

    candidats: list[tuple[str, str]] = []
    if chemin_source and chemin_source.lower().endswith(".pdf"):
        candidats.append(("PDF d'origine", chemin_source))
    try:
        loc = reconstruction.localiser(chemin_traduit)
        candidats.append(("PDF d'origine (scan)", loc["pdf"]))
        candidats.append(("PDF traduit — mise en page d'origine", loc["sortie_pdf"]))
    except reconstruction.ReconstructionImpossible:
        pass
    vus, liste = set(), []
    for libelle, chemin in candidats:
        for c, lib in ((chemin, libelle), (chemin_signe(chemin), f"{libelle} — signé")):
            if c in vus or not os.path.isfile(c):
                continue
            vus.add(c)
            liste.append({"libelle": lib, "chemin": c, "nom": os.path.basename(c)})
    return liste


def info_pdf(chemin_pdf: str) -> dict:
    doc = _ouvrir(chemin_pdf)
    try:
        return {"pages": doc.page_count,
                "formats": [[round(p.rect.width, 1), round(p.rect.height, 1)] for p in doc]}
    finally:
        doc.close()


def apercu_page(chemin_pdf: str, page: int, largeur: int = 900) -> bytes:
    """PNG d'une page, telle qu'affichée (rotation comprise), à `largeur` pixels."""
    import pymupdf
    doc = _ouvrir(chemin_pdf)
    try:
        if not 1 <= page <= doc.page_count:
            raise SignatureInvalide(f"Page {page} hors du document ({doc.page_count} pages).")
        p = doc[page - 1]
        zoom = max(100, min(largeur, LARGEUR_APERCU_MAX)) / p.rect.width
        return p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    finally:
        doc.close()


def _signature_png(octets: bytes) -> bytes:
    """Valide l'image reçue (vraie image, taille bornée) et la normalise en PNG RGBA."""
    from PIL import Image, UnidentifiedImageError
    if not octets or len(octets) > TAILLE_MAX_SIGNATURE:
        raise SignatureInvalide("Image de signature vide ou trop lourde (2 Mo au plus).")
    try:
        img = Image.open(io.BytesIO(octets))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise SignatureInvalide("L'image de signature est illisible.")
    if max(img.size) > COTE_MAX_SIGNATURE:
        raise SignatureInvalide("Image de signature trop grande (4000 px au plus).")
    tampon = io.BytesIO()
    img.convert("RGBA").save(tampon, format="PNG")
    return tampon.getvalue()


def signer(chemin_pdf: str, signature: bytes, placements: list[dict], mention: str | None = None) -> dict:
    """
    Appose `signature` (PNG/JPEG) sur chaque placement {page (1-n), x, y, largeur, hauteur} — fractions
    de la page affichée. `mention` (facultative) est écrite sous chaque signature. Retourne {chemin, pages}.
    """
    import pymupdf

    if not placements:
        raise SignatureInvalide("Aucun emplacement de signature.")
    if len(placements) > NB_MAX_PLACEMENTS:
        raise SignatureInvalide(f"Trop d'emplacements ({NB_MAX_PLACEMENTS} au plus).")
    png = _signature_png(signature)
    mention = re.sub(r"\s+", " ", mention or "").strip()[:200] or None

    doc = _ouvrir(chemin_pdf)
    try:
        signees = set()
        for pl in placements:
            n = int(pl["page"])
            if not 1 <= n <= doc.page_count:
                raise SignatureInvalide(f"Page {n} hors du document ({doc.page_count} pages).")
            x, y, l, h = (float(pl[k]) for k in ("x", "y", "largeur", "hauteur"))
            if not (0 <= x < 1 and 0 <= y < 1 and 0 < l <= 1 and 0 < h <= 1):
                raise SignatureInvalide("Emplacement hors de la page.")
            page = doc[n - 1]
            W, H = page.rect.width, page.rect.height
            visible = pymupdf.Rect(x * W, y * H, min(1, x + l) * W, min(1, y + h) * H)
            # page.rect est la page AFFICHÉE ; insert_image attend les coordonnées non tournées
            page.insert_image(visible * page.derotation_matrix, stream=png, keep_proportion=True,
                              overlay=True, rotate=page.rotation)
            if mention:
                taille = max(6.0, min(10.0, visible.height * 0.16))
                zone = pymupdf.Rect(visible.x0, visible.y1 + 1, visible.x0 + max(visible.width, 220), visible.y1 + taille * 2.6)
                page.insert_textbox(zone * page.derotation_matrix, mention, fontsize=taille,
                                    color=(0.25, 0.25, 0.3), rotate=page.rotation)
            signees.add(n)
        sortie = chemin_signe(chemin_pdf)
        contenu = doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
    tmp = f"{sortie}.tmp-{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(contenu)
    os.replace(tmp, sortie)
    return {"chemin": sortie, "nom": os.path.basename(sortie), "pages": sorted(signees)}
