"""
Tests de la garde de chemin centralisée (app/api/validation.py) : chemin absolu,
extension autorisée, fichier existant — sans casser le flux « chemin absolu »
du mode avancé (un symlink vers un fichier hors uploads/ doit passer).
"""

import os

import pytest
from fastapi import HTTPException
from reportlab.pdfgen import canvas

from app.api.validation import (
    valider_chemin_source,
    verifier_origine_upload,
    ORIGINES_LOCALES,
)


def _ecrire_pdf(chemin) -> str:
    c = canvas.Canvas(str(chemin))
    c.drawString(100, 750, "x")
    c.save()
    return str(chemin)


def test_chemin_relatif_rejete():
    with pytest.raises(HTTPException) as e:
        valider_chemin_source("docs/x.pdf")
    assert e.value.status_code == 422


def test_extension_non_autorisee_rejetee(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello")
    with pytest.raises(HTTPException) as e:
        valider_chemin_source(str(f))
    assert e.value.status_code == 422


def test_fichier_absent_donne_404(tmp_path):
    with pytest.raises(HTTPException) as e:
        valider_chemin_source(str(tmp_path / "absent.pdf"))
    assert e.value.status_code == 404


def test_dossier_au_lieu_d_un_fichier_donne_404(tmp_path):
    # Avant : os.path.exists laissait passer un dossier → IsADirectoryError → 500.
    # Un dossier nommé comme un fichier .pdf passe l'extension mais échoue isfile.
    faux = tmp_path / "piege.pdf"
    faux.mkdir()
    with pytest.raises(HTTPException) as e:
        valider_chemin_source(str(faux))
    assert e.value.status_code == 404


def test_chemin_vide_ou_nul_rejete():
    with pytest.raises(HTTPException):
        valider_chemin_source("")
    with pytest.raises(HTTPException):
        valider_chemin_source("/tmp/a\x00b.pdf")


def test_pdf_valide_retourne_le_realpath(tmp_path):
    chemin = _ecrire_pdf(tmp_path / "doc.pdf")
    resolu = valider_chemin_source(chemin)
    assert resolu == os.path.realpath(chemin)


def test_symlink_hors_uploads_passe(tmp_path):
    """Non-régression du flux « chemin absolu » : un lien vers un PDF réel
    ailleurs sur le disque reste accepté (les deux flux coexistent)."""
    cible = _ecrire_pdf(tmp_path / "reel.pdf")
    lien = tmp_path / "lien.pdf"
    os.symlink(cible, lien)
    resolu = valider_chemin_source(str(lien))
    assert resolu == os.path.realpath(cible)


# ── Garde d'origine de l'upload ───────────────────────────────────────────────

def test_origine_absente_acceptee():
    verifier_origine_upload(None)  # curl / Swift / file:// → OK


def test_origine_locale_acceptee():
    for o in ORIGINES_LOCALES:
        verifier_origine_upload(o)


def test_origine_tierce_refusee():
    with pytest.raises(HTTPException) as e:
        verifier_origine_upload("https://evil.example")
    assert e.value.status_code == 403
