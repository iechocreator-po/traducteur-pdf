"""
Garde de validation des chemins de fichiers source, partagée par les routes.

CONTEXTE DE SÉCURITÉ — à lire avant de « durcir » :
Cette app est 100 % locale. Deux flux coexistent volontairement :
  1. l'upload (POST /upload → backend/uploads/<hash>/...) ;
  2. le champ « chemin absolu » du mode avancé, qui pointe n'importe quel
     fichier du disque de l'utilisateur (c'est le flux historique, conservé).
Ce helper NE DOIT DONC PAS imposer une whitelist backend/uploads/ : cela
casserait le flux 2. Le CORS restrictif (voir main.py) reste la protection
contre un navigateur tiers. Ici on se contente de gardes qui ajoutent de la
valeur sans rien casser : chemin absolu, extension autorisée, fichier existant.
"""

import os

from fastapi import HTTPException

EXTENSIONS_SOURCE = (".pdf", ".md")

# Origines autorisées à écrire via multipart (POST /upload). Doit rester alignée
# sur la CORS allowlist de main.py — partagée ici pour éviter la dérive.
ORIGINES_LOCALES = (
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "null",
)


def verifier_origine_upload(origin: str | None) -> None:
    """
    multipart/form-data est CORS-safelisted : un <form> sur un site tiers peut
    POSTer /upload SANS préflight (le middleware CORS n'ajoute que des en-têtes
    de réponse, il ne bloque pas la requête). On refuse donc explicitement une
    Origin présente mais hors allowlist. Absente (curl, Swift, file://→"null")
    = accepté, comme le reste de l'API.
    """
    if origin is not None and origin not in ORIGINES_LOCALES:
        raise HTTPException(status_code=403, detail="Origine non autorisée.")


def valider_chemin_source(
    chemin: str | None,
    extensions: tuple[str, ...] = EXTENSIONS_SOURCE,
    label: str = "Fichier",
) -> str:
    """
    Valide un chemin source et retourne sa forme canonique (realpath).
    Lève HTTPException (422 entrée invalide, 404 introuvable) — à appeler dans
    le corps de la route, jamais dans un model_validator (qui produirait un 422
    là où les tests existants attendent un 404).
    """
    if not chemin or "\x00" in chemin:
        raise HTTPException(status_code=422, detail=f"{label} : chemin manquant ou invalide.")
    if not os.path.isabs(chemin):
        raise HTTPException(status_code=422, detail=f"{label} : un chemin absolu est requis.")

    # Normalise « .. » et les liens symboliques ; deux chemins vers le même
    # fichier convergent, ce qui évite des doublons dans la Bibliothèque.
    chemin = os.path.realpath(chemin)

    if not chemin.lower().endswith(tuple(e.lower() for e in extensions)):
        attendues = " ou ".join(extensions)
        raise HTTPException(status_code=422, detail=f"{label} : extension attendue {attendues}.")
    if not os.path.isfile(chemin):
        raise HTTPException(status_code=404, detail=f"{label} introuvable.")
    return chemin


def resoudre_source(
    chemin_pdf: str | None,
    chemin_md: str | None,
    extensions: tuple[str, ...] = EXTENSIONS_SOURCE,
) -> str:
    """Résout et valide la source d'une requête à double champ (chemin_pdf/chemin_md)."""
    return valider_chemin_source(chemin_md or chemin_pdf, extensions=extensions)
