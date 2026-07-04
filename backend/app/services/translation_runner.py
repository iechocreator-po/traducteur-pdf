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
from app.config.settings import CHUNK_TAILLE_MAX, CHAPITRE_SOUS_CHUNK_TAILLE_MAX
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


import re
import glob as _glob


def _base_depuis_source(chemin: str) -> str:
    """Calcule la base commune d'un PDF ou d'un fichier _converti_xx.md."""
    base, _ = os.path.splitext(chemin)
    # Retire le suffixe _converti_xx généré lors de la conversion
    base = re.sub(r"_converti_[a-z]{0,4}$", "", base)
    return base


def build_output_path(source_path: str, modele: str = "") -> str:
    base = _base_depuis_source(source_path)
    suffixe = modele[:2] if modele else ""
    return f"{base}_traduit_{suffixe}.md" if suffixe else f"{base}_traduit.md"


def _trouver_etat_existant(chemin: str) -> "EtatJob | None":
    """Cherche un fichier .state.json correspondant à ce fichier source (PDF ou MD)."""
    base = _base_depuis_source(chemin)
    candidats = _glob.glob(f"{_glob.escape(base)}_traduit*.state.json")
    for chemin_etat in candidats:
        chemin_md = chemin_etat.replace(".state.json", ".md")
        etat = charger_etat(chemin_md)
        if etat:
            return etat
    return None


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


def _lire_source_markdown(source_path: str, extracteur: str) -> str:
    """
    Retourne le contenu Markdown.
    - Si source_path est un .md : le lit directement.
    - Si c'est un PDF : cherche un _converti_*.md existant, sinon re-extrait.
    """
    if source_path.lower().endswith(".md"):
        with open(source_path, "r", encoding="utf-8") as f:
            return f.read()
    base, _ = os.path.splitext(source_path)
    candidats = _glob.glob(f"{_glob.escape(base)}_converti*.md")
    if candidats:
        with open(candidats[0], "r", encoding="utf-8") as f:
            return f.read()
    return extraire_texte(source_path, extracteur)


def _relier_toc_a_markdown(toc: list[dict], chapitres_md: list[dict]) -> list[dict]:
    """
    Relie les entrées de la TOC PDF (signets) aux chapitres Markdown.
    Pour chaque signet, cherche le chapitre Markdown dont le titre correspond
    (correspondance partielle insensible à la casse).
    Les signets sans correspondance Markdown conservent un contenu vide.
    """
    import re as _re

    def normaliser(titre: str) -> str:
        return _re.sub(r"[^\w\s]", "", titre).lower().strip()

    chapitres_relies = []
    for entree in toc:
        titre_toc = normaliser(entree["titre"])
        meilleur = None
        for chap_md in chapitres_md:
            titre_md = normaliser(chap_md["titre"])
            # Correspondance si le titre du signet est contenu dans le titre Markdown ou vice-versa
            if titre_toc in titre_md or titre_md in titre_toc:
                meilleur = chap_md
                break
        chapitres_relies.append({
            "index": entree["index"],
            "titre": entree["titre"],
            "niveau": entree["niveau"],
            "page": entree.get("page"),
            "contenu": meilleur["contenu"] if meilleur else "",
            "ligne_debut": meilleur["ligne_debut"] if meilleur else 0,
            "ligne_fin": meilleur["ligne_fin"] if meilleur else 0,
        })
    return chapitres_relies


def _mettre_a_jour_entete_chapitres(chemin_sortie: str, chapitres_traduits: list[int], state: EtatJob) -> None:
    """Met à jour la ligne d'en-tête du fichier de sortie pour refléter les chapitres traduits."""
    try:
        with open(chemin_sortie, "r", encoding="utf-8") as f:
            contenu = f.read()
        indices_str = ", ".join(str(i) for i in sorted(chapitres_traduits))
        nouvelle_entete = (
            f"<!-- modèle : {state.modele_ollama} | source : {state.langue_source.value}"
            f" → {state.langue_cible.value} | chapitres traduits : {indices_str} -->\n"
        )
        reste = contenu.split("\n", 1)[1] if "\n" in contenu else ""
        with open(chemin_sortie, "w", encoding="utf-8") as f:
            f.write(nouvelle_entete + reste)
    except Exception:
        pass  # Non-critique — state.json fait foi


