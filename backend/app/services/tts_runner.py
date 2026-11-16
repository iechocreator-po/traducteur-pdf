"""
Génération audio d'un fichier Markdown complet, exécutée par le worker unique
de la file d'attente (comme les traductions). Annulation entre les sections.
État persisté dans <sortie>.tts.state.json pour le suivi côté interface.
"""

import datetime
import glob as _glob
from app.services.persistance import ecrire_json_atomique, lire_json_tolerant
import os
import re
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


def _segment_de_nom(texte: str) -> str:
    """
    Rend un libellé sûr comme MORCEAU de nom de fichier.

    Le nom d'une voix clonée est saisi librement dans le Laboratoire : « Papa/Maman »
    produisait un chemin dans un dossier inexistant (erreur 500, aucun audio possible
    avec cette voix) et « ../x » écrivait hors du dossier du document. On ne remplace
    que ce qui est dangereux dans un chemin (séparateurs, deux-points, caractères de
    contrôle) : lettres accentuées, espaces et parenthèses restent tels quels, donc
    les noms des fichiers déjà produits ne changent pas.
    """
    return re.sub(r"[^\w .()-]", "-", texte, flags=re.UNICODE).strip() or "voix"


def chemin_sortie_audio(chemin_md: str, moteur: str, voix: str) -> str:
    base, _ = os.path.splitext(chemin_md)
    return f"{base}_audio_{_segment_de_nom(moteur)}_{_segment_de_nom(voix)}.wav"


def _chemin_partiel(chemin_wav: str) -> str:
    return f"{chemin_wav}.part"


def _retirer_partiel(chemin_wav: str) -> None:
    try:
        os.remove(_chemin_partiel(chemin_wav))
    except FileNotFoundError:
        pass


def _chemin_etat(chemin_wav: str) -> str:
    return f"{chemin_wav}.tts.state.json"


def _sauvegarder_etat(etat: dict) -> None:
    """Écriture atomique — même exposition que la traduction (principe cible ③)."""
    ecrire_json_atomique(_chemin_etat(etat["chemin_sortie"]), etat)


def lire_etat(chemin_md: str) -> dict | None:
    """Retourne l'état du job audio le plus récent pour ce fichier source."""
    base, _ = os.path.splitext(chemin_md)
    candidats = _glob.glob(f"{_glob.escape(base)}_audio_*.wav.tts.state.json")
    plus_recent = None
    for chemin in candidats:
        etat = lire_json_tolerant(chemin)
        if not isinstance(etat, dict):
            continue
        if plus_recent is None or etat.get("demarre_a", 0) > plus_recent.get("demarre_a", 0):
            plus_recent = etat
    return plus_recent


def _executer_generation(etat: dict, sections: list[str]) -> None:
    """
    Exécuté par le worker de la file. Vérifie l'annulation entre chaque section.

    ⚠️ Le WAV s'écrit dans `<sortie>.part` et ne remplace le fichier final qu'une
    fois TOUTES les sections synthétisées. Écrit directement à sa place, il
    écrasait dès la première section l'audio complet d'un passage précédent avec
    la même voix : une annulation ou une erreur laissait alors un fichier partiel
    que l'interface refuse de lire, et l'ancien audio était perdu.
    """
    etat["statut"] = "en_cours"
    _sauvegarder_etat(etat)
    debut = time.time()
    wav = None

    try:
        for i, section in enumerate(sections):
            if est_annule(etat["job_id"]):
                wav = _fermer(wav)
                _retirer_partiel(etat["chemin_sortie"])
                etat["statut"] = "annule"
                _sauvegarder_etat(etat)
                return

            echantillons, frequence = synthetiser(
                section, etat["moteur"], etat["voix"], etat.get("langue", "français")
            )

            if wav is None:
                wav = wave.open(_chemin_partiel(etat["chemin_sortie"]), "wb")
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(frequence)
            wav.writeframes(echantillons.tobytes())

            etat["sections_completees"] = i + 1
            etat["temps_ecoule_secondes"] = time.time() - debut
            _sauvegarder_etat(etat)

        wav = _fermer(wav)   # écrit l'entête définitif avant le remplacement
        if os.path.exists(_chemin_partiel(etat["chemin_sortie"])):
            os.replace(_chemin_partiel(etat["chemin_sortie"]), etat["chemin_sortie"])
        etat["statut"] = "termine"
        etat["termine_a"] = datetime.datetime.now().isoformat()
        _sauvegarder_etat(etat)
    except Exception as e:
        wav = _fermer(wav)
        _retirer_partiel(etat["chemin_sortie"])
        etat["statut"] = "erreur"
        etat["erreur"] = str(e)
        _sauvegarder_etat(etat)
    finally:
        _fermer(wav)
        supprimer_job_registre(etat["job_id"])


def _fermer(wav):
    """Ferme le WAV s'il est ouvert ; retourne toujours None (pour `wav = _fermer(wav)`)."""
    if wav is not None:
        wav.close()
    return None


def demarrer_generation_audio(chemin_md: str, moteur: str, voix: str, langue: str = "français") -> dict:
    """
    Enfile la génération audio d'un fichier Markdown. Retourne l'état initial
    (job_id + chemin de sortie). La file d'attente unique garantit qu'elle ne
    tournera pas en même temps qu'une traduction. `langue` : langue de synthèse
    (moteur openvoice) — devrait suivre la langue du document traduit.
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
        "langue": langue,
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
