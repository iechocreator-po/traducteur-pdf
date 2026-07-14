"""
Tests du service Text-to-Speech local.
La synthèse réelle (Piper/Kokoro) est simulée pour que les tests soient rapides
et n'exigent pas les modèles téléchargés.
"""

import os

import numpy as np
import pytest

from app.services import tts


# ── Nettoyage Markdown ────────────────────────────────────────────────────────

def test_nettoyer_markdown_retire_la_mise_en_forme():
    md = "# Titre\n\nUn **gras** et un [lien](https://x.org).\n\n- puce"
    texte = tts.nettoyer_markdown_pour_lecture(md)
    assert "#" not in texte
    assert "**" not in texte
    assert "https://x.org" not in texte
    assert "lien" in texte          # le libellé du lien est conservé
    assert "Titre" in texte
    assert "puce" in texte


def test_nettoyer_markdown_retire_commentaires_et_code():
    md = "<!-- meta -->\nAvant\n```\ncode ignoré\n```\nAprès"
    texte = tts.nettoyer_markdown_pour_lecture(md)
    assert "meta" not in texte
    assert "code ignoré" not in texte
    assert "Avant" in texte and "Après" in texte


# ── Découverte des moteurs ────────────────────────────────────────────────────

def test_lister_moteurs_piper_indisponible_sans_voix(monkeypatch):
    monkeypatch.setattr(tts, "_piper_importable", lambda: True)
    monkeypatch.setattr(tts, "lister_voix_piper", lambda: [])
    monkeypatch.setattr(tts, "_kokoro_importable", lambda: False)
    piper = next(m for m in tts.lister_moteurs() if m["id"] == "piper")
    assert piper["disponible"] is False
    assert piper["aide"] is not None       # message d'installation fourni


def test_lister_moteurs_piper_disponible_avec_voix(monkeypatch):
    monkeypatch.setattr(tts, "_piper_importable", lambda: True)
    monkeypatch.setattr(tts, "lister_voix_piper", lambda: ["fr_FR-siwis-medium"])
    monkeypatch.setattr(tts, "_kokoro_importable", lambda: False)
    piper = next(m for m in tts.lister_moteurs() if m["id"] == "piper")
    assert piper["disponible"] is True
    assert piper["voix"] == ["fr_FR-siwis-medium"]
    assert piper["aide"] is None


def test_langue_kokoro_depuis_prefixe_voix():
    assert tts._langue_kokoro("ff_siwis") == "fr-fr"
    assert tts._langue_kokoro("af_bella") == "en-us"
    assert tts._langue_kokoro("ef_dora") == "es"


# ── Synthèse ──────────────────────────────────────────────────────────────────

def test_synthetiser_moteur_inconnu_leve_une_erreur():
    import pytest
    with pytest.raises(ValueError, match="Moteur TTS inconnu"):
        tts.synthetiser("bonjour", "festival", "x")


def test_synthetiser_extrait_wav_produit_un_entete_riff(monkeypatch):
    faux_audio = (np.zeros(8000, dtype=np.int16), 22050)
    monkeypatch.setattr(tts, "synthetiser", lambda t, m, v, lang="français": faux_audio)
    wav = tts.synthetiser_extrait_wav("# Bonjour\n\ntexte", "piper", "fr_FR-siwis-medium")
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"


def test_synthetiser_extrait_tronque_les_textes_longs(monkeypatch):
    recu = {}

    def faux_synth(texte, moteur, voix, langue="français"):
        recu["len"] = len(texte)
        return np.zeros(10, dtype=np.int16), 22050

    monkeypatch.setattr(tts, "synthetiser", faux_synth)
    tts.synthetiser_extrait_wav("a" * 5000, "piper", "v")
    assert recu["len"] <= tts.EXTRAIT_LONGUEUR_MAX


# ── Moteur openvoice (voix clonées) ──────────────────────────────────────────

def test_lister_moteurs_inclut_openvoice_non_installe(monkeypatch):
    monkeypatch.setattr(tts, "_piper_importable", lambda: False)
    monkeypatch.setattr(tts, "_kokoro_importable", lambda: False)
    monkeypatch.setattr(tts, "_openvoice_disponible", lambda: False)
    openvoice = next(m for m in tts.lister_moteurs() if m["id"] == "openvoice")
    assert openvoice["disponible"] is False
    assert "non installé" in openvoice["aide"]


def test_lister_moteurs_openvoice_installe_sans_voix(monkeypatch):
    monkeypatch.setattr(tts, "_piper_importable", lambda: False)
    monkeypatch.setattr(tts, "_kokoro_importable", lambda: False)
    monkeypatch.setattr(tts, "_openvoice_disponible", lambda: True)
    monkeypatch.setattr(tts, "lister_voix_openvoice", lambda: [])
    openvoice = next(m for m in tts.lister_moteurs() if m["id"] == "openvoice")
    assert openvoice["disponible"] is False
    assert "Aucune voix clonée" in openvoice["aide"]


def test_lister_moteurs_openvoice_pret_avec_voix(monkeypatch):
    monkeypatch.setattr(tts, "_piper_importable", lambda: False)
    monkeypatch.setattr(tts, "_kokoro_importable", lambda: False)
    monkeypatch.setattr(tts, "_openvoice_disponible", lambda: True)
    monkeypatch.setattr(tts, "lister_voix_openvoice", lambda: ["Ma voix"])
    openvoice = next(m for m in tts.lister_moteurs() if m["id"] == "openvoice")
    assert openvoice["disponible"] is True
    assert openvoice["voix"] == ["Ma voix"]
    assert openvoice["aide"] is None


def test_synthetiser_openvoice_voix_introuvable_leve_une_erreur(monkeypatch):
    from app.services import voix_clonees
    monkeypatch.setattr(voix_clonees, "lister_voix", lambda: [])
    with pytest.raises(ValueError, match="introuvable"):
        tts.synthetiser("bonjour", "openvoice", "Ma voix")


def test_synthetiser_openvoice_appelle_le_sous_processus(monkeypatch, tmp_path):
    import wave

    from app.services import voix_clonees

    monkeypatch.setattr(
        voix_clonees, "lister_voix",
        lambda: [{"nom": "Ma voix", "statut": "termine", "chemin_embedding": "embedding.pth"}],
    )

    chemin_wav_attendu = {}

    def faux_run(cmd, **kwargs):
        chemin_wav_attendu["cmd"] = cmd
        chemin_sortie = cmd[cmd.index("--sortie") + 1]
        chemin_wav_attendu["chemin"] = chemin_sortie
        with wave.open(chemin_sortie, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(np.zeros(100, dtype=np.int16).tobytes())

        class FauxResultat:
            returncode = 0
            stderr = ""
        return FauxResultat()

    monkeypatch.setattr(tts.subprocess, "run", faux_run)
    echantillons, frequence = tts.synthetiser("bonjour", "openvoice", "Ma voix", "anglais")
    assert frequence == 22050
    assert len(echantillons) == 100
    # La langue demandée est transmise au sous-processus de synthèse
    cmd = chemin_wav_attendu["cmd"]
    assert cmd[cmd.index("--langue") + 1] == "anglais"
    assert not os.path.exists(chemin_wav_attendu["chemin"])  # nettoyé après lecture
