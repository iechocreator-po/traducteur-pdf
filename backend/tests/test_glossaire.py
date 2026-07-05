"""
Tests du glossaire de termes à ne pas traduire :
persistance, nettoyage, détection dans un texte, vérification post-traduction,
et injection dans le prompt système envoyé à Ollama.
"""

from unittest.mock import MagicMock, patch

from app.services import glossaire
from app.services.translator import traduire_texte


def _rediriger_fichier(tmp_path, monkeypatch):
    monkeypatch.setattr(glossaire, "_FICHIER_GLOSSAIRE", str(tmp_path / "glossaire.json"))


def test_charger_termes_retourne_vide_sans_fichier(tmp_path, monkeypatch):
    _rediriger_fichier(tmp_path, monkeypatch)
    assert glossaire.charger_termes() == []


def test_sauvegarder_puis_charger_termes(tmp_path, monkeypatch):
    _rediriger_fichier(tmp_path, monkeypatch)
    glossaire.sauvegarder_termes(["Quatre-Chemins.org", "FastAPI"])
    assert glossaire.charger_termes() == ["Quatre-Chemins.org", "FastAPI"]


def test_sauvegarder_nettoie_vides_espaces_et_doublons(tmp_path, monkeypatch):
    _rediriger_fichier(tmp_path, monkeypatch)
    resultat = glossaire.sauvegarder_termes(["  FastAPI  ", "", "fastapi", "Ollama", "   "])
    assert resultat == ["FastAPI", "Ollama"]


def test_termes_presents_insensible_a_la_casse():
    termes = ["Quatre-Chemins.org", "FastAPI", "Ollama"]
    texte = "Le site quatre-chemins.org utilise FastAPI."
    assert glossaire.termes_presents(texte, termes) == ["Quatre-Chemins.org", "FastAPI"]


def test_termes_perdus_detecte_les_termes_traduits():
    termes = ["Quatre-Chemins.org", "FastAPI"]
    traduit = "Le site Quatre-Chemins.org utilise une API rapide."
    assert glossaire.termes_perdus(termes, traduit) == ["FastAPI"]


def test_termes_perdus_tolere_le_changement_de_casse():
    assert glossaire.termes_perdus(["FastAPI"], "on parle de fastapi ici") == []


@patch("app.services.translator.requests.post")
def test_traduire_texte_injecte_les_termes_dans_le_prompt(mock_post):
    mock_reponse = MagicMock()
    mock_reponse.json.return_value = {"response": "Bonjour FastAPI"}
    mock_post.return_value = mock_reponse

    traduire_texte(
        "Hello FastAPI", modele="llama3.1", langue_source="anglais",
        langue_cible="français", termes_a_conserver=["FastAPI"],
    )

    corps = mock_post.call_args.kwargs["json"]
    assert "FastAPI" in corps["system"]
    assert "Ne traduis JAMAIS ces termes" in corps["system"]


@patch("app.services.translator.requests.post")
def test_traduire_texte_sans_glossaire_ne_change_pas_le_prompt(mock_post):
    mock_reponse = MagicMock()
    mock_reponse.json.return_value = {"response": "Bonjour"}
    mock_post.return_value = mock_reponse

    traduire_texte("Hello", modele="llama3.1", langue_source="anglais", langue_cible="français")

    corps = mock_post.call_args.kwargs["json"]
    assert "Ne traduis JAMAIS ces termes" not in corps["system"]
