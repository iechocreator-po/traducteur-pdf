"""
Génération audio d'un fichier Markdown complet, exécutée par le worker unique
de la file d'attente (comme les traductions). Annulation entre les sections.
État persisté dans <sortie>.tts.state.json pour le suivi côté interface.
"""

import datetime
import glob as _glob
import json
import os
import time
import uuid
import wave

from app.services.pdf_extractor import decouper_en_chunks
from app.services.tts import nettoyer_markdown_pour_lecture, synthetiser
from app.services.job_manager import (
    enregistrer_job,
    est_annule,
    soumettre_travail,
    supprimer_job_registre,
)

# Taille des sections envoyées au moteur TTS (petites = progression fine)
TTS_CHUNK_TAILLE_MAX = 1200


def chemin_sortie_audio(chemin_md: str, moteur: str, voix: str) -> str:
    base, _ = os.path.splitext(chemin_md)
    return f"{base}_audio_{moteur}_{voix}.wav"


def _chemin_etat(chemin_wav: str) -> str:
    return f"{chemin_wav}.tts.state.json"


def _sauvegarder_etat(etat: dict) -> None:
    with open(_chemin_etat(etat["chemin_sortie"]), "w", encoding="utf-8") as f:
        json.dump(etat, f, indent=2, ensure_ascii=False)


def lire_etat(chemin_md: str) -> dict | None:
    """Retourne l'état du job audio le plus récent pour ce fichier source."""
    base, _ = os.path.splitext(chemin_md)
    candidats = _glob.glob(f"{_glob.escape(base)}_audio_*.wav.tts.state.json")
    plus_recent = None
    for chemin in candidats:
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                etat = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if plus_recent is None or etat.get("demarre_a", 0) > plus_recent.get("demarre_a", 0):
            plus_recent = etat
    return plus_recent


def _executer_generation(etat: dict, sections: list[str]) -> None:
    """Exécuté par le worker de la file. Vérifie l'annulation entre chaque section."""
    etat["statut"] = "en_cours"
    _sauvegarder_etat(etat)
    debut = time.time()
    wav = None

    try:
        for i, section in enumerate(sections):
            if est_annule(etat["job_id"]):
                etat["statut"] = "annule"
                _sauvegarder_etat(etat)
                return

            echantillons, frequence = synthetiser(section, etat["moteur"], etat["voix"])

            if wav is None:
                wav = wave.open(etat["chemin_sortie"], "wb")
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(frequence)
            wav.writeframes(echantillons.tobytes())

            etat["sections_completees"] = i + 1
            etat["temps_ecoule_secondes"] = time.time() - debut
            _sauvegarder_etat(etat)

        etat["statut"] = "termine"
        etat["termine_a"] = datetime.datetime.now().isoformat()
        _sauvegarder_etat(etat)
    except Exception as e:
        etat["statut"] = "erreur"
        etat["erreur"] = str(e)
        _sauvegarder_etat(etat)
    finally:
        if wav is not None:
            wav.close()
        supprimer_job_registre(etat["job_id"])


def demarrer_generation_audio(chemin_md: str, moteur: str, voix: str) -> dict:
    """
    Enfile la génération audio d'un fichier Markdown. Retourne l'état initial
    (job_id + chemin de sortie). La file d'attente unique garantit qu'elle ne
    tournera pas en même temps qu'une traduction.
    """
    with open(chemin_md, "r", encoding="utf-8") as f:
        texte = nettoyer_markdown_pour_lecture(f.read())

    sections = decouper_en_chunks(texte, taille_max=TTS_CHUNK_TAILLE_MAX)
    if not sections:
        raise ValueError("Le fichier ne contient aucun texte lisible.")

    etat = {
        "job_id": str(uuid.uuid4()),
        "chemin_source": chemin_md,
        "chemin_sortie": chemin_sortie_audio(chemin_md, moteur, voix),
        "moteur": moteur,
        "voix": voix,
        "statut": "en_attente",
        "sections_completees": 0,
        "total_sections": len(sections),
        "temps_ecoule_secondes": 0.0,
        "demarre_a": time.time(),
    }
    _sauvegarder_etat(etat)
    enregistrer_job(etat["job_id"])
    soumettre_travail(etat["job_id"], lambda: _executer_generation(etat, sections))
    return etat
