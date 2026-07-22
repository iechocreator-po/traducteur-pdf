"""
Tests du service d'extraction PDF.
On utilise un petit PDF généré à la volée pour ne dépendre d'aucun fichier externe.
"""

import glob
import os
import re

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


@pytest.fixture
def pdf_avec_image(tmp_path):
    """Crée un petit PDF de test avec une image intégrée."""
    from PIL import Image

    chemin_image = tmp_path / "source.png"
    Image.new("RGB", (200, 200), color=(255, 0, 0)).save(chemin_image)

    chemin = tmp_path / "test_image.pdf"
    c = canvas.Canvas(str(chemin))
    c.drawString(100, 750, "Texte avec une image.")
    c.drawImage(str(chemin_image), 100, 400, width=200, height=200)
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


# ── Extraction d'images (flag "extraction_images_pdf") ────────────────────────

def test_extraction_images_off_par_defaut_aucun_tag(pdf_avec_image):
    """Flag off (défaut) : comportement strictement inchangé, aucun tag image."""
    texte = extraire_texte(pdf_avec_image)
    assert "![" not in texte
    base, _ = os.path.splitext(pdf_avec_image)
    assert not os.path.isdir(f"{base}_images")


def test_extraction_images_actif_produit_un_tag_et_le_fichier(pdf_avec_image, monkeypatch):
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: True)

    texte = pdf_extractor.extraire_texte(pdf_avec_image)
    m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", texte)
    assert m, "aucun tag d'image trouvé dans le markdown"

    chemin_relatif = m.group(1)
    dossier_source = os.path.dirname(pdf_avec_image)
    assert os.path.isfile(os.path.join(dossier_source, chemin_relatif))
    # Chemin relatif court, pas le chemin absolu brut renvoyé par la lib.
    assert not os.path.isabs(chemin_relatif)


def test_nettoyage_texte_image_retire_les_marqueurs():
    """
    Régression du bug rapporté : quand pymupdf4llm ne parvient pas à capturer
    l'image elle-même, il laisse un texte de secours entouré de marqueurs
    (traduits en français par le modèle, d'où le rapport initial) — ils ne
    doivent jamais fuiter tels quels dans le document traduit/exporté.
    """
    from app.services.pdf_extractor import _RE_TEXTE_IMAGE, _nettoyer_texte_image

    brut = (
        "Texte avant.\n\n"
        "<!-- Start of picture text -->\n"
        "Leyden Jar<br>Capacitor<br>\\ ee<br>Metal coating<br>"
        "<!-- End of picture text -->\n\n"
        "Texte après."
    )
    nettoye = _RE_TEXTE_IMAGE.sub(_nettoyer_texte_image, brut)

    assert "<!-- Start of picture text -->" not in nettoye
    assert "<!-- End of picture text -->" not in nettoye
    assert "<br>" not in nettoye
    assert "Leyden Jar, Capacitor, \\ ee, Metal coating" in nettoye
    assert "Texte avant." in nettoye and "Texte après." in nettoye


def test_nettoyage_texte_image_bloc_vide_ne_laisse_rien():
    from app.services.pdf_extractor import _RE_TEXTE_IMAGE, _nettoyer_texte_image
    brut = "Avant.\n\n<!-- Start of picture text -->\n<!-- End of picture text -->\n\nAprès."
    nettoye = _RE_TEXTE_IMAGE.sub(_nettoyer_texte_image, brut)
    assert "picture text" not in nettoye
    assert "Avant." in nettoye and "Après." in nettoye


def test_extraction_images_actif_sans_image_ne_cree_pas_de_dossier(pdf_simple, monkeypatch):
    """Un PDF sans image, même avec le flag actif, ne crée aucun dossier _images."""
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: True)

    pdf_extractor.extraire_texte(pdf_simple)
    base, _ = os.path.splitext(pdf_simple)
    assert not os.path.isdir(f"{base}_images")


def test_decouper_en_chunks_ne_scinde_pas_un_bloc_contenant_une_image():
    """
    Sans la protection du tag ![]() comme frontière (au même titre que les
    tableaux), ce texte serait scindé en 3 blocs à la fusion, isolant le tag
    image tout seul dans son propre chunk.
    """
    tag = "![](images/fig.png)"
    para_avant = "Paragraphe avant l'image, assez long pour dépasser la taille max à lui seul."
    para_apres = "Paragraphe après l'image, également assez long pour dépasser la taille max."
    texte = f"{para_avant}\n\n{tag}\n\n{para_apres}"

    chunks = decouper_en_chunks(texte, taille_max=50)

    assert len(chunks) == 1
    assert tag in chunks[0]


def test_decouper_en_chunks_reconnait_le_tag_image():
    from app.services.pdf_extractor import _est_tag_image
    assert _est_tag_image("![](images/x.png)")
    assert _est_tag_image("  ![alt text](chemin/vers/img.png)  ")
    assert not _est_tag_image("Du texte normal.")
    assert not _est_tag_image("[un lien](https://example.com)")


# ── Persistance automatique (une seule extraction PDF) ─────────────────────────

def _compter_appels_extraction(monkeypatch):
    from app.services import pdf_extractor
    compteur = {"n": 0}
    original = pdf_extractor.pymupdf4llm.to_markdown

    def to_markdown_compte(*args, **kwargs):
        compteur["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pdf_extractor.pymupdf4llm, "to_markdown", to_markdown_compte)
    return compteur


def test_flag_actif_extraction_pdf_une_seule_fois(pdf_simple, monkeypatch):
    """
    Avec le flag actif, le premier appel persiste le _converti_*.md ; les
    appels suivants le relisent au lieu de rappeler pymupdf4llm.to_markdown().
    """
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: True)
    compteur = _compter_appels_extraction(monkeypatch)

    pdf_extractor.identifier_chapitres(pdf_simple)
    pdf_extractor.identifier_chapitres(pdf_simple)
    pdf_extractor.identifier_chapitres(pdf_simple)

    assert compteur["n"] == 1
    base, _ = os.path.splitext(pdf_simple)
    assert glob.glob(f"{base}_converti*.md")


def test_flag_inactif_extrait_a_chaque_appel(pdf_simple, monkeypatch):
    """Flag off (défaut) : comportement inchangé, aucune persistance automatique."""
    from app.services import pdf_extractor
    compteur = _compter_appels_extraction(monkeypatch)

    pdf_extractor.identifier_chapitres(pdf_simple)
    pdf_extractor.identifier_chapitres(pdf_simple)

    assert compteur["n"] == 2
    base, _ = os.path.splitext(pdf_simple)
    assert not glob.glob(f"{base}_converti*.md")


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