def _executer_traduction_chapitres(state: EtatJob, chapitres: list[dict]) -> None:
    """Traduit une liste de chapitres et les ajoute au fichier de sortie."""
    output_path = state.chemin_sortie
    temps_debut_session = time.time()

    try:
        for i, chap in enumerate(chapitres):
            _journaliser(state, f"Début chapitre {i + 1}/{state.total_sections} — {chap['titre']}")
            sauvegarder_etat(state)
            try:
                # Sous-découpe les chapitres en petits morceaux pour limiter le temps par appel Ollama
                sous_chunks = decouper_en_chunks(chap["contenu"], taille_max=CHAPITRE_SOUS_CHUNK_TAILLE_MAX)
                parties_traduites = []
                for sous_chunk in sous_chunks:
                    parties_traduites.append(
                        traduire_texte(
                            sous_chunk,
                            state.modele_ollama,
                            state.langue_source.value,
                            state.langue_cible.value,
                        )
                    )
                traduit = "\n\n".join(parties_traduites)
                with open(output_path, "a", encoding="utf-8") as f:
                    f.write(f"\n<!-- === chapitre {chap['index']} : {chap['titre']} === -->\n\n")
                    f.write(traduit + "\n")
            except Exception as e:
                msg = f"Erreur chapitre {chap['index']} ({chap['titre']}) : {e}"
                state.erreurs.append(msg)
                journaliser_erreur(output_path, msg)
                with open(output_path, "a", encoding="utf-8") as f:
                    f.write(f"\n<!-- === chapitre {chap['index']} : {chap['titre']} === -->\n\n")
                    f.write("[ERREUR DE TRADUCTION]\n")

            state.chapitres_traduits.append(chap["index"])
            state.derniere_section_completee = i + 1
            state.mots_traduits += len(chap["contenu"].split())
            elapsed = state.temps_ecoule_secondes + (time.time() - temps_debut_session)
            state.temps_ecoule_secondes = elapsed
            if state.total_sections > 0:
                pace = elapsed / state.derniere_section_completee
                state.estimation_temps_total_secondes = pace * state.total_sections
            _journaliser(state, f"Fin chapitre {i + 1}/{state.total_sections} — {chap['titre']}")
            sauvegarder_etat(state)
            _mettre_a_jour_entete_chapitres(output_path, state.chapitres_traduits, state)

        state.statut = StatutJob.TERMINE
        _journaliser(state, "Traduction des chapitres terminée")
        sauvegarder_etat(state)

    except Exception as e:
        msg = f"Erreur fatale du job : {e}"
        state.erreurs.append(msg)
        state.statut = StatutJob.ERREUR
        _journaliser(state, msg)
        sauvegarder_etat(state)
    finally:
        supprimer_job_registre(state.job_id)


