"""
Tests du service de traduction.
On simule (mock) les appels réseau à Ollama pour que les tests soient
rapides et ne dépendent pas d'un serveur Ollama réellement lancé.
"""

from unittest.mock import MagicMock, patch

from app.services.translator import lister_modeles_disponibles, traduire_texte


@patch("app.services.translator.requests.post")
def test_traduire_texte_retourne_la_reponse_du_modele(mock_post):
    mock_reponse = MagicMock()
    mock_reponse.json.return_value = {"response": "Bonjour le monde"}
    mock_post.return_value = mock_reponse

    resultat = traduire_texte("Hello world", modele="llama3.1", langue_source="anglais", langue_cible="français")

    assert resultat == "Bonjour le monde"
    mock_post.assert_called_once()


@patch("app.services.translator.requests.get")
def test_lister_modeles_disponibles_retourne_les_noms(mock_get):
    mock_reponse = MagicMock()
    mock_reponse.json.return_value = {"models": [{"name": "llama3.1"}, {"name": "mistral"}]}
    mock_get.return_value = mock_reponse

    modeles = lister_modeles_disponibles()

    assert modeles == ["llama3.1", "mistral"]


@patch("app.services.translator.requests.get", side_effect=Exception("Connexion refusée"))
def test_lister_modeles_disponibles_retourne_liste_vide_si_ollama_inaccessible(mock_get):
    modeles = lister_modeles_disponibles()
    assert modeles == []
