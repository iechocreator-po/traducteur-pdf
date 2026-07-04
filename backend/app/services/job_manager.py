"""
Gestionnaire d'état des jobs de traduction.
Gère la persistance (fichier .state.json) et l'état en mémoire (pause/arrêt).
"""

import json
import os
import threading

from app.models.schemas import EtatJob

# Registre en mémoire des jobs actifs — réinitialisé au redémarrage du serveur
_lock = threading.Lock()
_jobs: dict[str, dict] = {}
# {job_id: {"paused": bool, "thread": Thread | None}}


# ── Persistance ──────────────────────────────────────────────────────────────

def chemin_fichier_etat(chemin_sortie: str) -> str:
    base, _ = os.path.splitext(chemin_sortie)
    return f"{base}.state.json"


def chemin_fichier_log(chemin_sortie: str) -> str:
    base, _ = os.path.splitext(chemin_sortie)
    return f"{base}.errors.log"


def sauvegarder_etat(etat: EtatJob) -> None:
    chemin = chemin_fichier_etat(etat.chemin_sortie)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(etat.model_dump_json(indent=2))


def charger_etat(chemin_sortie: str) -> EtatJob | None:
    chemin = chemin_fichier_etat(chemin_sortie)
    if not os.path.exists(chemin):
        return None
    with open(chemin, "r", encoding="utf-8") as f:
        data = json.load(f)
    return EtatJob(**data)


def supprimer_etat(chemin_sortie: str) -> None:
    chemin = chemin_fichier_etat(chemin_sortie)
    if os.path.exists(chemin):
        os.remove(chemin)


def journaliser_erreur(chemin_sortie: str, message: str) -> None:
    """Écrit une erreur dans le fichier .errors.log à côté du fichier traduit."""
    import datetime
    chemin = chemin_fichier_log(chemin_sortie)
    horodatage = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(f"[{horodatage}] {message}\n")


# ── Registre en mémoire ──────────────────────────────────────────────────────

def enregistrer_job(job_id: str, thread: threading.Thread | None = None) -> None:
    with _lock:
        _jobs[job_id] = {"paused": False, "thread": thread}


def mettre_en_pause(job_id: str) -> bool:
    with _lock:
        if job_id not in _jobs:
            return False
        _jobs[job_id]["paused"] = True
        return True


def est_en_pause(job_id: str) -> bool:
    with _lock:
        return _jobs.get(job_id, {}).get("paused", False)


def lever_pause(job_id: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["paused"] = False


def enregistrer_thread(job_id: str, thread: threading.Thread) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["thread"] = thread


def supprimer_job_registre(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)
