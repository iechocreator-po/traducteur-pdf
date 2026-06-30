"""
Orchestrates a full PDF translation job in a background thread.
Supports pause/resume via an in-memory flag checked between chunks.
"""

import time
import threading
import uuid
import os

import datetime

from app.models.schemas import EtatJob, StatutJob, Langue
from app.services.pdf_extractor import extraire_texte, decouper_en_chunks, compter_pages
from app.services.translator import traduire_texte
from app.services.job_manager import (
    sauvegarder_etat,
    charger_etat,
    enregistrer_job,
    enregistrer_thread,
    est_en_pause,
    supprimer_job_registre,
    journaliser_erreur,
)

SECONDES_PAR_CHUNK_ESTIME = 10  # used for initial estimate before real timing data


def build_output_path(pdf_path: str) -> str:
    base, _ = os.path.splitext(pdf_path)
    return f"{base}_traduit.md"


def _horodatage() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def _journaliser(state: EtatJob, message: str) -> None:
    state.journal.append(f"[{_horodatage()}] {message}")


def _executer_traduction(state: EtatJob, chunks: list[str]) -> None:
    """Runs in a background thread. Checks pause flag between each chunk."""
    output_path = state.chemin_sortie
    start_index = state.derniere_section_completee
    temps_debut_session = time.time()

    try:
        with open(output_path, "a", encoding="utf-8") as f:
            for i, chunk in enumerate(chunks):
                if i < start_index:
                    continue

                # Check pause flag before each chunk
                if est_en_pause(state.job_id):
                    elapsed = time.time() - temps_debut_session
                    state.temps_ecoule_secondes += elapsed
                    state.statut = StatutJob.EN_PAUSE
                    _journaliser(state, f"Mise en pause — section {i}/{state.total_sections}")
                    sauvegarder_etat(state)
                    supprimer_job_registre(state.job_id)
                    return

                try:
                    translated = traduire_texte(
                        chunk,
                        state.modele_ollama,
                        state.langue_source.value,
                        state.langue_cible.value,
                    )
                    f.write(translated + "\n\n")
                except Exception as e:
                    msg = f"Erreur section {i + 1}/{state.total_sections} : {e}"
                    state.erreurs.append(msg)
                    journaliser_erreur(output_path, msg)
                    f.write(f"[ERREUR DE TRADUCTION — section {i + 1}]\n\n")

                state.derniere_section_completee = i + 1
                state.mots_traduits += len(chunk.split())
                elapsed_total = state.temps_ecoule_secondes + (time.time() - temps_debut_session)

                # Recalculate estimate from actual pace
                sections_done = state.derniere_section_completee
                if sections_done > 0:
                    pace = elapsed_total / sections_done
                    state.estimation_temps_total_secondes = pace * state.total_sections

                state.temps_ecoule_secondes = elapsed_total
                sauvegarder_etat(state)

        state.statut = StatutJob.TERMINE
        state.temps_ecoule_secondes += time.time() - temps_debut_session
        _journaliser(state, "Traduction terminée")
        sauvegarder_etat(state)

    except Exception as e:
        msg = f"Erreur fatale du job : {e}"
        state.erreurs.append(msg)
        journaliser_erreur(output_path, msg)
        state.statut = StatutJob.ERREUR
        state.temps_ecoule_secondes += time.time() - temps_debut_session
        _journaliser(state, f"Erreur fatale : {e}")
        sauvegarder_etat(state)
    finally:
        supprimer_job_registre(state.job_id)


def _lire_source_markdown(pdf_path: str, extracteur: str) -> str:
    """Retourne le contenu Markdown : depuis le fichier _converti.md s'il existe, sinon re-extrait."""
    base, _ = os.path.splitext(pdf_path)
    chemin_md = f"{base}_converti.md"
    if os.path.exists(chemin_md):
        with open(chemin_md, "r", encoding="utf-8") as f:
            return f.read()
    return extraire_texte(pdf_path, extracteur)


def demarrer_traduction(
    pdf_path: str,
    langue_source: Langue,
    langue_cible: Langue,
    modele: str,
    extracteur: str = "pymupdf4llm",
    resume: bool = False,
    estimation_temps_total: float | None = None,
) -> str:
    """Starts (or resumes) a translation job in a background thread. Returns job_id."""
    output_path = build_output_path(pdf_path)
    existing = charger_etat(output_path)

    if resume and existing and existing.statut in (StatutJob.EN_PAUSE, StatutJob.EN_COURS):
        state = existing
        state.statut = StatutJob.EN_COURS
        _journaliser(state, f"Reprise de la traduction — section {state.derniere_section_completee}/{state.total_sections}")
    else:
        texte = _lire_source_markdown(pdf_path, extracteur)
        chunks = decouper_en_chunks(texte)
        nb_chunks = len(chunks)
        try:
            nb_pages = compter_pages(pdf_path)
        except Exception:
            nb_pages = 0
        total_mots = sum(len(c.split()) for c in chunks)
        state = EtatJob(
            job_id=str(uuid.uuid4()),
            chemin_pdf=pdf_path,
            chemin_sortie=output_path,
            langue_source=langue_source,
            langue_cible=langue_cible,
            modele_ollama=modele,
            statut=StatutJob.EN_COURS,
            derniere_section_completee=0,
            total_sections=nb_chunks,
            total_pages=nb_pages,
            total_mots=total_mots,
            mots_traduits=0,
            temps_debut=time.time(),
            estimation_temps_total_secondes=(
                estimation_temps_total or nb_chunks * SECONDES_PAR_CHUNK_ESTIME
            ),
        )
        _journaliser(state, f"Traduction lancée — {nb_chunks} sections, {nb_pages} pages, {total_mots} mots")
        open(output_path, "w", encoding="utf-8").close()

    texte = _lire_source_markdown(pdf_path, extracteur)
    chunks = decouper_en_chunks(texte)

    enregistrer_job(state.job_id)
    sauvegarder_etat(state)

    thread = threading.Thread(
        target=_executer_traduction,
        args=(state, chunks),
        daemon=True,
    )
    enregistrer_thread(state.job_id, thread)
    thread.start()

    return state.job_id


def lire_statut(job_id: str, pdf_path: str) -> EtatJob | None:
    output_path = build_output_path(pdf_path)
    return charger_etat(output_path)


def check_resume(pdf_path: str) -> EtatJob | None:
    output_path = build_output_path(pdf_path)
    return charger_etat(output_path)
