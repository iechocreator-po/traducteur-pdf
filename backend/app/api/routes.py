"""
Routes HTTP exposées par l'API. Cette couche ne contient aucune logique
métier — elle valide les entrées et délègue aux services/agents.
"""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

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


class ConvertRequest(BaseModel):
    chemin_pdf: str
    extracteur_pdf: str = EXTRACTEUR_PAR_DEFAUT


@router.post("/convert")
def convertir_pdf(req: ConvertRequest) -> dict:
    """Converts a PDF to Markdown using the chosen extractor. Returns the output path."""
    if not os.path.exists(req.chemin_pdf):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable.")

    from app.services.pdf_extractor import extraire_texte

    try:
        contenu_md = extraire_texte(req.chemin_pdf, req.extracteur_pdf)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'extraction : {e}")

    base, _ = os.path.splitext(req.chemin_pdf)
    suffixe = req.extracteur_pdf[:2] if req.extracteur_pdf else ""
    chemin_sortie = f"{base}_converti_{suffixe}.md" if suffixe else f"{base}_converti.md"
    with open(chemin_sortie, "w", encoding="utf-8") as f:
        f.write(f"<!-- extracteur : {req.extracteur_pdf} -->\n\n")
        f.write(contenu_md)

    return {"chemin_sortie": chemin_sortie, "nb_caracteres": len(contenu_md)}


class ResumeCheckRequest(BaseModel):
    chemin_pdf: str | None = None
    chemin_md: str | None = None

    @model_validator(mode="after")
    def valider_source(self):
        if not self.chemin_pdf and not self.chemin_md:
            raise ValueError("chemin_pdf ou chemin_md est requis.")
        return self


class ChapitresRequest(BaseModel):
    chemin_pdf: str | None = None
    chemin_md: str | None = None
    extracteur_pdf: str = EXTRACTEUR_PAR_DEFAUT

    @model_validator(mode="after")
    def valider_source(self):
        if not self.chemin_pdf and not self.chemin_md:
            raise ValueError("chemin_pdf ou chemin_md est requis.")
        return self


@router.post("/chapitres")
def lister_chapitres(req: ChapitresRequest) -> dict:
    """
    Identifie les chapitres d'un PDF ou Markdown.
    Priorité : signets PDF intégrés → fallback titres Markdown.
    """
    chemin = req.chemin_md or req.chemin_pdf
    if not os.path.exists(chemin):
        label = "Fichier Markdown" if req.chemin_md else "Fichier PDF"
        raise HTTPException(status_code=404, detail=f"{label} introuvable.")

    from app.services.pdf_extractor import extraire_toc_pdf, identifier_chapitres

    # Essaie d'abord les signets PDF (TOC officielle)
    if req.chemin_pdf:
        toc = extraire_toc_pdf(req.chemin_pdf)
        if toc:
            return {"chapitres": toc, "source": "signets_pdf"}

    # Fallback : analyse des titres Markdown
    try:
        chapitres = identifier_chapitres(chemin, req.extracteur_pdf)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'identification : {e}")

    chapitres_publics = [
        {k: v for k, v in c.items() if k not in ("contenu", "ligne_debut", "ligne_fin")}
        for c in chapitres
    ]
    return {"chapitres": chapitres_publics, "source": "titres_markdown"}


@router.post("/check-resume", response_model=EtatJob | None)
def check_resume(req: ResumeCheckRequest) -> EtatJob | None:
    from app.services.translation_runner import check_resume as _check
    return _check(req.chemin_md or req.chemin_pdf)


class TranslateRequest(BaseModel):
    chemin_pdf: str | None = None
    chemin_md: str | None = None
    langue_source: str = "anglais"
    langue_cible: str = "français"
    modele_ollama: str = "llama3.1"
    extracteur_pdf: str = EXTRACTEUR_PAR_DEFAUT
    resume: bool = False
    estimation_temps_total: float | None = None
    chapitres_selectionnes: list[int] | None = None

    @model_validator(mode="after")
    def valider_source(self):
        if not self.chemin_pdf and not self.chemin_md:
            raise ValueError("chemin_pdf ou chemin_md est requis.")
        return self


@router.post("/translate")
def translate(req: TranslateRequest) -> dict:
    """Starts or resumes a translation job (from PDF or Markdown) in background. Returns job_id."""
    chemin_source = req.chemin_md or req.chemin_pdf
    if not os.path.exists(chemin_source):
        label = "Fichier Markdown" if req.chemin_md else "Fichier PDF"
        raise HTTPException(status_code=404, detail=f"{label} introuvable.")

    from app.models.schemas import Langue
    from app.services.translation_runner import demarrer_traduction

    try:
        langue_source = Langue(req.langue_source)
        langue_cible = Langue(req.langue_cible)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    job_id = demarrer_traduction(
        source_path=chemin_source,
        langue_source=langue_source,
        langue_cible=langue_cible,
        modele=req.modele_ollama,
        extracteur=req.extracteur_pdf,
        resume=req.resume,
        estimation_temps_total=req.estimation_temps_total,
        chapitres_selectionnes=req.chapitres_selectionnes,
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


class ScheduleRequest(BaseModel):
    chemin_pdf: str | None = None
    chemin_md: str | None = None
    langue_source: str = "anglais"
    langue_cible: str = "français"
    modele_ollama: str = "llama3.1"
    extracteur_pdf: str = EXTRACTEUR_PAR_DEFAUT
    executer_a: str  # ISO 8601 datetime
    chapitres_selectionnes: list[int] | None = None

    @model_validator(mode="after")
    def valider_source(self):
        if not self.chemin_pdf and not self.chemin_md:
            raise ValueError("chemin_pdf ou chemin_md est requis.")
        return self


@router.post("/schedule")
def creer_job_planifie(req: ScheduleRequest) -> dict:
    chemin_source = req.chemin_md or req.chemin_pdf
    if not os.path.exists(chemin_source):
        label = "Fichier Markdown" if req.chemin_md else "Fichier PDF"
        raise HTTPException(status_code=404, detail=f"{label} introuvable.")

    from datetime import datetime
    from app.services.scheduler import planifier_job

    try:
        executer_a = datetime.fromisoformat(req.executer_a)
    except ValueError:
        raise HTTPException(status_code=422, detail="Format de date invalide (attendu ISO 8601).")

    job = planifier_job(
        chemin_source=chemin_source,
        langue_source=req.langue_source,
        langue_cible=req.langue_cible,
        modele_ollama=req.modele_ollama,
        extracteur_pdf=req.extracteur_pdf,
        executer_a=executer_a,
        chapitres_selectionnes=req.chapitres_selectionnes,
    )
    return job


@router.get("/scheduled")
def liste_jobs_planifies() -> dict:
    from app.services.scheduler import lister_jobs_planifies
    return {"jobs": lister_jobs_planifies()}


@router.delete("/scheduled/{job_id}")
def annuler_job_planifie(job_id: str) -> dict:
    from app.services.scheduler import annuler_job
    ok = annuler_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job introuvable ou déjà déclenché/annulé.")
    return {"statut": "annule"}


@router.post("/analyser", response_model=ResultatAnalyse)
def analyser_document(demande: DemandeTraduction) -> ResultatAnalyse:
    """Lance l'analyse préliminaire d'un PDF."""
    if not os.path.exists(demande.chemin_pdf):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable.")

    from app.agents.analysis_agent import analyser_pdf

    return analyser_pdf(demande.chemin_pdf)
