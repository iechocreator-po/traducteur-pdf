"""
Registre des documents traduits (la « Bibliothèque » de la refonte Workflow).
Persisté dans bibliotheque.json à la racine du backend, alimenté automatiquement
au lancement de chaque traduction. Le statut et la progression sont lus depuis
les fichiers .state.json des jobs (source de vérité), jamais dupliqués ici.
"""

import datetime
import os
import threading

from app.services.job_manager import charger_etat
from app.services.persistance import ecrire_json_atomique, lire_json_tolerant

_FICHIER_BIBLIO = os.path.join(os.path.dirname(__file__), "..", "..", "bibliotheque.json")
_FICHIER_BIBLIO = os.path.normpath(_FICHIER_BIBLIO)

_lock = threading.Lock()


def _charger() -> list[dict]:
    donnees = lire_json_tolerant(_FICHIER_BIBLIO, defaut={})
    if not isinstance(donnees, dict):
        return []
    documents = donnees.get("documents", [])
    return documents if isinstance(documents, list) else []


def _sauvegarder(documents: list[dict]) -> None:
    ecrire_json_atomique(_FICHIER_BIBLIO, {"documents": documents})


def enregistrer_document(
    chemin_source: str,
    chemin_sortie: str,
    modele: str,
    langue_source: str,
    langue_cible: str,
) -> None:
    """
    Ajoute (ou met à jour) un document du registre, identifié par son fichier
    de sortie. Appelé au lancement de chaque traduction.
    """
    maintenant = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        documents = _charger()
        existant = next((d for d in documents if d["chemin_sortie"] == chemin_sortie), None)
        if existant:
            existant.update(
                chemin_source=chemin_source,
                modele=modele,
                langue_source=langue_source,
                langue_cible=langue_cible,
                maj_a=maintenant,
            )
        else:
            documents.append({
                "chemin_source": chemin_source,
                "chemin_sortie": chemin_sortie,
                "nom": os.path.basename(chemin_source),
                "modele": modele,
                "langue_source": langue_source,
                "langue_cible": langue_cible,
                "cree_a": maintenant,
                "maj_a": maintenant,
            })
        _sauvegarder(documents)


# Champs d'annotation acceptés. Liste FERMÉE volontairement : une annotation
# libre laisserait n'importe quel appelant écrire dans le registre, qui est la
# porte d'entrée vers tout le travail de l'utilisateur.
_CHAMPS_ANNOTABLES = {"qualite"}


def annoter_document(chemin_sortie: str, **champs) -> bool:
    """
    Attache des métadonnées d'affichage à une entrée existante du registre.

    Sert au report de la « Qualité » (feature 320) : elle est calculée par
    `POST /analyser`, donc connue du frontend au moment du lancement, mais
    n'était mémorisée nulle part — elle disparaissait dès que le document
    quittait le lot en mémoire pour rejoindre « Vos traductions ».

    Volontairement séparé de `enregistrer_document` : celui-ci décrit ce que le
    moteur produit, celui-là ce que l'interface a observé. Retourne False si le
    document n'est pas (ou pas encore) dans le registre.
    """
    retenus = {k: v for k, v in champs.items() if k in _CHAMPS_ANNOTABLES and v is not None}
    if not retenus:
        return False
    with _lock:
        documents = _charger()
        entree = next((d for d in documents if d.get("chemin_sortie") == chemin_sortie), None)
        if entree is None:
            return False
        entree.update(retenus)
        _sauvegarder(documents)
        return True


def retirer_document(chemin_sortie: str) -> bool:
    """
    Retire une entrée du registre, identifiée par son fichier de sortie.
    NE TOUCHE PAS aux fichiers sur disque (_traduit.md, .state.json, .errors.log,
    dossier upload) — c'est un simple nettoyage de la liste, réversible en
    relançant une traduction. Retourne True si une entrée a été retirée.
    """
    with _lock:
        documents = _charger()
        restants = [d for d in documents if d.get("chemin_sortie") != chemin_sortie]
        if len(restants) == len(documents):
            return False
        _sauvegarder(restants)
        return True


