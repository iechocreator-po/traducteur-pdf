"""
Tests des routes API, via le client de test FastAPI (aucun serveur réel nécessaire).
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.api.routes.lister_modeles_disponibles", return_value=["llama3.1"])
def test_health_check_ok(mock_lister):
    reponse = client.get("/api/health")
    assert reponse.status_code == 200
    assert reponse.json()["ollama_accessible"] == "oui"


@patch("app.api.routes.lister_modeles_disponibles", return_value=[])
def test_health_check_ollama_inaccessible(mock_lister):
    reponse = client.get("/api/health")
    assert reponse.status_code == 200
    assert reponse.json()["ollama_accessible"] == "non"


def test_feature_flags_retourne_un_dictionnaire():
    reponse = client.get("/api/feature-flags")
    assert reponse.status_code == 200
    assert isinstance(reponse.json(), dict)


def test_analyser_document_fichier_introuvable():
    reponse = client.post("/api/analyser", json={"chemin_pdf": "/chemin/inexistant.pdf"})
    assert reponse.status_code == 404


def test_glossaire_lecture_et_ecriture(tmp_path, monkeypatch):
    from app.services import glossaire
    monkeypatch.setattr(glossaire, "_FICHIER_GLOSSAIRE", str(tmp_path / "glossaire.json"))

    reponse = client.get("/api/glossaire")
    assert reponse.status_code == 200
    assert reponse.json() == {"termes": []}

    reponse = client.put("/api/glossaire", json={"termes": [" FastAPI ", "", "fastapi", "Ollama"]})
    assert reponse.status_code == 200
    assert reponse.json() == {"termes": ["FastAPI", "Ollama"]}

    reponse = client.get("/api/glossaire")
    assert reponse.json() == {"termes": ["FastAPI", "Ollama"]}


def test_schedule_batch_planifie_plusieurs_fichiers(tmp_path, monkeypatch):
    from app.services import scheduler
    monkeypatch.setattr(scheduler, "_FICHIER_JOBS", str(tmp_path / "scheduled_jobs.json"))

    doc1 = tmp_path / "doc1.md"
    doc2 = tmp_path / "doc2.md"
    doc1.write_text("# Un")
    doc2.write_text("# Deux")

    reponse = client.post("/api/schedule/batch", json={
        "chemins": [str(doc1), str(doc2)],
        "executer_a": "2030-01-01T23:00:00",
    })
    assert reponse.status_code == 200
    jobs = reponse.json()["jobs"]
    assert len(jobs) == 2
    assert all(j["statut"] == "planifie" for j in jobs)

    reponse = client.get("/api/scheduled/tous")
    assert len(reponse.json()["jobs"]) == 2


def test_schedule_batch_fichier_introuvable(tmp_path, monkeypatch):
    from app.services import scheduler
    monkeypatch.setattr(scheduler, "_FICHIER_JOBS", str(tmp_path / "scheduled_jobs.json"))

    reponse = client.post("/api/schedule/batch", json={
        "chemins": ["/chemin/inexistant.pdf"],
        "executer_a": "2030-01-01T23:00:00",
    })
    assert reponse.status_code == 404


def test_schedule_batch_liste_vide_refusee():
    reponse = client.post("/api/schedule/batch", json={
        "chemins": [],
        "executer_a": "2030-01-01T23:00:00",
    })
    assert reponse.status_code == 422
