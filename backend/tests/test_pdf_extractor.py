"""
Tests du service d'extraction PDF.
On utilise un petit PDF généré à la volée pour ne dépendre d'aucun fichier externe.
"""

import pdfplumber
import pytest
from reportlab.pdfgen import canvas

from app.services.pdf_extractor import decouper_en_chunks, extraire_texte


@pytest.fixture
def pdf_simple(tmp_path):
    """Crée un petit PDF de test avec du texte connu."""
    chemin = tmp_path / "test.pdf"
    c = canvas.Canvas(str(chemin))
    c.drawString(100, 750, "Hello world, this is a test document.")
    c.showPage()
    c.save()
    return str(chemin)


def test_extraire_texte_retourne_le_contenu(pdf_simple):
    texte = extraire_texte(pdf_simple)
    assert "Hello world" in texte


def test_extraire_texte_pdf_inexistant_leve_une_erreur():
    with pytest.raises(Exception):
        extraire_texte("/chemin/qui/n_existe/pas.pdf")


def test_decouper_en_chunks_respecte_la_taille_max():
    texte = "Paragraphe un.\n\n" * 500  # texte volontairement long
    chunks = decouper_en_chunks(texte, taille_max=100)
    assert len(chunks) > 1
    for chunk in chunks:
        # Un peu de marge car on ne coupe jamais en plein milieu d'un paragraphe
        assert len(chunk) < 200


def test_decouper_en_chunks_texte_court_donne_un_seul_chunk():
    texte = "Un texte court."
    chunks = decouper_en_chunks(texte, taille_max=3000)
    assert len(chunks) == 1
    assert chunks[0] == texte
