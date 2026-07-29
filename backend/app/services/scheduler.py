"""
Planificateur de traductions différées.
Les jobs planifiés sont persistés dans scheduled_jobs.json à côté de ce fichier.
Un thread de surveillance vérifie toutes les 60 secondes et déclenche les jobs échus.
"""

import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.schemas import Langue
from app.services.persistance import ecrire_json_atomique, lire_json_tolerant

_FICHIER_JOBS = os.path.join(os.path.dirname(__file__), "..", "..", "scheduled_jobs.json")
_FICHIER_JOBS = os.path.normpath(_FICHIER_JOBS)

_lock = threading.Lock()
_thread_surveillance: threading.Thread | None = None

# ── Réglages du différé (principe cible ⑪) ───────────────────────────────────
# Au-delà de ce retard, une échéance manquée n'est plus rattrapée : on expire.
# Sinon, rallumer le Mac après trois semaines de vacances lancerait d'un coup
# toutes les planifications oubliées, en silence.
RATTRAPAGE_MAX_HEURES = 24
# Nombre de déclenchements ratés tolérés avant abandon explicite.
MAX_TENTATIVES = 3

# ── Santé de la boucle (principe cible ⑫) ────────────────────────────────────
# Un composant qui travaille quand personne ne regarde doit pouvoir dire qu'il ne
# travaille plus. Sans ça, une boucle morte n'a pour seul signe qu'une ligne sur
# stdout toutes les 60 s (F13).
_sante: dict[str, Any] = {
    "demarre": False,
    "dernier_tick_reussi": None,
    "derniere_erreur": None,
    "derniere_erreur_a": None,
    "ticks_en_erreur_consecutifs": 0,
}


# ── Persistance ───────────────────────────────────────────────────────────────

def _charger() -> list[dict[str, Any]]:
    """
    Charge les planifications. Ne lève JAMAIS (F13) : avant, un fichier tronqué
    faisait lever `GET /scheduled`, `GET /scheduled/tous` ET le tick de
    surveillance. La boucle attrapait, imprimait, et continuait de tourner — donc
    plus aucun job planifié ne se déclenchait, pour toujours, avec pour seul signe
    une ligne sur stdout toutes les 60 s.
    """
    donnees = lire_json_tolerant(_FICHIER_JOBS, defaut=[])
    return donnees if isinstance(donnees, list) else []


def _sauvegarder(jobs: list[dict[str, Any]]) -> None:
    """
    Écriture ATOMIQUE. Ce fichier est le plus exposé du produit : il porte
    TOUTES les planifications, donc une écriture interrompue ne perd pas un job,
    elle les emporte tous d'un coup.
    """
    ecrire_json_atomique(_FICHIER_JOBS, jobs)


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
        # planifie | annule | expire | abandonne
        # `declenche` a disparu (principe cible ⑪) : ce n'était pas un état du
        # travail mais un marqueur laissé par un lancement sans transaction, dont
        # rien ne sortait et que rien ne récupérait au démarrage (F9).
        "statut": "planifie",
        "chapitres_selectionnes": chapitres_selectionnes,
        # Tentatives de déclenchement déjà consommées. Un échec ne tue plus le
        # job : il le laisse `planifie` avec un compteur, jusqu'à la limite.
        "tentatives": 0,
        "derniere_erreur": None,
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


def supprimer_job(job_id: str) -> bool:
    """
    Retire définitivement un job planifié de la liste, QUEL QUE SOIT son statut
    (planifié, déclenché ou annulé). Un job « planifié » supprimé ne se
    déclenchera pas (il n'existe plus). Retourne True si un job a été retiré.
    """
    with _lock:
        jobs = _charger()
        restants = [j for j in jobs if j["id"] != job_id]
        if len(restants) == len(jobs):
            return False
        _sauvegarder(restants)
        return True


# ── Thread de surveillance ────────────────────────────────────────────────────

def _boucle_surveillance() -> None:
    print("[scheduler] thread de surveillance démarré", flush=True)
    while True:
        time.sleep(60)
        _tick()


def _tick() -> None:
    """
    Un passage de la boucle. Isolé de `_boucle_surveillance` pour être testable
    sans attendre 60 s, et pour que la santé soit mise à jour au même endroit
    quel que soit le résultat (principe cible ⑫).
    """
    try:
        _verifier_et_declencher()
    except Exception as e:
        _sante["derniere_erreur"] = str(e)
        _sante["derniere_erreur_a"] = datetime.now(timezone.utc).isoformat()
        _sante["ticks_en_erreur_consecutifs"] += 1
        print(
            f"[scheduler] erreur lors de la vérification des jobs planifiés : {e}",
            flush=True,
        )
    else:
        _sante["dernier_tick_reussi"] = datetime.now(timezone.utc).isoformat()
        _sante["ticks_en_erreur_consecutifs"] = 0


def sante() -> dict[str, Any]:
    """
    État observable du planificateur (principe cible ⑫).

    `en_panne` est la question à laquelle une interface doit pouvoir répondre :
    le thread tourne-t-il encore, et fait-il quelque chose d'utile ? Une échéance
    dépassée alors que la boucle est vivante est une anomalie à afficher, pas une
    liste vide à laisser passer.
    """
    with _lock:
        jobs = _charger()
    en_attente = [j for j in jobs if j.get("statut") == "planifie"]
    prochaine = None
    if en_attente:
        prochaine = min(_heure_planifiee(j["executer_a"]) for j in en_attente).isoformat()

    maintenant = datetime.now(timezone.utc)
    echeances_depassees = sum(
        1 for j in en_attente if _heure_planifiee(j["executer_a"]) <= maintenant
    )

    vivant = _thread_surveillance is not None and _thread_surveillance.is_alive()
    return {
        "thread_vivant": vivant,
        "demarre": _sante["demarre"],
        "dernier_tick_reussi": _sante["dernier_tick_reussi"],
        "derniere_erreur": _sante["derniere_erreur"],
        "derniere_erreur_a": _sante["derniere_erreur_a"],
        "ticks_en_erreur_consecutifs": _sante["ticks_en_erreur_consecutifs"],
        "jobs_en_attente": len(en_attente),
        "prochaine_echeance": prochaine,
        # > 0 avec un thread vivant = quelque chose ne va pas : l'heure est passée
        # et rien n'est parti.
        "echeances_depassees": echeances_depassees,
        "en_panne": (not vivant and _sante["demarre"])
        or _sante["ticks_en_erreur_consecutifs"] >= 3,
    }


