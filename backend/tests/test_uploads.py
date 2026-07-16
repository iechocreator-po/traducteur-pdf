"""
Tests unitaires du service d'upload : assainissement des noms (anti-évasion +
lisibilité), détection de type par contenu, écriture en flux, purge conservatrice.
"""

import io
import os
import time

import pytest
from reportlab.pdfgen import canvas

from app.services import uploads


def _pdf_octets() -> bytes:
    """Un vrai PDF minimal, ouvrable par pdfplumber (validation forte)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Bonjour")
    c.save()
    return buf.getvalue()


def _flux(octets: bytes):
    """Adapte des octets en callable lire_morceau (blocs de 64 Ko)."""
    bio = io.BytesIO(octets)
    return lambda: bio.read(65536)


# ── assainir_nom : anti-évasion ───────────────────────────────────────────────

@pytest.mark.parametrize("entree,attendu", [
    ("../../../etc/passwd", "passwd"),
    ("..\\..\\x.pdf", "x.pdf"),
    ("/absolu.pdf", "absolu.pdf"),
    ("dossier/sous/fichier.md", "fichier.md"),
])
def test_assainir_nom_neutralise_les_chemins(entree, attendu):
    assert uploads.assainir_nom(entree) == attendu


@pytest.mark.parametrize("entree", ["", ".", "..", "...", "   "])
def test_assainir_nom_fallback_document(entree):
    assert uploads.assainir_nom(entree).startswith("document")


def test_assainir_nom_preserve_les_accents():
    # Lisibilité : la Bibliothèque et le lot affichent ce nom tel quel.
    assert uploads.assainir_nom("Mon Résumé Final.pdf") == "Mon Résumé Final.pdf"


def test_assainir_nom_retire_les_caracteres_de_controle():
    assert "\x00" not in uploads.assainir_nom("a\x00b.pdf")
    assert "\n" not in uploads.assainir_nom("a\nb.md")


def test_assainir_nom_tronque_en_octets():
    nom = "é" * 400 + ".pdf"
    assaini = uploads.assainir_nom(nom)
    stem = os.path.splitext(assaini)[0]
    assert len(stem.encode("utf-8")) <= uploads.STEM_MAX_OCTETS


# ── detecter_type : le contenu prime sur l'extension ──────────────────────────

def test_detecter_type_pdf():
    pdf = _pdf_octets()
    assert uploads.detecter_type(pdf[:5], pdf) == "pdf"


def test_detecter_type_md():
    txt = "# Titre\n\ndu texte".encode("utf-8")
    assert uploads.detecter_type(txt[:5], txt) == "md"


def test_detecter_type_binaire_rejete():
    binaire = b"\xff\xfe\x00\x01parasite"
    assert uploads.detecter_type(binaire[:5], binaire) is None


# ── enregistrer_flux ──────────────────────────────────────────────────────────

def test_enregistrer_flux_pdf_valide():
    res = uploads.enregistrer_flux(_flux(_pdf_octets()), "cours.pdf")
    assert res["type"] == "PDF"
    assert res["nom"] == "cours.pdf"
    assert res["chemin"].endswith("/cours.pdf")
    assert os.path.isfile(res["chemin"])


def test_enregistrer_flux_md_valide():
    res = uploads.enregistrer_flux(_flux(b"# Chapitre\n\ntexte"), "notes.md")
    assert res["type"] == "MD"
    assert res["chemin"].endswith("/notes.md")


def test_enregistrer_flux_extension_suit_le_contenu():
    # Nommé .md mais contient un PDF → stocké en .pdf.
    res = uploads.enregistrer_flux(_flux(_pdf_octets()), "piege.md")
    assert res["chemin"].endswith(".pdf")


def test_enregistrer_flux_anti_evasion():
    res = uploads.enregistrer_flux(_flux(_pdf_octets()), "../../../etc/passwd")
    reel = os.path.realpath(res["chemin"])
    racine = os.path.realpath(uploads.DOSSIER_UPLOADS)
    assert os.path.commonpath([reel, racine]) == racine


def test_enregistrer_flux_vide_rejete():
    with pytest.raises(uploads.UploadInvalide):
        uploads.enregistrer_flux(_flux(b""), "vide.md")


def test_enregistrer_flux_binaire_rejete():
    with pytest.raises(uploads.UploadInvalide):
        uploads.enregistrer_flux(_flux(b"\xff\xfe\x00parasite"), "x.md")


def test_enregistrer_flux_pdf_corrompu_rejete_et_nettoie():
    with pytest.raises(uploads.UploadInvalide):
        uploads.enregistrer_flux(_flux(b"%PDF-1.4 poubelle non ouvrable"), "faux.pdf")
    # Aucun dossier ne doit rester derrière un rejet.
    if os.path.isdir(uploads.DOSSIER_UPLOADS):
        restants = [d for d in os.listdir(uploads.DOSSIER_UPLOADS) if d != ".tmp"]
        assert restants == []


def test_enregistrer_flux_trop_gros_rejete_sans_laisser_de_trace(monkeypatch):
    monkeypatch.setattr(uploads, "TAILLE_MAX_UPLOAD_OCTETS", 10)
    with pytest.raises(uploads.UploadTropVolumineux):
        uploads.enregistrer_flux(_flux(b"# " + b"x" * 100), "gros.md")
    # Le .part est nettoyé (finally), rien ne subsiste.
    if os.path.isdir(uploads.DOSSIER_TMP):
        assert os.listdir(uploads.DOSSIER_TMP) == []


def test_enregistrer_flux_idempotent_par_contenu():
    pdf = _pdf_octets()
    r1 = uploads.enregistrer_flux(_flux(pdf), "a.pdf")
    r2 = uploads.enregistrer_flux(_flux(pdf), "a.pdf")
    assert r1["chemin"] == r2["chemin"]
    assert r2["deja_present"] is True


# ── purge ─────────────────────────────────────────────────────────────────────

def _vieux_dossier(nom: str, *fichiers: str) -> str:
    dossier = os.path.join(uploads.DOSSIER_UPLOADS, nom)
    os.makedirs(dossier, exist_ok=True)
    for f in fichiers or ("source.pdf",):
        chemin = os.path.join(dossier, f)
        open(chemin, "w").close()
        vieux = time.time() - 40 * 86400
        os.utime(chemin, (vieux, vieux))
    return dossier


def test_purge_supprime_un_upload_abandonne():
    _vieux_dossier("abandonne", "source.pdf")
    n = uploads.purger_uploads_anciens(references=set())
    assert n == 1
    assert not os.path.isdir(os.path.join(uploads.DOSSIER_UPLOADS, "abandonne"))


def test_purge_conserve_un_dossier_avec_traduction():
    _vieux_dossier("travaille", "source.pdf", "source_traduit_ll.md")
    uploads.purger_uploads_anciens(references=set())
    assert os.path.isdir(os.path.join(uploads.DOSSIER_UPLOADS, "travaille"))


def test_purge_conserve_un_dossier_reference_en_bibliotheque():
    d = _vieux_dossier("cite", "source.pdf")
    ref = os.path.join(d, "source.pdf")
    uploads.purger_uploads_anciens(references={ref})
    assert os.path.isdir(d)


def test_purge_ignore_les_dossiers_recents():
    dossier = os.path.join(uploads.DOSSIER_UPLOADS, "recent")
    os.makedirs(dossier, exist_ok=True)
    open(os.path.join(dossier, "source.pdf"), "w").close()  # mtime = maintenant
    uploads.purger_uploads_anciens(references=set())
    assert os.path.isdir(dossier)


def test_purge_supprime_toujours_les_residus_tmp():
    os.makedirs(uploads.DOSSIER_TMP, exist_ok=True)
    residu = os.path.join(uploads.DOSSIER_TMP, "abc.part")
    open(residu, "w").close()
    uploads.purger_uploads_anciens(references=set())
    assert not os.path.exists(residu)
