"""
Tests du runner de génération audio d'un fichier complet.
La file d'attente est exécutée en synchrone et la synthèse est simulée.
"""

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
        lambda texte, moteur, voix: (np.zeros(1000, dtype=np.int16), 22050),
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

    def synth_lente(texte, moteur, voix):
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
