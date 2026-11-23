"""Pages de table des matières : repérage (signet ou repli) et rendu une entrée par ligne."""

import pymupdf
import pytest

from app.services import pdf_extractor
from app.services.table_matieres import pages_table_matieres, rendre_page


def _page_tdm(doc, entete=None, avec_titre=True, gauche=54):
    """Page imitant un livre d'éditeur : chapitre en gras + numéro à droite, sections ', n'."""
    page = doc.new_page(width=432, height=648)
    y = 60
    if entete:
        page.insert_text((gauche, 40), entete[0], fontname="tiro", fontsize=9)
        page.insert_text((330, 40), entete[1], fontname="tiro", fontsize=9)
    if avec_titre:
        page.insert_text((gauche + 20, y), "Contents", fontname="tiro", fontsize=20)
        y += 50
    lignes = [
        (0, "1", "Financial Machine Learning", "3", True),
        (1, "1.1", "Motivation ,  3", None, False),
        (1, "1.2", "The Main Reason Projects Fail ,  4", None, False),
        (2, "1.2.1", "The Sisyphus Paradigm ,  4", None, False),
        (1, None, "Exercises ,  19", None, False),
        (0, "2", "Financial Data Structures", "23", True),
        (1, "2.1", "Motivation ,  23", None, False),
        (1, None, "References ,  41", None, False),
    ]
    for niveau, numero, titre, droite, gras in lignes:
        police = "tibo" if gras else "tiro"
        x = gauche + 15 * niveau + (8 if niveau == 2 else 0)
        if numero:
            page.insert_text((x, y), numero, fontname=police, fontsize=10)
            page.insert_text((x + 25, y), titre, fontname=police, fontsize=10)
        else:
            page.insert_text((x, y), titre, fontname=police, fontsize=10)
        if droite:
            page.insert_text((380, y), droite, fontname=police, fontsize=10)
        y += 16
    page.insert_text((380, 620), "ix", fontname="tibo", fontsize=9)
    return page


def _pdf(tmp_path, signet=True, suite=True):
    doc = pymupdf.open()
    p = doc.new_page(width=432, height=648)
    p.insert_text((54, 100), "Title page", fontsize=20)
    _page_tdm(doc)
    if suite:
        _page_tdm(doc, entete=("x", "CONTENTS"), avec_titre=False, gauche=43)
    corps = doc.new_page(width=432, height=648)
    corps.insert_text((54, 100), "Chapter 1 body text, a paragraph of prose.", fontsize=11)
    if signet:
        doc.set_toc([[1, "Title Page", 1], [1, "Contents", 2], [1, "1 Financial Machine Learning", 2 + (2 if suite else 1)]])
    chemin = tmp_path / "livre.pdf"
    doc.save(chemin)
    return str(chemin)


def test_pages_par_signet(tmp_path):
    with pymupdf.open(_pdf(tmp_path)) as doc:
        assert pages_table_matieres(doc) == [1, 2]


def test_pages_par_repli_sans_signet(tmp_path):
    with pymupdf.open(_pdf(tmp_path, signet=False)) as doc:
        assert pages_table_matieres(doc) == [1, 2]


def test_document_sans_tdm_non_touche(tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((54, 100), f"Plain prose page {i}, with a comma, 12", fontsize=11)
    assert pages_table_matieres(doc) == []


def test_rendu_une_entree_par_ligne_avec_niveaux(tmp_path):
    with pymupdf.open(_pdf(tmp_path)) as doc:
        md = rendre_page(doc[1], "### Contents", premiere=True)
    lignes = md.strip().splitlines()
    assert lignes[0] == "### Contents"
    assert "- **1 Financial Machine Learning**, 3" in lignes
    assert "  - 1.1 Motivation, 3" in lignes
    assert "    - 1.2.1 The Sisyphus Paradigm, 4" in lignes
    assert "  - Exercises, 19" in lignes            # sans numéro : niveau de son retrait
    assert not any(l.strip() == "- ix" or "ix" == l.strip() for l in lignes)  # folio retiré
    assert "<br>" not in md and "|" not in md


def test_page_de_suite_sans_en_tete_ni_titre(tmp_path):
    with pymupdf.open(_pdf(tmp_path)) as doc:
        md = rendre_page(doc[2], None, premiere=False)
    assert "CONTENTS" not in md and "Contents" not in md
    assert md.strip().splitlines()[0] == "- **1 Financial Machine Learning**, 3"


@pytest.mark.parametrize("flag_images", [False, True])
def test_extraction_remplace_seulement_la_tdm(tmp_path, monkeypatch, flag_images):
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: flag_images and nom == "extraction_images_pdf")
    texte = pdf_extractor._extraire_avec_pymupdf4llm(_pdf(tmp_path))
    assert "  - 1.1 Motivation, 3" in texte
    assert "Chapter 1 body text" in texte
    assert "Title page" in texte


def test_table_entiere_paragraphes_seulement_devant_un_niveau_0(tmp_path):
    """Un saut de page n'ouvre pas de paragraphe : la traduction retire le retrait de tête (22/11/2026)."""
    from app.services.table_matieres import rendre_table
    with pymupdf.open(_pdf(tmp_path)) as doc:
        md = rendre_table(doc, [1, 2], "### Contents")
    paragraphes = [p for p in md.split("\n\n") if p.strip()]
    assert paragraphes[0] == "### Contents"
    for p in paragraphes[1:]:
        assert p.startswith("- "), p          # jamais de retrait en tête de paragraphe
    assert md.count("  - 1.1 Motivation, 3") == 2   # les deux pages sont bien là
