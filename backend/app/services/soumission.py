"""
Point d'entrée UNIQUE de soumission d'une traduction (principes cibles ⑧ et ⑨).

Pourquoi ce module existe : il y avait deux chemins pour lancer une traduction.
La route `POST /translate` passait par le preflight Ollama ; le planificateur
appelait `demarrer_traduction()` en direct et le contournait (F9). Résultat, le
garde le plus important du système était absent précisément là où personne n'est
devant l'écran pour constater la panne — un lot planifié à 23 h sur un Ollama
figé brûlait 30 minutes de recul puis finissait en `erreur`, la nuit perdue.

Désormais : route HTTP, planificateur et reprise appellent tous
`soumettre_traduction()`, donc tous passent par le même preflight, la même
validation et la même clé d'idempotence.

⚠️ Ne JAMAIS rappeler `demarrer_traduction()` directement depuis une route ou le
planificateur. C'est exactement la dérive que ce module supprime.
"""

import hashlib
import threading

from app.api.erreurs import ollama_indisponible
from app.models.schemas import Langue, StatutJob
from app.services.job_manager import charger_etat

# Jobs déjà soumis, par clé d'idempotence → job_id. Volatile (vidé au
# redémarrage), ce qui est correct : après un redémarrage la file est vide, donc
# aucune soumission n'est « en vol » et rejouer une requête est légitime.
_lock = threading.Lock()
_soumissions: dict[str, str] = {}

# Statuts pour lesquels une re-soumission identique doit être considérée comme un
# doublon plutôt que comme une relance. Un job terminé ou en erreur, lui, DOIT
# pouvoir être relancé — c'est la reprise.
_STATUTS_ACTIFS = (StatutJob.EN_ATTENTE, StatutJob.EN_COURS)


def calculer_cle_idempotence(
    source_path: str,
    langue_source: Langue,
    langue_cible: Langue,
    modele: str,
    chapitres_selectionnes: list[int] | None,
) -> str:
    """
    Clé dérivée de (source, options, portée) — principe cible ⑧.

    Deux soumissions identiques doivent retourner le MÊME job plutôt que d'en
    créer deux sur le même fichier de sortie, le second parti d'un état lu avant
    le premier (F10). La portée est triée : demander les chapitres [3, 1] et
    [1, 3] est la même demande.
    """
    portee = ",".join(str(i) for i in sorted(chapitres_selectionnes or []))
    brut = f"{source_path}|{langue_source.value}|{langue_cible.value}|{modele}|{portee}"
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:32]


def _job_actif_pour(cle: str) -> str | None:
    """job_id déjà soumis pour cette clé, s'il tourne toujours."""
    with _lock:
        job_id = _soumissions.get(cle)
    return job_id


def oublier_soumission(cle: str) -> None:
    with _lock:
        _soumissions.pop(cle, None)


def reinitialiser_soumissions() -> None:
    """Vide le registre d'idempotence — pour les tests."""
    with _lock:
        _soumissions.clear()


def soumettre_traduction(
    source_path: str,
    langue_source: Langue,
    langue_cible: Langue,
    modele: str,
    extracteur: str,
    resume: bool = False,
    estimation_temps_total: float | None = None,
    chapitres_selectionnes: list[int] | None = None,
    verifier_ollama: bool = True,
) -> tuple[str, bool]:
    """
    Soumet une traduction. Retourne `(job_id, deja_soumis)`.

    `deja_soumis=True` signale qu'une soumission identique était déjà en vol et
    qu'aucun nouveau job n'a été créé — l'appelant peut le dire à l'utilisateur
    au lieu de laisser croire à un second lancement.

    `verifier_ollama=False` n'existe que pour les tests : en production le
    preflight est non négociable, c'est tout l'objet de ce module.

    Lève `ErreurMetier` (503, code `ollama_indisponible`) si Ollama ne peut pas
    réellement traduire.
    """
    from app.services.translation_runner import build_output_path, demarrer_traduction

    cle = calculer_cle_idempotence(
        source_path, langue_source, langue_cible, modele, chapitres_selectionnes
    )

    # ── Idempotence (⑧) ──────────────────────────────────────────────────────
    # Une reprise explicite n'est jamais un doublon : l'utilisateur DEMANDE de
    # relancer un job existant.
    if not resume:
        job_id_existant = _job_actif_pour(cle)
        if job_id_existant:
            etat = charger_etat(build_output_path(source_path, modele))
            if etat is not None and etat.statut in _STATUTS_ACTIFS:
                return job_id_existant, True
            # Le job précédent est fini (ou son état a disparu) : la clé est
            # périmée, on repart proprement.
            oublier_soumission(cle)

    # ── Preflight (⑨) ────────────────────────────────────────────────────────
    # Une VRAIE mini-traduction, pas un ping : un llama-server figé répond encore
    # à /api/tags mais ne génère plus rien.
    if verifier_ollama:
        from app.services.translator import verifier_ollama_pret

        pret, message = verifier_ollama_pret(modele)
        if not pret:
            raise ollama_indisponible(message)

    job_id = demarrer_traduction(
        source_path=source_path,
        langue_source=langue_source,
        langue_cible=langue_cible,
        modele=modele,
        extracteur=extracteur,
        resume=resume,
        estimation_temps_total=estimation_temps_total,
        chapitres_selectionnes=chapitres_selectionnes,
    )

    with _lock:
        _soumissions[cle] = job_id
    return job_id, False
