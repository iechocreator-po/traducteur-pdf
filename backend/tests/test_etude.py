"""
Tests du service de génération LLM de la fiche d'étude (etude.py).
Les réponses HTTP d'Ollama sont simulées.
"""

import json

import pytest

from app.services import etude


class _ReponseHttp:
    def __init__(self, contenu: str):
        self._contenu = contenu

    def raise_for_status(self):
        pass

    def json(self):
        return {"response": self._contenu}


def _mock_post(monkeypatch, reponses: list[str]):
    """Fait retourner à requests.post les contenus donnés, dans l'ordre."""
    file_reponses = list(reponses)

    def faux_post(url, json=None, timeout=None):
        return _ReponseHttp(file_reponses.pop(0))

    monkeypatch.setattr(etude.requests, "post", faux_post)


def test_generer_points_valide(monkeypatch):
    _mock_post(monkeypatch, [json.dumps({"points": ["A", "B", "C", "D", "E"]})])
    points = etude.generer_points("texte", "llama3.1", "français", 5)
    assert points == ["A", "B", "C", "D", "E"]


def test_generer_points_tronque_le_surplus(monkeypatch):
    _mock_post(monkeypatch, [json.dumps({"points": ["A", "B", "C", "D"]})])
    points = etude.generer_points("texte", "llama3.1", "français", 3)
    assert points == ["A", "B", "C"]


def test_json_invalide_relance_une_fois(monkeypatch):
    _mock_post(monkeypatch, ["pas du json {", json.dumps({"points": ["A"]})])
    points = etude.generer_points("texte", "llama3.1", "français", 1)
    assert points == ["A"]


def test_json_invalide_deux_fois_leve_une_erreur(monkeypatch):
    _mock_post(monkeypatch, ["{}", '{"points": []}'])
    with pytest.raises(etude.ReponseJsonInvalide):
        etude.generer_points("texte", "llama3.1", "français", 3)


def test_generer_questions_valide(monkeypatch):
    _mock_post(monkeypatch, [json.dumps({
        "questions": [
            {"question": "Pourquoi ?", "reponse": "Parce que."},
            {"question": "Comment ?", "reponse": "Ainsi."},
            {"question": "Quand ?", "reponse": "Hier."},
        ]
    })])
    questions = etude.generer_questions("texte", "llama3.1", "français", 3)
    assert len(questions) == 3
    assert questions[0].question == "Pourquoi ?"
    assert questions[0].reponse == "Parce que."


def test_generer_questions_schema_incomplet_relance(monkeypatch):
    _mock_post(monkeypatch, [
        json.dumps({"questions": [{"question": "Sans réponse ?"}]}),
        json.dumps({"questions": [{"question": "Q ?", "reponse": "R."}]}),
    ])
    questions = etude.generer_questions("texte", "llama3.1", "français", 1)
    assert questions[0].reponse == "R."


def test_condenser_texte(monkeypatch):
    _mock_post(monkeypatch, ["Notes condensées."])
    assert etude.condenser_texte("long texte", "llama3.1", "français") == "Notes condensées."
