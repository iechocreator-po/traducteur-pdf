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
