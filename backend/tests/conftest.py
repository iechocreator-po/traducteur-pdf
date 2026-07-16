"""
Fixtures communes à tous les tests.
"""

import pytest


@pytest.fixture(autouse=True)
def registre_bibliotheque_isole(tmp_path, monkeypatch):
    """
    Redirige le registre de la Bibliothèque vers un fichier temporaire pour
    chaque test : les tests qui lancent des traductions (demarrer_traduction)
    ne doivent jamais polluer le bibliotheque.json réel du développeur.
    """
    from app.services import bibliotheque
    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", str(tmp_path / "bibliotheque_test.json"))


@pytest.fixture(autouse=True)
def log_interet_isole(tmp_path, monkeypatch):
    """Même isolation pour le log d'intérêt des fonctionnalités."""
    from app.services import interet
    monkeypatch.setattr(interet, "_FICHIER_LOG", str(tmp_path / "interet_test.log"))


@pytest.fixture(autouse=True)
def registre_voix_clonees_isole(tmp_path, monkeypatch):
    """Même isolation pour le registre des voix clonées (OpenVoice)."""
    from app.services import voix_clonees
    dossier = tmp_path / "voix_utilisateur_test"
    monkeypatch.setattr(voix_clonees, "DOSSIER_VOIX_UTILISATEUR", str(dossier))
    monkeypatch.setattr(voix_clonees, "CHEMIN_REGISTRE", str(dossier / "registre.json"))


@pytest.fixture(autouse=True)
def dossier_uploads_isole(tmp_path, monkeypatch):
    """Redirige les uploads vers un dossier temporaire : sans ça, les tests
    d'upload écriraient dans le vrai backend/uploads/."""
    from app.services import uploads
    dossier = tmp_path / "uploads_test"
    monkeypatch.setattr(uploads, "DOSSIER_UPLOADS", str(dossier))
    monkeypatch.setattr(uploads, "DOSSIER_TMP", str(dossier / ".tmp"))
