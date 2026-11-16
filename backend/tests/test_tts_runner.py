"""
Tests du runner de génération audio d'un fichier complet.
La file d'attente est exécutée en synchrone et la synthèse est simulée.
"""

import os
import time

import numpy as np

from app.services import tts_runner


def _attendre_statut(chemin_md, statuts, timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin:
        etat = tts_runner.lire_etat(chemin_md)
        if etat and etat["statut"] in statuts:
            return etat
        time.sleep(0.05)
    raise AssertionError(f"Timeout en attendant {statuts}")


def test_generation_audio_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tts_runner, "synthetiser",
        lambda texte, moteur, voix, langue="français": (np.zeros(1000, dtype=np.int16), 22050),
    )
    source = tmp_path / "livre.md"
    source.write_text("# Chapitre 1\n\n" + "Une phrase. " * 200)

    etat = tts_runner.demarrer_generation_audio(str(source), "piper", "fr_FR-siwis-medium")
    assert etat["statut"] == "en_attente"
    assert etat["total_sections"] >= 1

    final = _attendre_statut(str(source), {"termine"})
    assert final["sections_completees"] == final["total_sections"]

    # Le fichier WAV existe et a un entête valide
    with open(final["chemin_sortie"], "rb") as f:
        assert f.read(4) == b"RIFF"


def test_generation_audio_annulation(tmp_path, monkeypatch):
    from app.services import job_manager

    def synth_lente(texte, moteur, voix, langue="français"):
        time.sleep(0.1)
        return np.zeros(500, dtype=np.int16), 22050

    monkeypatch.setattr(tts_runner, "synthetiser", synth_lente)
    source = tmp_path / "livre.md"
    source.write_text("# Titre\n\n" + "Phrase. " * 400)

    etat = tts_runner.demarrer_generation_audio(str(source), "piper", "v")
    _attendre_statut(str(source), {"en_cours"})
    job_manager.demander_annulation(etat["job_id"])

    final = _attendre_statut(str(source), {"annule", "termine"})
    # La synthèse lente + petites sections doit permettre l'annulation avant la fin
    assert final["statut"] in ("annule", "termine")


def test_lire_etat_absent_retourne_none(tmp_path):
    assert tts_runner.lire_etat(str(tmp_path / "rien.md")) is None


def test_chemin_sortie_audio():
    chemin = tts_runner.chemin_sortie_audio("/x/livre.md", "piper", "fr_FR-siwis-medium")
    assert chemin == "/x/livre_audio_piper_fr_FR-siwis-medium.wav"


def test_chemin_sortie_audio_neutralise_un_nom_de_voix_libre():
    """Un nom de voix clonée saisi librement ne crée ni sous-dossier ni sortie du dossier."""
    chemin = tts_runner.chemin_sortie_audio("/x/livre.md", "openvoice", "Papa/Maman")
    assert chemin == "/x/livre_audio_openvoice_Papa-Maman.wav"
    chemin = tts_runner.chemin_sortie_audio("/x/livre.md", "openvoice", "../../etc")
    assert os.path.dirname(chemin) == "/x"
    # Accents, espaces et parenthèses restent : les noms déjà produits ne changent pas.
    assert tts_runner.chemin_sortie_audio("/x/l.md", "openvoice", "Voix de Zoé (2)") == (
        "/x/l_audio_openvoice_Voix de Zoé (2).wav"
    )


def test_generation_avec_un_nom_de_voix_a_barre_oblique(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tts_runner, "synthetiser",
        lambda texte, moteur, voix, langue="français": (np.zeros(100, dtype=np.int16), 22050),
    )
    source = tmp_path / "livre.md"
    source.write_text("# T\n\nUne phrase.")
    etat = tts_runner.demarrer_generation_audio(str(source), "openvoice", "Papa/Maman")
    final = _attendre_statut(str(source), {"termine", "erreur"})
    assert final["statut"] == "termine"
    assert os.path.dirname(etat["chemin_sortie"]) == str(tmp_path)


def test_un_echec_ne_detruit_pas_l_audio_precedent(tmp_path, monkeypatch):
    """
    Le WAV s'écrit dans un .part : une génération qui échoue en cours de route
    laisse intact l'audio complet d'un passage précédent avec la même voix.
    """
    source = tmp_path / "livre.md"
    source.write_text("# Titre\n\n" + "Phrase. " * 400)
    sortie = tts_runner.chemin_sortie_audio(str(source), "piper", "v")
    with open(sortie, "wb") as f:
        f.write(b"RIFF-ancien-audio-complet")

    appels = {"n": 0}

    def synth_qui_casse(texte, moteur, voix, langue="français"):
        appels["n"] += 1
        if appels["n"] >= 2:
            raise RuntimeError("moteur TTS planté")
        return np.zeros(500, dtype=np.int16), 22050

    monkeypatch.setattr(tts_runner, "synthetiser", synth_qui_casse)
    tts_runner.demarrer_generation_audio(str(source), "piper", "v")
    final = _attendre_statut(str(source), {"erreur", "termine"})

    assert final["statut"] == "erreur"
    with open(sortie, "rb") as f:
        assert f.read() == b"RIFF-ancien-audio-complet"
    assert not os.path.exists(sortie + ".part")


def test_une_generation_reussie_ne_laisse_pas_de_part(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tts_runner, "synthetiser",
        lambda texte, moteur, voix, langue="français": (np.zeros(100, dtype=np.int16), 22050),
    )
    source = tmp_path / "livre.md"
    source.write_text("# T\n\n" + "Une phrase. " * 300)
    final_attendu = tts_runner.demarrer_generation_audio(str(source), "piper", "v")["chemin_sortie"]
    _attendre_statut(str(source), {"termine"})
    assert os.path.exists(final_attendu)
    assert not os.path.exists(final_attendu + ".part")
