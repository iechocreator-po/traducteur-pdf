"""
Récupération des jobs interrompus au démarrage — TOUTES les familles.

Le défaut corrigé (F7 de l'audit du 27/7) : `recuperer_jobs_interrompus()` ne
connaissait que la traduction. Un job d'étude coupé par un redémarrage restait
`en_cours` **pour toujours**, et les conséquences étaient définitives côté
interface : `pollStatutFiche` ne s'arrête que sur `termine`/`erreur`/`annule`, et
`majBoutonGenerer` désactive « Générer » tant que le poll tourne. Le panneau
Résumé & Quiz de ce document était donc bloqué à jamais, y compris après
rechargement de la page (`chargerFicheExistante` relance le poll). La seule
sortie était de supprimer le `_fiche_*.state.json` à la main. Le TTS avait la
même absence de récupération.

Le raisonnement est identique pour les trois familles : au redémarrage, le
registre mémoire des jobs est vide. Un état persisté qui dit `en_cours` ou
`en_attente` décrit donc forcément un job qu'un arrêt a coupé — il n'y a plus
personne pour le faire avancer.

Ce module est l'étape intermédiaire vers le principe cible ① (un seul modèle de
job) : la logique de balayage est factorisée ici, mais chaque famille garde
encore sa persistance. Quand ① sera fait, ce module disparaîtra au profit d'une
seule requête sur la table des jobs.
"""

import glob as _glob
import os

from app.models.schemas import StatutJob

# Statuts qui, au démarrage, ne peuvent décrire qu'un job interrompu.
_STATUTS_INTERROMPUS = (StatutJob.EN_COURS.value, StatutJob.EN_ATTENTE.value)


def recuperer_jobs_etude() -> int:
    """
    Bascule les jobs de fiche d'étude restés `en_cours`/`en_attente` en `erreur`.

    Pourquoi `erreur` et non `en_pause` comme la traduction : l'étude n'a pas de
    reprise à mi-parcours équivalente, et surtout c'est un statut TERMINAL que
    `pollStatutFiche` sait reconnaître — donc le poll s'arrête et le bouton
    « Générer » redevient cliquable. Basculer en `en_pause` laisserait le panneau
    aussi bloqué qu'avant.
    """
    from app.services.study_runner import _charger_etat, _sauvegarder_etat

    recuperes = 0
    for chemin in _etats_sur_disque("*_fiche*.state.json"):
        etat = _charger_etat(chemin)
        if etat is None or etat.statut.value not in _STATUTS_INTERROMPUS:
            continue
        etat.statut = StatutJob.ERREUR
        etat.erreurs.append(
            "Interrompu par un arrêt du serveur — relancez la génération."
        )
        _sauvegarder_etat(etat)
        recuperes += 1
    return recuperes


def recuperer_jobs_tts() -> int:
    """
    Même traitement pour l'audio. Le TTS n'a ni pause, ni reprise, ni cache :
    relancer repart de zéro, donc le seul service à rendre est de débloquer
    l'interface et de le DIRE, au lieu de laisser un `en_cours` éternel.
    """
    from app.services.persistance import ecrire_json_atomique, lire_json_tolerant

    recuperes = 0
    for chemin in _etats_sur_disque("*_audio_*.wav.tts.state.json"):
        etat = lire_json_tolerant(chemin)
        if not isinstance(etat, dict) or etat.get("statut") not in _STATUTS_INTERROMPUS:
            continue
        etat["statut"] = "erreur"
        etat["erreur"] = "Interrompu par un arrêt du serveur — relancez la génération."
        ecrire_json_atomique(chemin, etat)
        recuperes += 1
    return recuperes


def recuperer_voix_en_cours() -> int:
    """
    Le clonage vocal : une voix restée `en_traitement` au démarrage ne sera
    jamais terminée (le sous-processus est mort avec le serveur). On la marque
    `erreur` pour qu'elle cesse d'apparaître comme « en cours » indéfiniment
    dans le Laboratoire.
    """
    from app.services.voix_clonees import lister_voix, mettre_a_jour_voix

    recuperes = 0
    for voix in lister_voix():
        if voix.get("statut") != "en_traitement":
            continue
        mettre_a_jour_voix(
            voix["id"],
            statut="erreur",
            erreur="Interrompu par un arrêt du serveur — relancez la capture.",
        )
        recuperes += 1
    return recuperes


def _etats_sur_disque(motif: str) -> list[str]:
    """
    Cherche les fichiers d'état des jobs non-traduction.

    Ces familles écrivent leur état À CÔTÉ du document source, dont le chemin
    n'est connu que par le registre de la Bibliothèque — il n'existe pas de
    dossier central à balayer. On passe donc par les répertoires connus de la
    Bibliothèque, ce qui couvre tout ce que l'utilisateur peut voir dans
    l'interface.
    """
    from app.services.bibliotheque import lister_documents

    try:
        documents = lister_documents()
    except Exception:
        return []

    repertoires = set()
    for doc in documents:
        for cle in ("chemin_sortie", "chemin_source"):
            chemin = doc.get(cle)
            if chemin:
                repertoires.add(os.path.dirname(chemin))

    trouves: list[str] = []
    for repertoire in repertoires:
        if repertoire and os.path.isdir(repertoire):
            trouves.extend(_glob.glob(os.path.join(repertoire, motif)))
    return trouves


def recuperer_tout() -> dict[str, int]:
    """
    Appelée au démarrage du backend (main.py). Chaque famille est isolée : une
    exception dans l'une ne doit pas empêcher les autres d'être récupérées.
    """
    from app.services.translation_runner import recuperer_jobs_interrompus

    resultats = {}
    for nom, fonction in (
        ("traduction", recuperer_jobs_interrompus),
        ("etude", recuperer_jobs_etude),
        ("tts", recuperer_jobs_tts),
        ("clonage", recuperer_voix_en_cours),
    ):
        try:
            resultats[nom] = fonction()
        except Exception as e:
            print(f"[recuperation] {nom} : {e}", flush=True)
            resultats[nom] = 0

    total = sum(resultats.values())
    if total:
        print(f"[recuperation] jobs interrompus récupérés : {resultats}", flush=True)
    return resultats