def lister_documents() -> list[dict]:
    """
    Retourne les documents du registre, enrichis du statut et de la progression
    lus dans le .state.json du job. Les entrées dont ni la sortie ni l'état
    n'existent plus sur disque sont ignorées (fichiers supprimés par l'usager).
    Triés du plus récent au plus ancien.
    """
    with _lock:
        documents = _charger()

    resultats = []
    for doc in documents:
        try:
            enrichi = _enrichir(doc)
        except Exception as e:
            # Garde PAR DOCUMENT (F1) : une entrée abîmée fait disparaître cette
            # ligne, jamais la liste entière. Avant ce garde, un seul .state.json
            # tronqué faisait renvoyer 500 à `GET /api/bibliotheque` et vidait la
            # Bibliothèque des DEUX frontends à la fois, alors que le travail
            # était intact sur le disque.
            print(
                f"[bibliotheque] entrée ignorée ({doc.get('chemin_sortie', '?')}) : {e}",
                flush=True,
            )
            continue
        if enrichi is not None:
            resultats.append(enrichi)

    resultats.sort(key=lambda d: d.get("maj_a", ""), reverse=True)
    return resultats


def _enrichir(doc: dict) -> dict | None:
    """
    Enrichit une entrée du registre du statut et de la progression lus dans son
    .state.json. Retourne None si le document n'existe plus du tout sur disque.
    """
    chemin_sortie = doc.get("chemin_sortie")
    if not chemin_sortie:
        raise ValueError("entrée sans chemin_sortie")

    etat = charger_etat(chemin_sortie)
    sortie_existe = os.path.exists(chemin_sortie)
    if etat is None and not sortie_existe:
        return None

    enrichi = dict(doc)
    if etat is not None:
        enrichi["statut"] = etat.statut.value
        enrichi["sections_completees"] = etat.derniere_section_completee
        enrichi["total_sections"] = etat.total_sections
        # Minutage (feature 320) : le temps écoulé et l'estimation vivaient déjà
        # dans EtatJob mais n'étaient exposés nulle part, si bien que l'interface
        # perdait toute notion de durée dès qu'une traduction était lancée.
        # ⚠️ `temps_ecoule_secondes` est FIGÉ pendant la boucle du moteur (il n'est
        # réécrit qu'aux points de sortie : pause, annulation, fin) — c'est
        # délibéré, ça évite une réécriture d'état à chaque sous-morceau. Le
        # client doit donc y ajouter lui-même le temps passé depuis `maj_a` pour
        # afficher un compteur qui avance.
        enrichi["temps_ecoule_secondes"] = etat.temps_ecoule_secondes
        enrichi["estimation_temps_total_secondes"] = etat.estimation_temps_total_secondes
        # Ancrage pour un compteur qui AVANCE : `temps_ecoule_secondes` étant
        # figé, seul `temps_debut` permet au client de calculer l'écoulé réel.
        # Comparer une horloge serveur à une horloge client serait fragile en
        # général — ici les deux sont la MÊME machine (app 100 % locale), donc
        # `Date.now()/1000 - temps_debut` est exact. Ne pas reprendre ce raccourci
        # si le backend devenait un jour distant.
        enrichi["temps_debut"] = etat.temps_debut
        # job_id du run courant : permet de mettre en pause un job en cours
        # directement depuis « Reprendre une traduction ».
        enrichi["job_id"] = etat.job_id
        # Chapitres déjà traduits : le sélecteur de « ➕ Chapitres » les marque
        # (✓ désactivés) pour que l'utilisateur coche uniquement les NOUVEAUX.
        enrichi["chapitres_traduits"] = etat.chapitres_traduits
        # Portée du run courant : sert au compteur « N/M chapitres » de la barre.
        enrichi["chapitres_selectionnes"] = etat.chapitres_selectionnes
        # Permet à la Bibliothèque de proposer « Reprendre » sur un document
        # lisible mais troué, plutôt que de le déclarer inaccessible.
        enrichi["nb_sections_echouees"] = len(etat.sections_echouees) + len(etat.chapitres_echoues)
    else:
        # Sortie présente sans état : traduction d'avant le registre — jugée finie
        enrichi["statut"] = "termine"
        enrichi["sections_completees"] = 0
        enrichi["total_sections"] = 0
        enrichi["nb_sections_echouees"] = 0
    return enrichi
