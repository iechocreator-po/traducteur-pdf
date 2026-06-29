"""
Routes HTTP exposées par l'API. Cette couche ne contient aucune logique
métier — elle valide les entrées et délègue aux services/agents.
"""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.feature_flags import charger_flags
from app.models.schemas import DemandeTraduction, EtatJob, ResultatAnalyse
from app.services.translator import lister_modeles_disponibles

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    """Vérifie que l'API tourne, et que Ollama est accessible."""
    modeles = lister_modeles_disponibles()
    return {
        "statut": "ok",
        "ollama_accessible": "oui" if modeles else "non",
    }


@router.get("/modeles")
def modeles_disponibles() -> dict[str, list[str]]:
    """Liste les modèles Ollama installés localement."""
    return {"modeles": lister_modeles_disponibles()}


@router.get("/feature-flags")
def feature_flags() -> dict[str, bool]:
    """Expose les feature flags actifs, utile pour adapter l'UI dynamiquement."""
    return charger_flags()


class ResumeCheckRequest(BaseModel):
    chemin_pdf: str


@router.post("/check-resume", response_model=EtatJob | None)
def check_resume(req: ResumeCheckRequest) -> EtatJob | None:
    """Returns existing job state if a translation was interrupted, else null."""
    from app.services.translation_runner import check_resume as _check
    return _check(req.chemin_pdf)


class TranslateRequest(BaseModel):
    chemin_pdf: str
    langue_source: str = "anglais"
    langue_cible: str = "français"
    modele_ollama: str = "llama3.1"
    resume: bool = False


@router.post("/translate")
def translate(req: TranslateRequest) -> dict:
    """Starts or resumes a PDF translation job. Saves progress after each chunk."""
    if not os.path.exists(req.chemin_pdf):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable.")

    from app.models.schemas import Langue
    from app.services.translation_runner import run_translation

    try:
        langue_source = Langue(req.langue_source)
        langue_cible = Langue(req.langue_cible)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return run_translation(
        pdf_path=req.chemin_pdf,
        langue_source=langue_source,
        langue_cible=langue_cible,
        modele=req.modele_ollama,
        resume=req.resume,
    )


@router.post("/analyser", response_model=ResultatAnalyse)
def analyser_document(demande: DemandeTraduction) -> ResultatAnalyse:
    """Lance l'analyse préliminaire d'un PDF (feature #5)."""
    if not os.path.exists(demande.chemin_pdf):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable.")

    # Import local pour éviter de charger l'agent (et sa dépendance à Ollama)
    # si cette route n'est jamais appelée.
    from app.agents.analysis_agent import analyser_pdf

    return analyser_pdf(demande.chemin_pdf)
