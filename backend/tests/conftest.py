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
