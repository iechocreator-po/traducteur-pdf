"""
Cache de traductions par chunk, persisté en JSON à côté du fichier de sortie.
Clé : SHA-256(texte|modèle|langue_source|langue_cible) → texte traduit.
Permet de sauter les sections déjà traduites lors d'un re-run du même document.
"""

import hashlib
import json
import os


def calculer_cle(
    texte: str, modele: str, langue_source: str, langue_cible: str, extra: str = ""
) -> str:
    """
    extra : contexte additionnel qui invalide le cache s'il change (ex. glossaire).
    N'entre dans la clé que s'il est non vide, pour préserver les caches existants.
    """
    contenu = f"{texte}|{modele}|{langue_source}|{langue_cible}"
    if extra:
        contenu += f"|{extra}"
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


def chemin_fichier_cache(chemin_sortie: str) -> str:
    base, _ = os.path.splitext(chemin_sortie)
    return f"{base}.cache.json"


def charger_cache(chemin_sortie: str) -> dict[str, str]:
    chemin = chemin_fichier_cache(chemin_sortie)
    if not os.path.exists(chemin):
        return {}
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Cache corrompu ou illisible : on repart de zéro, sans bloquer le job
        return {}


def sauvegarder_cache(chemin_sortie: str, cache: dict[str, str]) -> None:
    chemin = chemin_fichier_cache(chemin_sortie)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
