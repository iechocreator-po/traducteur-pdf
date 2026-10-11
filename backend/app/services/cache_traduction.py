"""
Cache de traductions par chunk, persisté en JSON à côté du fichier de sortie.
Clé : SHA-256(texte|modèle|langue_source|langue_cible) → texte traduit.
Permet de sauter les sections déjà traduites lors d'un re-run du même document.
"""

import hashlib
import os

from app.services import store
from app.services.persistance import ecrire_json_atomique, lire_json_tolerant


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
    """
    Cache corrompu ou illisible : on repart de zéro sans bloquer le job.

    La perte reste réelle (il faudra repayer ce travail chez Ollama) mais elle
    n'est plus SILENCIEUSE : `lire_json_tolerant` met le fichier en quarantaine et
    l'inscrit au journal des corruptions, consultable via l'API. C'était le cœur
    de F2 — le cache porte la traduction entre deux écritures de chapitre, et sa
    disparition ne se signalait nulle part.
    """
    donnees = lire_json_tolerant(chemin_fichier_cache(chemin_sortie), defaut={})
    depuis_json = donnees if isinstance(donnees, dict) else {}

    # ── Étape B de la phase 9 : double écriture, lecture prioritaire SQLite ──
    # Le store fait foi quand il connaît le document ; le JSON reste le repli
    # pour tout ce qui a été traduit avant la bascule. On FUSIONNE plutôt que de
    # choisir : un document en cours de migration a une partie de son cache de
    # chaque côté, et perdre l'une des deux ferait repayer du travail chez Ollama
    # — exactement ce que ce cache existe pour éviter.
    try:
        depuis_store = store.lire_morceaux(chemin_sortie)
    except Exception as e:  # noqa: BLE001 — le store ne doit JAMAIS casser un job
        print(f"[cache] store illisible, repli sur le JSON : {e}", flush=True)
        return depuis_json
    return {**depuis_json, **depuis_store}


def sauvegarder_cache(chemin_sortie: str, cache: dict[str, str]) -> None:
    """
    Écriture ATOMIQUE — le cache est réécrit en entier après chaque sous-morceau ;
    sans atomicité, chacune de ces réécritures pouvait le détruire (F2).
    """
    ecrire_json_atomique(chemin_fichier_cache(chemin_sortie), cache, indent=None)
    # Double écriture (étape B) : le JSON reste la source tant que la bascule
    # n'est pas terminée, le store se remplit en parallèle. Un échec du store ne
    # doit pas faire échouer la traduction — le JSON, lui, a déjà réussi.
    try:
        store.ecrire_morceaux(chemin_sortie, cache)
    except Exception as e:  # noqa: BLE001
        print(f"[cache] écriture store ignorée : {e}", flush=True)
