"""
Tests du service de traduction.
On simule (mock) les appels réseau à Ollama pour que les tests soient
rapides et ne dépendent pas d'un serveur Ollama réellement lancé.
"""

import pytest
import requests
from unittest.mock import MagicMock, patch

from app.services import translator
from app.services.translator import (
    AppelInterrompu,
    OllamaErreurApplicative,
    OllamaIndisponible,
    appeler_ollama,
    lister_modeles_disponibles,
    traduire_texte,
)


@pytest.fixture
def horloge(monkeypatch):
    """
    Horloge factice : time.sleep n'attend pas, il avance le temps. Sans ça, un
    test du budget de 30 min durerait 30 min — ou serait non déterministe.
    Retourne la liste des délais demandés, pour asserter le backoff.
    """
    faux_temps = [0.0]
    delais = []

    def faux_sleep(d):
        faux_temps[0] += d

    def faux_monotonic():
        return faux_temps[0]

    monkeypatch.setattr(translator.time, "sleep", faux_sleep)
    monkeypatch.setattr(translator.time, "monotonic", faux_monotonic)

    vrai_attendre = translator._attendre

    def espion_attendre(delai, interruption):
        delais.append(delai)
        return vrai_attendre(delai, interruption)

    monkeypatch.setattr(translator, "_attendre", espion_attendre)
    return delais


def _reponse(payload: dict) -> MagicMock:
    rep = MagicMock()
    rep.json.return_value = payload
    return rep


def _erreur_http(code: int) -> requests.HTTPError:
    rep = MagicMock()
    rep.status_code = code
    return requests.HTTPError(f"HTTP {code}", response=rep)


def test_retry_reseau_puis_succes(horloge):
    """Deux pannes réseau transitoires, puis Ollama revient : la section passe."""
    with patch("app.services.translator.requests.post") as post:
        post.side_effect = [
            requests.ConnectionError("connection refused"),
            requests.ConnectionError("connection refused"),
            _reponse({"response": "ok"}),
        ]
        assert appeler_ollama({"model": "llama3.1"}) == {"response": "ok"}
        assert post.call_count == 3
    # Backoff exponentiel : ~2 s puis ~4 s (jitter ±20 %).
    assert len(horloge) == 2
    assert 1.6 <= horloge[0] <= 2.4
    assert 3.2 <= horloge[1] <= 4.8


def test_panne_permanente_epuise_le_budget_et_abandonne(horloge, monkeypatch):
    """Budget épuisé → OllamaIndisponible, qui est fatale pour le job."""
    monkeypatch.setattr(translator, "OLLAMA_RETRY_BUDGET_SECONDES", 30)
    with patch("app.services.translator.requests.post") as post:
        post.side_effect = requests.ConnectionError("connection refused")
        with pytest.raises(OllamaIndisponible):
            appeler_ollama({"model": "llama3.1"})
    # Le budget borne les tentatives, et il est respecté.
    assert sum(horloge) <= 30


def test_erreur_4xx_abandonne_immediatement_sans_attendre(horloge):
    """Un modèle inconnu ne se répare pas en attendant : 0 retry, 0 sleep."""
    with patch("app.services.translator.requests.post") as post:
        rep = MagicMock()
        rep.raise_for_status.side_effect = _erreur_http(404)
        post.return_value = rep
        with pytest.raises(OllamaErreurApplicative):
            appeler_ollama({"model": "inexistant"})
        assert post.call_count == 1
    assert horloge == []


def test_erreur_5xx_est_reessayee(horloge):
    """Ollama vivant mais en vrac (chargement de modèle, OOM) → transitoire."""
    with patch("app.services.translator.requests.post") as post:
        rep_ko = MagicMock()
        rep_ko.raise_for_status.side_effect = _erreur_http(503)
        post.side_effect = [rep_ko, _reponse({"response": "ok"})]
        assert appeler_ollama({"model": "llama3.1"}) == {"response": "ok"}
        assert post.call_count == 2


def test_backoff_plafonne(horloge, monkeypatch):
    """Le délai ne croît pas indéfiniment : plafond à OLLAMA_RETRY_DELAI_MAX."""
    monkeypatch.setattr(translator, "OLLAMA_RETRY_BUDGET_SECONDES", 600)
    with patch("app.services.translator.requests.post") as post:
        post.side_effect = requests.ConnectionError("connection refused")
        with pytest.raises(OllamaIndisponible):
            appeler_ollama({"model": "llama3.1"})
    plafond = translator.OLLAMA_RETRY_DELAI_MAX * (1 + translator.OLLAMA_RETRY_JITTER)
    assert max(horloge) <= plafond


def test_interruption_sort_du_backoff_sans_epuiser_le_budget(horloge):
    """Pause/Annuler doivent rester vivants pendant l'attente entre 2 tentatives."""
    with patch("app.services.translator.requests.post") as post:
        post.side_effect = requests.ConnectionError("connection refused")
        with pytest.raises(AppelInterrompu):
            appeler_ollama({"model": "llama3.1"}, interruption=lambda: True)
        # L'interruption est vue avant même la première tentative.
        assert post.call_count == 0


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
