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


# ── Détection de couche texte corrompue ──────────────────────────────────────

def test_ratio_texte_corrompu():
    from app.agents.analysis_agent import _ratio_texte_corrompu
    assert _ratio_texte_corrompu("Texte parfaitement sain.") == 0.0
    assert _ratio_texte_corrompu("\x01\x01 \x01\x01\n\x01") == 1.0
    assert _ratio_texte_corrompu("") == 0.0
    assert 0.4 < _ratio_texte_corrompu("ab���") < 0.7


def test_analyse_detecte_couche_texte_corrompue(monkeypatch):
    from app.agents import analysis_agent

    monkeypatch.setattr(analysis_agent, "extraire_texte", lambda c: "\x01\x01\x01 \x01\x01 �����\n" * 50)
    monkeypatch.setattr(analysis_agent, "compter_pages", lambda c: 1)

    resultat = analysis_agent.analyser_pdf("/fake/corrompu.pdf")

    assert resultat.texte_extractible is False
    assert any("corrompue" in a for a in resultat.avertissements)
    assert "Tesseract" in resultat.recommandation
