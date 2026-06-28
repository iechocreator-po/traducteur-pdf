"""
Gestionnaire d'état des jobs de traduction.
Permet de sauvegarder/charger l'état d'avancement d'une traduction,
pour supporter la pause et la reprise (feature roadmap #1).
"""

import json
import os

from app.models.schemas import EtatJob


def chemin_fichier_etat(chemin_sortie: str) -> str:
    """Le fichier d'état est toujours stocké à côté du fichier de sortie, en .state.json"""
    base, _ = os.path.splitext(chemin_sortie)
    return f"{base}.state.json"


def sauvegarder_etat(etat: EtatJob) -> None:
    chemin = chemin_fichier_etat(etat.chemin_sortie)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(etat.model_dump_json(indent=2))


def charger_etat(chemin_sortie: str) -> EtatJob | None:
    """Retourne l'état sauvegardé, ou None si aucun job n'a été commencé pour ce fichier."""
    chemin = chemin_fichier_etat(chemin_sortie)
    if not os.path.exists(chemin):
        return None
    with open(chemin, "r", encoding="utf-8") as f:
        data = json.load(f)
    return EtatJob(**data)


def supprimer_etat(chemin_sortie: str) -> None:
    """Supprime le fichier d'état, typiquement une fois la traduction terminée avec succès."""
    chemin = chemin_fichier_etat(chemin_sortie)
    if os.path.exists(chemin):
        os.remove(chemin)
