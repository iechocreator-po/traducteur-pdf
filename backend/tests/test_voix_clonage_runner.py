"""
Tests du runner de traitement des voix clonées. Le sous-processus d'extraction
OpenVoice (venv dédié) est simulé — jamais lancé réellement en CI.
"""

import time

from app.services import voix_clonage_runner, voix_clonees


def _attendre_statut(id_voix, statuts, timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin:
        entree = voix_clonees.obtenir_voix(id_voix)
        if entree and entree["statut"] in statuts:
            return entree
        time.sleep(0.05)
    raise AssertionError(f"Timeout en attendant {statuts}")


def test_demarrer_traitement_sans_venv_marque_erreur(monkeypatch):
    monkeypatch.setattr(voix_clonage_runner, "venv_openvoice_disponible", lambda: False)
    entree = voix_clonees.creer_voix("Ma voix")
    voix_clonage_runner.demarrer_traitement(entree["id"])
    maj = voix_clonees.obtenir_voix(entree["id"])
    assert maj["statut"] == "erreur"
    assert "OpenVoice" in maj["erreur"]


def test_demarrer_traitement_reussi_passe_a_termine(monkeypatch):
    monkeypatch.setattr(voix_clonage_runner, "venv_openvoice_disponible", lambda: True)

    class FauxResultat:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(voix_clonage_runner.subprocess, "run", lambda *a, **k: FauxResultat())

    entree = voix_clonees.creer_voix("Ma voix")
    voix_clonage_runner.demarrer_traitement(entree["id"])

    final = _attendre_statut(entree["id"], {"termine", "erreur"})
    assert final["statut"] == "termine"
    assert final["chemin_embedding"] == voix_clonees.chemin_embedding(entree["id"])


def test_demarrer_traitement_echec_sous_processus_marque_erreur(monkeypatch):
    monkeypatch.setattr(voix_clonage_runner, "venv_openvoice_disponible", lambda: True)

    class FauxResultat:
        returncode = 1
        stderr = "extraction impossible"

    monkeypatch.setattr(voix_clonage_runner.subprocess, "run", lambda *a, **k: FauxResultat())

    entree = voix_clonees.creer_voix("Ma voix")
    voix_clonage_runner.demarrer_traitement(entree["id"])

    final = _attendre_statut(entree["id"], {"termine", "erreur"})
    assert final["statut"] == "erreur"
    assert "extraction impossible" in final["erreur"]


def test_lire_statut_introuvable_retourne_none():
    assert voix_clonage_runner.lire_statut("inconnu") is None
