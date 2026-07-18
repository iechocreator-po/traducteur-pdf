"""
Tests du service d'extraction PDF.
On utilise un petit PDF généré à la volée pour ne dépendre d'aucun fichier externe.
"""

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


# ── Extracteur Tesseract (OCR) ────────────────────────────────────────────────

def test_tesseract_indisponible_leve_une_erreur_claire(monkeypatch):
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor.shutil, "which", lambda _: None)
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="brew install tesseract"):
        pdf_extractor.extraire_texte("/fake/doc.pdf", "tesseract")


def test_langues_tesseract_croise_avec_les_modeles_installes(monkeypatch):
    from unittest.mock import MagicMock
    from app.services import pdf_extractor

    res = MagicMock()
    res.stdout = "List of available languages (3):\neng\nfra\nosd\n"
    monkeypatch.setattr(pdf_extractor.subprocess, "run", lambda *a, **k: res)
    assert pdf_extractor._langues_tesseract() == "eng+fra"


def test_langues_tesseract_retombe_sur_eng_en_cas_d_echec(monkeypatch):
    from app.services import pdf_extractor

    def echec(*a, **k):
        raise OSError("binaire introuvable")
    monkeypatch.setattr(pdf_extractor.subprocess, "run", echec)
    assert pdf_extractor._langues_tesseract() == "eng"


def test_extracteur_tesseract_liste_dans_la_config():
    from app.config.feature_flags import EXTRACTEURS_PDF
    ids = [e["id"] for e in EXTRACTEURS_PDF]
    assert "tesseract" in ids


def test_relier_toc_apparie_par_mots_distinctifs_sans_grab_du_titre_vide():
    """
    Régression du bug « Half-Title partout » : un heading Markdown sans mot
    distinctif (« * * * ») ne doit JAMAIS capter le contenu d'un signet, et
    chaque signet doit matcher son vrai chapitre par ses mots distinctifs.
    """
    from app.services.pdf_extractor import relier_toc_a_markdown

    toc = [
        {"index": 0, "titre": "Half-Title", "niveau": 1, "page": 1},
        {"index": 1, "titre": "Chapter 1: Spherical Cows", "niveau": 1, "page": 8},
        {"index": 2, "titre": "Chapter 6: Stages of Sight", "niveau": 1, "page": 120},
    ]
    # Le séparateur « * * * » (aucun mot distinctif) précède les vrais chapitres.
    chapitres_md = [
        {"index": 0, "titre": "* * *", "niveau": 6, "contenu": "SEPARATEUR", "ligne_debut": 0, "ligne_fin": 1},
        {"index": 1, "titre": "**Spherical Cows**", "niveau": 5, "contenu": "CONTENU CH1", "ligne_debut": 2, "ligne_fin": 9},
        {"index": 2, "titre": "**Stages of Sight**", "niveau": 5, "contenu": "CONTENU CH6", "ligne_debut": 10, "ligne_fin": 20},
    ]
    relies = relier_toc_a_markdown(toc, chapitres_md)
    par_index = {c["index"]: c for c in relies}
    # Le « * * * » n'a JAMAIS été attribué à un signet.
    assert "SEPARATEUR" not in {c["contenu"] for c in relies}
    # Chaque vrai chapitre a SON contenu (pas celui d'un autre).
    assert par_index[1]["contenu"] == "CONTENU CH1"
    assert par_index[2]["contenu"] == "CONTENU CH6"
    # Half-Title n'a pas de mot distinctif correspondant → contenu vide (pas de grab).
    assert par_index[0]["contenu"] == ""


def test_relier_toc_progression_monotone():
    """Deux signets qui partagent un mot (« neural ») ne doivent pas matcher le
    même heading : la progression est monotone (chacun matche après le précédent)."""
    from app.services.pdf_extractor import relier_toc_a_markdown

    toc = [
        {"index": 0, "titre": "Chapter 3: Neural Learning", "niveau": 1, "page": 40},
        {"index": 1, "titre": "Chapter 7: Neural Code", "niveau": 1, "page": 130},
    ]
    chapitres_md = [
        {"index": 0, "titre": "**Neural Learning**", "niveau": 5, "contenu": "C3", "ligne_debut": 0, "ligne_fin": 5},
        {"index": 1, "titre": "**Neural Code**", "niveau": 5, "contenu": "C7", "ligne_debut": 6, "ligne_fin": 10},
    ]
    relies = relier_toc_a_markdown(toc, chapitres_md)
    assert relies[0]["contenu"] == "C3"
    assert relies[1]["contenu"] == "C7"  # pas « C3 » à nouveau
