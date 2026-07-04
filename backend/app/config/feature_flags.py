"""
Feature flags simples, lus depuis un fichier JSON ou des variables d'environnement.
Permet d'activer/désactiver des fonctionnalités en développement sans changer le code.
"""

import json
import os
from pathlib import Path

CHEMIN_FLAGS_DEFAUT = Path(__file__).parent / "feature_flags.json"

FLAGS_PAR_DEFAUT = {
    "pause_reprise": True,
    "analyse_preliminaire": True,
    "extraction_urls": True,
    "agents_ia": False,
    "planification_differee": False,
}

EXTRACTEURS_PDF = [
    {"id": "pymupdf4llm",  "nom": "PyMuPDF4LLM",  "disponible": True},
    {"id": "marker",       "nom": "Marker",         "disponible": True},
    {"id": "llamaparse",   "nom": "LlamaParse",     "disponible": False},
    {"id": "unstructured", "nom": "Unstructured",   "disponible": False},
]

EXTRACTEUR_PAR_DEFAUT = "pymupdf4llm"


def charger_flags() -> dict[str, bool]:
    """
    Charge les feature flags depuis feature_flags.json si présent,
    sinon retourne les valeurs par défaut. Les variables d'environnement
    de la forme FEATURE_<NOM> écrasent les valeurs du fichier.
    """
    flags = dict(FLAGS_PAR_DEFAUT)

    if CHEMIN_FLAGS_DEFAUT.exists():
        with open(CHEMIN_FLAGS_DEFAUT, "r", encoding="utf-8") as f:
            flags.update(json.load(f))

    for nom in flags:
        env_var = f"FEATURE_{nom.upper()}"
        if env_var in os.environ:
            flags[nom] = os.environ[env_var].lower() in ("1", "true", "yes")

    return flags


def est_active(nom_flag: str) -> bool:
    return charger_flags().get(nom_flag, False)
