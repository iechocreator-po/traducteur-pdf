"""
Signature électronique VISUELLE d'un PDF (Bibliothèque › ✍ Signer), à la manière d'Aperçu ou d'Acrobat
« Remplir et signer » : l'image de la signature (dessinée, tapée ou importée) est apposée à l'endroit
choisi, sur une ou plusieurs pages, avec une mention facultative (« Signé par … le … »).

Ce n'est PAS une signature numérique certifiée (PAdES, certificat X.509) : rien ne prouve
cryptographiquement l'identité du signataire ni l'intégrité du fichier. L'interface le dit.

Règles :
  - le PDF d'origine n'est jamais modifié : la sortie est `<base>_signe.pdf`, à côté ;
  - signer un fichier déjà `_signe.pdf` ajoute la signature à ce même fichier (écriture atomique) ;
  - signer l'original alors que `<base>_signe.pdf` existe est refusé : ce serait écraser ses signatures ;
  - une mention demandée figure telle quelle dans le PDF, ou la signature échoue : jamais de succès sans mention,
    ni de mention dont des lettres seraient remplacées par « ? » (la police du PDF s'arrête au Latin-1) ;
  - les coordonnées reçues sont normalisées (0–1) sur la page TELLE QU'AFFICHÉE (rotation et CropBox comprises).
"""

from __future__ import annotations

import io
import math
import os
import re

from app.services.persistance import ecrire_binaire_atomique

TAILLE_MAX_SIGNATURE = 2 * 1024 * 1024   # octets du PNG reçu
COTE_MAX_SIGNATURE = 4000                # pixels
NB_MAX_PLACEMENTS = 50
MENTION_LARGEUR_MIN = 220                # points
MENTION_TAILLE_MIN = 6.0                 # points
LARGEUR_APERCU_MAX = 1600
# Typographie courante absente du Latin-1 : son équivalent garde le sens ; le reste est refusé, jamais déformé.
EQUIVALENTS_MENTION = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "―": "-", "−": "-",
    "…": "...", "œ": "oe", "Œ": "OE", "€": "EUR",
})


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
        # La taille se lit sans décoder : quelques Ko de PNG peuvent se décompresser en plusieurs Go.
        if max(img.size) > COTE_MAX_SIGNATURE:
            raise SignatureInvalide("Image de signature trop grande (4000 px au plus).")
        img.load()
    except Image.DecompressionBombError:
        raise SignatureInvalide("Image de signature trop grande (4000 px au plus).")
    except (UnidentifiedImageError, OSError):
        raise SignatureInvalide("L'image de signature est illisible.")
    tampon = io.BytesIO()
    img.convert("RGBA").save(tampon, format="PNG")
    return tampon.getvalue()


def _preparer_mention(mention: str | None) -> str | None:
    """Mention sur une ligne, 200 caractères au plus, écrivable par la police du PDF ; sinon `SignatureInvalide`."""
    texte = re.sub(r"\s+", " ", mention or "").strip().translate(EQUIVALENTS_MENTION)[:200]
    hors_police = sorted({c for c in texte if ord(c) > 0xFF})
    if hors_police:
        raise SignatureInvalide("La mention contient des caractères que le PDF ne peut pas écrire "
                                f"({' '.join(hors_police[:10])}) : écrivez-la en alphabet latin.")
    return texte or None


def _ecrire_mention(page, visible, mention: str, taille_voulue: float, W: float, H: float, derotation, rotation: int) -> None:
    """
    Écrit `mention` sous la signature, ou au-dessus si la place manque en bas de page, en restant dans la
    page et en réduisant la police au besoin. `insert_textbox` n'écrit RIEN quand le texte ne tient pas et
    rend alors une valeur négative : sans ce contrôle, le PDF sort sans mention et l'interface dit « signé ».
    `visible`, W et H sont en coordonnées affichées ; `page` est à rotation 0 (voir `signer`).
    """
    import pymupdf

    largeur = min(W - 4, max(visible.width, MENTION_LARGEUR_MIN))
    x0 = max(2, min(visible.x0, W - largeur - 2))
    taille = taille_voulue
    while True:
        lignes = math.ceil(pymupdf.get_text_length(mention, fontname="helv", fontsize=taille) / (largeur * 0.9))
        hauteur = (lignes + 1) * taille * 1.5    # une ligne de marge : la césure aux mots allonge le texte
        for y0 in (visible.y1 + 1, visible.y0 - 1 - hauteur):
            if y0 < 0 or y0 + hauteur > H:
                continue
            zone = pymupdf.Rect(x0, y0, x0 + largeur, y0 + hauteur)
            if page.insert_textbox(zone * derotation, mention, fontsize=taille,
                                   color=(0.25, 0.25, 0.3), rotate=rotation) >= 0:
                return
        if taille <= MENTION_TAILLE_MIN:
            raise SignatureInvalide("La mention ne tient pas à cet emplacement : déplacez la signature ou raccourcissez la mention.")
        taille = max(MENTION_TAILLE_MIN, taille - 1)


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
    sortie = chemin_signe(chemin_pdf)
    if sortie != chemin_pdf and os.path.exists(sortie):
        raise SignatureInvalide(f"Une copie signée existe déjà ({os.path.basename(sortie)}) : signez-la pour "
                                "ajouter une signature, ou supprimez-la pour repartir de l'original.")
    png = _signature_png(signature)
    mention = _preparer_mention(mention)

    doc = _ouvrir(chemin_pdf)
    try:
        signees = set()
        for pl in placements:
            n = int(pl["page"])
            if not 1 <= n <= doc.page_count:
                raise SignatureInvalide(f"Page {n} hors du document ({doc.page_count} pages).")
            x, y, larg, haut = (float(pl[k]) for k in ("x", "y", "largeur", "hauteur"))
            if not (0 <= x < 1 and 0 <= y < 1 and 0 < larg <= 1 and 0 < haut <= 1):
                raise SignatureInvalide("Emplacement hors de la page.")
            page = doc[n - 1]
            W, H = page.rect.width, page.rect.height
            visible = pymupdf.Rect(x * W, y * H, min(1, x + larg) * W, min(1, y + haut) * H)
            # page.rect est la page AFFICHÉE ; insert_image attend les coordonnées non tournées. Sur une page
            # tournée dont la CropBox diffère de la MediaBox, pymupdf les décale (mesuré : l'image part ailleurs et
            # se fait rogner) ; à rotation 0, elles sont justes. On insère donc à 0, puis on remet la rotation.
            rotation, derotation = page.rotation, page.derotation_matrix
            page.set_rotation(0)
            page.insert_image(visible * derotation, stream=png, keep_proportion=True, overlay=True, rotate=rotation)
            if mention:
                taille = max(6.0, min(10.0, visible.height * 0.16))
                _ecrire_mention(page, visible, mention, taille, W, H, derotation, rotation)
            page.set_rotation(rotation)
            signees.add(n)
        contenu = doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
    ecrire_binaire_atomique(sortie, contenu)
    return {"chemin": sortie, "nom": os.path.basename(sortie), "pages": sorted(signees)}
