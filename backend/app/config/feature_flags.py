"""
Feature flags simples, lus depuis plusieurs sources fusionnées.

Ordre de priorité (bas → haut) :
1. FLAGS_PAR_DEFAUT (ci-dessous, codé en dur) ;
2. bilbao.features.json à la RACINE du repo produit — artefact géré par la
   console bilbao (feature-factory), committé dans le repo (voir la clé
   "flags"). C'est le contrat d'intégration portfolio : bilbao émet ce fichier,
   JP le committe, et le produit l'honore ici ;
3. feature_flags.json (même dossier que ce module) — override local de dev ;
4. variables d'environnement FEATURE_<NOM> — override ponctuel (CI, tests).
"""

import json
import os
import shutil
from pathlib import Path

CHEMIN_FLAGS_DEFAUT = Path(__file__).parent / "feature_flags.json"
# Racine du repo produit : config → app → backend → traducteur-pdf/
CHEMIN_FLAGS_BILBAO = Path(__file__).parents[3] / "bilbao.features.json"

FLAGS_PAR_DEFAUT = {
    "pause_reprise": True,
    "analyse_preliminaire": True,
    "extraction_urls": True,
    "agents_ia": False,
    "planification_differee": False,
    # Affichage du bouton « mode avancé » (barre supérieure). Off → le toggle
    # disparaît et le contenu avancé (Laboratoire, Résumé & Quiz) reste masqué.
    "mode_avance": True,
    # Bouton d'export HTML de la fiche d'étude (Bibliothèque, section avancée).
    "export_fiche_html": True,
    # Bibliothèque (mode avancé) : bouton « Afficher/Masquer le texte ». Le texte
    # du chapitre est masqué par défaut — le panneau Résumé & Quiz occupe le
    # centre. Off → texte toujours visible et bouton masqué (comportement
    # historique).
    "biblio_toggle_contenu": True,
    # Teasers (refonte Workflow) : fonctionnalités futures affichées dans le
    # Laboratoire avec capture d'intérêt (POST /api/interet).
    # Note : "voix personnalisées" n'est plus un teaser depuis l'ajout du
    # clonage vocal réel (moteur "openvoice") — sa carte est désormais pilotée
    # par la disponibilité du moteur (GET /tts/moteurs), pas par un flag.
    "teaser_export_pdf": True,
    # Extraction des images du PDF (pymupdf4llm) + affichage en Bibliothèque +
    # export HTML du document traduit avec images. Off par défaut (contrairement
    # aux autres flags) : touche l'extraction PDF et le chunking envoyé à Ollama,
    # rollout prudent le temps de valider sur un document réel.
    "extraction_images_pdf": False,
}

EXTRACTEURS_PDF = [
    {"id": "pymupdf4llm",  "nom": "PyMuPDF4LLM",  "disponible": True},
    {"id": "marker",       "nom": "Marker",         "disponible": True},
    # OCR — pour les PDF sans couche texte exploitable (scans, exports Aperçu).
    # Nécessite le binaire système : brew install tesseract
    {"id": "tesseract",    "nom": "Tesseract (OCR)", "disponible": shutil.which("tesseract") is not None},
    {"id": "llamaparse",   "nom": "LlamaParse",     "disponible": False},
    {"id": "unstructured", "nom": "Unstructured",   "disponible": False},
]

EXTRACTEUR_PAR_DEFAUT = "pymupdf4llm"


def _flags_bilbao() -> dict:
    """
    Flags gérés par bilbao (bilbao.features.json à la racine du repo). On ne lit
    que la clé "flags" ({str: bool}) ; les métadonnées (genere_par, produit…)
    sont ignorées. Fichier absent ou invalide → dict vide (jamais d'erreur).
    """
    if not CHEMIN_FLAGS_BILBAO.exists():
        return {}
    try:
        with open(CHEMIN_FLAGS_BILBAO, "r", encoding="utf-8") as f:
            data = json.load(f)
        flags = data.get("flags", {})
        return flags if isinstance(flags, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def charger_flags() -> dict[str, bool]:
    """
    Fusionne les feature flags selon l'ordre de priorité documenté en tête de
    module : défauts < artefact bilbao < feature_flags.json local < env FEATURE_*.
    """
    flags = dict(FLAGS_PAR_DEFAUT)

    flags.update(_flags_bilbao())

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
