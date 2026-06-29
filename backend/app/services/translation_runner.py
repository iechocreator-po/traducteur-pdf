"""
Orchestrates a full PDF translation job: extract → chunk → translate → save.
Supports resume: if a .state.json exists and resume=True, skips already-done chunks.
"""

import uuid
import os

from app.models.schemas import EtatJob, StatutJob, Langue
from app.services.pdf_extractor import extraire_texte, decouper_en_chunks
from app.services.translator import traduire_texte
from app.services.job_manager import sauvegarder_etat, charger_etat, supprimer_etat


def build_output_path(pdf_path: str) -> str:
    base, _ = os.path.splitext(pdf_path)
    return f"{base}_traduit.txt"


def run_translation(
    pdf_path: str,
    langue_source: Langue,
    langue_cible: Langue,
    modele: str,
    resume: bool = False,
) -> dict:
    output_path = build_output_path(pdf_path)

    existing = charger_etat(output_path)
    if resume and existing:
        state = existing
        state.statut = StatutJob.EN_COURS
    else:
        texte = extraire_texte(pdf_path)
        chunks = decouper_en_chunks(texte)
        state = EtatJob(
            job_id=str(uuid.uuid4()),
            chemin_pdf=pdf_path,
            chemin_sortie=output_path,
            langue_source=langue_source,
            langue_cible=langue_cible,
            modele_ollama=modele,
            statut=StatutJob.EN_COURS,
            derniere_section_completee=0,
            total_sections=len(chunks),
        )
        # Overwrite output file on fresh start
        open(output_path, "w", encoding="utf-8").close()

    texte = extraire_texte(pdf_path)
    chunks = decouper_en_chunks(texte)
    start_index = state.derniere_section_completee

    with open(output_path, "a", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks):
            if i < start_index:
                continue
            translated = traduire_texte(
                chunk, modele, langue_source.value, langue_cible.value
            )
            f.write(translated + "\n\n")
            state.derniere_section_completee = i + 1
            sauvegarder_etat(state)

    state.statut = StatutJob.TERMINE
    sauvegarder_etat(state)
    supprimer_etat(output_path)

    return {
        "job_id": state.job_id,
        "statut": state.statut,
        "sections_traitees": state.total_sections,
        "chemin_sortie": output_path,
    }


def check_resume(pdf_path: str) -> EtatJob | None:
    output_path = build_output_path(pdf_path)
    return charger_etat(output_path)
