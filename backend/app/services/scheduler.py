"""
Planificateur de traductions différées.
Les jobs planifiés sont persistés dans scheduled_jobs.json à côté de ce fichier.
Un thread de surveillance vérifie toutes les 60 secondes et déclenche les jobs échus.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models.schemas import Langue

_FICHIER_JOBS = os.path.join(os.path.dirname(__file__), "..", "..", "scheduled_jobs.json")
_FICHIER_JOBS = os.path.normpath(_FICHIER_JOBS)

_lock = threading.Lock()
_thread_surveillance: threading.Thread | None = None


# ── Persistance ───────────────────────────────────────────────────────────────

def _charger() -> list[dict[str, Any]]:
    if not os.path.exists(_FICHIER_JOBS):
        return []
    with open(_FICHIER_JOBS, "r", encoding="utf-8") as f:
        return json.load(f)


def _sauvegarder(jobs: list[dict[str, Any]]) -> None:
    with open(_FICHIER_JOBS, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)


# ── API publique ──────────────────────────────────────────────────────────────

def planifier_job(
    chemin_source: str,
    langue_source: str,
    langue_cible: str,
    modele_ollama: str,
    extracteur_pdf: str,
    executer_a: datetime,
    chemin_pdf: str | None = None,  # rétrocompat — remplacé par chemin_source
    chapitres_selectionnes: list[int] | None = None,
) -> dict[str, Any]:
    """Crée et persiste un job planifié. Retourne le dict du job."""
    source = chemin_source or chemin_pdf
    job: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "chemin_pdf": source,
        "langue_source": langue_source,
        "langue_cible": langue_cible,
        "modele_ollama": modele_ollama,
        "extracteur_pdf": extracteur_pdf,
        "executer_a": executer_a.isoformat(),
        "cree_a": datetime.now().isoformat(),
        "statut": "planifie",  # planifie | declenche | annule
        "chapitres_selectionnes": chapitres_selectionnes,
    }
    with _lock:
        jobs = _charger()
        jobs.append(job)
        _sauvegarder(jobs)
    return job


def lister_jobs_planifies() -> list[dict[str, Any]]:
    with _lock:
        return [j for j in _charger() if j["statut"] == "planifie"]


def lister_tous_jobs() -> list[dict[str, Any]]:
    """Tous les jobs planifiés, y compris déclenchés et annulés (pour la vue liste)."""
    with _lock:
        return _charger()


def annuler_job(job_id: str) -> bool:
    with _lock:
        jobs = _charger()
        for j in jobs:
            if j["id"] == job_id and j["statut"] == "planifie":
                j["statut"] = "annule"
                _sauvegarder(jobs)
                return True
        return False


# ── Thread de surveillance ────────────────────────────────────────────────────

def _boucle_surveillance() -> None:
    print("[scheduler] thread de surveillance démarré", flush=True)
    while True:
        time.sleep(60)
        try:
            _verifier_et_declencher()
        except Exception as e:
            print(f"[scheduler] erreur lors de la vérification des jobs planifiés : {e}", flush=True)


def _heure_planifiee(valeur: str) -> datetime:
    """Parse une date ISO 8601, en la rendant aware-UTC si elle est naive."""
    dt = datetime.fromisoformat(valeur)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _verifier_et_declencher() -> None:
    maintenant = datetime.now(timezone.utc)
    with _lock:
        jobs = _charger()
        a_declencher = [
            j for j in jobs
            if j["statut"] == "planifie"
            and _heure_planifiee(j["executer_a"]) <= maintenant
        ]
        for j in a_declencher:
            j["statut"] = "declenche"
        if a_declencher:
            _sauvegarder(jobs)

    for j in a_declencher:
        _lancer_job(j)


def _lancer_job(job: dict[str, Any]) -> None:
    chapitres = job.get("chapitres_selectionnes")
    chapitres_info = (
        f"chapitres {chapitres}" if chapitres else "document complet"
    )
    print(f"[scheduler] déclenchement du job {job['id']} — {chapitres_info}", flush=True)
    from app.services.translation_runner import demarrer_traduction
    try:
        demarrer_traduction(
            source_path=job["chemin_pdf"],
            langue_source=Langue(job["langue_source"]),
            langue_cible=Langue(job["langue_cible"]),
            modele=job["modele_ollama"],
            extracteur=job["extracteur_pdf"],
            resume=False,
            chapitres_selectionnes=chapitres,
        )
        print(f"[scheduler] job {job['id']} démarré avec succès — {chapitres_info}", flush=True)
    except Exception as e:
        import sys
        import traceback
        print(f"[scheduler] échec du déclenchement du job {job['id']} : {e}", flush=True)
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()


def demarrer_surveillance() -> None:
    """Appelé au démarrage du backend. Lance le thread de surveillance."""
    global _thread_surveillance
    if _thread_surveillance is not None and _thread_surveillance.is_alive():
        return
    _thread_surveillance = threading.Thread(target=_boucle_surveillance, daemon=True)
    _thread_surveillance.start()
