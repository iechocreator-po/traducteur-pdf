"""Tests pour la classe de base partagée par les agents IA."""

from app.agents.base_agent import ResultatAgent, executer_agent_en_securite


def test_resultat_agent_ok_contient_les_donnees():
    resultat = ResultatAgent.ok({"cle": "valeur"})
    assert resultat.succes is True
    assert resultat.donnees == {"cle": "valeur"}
    assert resultat.erreur is None


def test_resultat_agent_echec_contient_le_message_erreur():
    resultat = ResultatAgent.echec("quelque chose a échoué")
    assert resultat.succes is False
    assert resultat.donnees is None
    assert resultat.erreur == "quelque chose a échoué"


def test_executer_agent_en_securite_retourne_ok_si_pas_d_erreur():
    resultat = executer_agent_en_securite(lambda x: x * 2, 21)
    assert resultat.succes is True
    assert resultat.donnees == 42


def test_executer_agent_en_securite_capture_les_exceptions():
    def fonction_qui_echoue():
        raise ConnectionError("Ollama inaccessible")

    resultat = executer_agent_en_securite(fonction_qui_echoue)
    assert resultat.succes is False
    assert "Ollama inaccessible" in resultat.erreur