def _demarrer_traduction_chapitres(
    source_path: str,
    output_path: str,
    existing: "EtatJob | None",
    langue_source: Langue,
    langue_cible: Langue,
    modele: str,
    extracteur: str,
    chapitres_selectionnes: list[int],
    estimation_temps_total: float | None,
) -> str:
    """Lance un job de traduction par chapitres (mode ajout). Retourne le job_id."""
    from app.services.pdf_extractor import extraire_toc_pdf, identifier_chapitres as _identifier

    # Utilise les signets PDF si disponibles, sinon fallback Markdown
    toc_pdf = extraire_toc_pdf(source_path) if source_path.lower().endswith(".pdf") else None
    chapitres_md = _identifier(source_path, extracteur)

    if toc_pdf:
        # Relie chaque signet PDF à son contenu dans le Markdown via correspondance de titre
        tous_chapitres = _relier_toc_a_markdown(toc_pdf, chapitres_md)
    else:
        tous_chapitres = chapitres_md
    deja_traduits = set(existing.chapitres_traduits) if existing else set()
    sel_set = set(chapitres_selectionnes)

    # Exclut les sous-chapitres dont un ancêtre sélectionné couvre déjà le contenu.
    # Un chapitre B est couvert par A si A est sélectionné, A précède B et
    # la ligne de début de B tombe dans la plage [ligne_debut, ligne_fin) de A.
    def est_couvert_par_ancetre(chap: dict) -> bool:
        for autre in tous_chapitres:
            if autre["index"] >= chap["index"]:
                break
            if autre["index"] in sel_set and autre["niveau"] < chap["niveau"]:
                if autre["ligne_debut"] < chap["ligne_debut"] < autre["ligne_fin"]:
                    return True
        return False

    chapitres_a_traduire = [
        c for c in tous_chapitres
        if c["index"] in sel_set
        and c["index"] not in deja_traduits
        and not est_couvert_par_ancetre(c)
    ]

    nb = len(chapitres_a_traduire)
    total_mots = sum(len(c["contenu"].split()) for c in chapitres_a_traduire)

    if existing:
        state = existing
        state.job_id = str(uuid.uuid4())
        state.statut = StatutJob.EN_COURS
        state.total_sections = nb
        state.derniere_section_completee = 0
        state.total_mots = total_mots
        state.mots_traduits = 0
        state.temps_debut = time.time()
        state.temps_ecoule_secondes = 0.0
        state.estimation_temps_total_secondes = estimation_temps_total or nb * SECONDES_PAR_CHUNK_ESTIME
        _journaliser(state, f"Ajout de {nb} chapitre(s) — déjà traduits : {sorted(deja_traduits)}")
    else:
        state = EtatJob(
            job_id=str(uuid.uuid4()),
            chemin_pdf=source_path,
            chemin_sortie=output_path,
            langue_source=langue_source,
            langue_cible=langue_cible,
            modele_ollama=modele,
            statut=StatutJob.EN_COURS,
            derniere_section_completee=0,
            total_sections=nb,
            total_pages=0,
            total_mots=total_mots,
            mots_traduits=0,
            temps_debut=time.time(),
            estimation_temps_total_secondes=estimation_temps_total or nb * SECONDES_PAR_CHUNK_ESTIME,
        )

    if not os.path.exists(output_path):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(
                f"<!-- modèle : {modele} | source : {langue_source.value}"
                f" → {langue_cible.value} | chapitres traduits : -->\n"
            )

    _journaliser(
        state,
        f"Traduction de {nb} chapitre(s) : {[c['index'] for c in chapitres_a_traduire]}",
    )
    enregistrer_job(state.job_id)
    sauvegarder_etat(state)

    thread = threading.Thread(
        target=_executer_traduction_chapitres,
        args=(state, chapitres_a_traduire),
        daemon=True,
    )
    enregistrer_thread(state.job_id, thread)
    thread.start()
    return state.job_id


def demarrer_traduction(
    source_path: str,
    langue_source: Langue,
    langue_cible: Langue,
    modele: str,
    extracteur: str = "pymupdf4llm",
    resume: bool = False,
    estimation_temps_total: float | None = None,
    chapitres_selectionnes: list[int] | None = None,
) -> str:
    """Starts (or resumes) a translation job in a background thread. Returns job_id."""
    output_path = build_output_path(source_path, modele)
    existing = _trouver_etat_existant(source_path)

    if chapitres_selectionnes is not None:
        return _demarrer_traduction_chapitres(
            source_path, output_path, existing,
            langue_source, langue_cible, modele, extracteur,
            chapitres_selectionnes, estimation_temps_total,
        )

    if resume and existing and existing.statut in (StatutJob.EN_PAUSE, StatutJob.EN_COURS):
        state = existing
        state.statut = StatutJob.EN_COURS
        _journaliser(state, f"Reprise de la traduction — section {state.derniere_section_completee}/{state.total_sections}")
    else:
        texte = _lire_source_markdown(source_path, extracteur)
        chunks = decouper_en_chunks(texte, taille_max=CHUNK_TAILLE_MAX)
        nb_chunks = len(chunks)
        try:
            nb_pages = compter_pages(source_path) if not source_path.lower().endswith(".md") else 0
        except Exception:
            nb_pages = 0
        total_mots = sum(len(c.split()) for c in chunks)
        state = EtatJob(
            job_id=str(uuid.uuid4()),
            chemin_pdf=source_path,
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
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"<!-- modèle : {modele} | source : {langue_source.value} → {langue_cible.value} -->\n\n")

    texte = _lire_source_markdown(source_path, extracteur)
    chunks = decouper_en_chunks(texte, taille_max=CHUNK_TAILLE_MAX)

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


def lire_statut(job_id: str, chemin: str) -> EtatJob | None:
    return _trouver_etat_existant(chemin)


def check_resume(chemin: str) -> EtatJob | None:
    return _trouver_etat_existant(chemin)
