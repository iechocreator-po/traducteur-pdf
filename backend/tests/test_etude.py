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


# ── Dimensionnement automatique (18/8) ───────────────────────────────────────

def test_le_nombre_de_points_suit_la_longueur():
    """
    Le produit demandait 5 points quelle que soit la taille. Cinq points pour un
    chapitre de livre de 50 000 caractères ne peuvent qu'être vagues — première
    cause du « trop simpliste » signalé.
    """
    from app.services.etude import calculer_nb_points

    assert calculer_nb_points(1_500) == 3
    assert calculer_nb_points(8_000) == 5
    assert calculer_nb_points(20_000) == 8
    assert calculer_nb_points(60_000) == 12
    # Monotone : un chapitre plus long n'a jamais MOINS de points.
    valeurs = [calculer_nb_points(n) for n in (500, 4_000, 12_000, 30_000, 100_000)]
    assert valeurs == sorted(valeurs)


def test_moins_de_questions_que_de_points():
    """Une question coûte plus cher à lire qu'une puce ; au-delà de 6 c'est du remplissage."""
    from app.services.etude import calculer_nb_points, calculer_nb_questions

    for n in (1_000, 8_000, 20_000, 60_000):
        assert calculer_nb_questions(n) <= calculer_nb_points(n)
    assert calculer_nb_questions(200_000) <= 6


def test_les_types_de_questions_sont_imposes_et_varies(monkeypatch):
    """
    Sans types nommés, les trois questions étaient du rappel déguisé, toutes
    introduites par « selon le texte » — alors que l'ancienne consigne demandait
    déjà « comprendre, pas mémoriser ». Nommer les types obtient ce que la
    formulation générale n'obtenait pas.
    """
    from app.services import etude

    captures = {}

    def faux_appel(modele, system, prompt):
        captures["system"] = system
        return '{"questions": [{"question": "Q ?", "reponse": "R."}]}'

    monkeypatch.setattr(etude, "_appeler_ollama_json", faux_appel)
    etude.generer_questions("texte", "llama3.1", "français", 3, points=["Point A", "Point B"])

    system = captures["system"]
    assert "MÉCANISME" in system
    assert "RAISON" in system
    assert "TRANSFERT" in system
    assert "Selon le texte" in system          # l'interdiction est bien posée
    assert "Point A" in system                 # les points servent de contexte


def test_la_consigne_des_points_exige_une_phrase_complete(monkeypatch):
    """
    ⚠️ Régression de formulation. « doit CONTENIR un élément concret » faisait
    rendre à llama3.1 des mots-clés nus (« Santiago Ramón y Cajal », « 1931 »).
    La consigne doit dire PHRASE COMPLÈTE.
    """
    from app.services import etude

    captures = {}

    def faux_appel(modele, system, prompt):
        captures["system"] = system
        return '{"points": ["Un point."]}'

    monkeypatch.setattr(etude, "_appeler_ollama_json", faux_appel)
    etude.generer_points("texte", "llama3.1", "français", 5)

    assert "PHRASE COMPLÈTE" in captures["system"]
    assert "circulaires" in captures["system"]
