"""
Registre des voix TTS clonées par l'utilisateur (moteur OpenVoice).
Persisté dans tts_modeles/openvoice/voix_utilisateur/registre.json — même
philosophie que le glossaire (fichier JSON local, pas de base de données).

Chaque voix a son propre dossier tts_modeles/openvoice/voix_utilisateur/<id>/
contenant l'échantillon brut (echantillon.wav) et, une fois le traitement
terminé, l'embedding de locuteur (embedding.pth) utilisé par tts.py.
"""

import datetime
import json
import os
import threading
import uuid

DOSSIER_OPENVOICE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tts_modeles", "openvoice")
)
DOSSIER_VOIX_UTILISATEUR = os.path.join(DOSSIER_OPENVOICE, "voix_utilisateur")
CHEMIN_REGISTRE = os.path.join(DOSSIER_VOIX_UTILISATEUR, "registre.json")

_lock = threading.Lock()


def _charger() -> list[dict]:
    if not os.path.exists(CHEMIN_REGISTRE):
        return []
    try:
        with open(CHEMIN_REGISTRE, "r", encoding="utf-8") as f:
            return json.load(f).get("voix", [])
    except (json.JSONDecodeError, OSError):
        return []


def _sauvegarder(voix: list[dict]) -> None:
    os.makedirs(DOSSIER_VOIX_UTILISATEUR, exist_ok=True)
    with open(CHEMIN_REGISTRE, "w", encoding="utf-8") as f:
        json.dump({"voix": voix}, f, indent=2, ensure_ascii=False)


def chemin_dossier_voix(id_voix: str) -> str:
    return os.path.join(DOSSIER_VOIX_UTILISATEUR, id_voix)


def chemin_echantillon(id_voix: str) -> str:
    return os.path.join(chemin_dossier_voix(id_voix), "echantillon.wav")


def chemin_embedding(id_voix: str) -> str:
    return os.path.join(chemin_dossier_voix(id_voix), "embedding.pth")


def _nom_unique(nom: str, voix_existantes: list[dict], exclure_id: str | None = None) -> str:
    """Suffixe le nom si une voix du même nom existe déjà (clonée ou non)."""
    from app.services.tts import lister_voix_piper, lister_voix_kokoro

    noms_pris = {v["nom"] for v in voix_existantes if v["id"] != exclure_id}
    noms_pris |= set(lister_voix_piper())
    noms_pris |= set(lister_voix_kokoro())

    if nom not in noms_pris:
        return nom
    compteur = 2
    while f"{nom} ({compteur})" in noms_pris:
        compteur += 1
    return f"{nom} ({compteur})"


def lister_voix() -> list[dict]:
    """Toutes les voix clonées, quel que soit leur statut de traitement."""
    with _lock:
        return _charger()


def obtenir_voix(id_voix: str) -> dict | None:
    with _lock:
        return next((v for v in _charger() if v["id"] == id_voix), None)


def creer_voix(nom: str) -> dict:
    """
    Crée l'entrée registre en statut 'en_attente' et le dossier disque
    associé. Retourne l'entrée (avec son id) — l'appelant y écrit ensuite
    l'échantillon avant de démarrer le traitement.
    """
    nom = nom.strip()
    if not nom:
        raise ValueError("Le nom de la voix ne peut pas être vide.")

    with _lock:
        voix = _charger()
        entree = {
            "id": str(uuid.uuid4()),
            "nom": _nom_unique(nom, voix),
            "cree_le": datetime.datetime.now().isoformat(),
            "chemin_echantillon": None,
            "chemin_embedding": None,
            "statut": "en_attente",
            "erreur": None,
        }
        os.makedirs(chemin_dossier_voix(entree["id"]), exist_ok=True)
        voix.append(entree)
        _sauvegarder(voix)
        return entree


def mettre_a_jour_voix(id_voix: str, **champs) -> dict | None:
    with _lock:
        voix = _charger()
        entree = next((v for v in voix if v["id"] == id_voix), None)
        if entree is None:
            return None
        entree.update(champs)
        _sauvegarder(voix)
        return entree


def renommer_voix(id_voix: str, nouveau_nom: str) -> dict | None:
    nouveau_nom = nouveau_nom.strip()
    if not nouveau_nom:
        raise ValueError("Le nom de la voix ne peut pas être vide.")
    with _lock:
        voix = _charger()
        entree = next((v for v in voix if v["id"] == id_voix), None)
        if entree is None:
            return None
        entree["nom"] = _nom_unique(nouveau_nom, voix, exclure_id=id_voix)
        _sauvegarder(voix)
        return entree


def supprimer_voix(id_voix: str) -> bool:
    with _lock:
        voix = _charger()
        restantes = [v for v in voix if v["id"] != id_voix]
        if len(restantes) == len(voix):
            return False
        _sauvegarder(restantes)

    import shutil
    dossier = chemin_dossier_voix(id_voix)
    if os.path.isdir(dossier):
        shutil.rmtree(dossier, ignore_errors=True)
    return True
