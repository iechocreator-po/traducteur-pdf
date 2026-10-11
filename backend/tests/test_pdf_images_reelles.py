"""
Non-régression sur l'extraction PDF (bug du 30/7/2026).

Deux défauts distincts, tous deux silencieux :

1. pymupdf4llm ≥ 1.28 active Tesseract DE LUI-MÊME dès qu'il le détecte sur la
   machine, puis REMPLACE le texte réel des pages illustrées par le résultat de
   l'OCR. Une page entière du livre disparaissait de la traduction.
2. Les images « embarquées » par la librairie sont des rognures de son analyse
   de mise en page, pas les figures du document.
"""

import os

import pytest

from app.services import pdf_extractor

PDF_REEL = os.path.join(
    os.path.dirname(__file__), "..", "..", "test_integre",
    "Models of the Mind_ Chapter 9.pdf",
)
PDF_REEL = os.path.normpath(PDF_REEL)

besoin_pdf = pytest.mark.skipif(
    not os.path.isfile(PDF_REEL), reason="PDF de référence absent"
)


def test_le_mode_ocr_jamais_est_disponible():
    """
    Si la librairie déplace OCRMode, on doit le voir ICI plutôt que de découvrir
    des traductions amputées : le repli est silencieux par conception.
    """
    assert pdf_extractor._mode_ocr_jamais() is not None, (
        "OCRMode.NEVER introuvable — pymupdf4llm a changé, l'OCR automatique "
        "peut de nouveau remplacer le texte réel des pages illustrées."
    )


@besoin_pdf
def test_le_texte_des_pages_illustrees_n_est_pas_remplace_par_de_l_ocr(monkeypatch):
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: False)
    texte = pdf_extractor._extraire_avec_pymupdf4llm(PDF_REEL)

    # Page 4 : le vrai texte, pas la sortie OCR « Map of K6nigsberg … ®—2 = @ ».
    assert "Leonhard Euler" in texte
    assert "K6nigsberg" not in texte, "l'OCR a de nouveau remplacé le texte réel"
    # Page 13.
    assert "Hubs are nodes" in texte


@besoin_pdf
def test_les_images_extraites_sont_les_figures_du_pdf(tmp_path, monkeypatch):
    import shutil

    copie = tmp_path / "chap9.pdf"
    shutil.copy(PDF_REEL, copie)
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: True)

    texte = pdf_extractor._extraire_avec_pymupdf4llm(str(copie))

    dossier = tmp_path / "chap9_images"
    fichiers = sorted(os.listdir(dossier))
    assert len(fichiers) == 2, f"attendu les 2 figures du chapitre, obtenu {fichiers}"

    # Les figures RÉELLES font 500 px de large ; les rognures produites par
    # l'ancienne méthode faisaient 395×311 et 263×33 (un fragment de texte).
    import pymupdf
    for nom in fichiers:
        # Pixmap donne les dimensions en PIXELS ; `page.rect` d'un JPEG ouvert
        # comme document rendrait sa taille d'affichage en points (72 dpi).
        pix = pymupdf.Pixmap(str(dossier / nom))
        assert pix.width == 500, (
            f"{nom} fait {pix.width}x{pix.height} px — ce n'est pas la figure du "
            "PDF mais une rognure de l'analyse de mise en page (l'ancienne "
            "méthode donnait 395x311 et 263x33)"
        )

    for nom in fichiers:
        assert f"chap9_images/{nom}" in texte
