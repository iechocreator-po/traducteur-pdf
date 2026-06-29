"""
Routes HTTP exposées par l'API. Cette couche ne contient aucune logique
métier — elle valide les entrées et délègue aux services/agents.
"""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.feature_flags import charger_flags, EXTRACTEURS_PDF, EXTRACTEUR_PAR_DEFAUT
from app.models.schemas import DemandeTraduction, EtatJob, ResultatAnalyse
from app.services.translator import lister_modeles_disponibles

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    modeles = lister_modeles_disponibles()
    return {
        "statut": "ok",
        "ollama_accessible": "oui" if modeles else "non",
    }


@router.get("/modeles")
def modeles_disponibles() -> dict[str, list[str]]:
    return {"modeles": lister_modeles_disponibles()}


@router.get("/feature-flags")
def feature_flags() -> dict[str, bool]:
    return charger_flags()


@router.get("/config/extracteurs")
def liste_extracteurs() -> dict:
    return {"extracteurs": EXTRACTEURS_PDF, "defaut": EXTRACTEUR_PAR_DEFAUT}


class ResumeCheckRequest(BaseModel):
    chemin_pdf: str


@router.post("/check-resume", response_model=EtatJob | None)
def check_resume(req: ResumeCheckRequest) -> EtatJob | None:
    from app.services.translation_runner import check_resume as _check
    return _check(req.chemin_pdf)


class TranslateRequest(BaseModel):
    chemin_pdf: str
    langue_source: str = "anglais"
    langue_cible: str = "français"
    modele_ollama: str = "llama3.1"
    extracteur_pdf: str = EXTRACTEUR_PAR_DEFAUT
    resume: bool = False
    estimation_temps_total: float | None = None


@router.post("/translate")
def translate(req: TranslateRequest) -> dict:
    """Starts or resumes a PDF translation job in the background. Returns job_id immediately."""
    if not os.path.exists(req.chemin_pdf):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable.")

    from app.models.schemas import Langue
    from app.services.translation_runner import demarrer_traduction

    try:
        langue_source = Langue(req.langue_source)
        langue_cible = Langue(req.langue_cible)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    job_id = demarrer_traduction(
        pdf_path=req.chemin_pdf,
        langue_source=langue_source,
        langue_cible=langue_cible,
        modele=req.modele_ollama,
        extracteur=req.extracteur_pdf,
        resume=req.resume,
        estimation_temps_total=req.estimation_temps_total,
    )
    return {"job_id": job_id}


@router.get("/job/{job_id}/statut")
def statut_job(job_id: str, chemin_pdf: str) -> EtatJob:
    from app.services.translation_runner import lire_statut
    etat = lire_statut(job_id, chemin_pdf)
    if etat is None:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    return etat


@router.post("/job/{job_id}/pause")
def pause_job(job_id: str) -> dict:
    from app.services.job_manager import mettre_en_pause
    ok = mettre_en_pause(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job introuvable ou déjà terminé.")
    return {"statut": "pause_demandee"}


@router.post("/job/{job_id}/reprendre")
def reprendre_job(job_id: str) -> dict:
    """
    Re-starts a paused job. Reads parameters from the saved state file.
    The client must pass chemin_pdf so we can locate the state file.
    """
    from pydantic import BaseModel as BM

    class ReprendreRequest(BM):
        chemin_pdf: str

    raise HTTPException(
        status_code=400,
        detail="Utiliser POST /translate avec resume=true pour reprendre un job.",
    )


@router.post("/analyser", response_model=ResultatAnalyse)
def analyser_document(demande: DemandeTraduction) -> ResultatAnalyse:
    """Lance l'analyse préliminaire d'un PDF."""
    if not os.path.exists(demande.chemin_pdf):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable.")

    from app.agents.analysis_agent import analyser_pdf

    return analyser_pdf(demande.chemin_pdf)
