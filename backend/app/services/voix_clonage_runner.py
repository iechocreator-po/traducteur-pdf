"""
Traitement d'un échantillon vocal capturé au micro : extraction de l'embedding
de locuteur OpenVoice, exécutée dans le venv Python dédié (venv_openvoice/,
voir tts_modeles/openvoice/README.md) via sous-processus — le venv backend
principal reste en Python 3.13, incompatible avec les dépendances OpenVoice.

Suit le même patron de job asynchrone que tts_runner.py : enfilé dans la file
unique du job_manager, statut persisté dans le registre (voix_clonees.py).
"""

import os
import subprocess

from app.services import voix_clonees
from app.services.job_manager import enregistrer_job, soumettre_travail, supprimer_job_registre

CHEMIN_VENV_PYTHON = os.path.join(voix_clonees.DOSSIER_OPENVOICE, "venv_openvoice", "bin", "python")
CHEMIN_SCRIPT_EXTRACTION = os.path.join(os.path.dirname(__file__), "openvoice_extract.py")
DOSSIER_CHECKPOINTS = os.path.join(voix_clonees.DOSSIER_OPENVOICE, "checkpoints")

TIMEOUT_EXTRACTION_SECONDES = 600


def venv_openvoice_disponible() -> bool:
    return os.path.exists(CHEMIN_VENV_PYTHON)


def _executer_extraction(id_voix: str) -> None:
    voix_clonees.mettre_a_jour_voix(id_voix, statut="en_cours")
    try:
        resultat = subprocess.run(
            [
                CHEMIN_VENV_PYTHON, CHEMIN_SCRIPT_EXTRACTION,
                "--audio", voix_clonees.chemin_echantillon(id_voix),
                "--sortie", voix_clonees.chemin_embedding(id_voix),
                "--checkpoints", DOSSIER_CHECKPOINTS,
            ],
            capture_output=True, text=True, timeout=TIMEOUT_EXTRACTION_SECONDES,
        )
        if resultat.returncode != 0:
            raise RuntimeError(
                resultat.stderr.strip()[-2000:] or "Échec de l'extraction de l'embedding."
            )
        voix_clonees.mettre_a_jour_voix(
            id_voix,
            statut="termine",
            chemin_embedding=voix_clonees.chemin_embedding(id_voix),
        )
    except Exception as e:
        voix_clonees.mettre_a_jour_voix(id_voix, statut="erreur", erreur=str(e))


def demarrer_traitement(id_voix: str) -> None:
    """
    Enfile l'extraction de l'embedding pour une voix nouvellement capturée
    (l'échantillon doit déjà être écrit sur disque, voir voix_clonees.py).
    """
    if not venv_openvoice_disponible():
        voix_clonees.mettre_a_jour_voix(
            id_voix, statut="erreur",
            erreur=(
                "Moteur OpenVoice non installé — voir "
                "backend/tts_modeles/openvoice/README.md"
            ),
        )
        return

    job_id = f"voix-{id_voix}"
    enregistrer_job(job_id)

    def travail() -> None:
        try:
            _executer_extraction(id_voix)
        finally:
            supprimer_job_registre(job_id)

    soumettre_travail(job_id, travail)


def lire_statut(id_voix: str) -> dict | None:
    return voix_clonees.obtenir_voix(id_voix)
