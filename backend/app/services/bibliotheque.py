"""
Registre des documents traduits (la « Bibliothèque » de la refonte Workflow).
Persisté dans bibliotheque.json à la racine du backend, alimenté automatiquement
au lancement de chaque traduction. Le statut et la progression sont lus depuis
les fichiers .state.json des jobs (source de vérité), jamais dupliqués ici.
"""

import datetime
import json
import os
import threading

from app.services.job_manager import charger_etat

_FICHIER_BIBLIO = os.path.join(os.path.dirname(__file__), "..", "..", "bibliotheque.json")
_FICHIER_BIBLIO = os.path.normpath(_FICHIER_BIBLIO)

_lock = threading.Lock()


def _charger() -> list[dict]:
    if not os.path.exists(_FICHIER_BIBLIO):
        return []
    try:
        with open(_FICHIER_BIBLIO, "r", encoding="utf-8") as f:
            return json.load(f).get("documents", [])
    except (json.JSONDecodeError, OSError):
        return []


def _sauvegarder(documents: list[dict]) -> None:
    with open(_FICHIER_BIBLIO, "w", encoding="utf-8") as f:
        json.dump({"documents": documents}, f, indent=2, ensure_ascii=False)


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
        etat = charger_etat(doc["chemin_sortie"])
        sortie_existe = os.path.exists(doc["chemin_sortie"])
        if etat is None and not sortie_existe:
            continue

        enrichi = dict(doc)
        if etat is not None:
            enrichi["statut"] = etat.statut.value
            enrichi["sections_completees"] = etat.derniere_section_completee
            enrichi["total_sections"] = etat.total_sections
        else:
            # Sortie présente sans état : traduction d'avant le registre — jugée finie
            enrichi["statut"] = "termine"
            enrichi["sections_completees"] = 0
            enrichi["total_sections"] = 0
        resultats.append(enrichi)

    resultats.sort(key=lambda d: d.get("maj_a", ""), reverse=True)
    return resultats