def _heure_planifiee(valeur: str) -> datetime:
    """Parse une date ISO 8601, en la rendant aware-UTC si elle est naive."""
    dt = datetime.fromisoformat(valeur)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _marquer(job_id: str, **champs: Any) -> None:
    """Met à jour les champs d'un job planifié, sous verrou, atomiquement."""
    with _lock:
        jobs = _charger()
        for j in jobs:
            if j["id"] == job_id:
                j.update(champs)
                _sauvegarder(jobs)
                return


def _verifier_et_declencher() -> None:
    """
    Un passage : expire les échéances trop vieilles, déclenche les autres.

    Différence majeure avec la version d'avant : on ne marque PLUS `declenche`
    avant de lancer. Ce marquage était un cul-de-sac — on y entrait avant le
    lancement, on n'en sortait qu'en cas de succès, et rien ne le récupérait au
    démarrage. Toute exception y laissait le job mort en silence (F9). Le job
    reste maintenant `planifie` jusqu'à ce que le lancement RÉUSSISSE.
    """
    maintenant = datetime.now(timezone.utc)
    limite_rattrapage = timedelta(hours=RATTRAPAGE_MAX_HEURES)

    with _lock:
        jobs = _charger()
        echus = [
            j for j in jobs
            if j.get("statut") == "planifie"
            and _heure_planifiee(j["executer_a"]) <= maintenant
        ]

    a_declencher = []
    for j in echus:
        retard = maintenant - _heure_planifiee(j["executer_a"])
        if retard > limite_rattrapage:
            # Rattrapage borné (principe cible ⑪) : au-delà, on le DIT au lieu
            # d'exécuter en silence une planification vieille de trois semaines.
            _marquer(
                j["id"],
                statut="expire",
                derniere_erreur=(
                    f"Échéance dépassée de {int(retard.total_seconds() // 3600)} h "
                    f"(limite de rattrapage : {RATTRAPAGE_MAX_HEURES} h)."
                ),
            )
            print(
                f"[scheduler] job {j['id']} expiré — retard supérieur à "
                f"{RATTRAPAGE_MAX_HEURES} h",
                flush=True,
            )
        else:
            a_declencher.append(j)

    for j in a_declencher:
        _lancer_job(j)


def _lancer_job(job: dict[str, Any]) -> None:
    """
    Tente de soumettre la traduction d'un job planifié.

    Passe par `soumettre_traduction` — le point d'entrée UNIQUE (principe cible
    ⑨) — donc par le preflight Ollama, que ce chemin contournait (F9).
    Un échec ne tue plus le job : il incrémente un compteur et le laisse
    `planifie` pour le prochain tick, jusqu'à `MAX_TENTATIVES`, puis `abandonne`
    — un état terminal, mais explicite et visible.
    """
    chapitres = job.get("chapitres_selectionnes")
    chapitres_info = f"chapitres {chapitres}" if chapitres else "document complet"
    print(f"[scheduler] déclenchement du job {job['id']} — {chapitres_info}", flush=True)

    from app.services.soumission import soumettre_traduction

    try:
        soumettre_traduction(
            source_path=job["chemin_pdf"],
            langue_source=Langue(job["langue_source"]),
            langue_cible=Langue(job["langue_cible"]),
            modele=job["modele_ollama"],
            extracteur=job["extracteur_pdf"],
            resume=False,
            chapitres_selectionnes=chapitres,
        )
    except Exception as e:
        tentatives = int(job.get("tentatives", 0)) + 1
        if tentatives >= MAX_TENTATIVES:
            _marquer(
                job["id"],
                statut="abandonne",
                tentatives=tentatives,
                derniere_erreur=str(e),
            )
            print(
                f"[scheduler] job {job['id']} abandonné après {tentatives} tentatives : {e}",
                flush=True,
            )
        else:
            # Reste `planifie` : le prochain tick réessaiera.
            _marquer(job["id"], tentatives=tentatives, derniere_erreur=str(e))
            print(
                f"[scheduler] échec du déclenchement du job {job['id']} "
                f"(tentative {tentatives}/{MAX_TENTATIVES}) : {e}",
                flush=True,
            )
        return

    print(f"[scheduler] job {job['id']} démarré avec succès — {chapitres_info}", flush=True)
    # Le job planifié a fini son rôle : la traduction est maintenant suivie dans
    # « Vos traductions » / la Bibliothèque. On le retire pour ne pas laisser un
    # fantôme dans la liste des planifiées.
    supprimer_job(job["id"])


def demarrer_surveillance() -> None:
    """Appelé au démarrage du backend. Lance le thread de surveillance."""
    global _thread_surveillance
    if _thread_surveillance is not None and _thread_surveillance.is_alive():
        return
    _thread_surveillance = threading.Thread(target=_boucle_surveillance, daemon=True)
    _thread_surveillance.start()
    _sante["demarre"] = True
