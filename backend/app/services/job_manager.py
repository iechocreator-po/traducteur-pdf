"""
Gestionnaire d'état des jobs de traduction.
Gère la persistance (fichier .state.json), l'état en mémoire (pause/annulation)
et la file d'attente séquentielle : un seul job traduit à la fois pour ne pas
saturer Ollama.
"""

import os
import queue
import threading
from typing import Callable

from app.models.schemas import EtatJob
from app.services import energie
from app.services.persistance import ecrire_texte_atomique, lire_json_tolerant

# Registre en mémoire des jobs actifs — réinitialisé au redémarrage du serveur
_lock = threading.Lock()
_jobs: dict[str, dict] = {}
# {job_id: {"paused": bool, "cancelled": bool, "thread": Thread | None}}


# ── Persistance ──────────────────────────────────────────────────────────────

def chemin_fichier_etat(chemin_sortie: str) -> str:
    base, _ = os.path.splitext(chemin_sortie)
    return f"{base}.state.json"


def chemin_fichier_log(chemin_sortie: str) -> str:
    base, _ = os.path.splitext(chemin_sortie)
    return f"{base}.errors.log"


def sauvegarder_etat(etat: EtatJob) -> None:
    """
    Persiste l'état du job. Écriture ATOMIQUE : appelée ≈1× par sous-morceau (41×
    pour un chapitre illustré, des centaines pour un livre), chaque appel était
    auparavant une fenêtre de corruption qui pouvait faire disparaître TOUS les
    documents des deux frontends (F1).
    """
    ecrire_texte_atomique(
        chemin_fichier_etat(etat.chemin_sortie), etat.model_dump_json(indent=2)
    )


def charger_etat(chemin_sortie: str) -> EtatJob | None:
    """
    Charge l'état d'un job, ou None s'il n'existe pas / n'est plus lisible.

    Ne lève jamais : un seul état corrompu ne doit pas faire tomber la Bibliothèque
    entière (F1). Le fichier illisible est mis en quarantaine par la couche
    persistance, donc l'appel suivant repart proprement.
    """
    data = lire_json_tolerant(chemin_fichier_etat(chemin_sortie))
    if data is None:
        return None
    try:
        return EtatJob(**data)
    except Exception as e:
        # JSON valide mais schéma inattendu (état d'une version future, champ
        # manquant après édition manuelle) : même traitement, on ne fait pas
        # tomber l'appelant.
        print(
            f"[job_manager] état illisible pour {chemin_sortie} : {e}", flush=True
        )
        return None


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
        _jobs[job_id] = {"paused": False, "cancelled": False, "thread": thread}


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


def demander_annulation(job_id: str) -> bool:
    """Demande l'annulation d'un job actif (en cours ou en file d'attente)."""
    with _lock:
        if job_id not in _jobs:
            return False
        _jobs[job_id]["cancelled"] = True
        return True


def est_annule(job_id: str) -> bool:
    with _lock:
        return _jobs.get(job_id, {}).get("cancelled", False)


def supprimer_job_registre(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)


# ── File d'attente séquentielle ──────────────────────────────────────────────
# Un worker unique dépile les travaux un par un : deux traductions simultanées
# satureraient Ollama (un seul modèle chargé, appels séquentiels plus rapides).

_file_travaux: "queue.Queue[tuple[str, Callable[[], None]]]" = queue.Queue()
_thread_worker: threading.Thread | None = None


def _boucle_worker() -> None:
    while True:
        job_id, travail = _file_travaux.get()
        # Assertion d'énergie (principe cible ⑩) : sur une app locale qui traduit
        # des livres pendant des heures, laisser le Mac s'endormir en plein job
        # est un défaut fonctionnel, pas un détail. Prise à l'entrée, relâchée
        # quand la file se vide — jamais autour d'un seul travail, sinon on la
        # reprend et la relâche à chaque élément de la file.
        energie.acquerir()
        try:
            travail()
        except Exception as e:
            print(f"[job_manager] erreur non gérée du job {job_id} : {e}", flush=True)
        finally:
            _file_travaux.task_done()
            if _file_travaux.empty():
                energie.relacher()


def soumettre_travail(job_id: str, travail: Callable[[], None]) -> None:
    """Ajoute un travail à la file. Démarre le worker au premier appel."""
    global _thread_worker
    with _lock:
        if _thread_worker is None or not _thread_worker.is_alive():
            _thread_worker = threading.Thread(target=_boucle_worker, daemon=True)
            _thread_worker.start()
    _file_travaux.put((job_id, travail))


def taille_file_attente() -> int:
    """Nombre de travaux en attente (sans compter celui en cours)."""
    return _file_travaux.qsize()
