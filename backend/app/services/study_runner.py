"""
Orchestre un job de fiche d'étude : pour chaque chapitre sélectionné, génère
les points à retenir puis les questions de compréhension (2 étapes/chapitre,
granularité de la progression). Exécuté par le worker unique de la file
d'attente (jamais en même temps qu'une traduction ou une génération audio).

L'état (EtatJobEtude) est la source de vérité, persistée dans un .state.json ;
le Markdown de la fiche est re-rendu depuis l'état après chaque étape, donc
toujours complet et ordonné même après pause, reprise ou ajout de chapitres.
"""

import datetime
import glob as _glob
import os
import re
import time
import uuid

from app.config.settings import ETUDE_CONTEXTE_MAX, ETUDE_CONDENSE_CHUNK
from app.models.schemas import EtatJobEtude, FicheChapitre, StatutJob
from app.services.etude import generer_points, generer_questions, condenser_texte
from app.services.pdf_extractor import chapitres_avec_contenu, decouper_en_chunks
from app.services.job_manager import (
    enregistrer_job,
    est_annule,
    est_en_pause,
    soumettre_travail,
    supprimer_job_registre,
)

SECONDES_PAR_ETAPE_ESTIME = 20  # estimation initiale avant les vraies mesures


class AnnulationDemandee(Exception):
    """Levée quand l'annulation du job est demandée pendant la génération."""


class PauseDemandee(Exception):
    """Levée quand la mise en pause du job est demandée pendant la génération."""


def _base_depuis_source(chemin: str) -> str:
    """Base commune d'un PDF ou d'un fichier _converti_xx.md (même règle que la traduction)."""
    base, _ = os.path.splitext(chemin)
    return re.sub(r"_converti_[a-z]{0,4}$", "", base)


def build_output_path(source_path: str, modele: str = "") -> str:
    base = _base_depuis_source(source_path)
    suffixe = modele[:2] if modele else ""
    return f"{base}_fiche_{suffixe}.md" if suffixe else f"{base}_fiche.md"


def _chemin_etat(chemin_sortie: str) -> str:
    base, _ = os.path.splitext(chemin_sortie)
    return f"{base}.state.json"


def _sauvegarder_etat(etat: EtatJobEtude) -> None:
    with open(_chemin_etat(etat.chemin_sortie), "w", encoding="utf-8") as f:
        f.write(etat.model_dump_json(indent=2))


def _charger_etat(chemin_etat: str) -> EtatJobEtude | None:
    try:
        with open(chemin_etat, "r", encoding="utf-8") as f:
            return EtatJobEtude.model_validate_json(f.read())
    except Exception:
        return None


def lire_statut_etude(chemin_source: str) -> EtatJobEtude | None:
    """Retourne l'état du job de fiche le plus récent pour ce fichier source."""
    base = _base_depuis_source(chemin_source)
    plus_recent = None
    for chemin in _glob.glob(f"{_glob.escape(base)}_fiche*.state.json"):
        etat = _charger_etat(chemin)
        if etat and (plus_recent is None or (etat.temps_debut or 0) > (plus_recent.temps_debut or 0)):
            plus_recent = etat
    return plus_recent


def _horodatage() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def _journaliser(etat: EtatJobEtude, message: str) -> None:
    etat.journal.append(f"[{_horodatage()}] {message}")


# ── Rendu Markdown ────────────────────────────────────────────────────────────

def _rendre_markdown(etat: EtatJobEtude) -> None:
    """Ré-écrit la fiche complète depuis l'état (chapitres triés par index)."""
    nom_source = os.path.basename(etat.chemin_source)
    indices = ", ".join(str(c.index) for c in etat.chapitres if c.etape == "termine")
    lignes = [
        f"<!-- fiche d'étude | modèle : {etat.modele_ollama} | langue : {etat.langue_fiche}"
        f" | chapitres : {indices} -->",
        "",
        f"# Fiche d'étude — {nom_source}",
        "",
    ]
    for chap in sorted(etat.chapitres, key=lambda c: c.index):
        if not chap.points and not chap.questions:
            continue
        lignes += [f"## {chap.titre}", ""]
        if chap.points:
            lignes.append("### Points à retenir")
            lignes.append("")
            lignes += [f"{i + 1}. {p}" for i, p in enumerate(chap.points)]
            lignes.append("")
        if chap.questions:
            lignes.append("### Questions de compréhension")
            lignes.append("")
            for i, q in enumerate(chap.questions):
                lignes += [
                    f"**Q{i + 1}.** {q.question}",
                    "",
                    "<details><summary>Voir la réponse</summary>",
                    "",
                    q.reponse,
                    "",
                    "</details>",
                    "",
                ]
        if chap.etape == "erreur":
            lignes += ["*(chapitre en erreur — fiche incomplète)*", ""]
    with open(etat.chemin_sortie, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes))


# ── Exécution du job ──────────────────────────────────────────────────────────

def _verifier_interruption(etat: EtatJobEtude) -> None:
    if est_annule(etat.job_id):
        raise AnnulationDemandee
    if est_en_pause(etat.job_id):
        raise PauseDemandee


def _texte_pour_generation(chap: FicheChapitre, contenu: str, etat: EtatJobEtude) -> str:
    """Contenu du chapitre, condensé en notes s'il dépasse le contexte du modèle."""
    if len(contenu) <= ETUDE_CONTEXTE_MAX:
        return contenu
    morceaux = decouper_en_chunks(contenu, taille_max=ETUDE_CONDENSE_CHUNK)
    _journaliser(etat, f"Chapitre {chap.index} : long ({len(contenu)} car.) — condensation en {len(morceaux)} partie(s)")
    _sauvegarder_etat(etat)
    notes = []
    for morceau in morceaux:
        _verifier_interruption(etat)
        notes.append(condenser_texte(morceau, etat.modele_ollama, etat.langue_fiche))
    return "\n\n".join(notes)


def _executer_etude(etat: EtatJobEtude, contenus: dict[int, str]) -> None:
    """Exécuté par le worker de la file. Vérifie pause/annulation entre les étapes."""
    if est_annule(etat.job_id):
        etat.statut = StatutJob.ANNULE
        _journaliser(etat, "Job annulé avant démarrage")
        _sauvegarder_etat(etat)
        supprimer_job_registre(etat.job_id)
        return

    etat.statut = StatutJob.EN_COURS
    _journaliser(etat, "Démarrage de la fiche d'étude")
    _sauvegarder_etat(etat)
    temps_debut_session = time.time()

    def _fin_etape() -> None:
        etat.etapes_completees += 1
        elapsed = etat.temps_ecoule_secondes + (time.time() - temps_debut_session)
        if etat.etapes_completees > 0:
            etat.estimation_temps_total_secondes = (elapsed / etat.etapes_completees) * etat.total_etapes

    try:
        for chap in sorted(etat.chapitres, key=lambda c: c.index):
            if chap.etape == "termine":
                continue
            contenu = contenus.get(chap.index, "")
            if not contenu.strip():
                chap.etape = "erreur"
                msg = f"Chapitre {chap.index} ({chap.titre}) : contenu introuvable dans le document"
                etat.erreurs.append(msg)
                _journaliser(etat, msg)
                _sauvegarder_etat(etat)
                continue

            try:
                _verifier_interruption(etat)
                texte = _texte_pour_generation(chap, contenu, etat)

                # Étape 1 : points à retenir (déjà faits si reprise en cours de chapitre)
                if chap.etape != "questions" or not chap.points:
                    chap.etape = "points"
                    _sauvegarder_etat(etat)
                    chap.points = generer_points(texte, etat.modele_ollama, etat.langue_fiche, etat.nb_points)
                    _fin_etape()
                    chap.etape = "questions"
                    _journaliser(etat, f"Chapitre {chap.index} ({chap.titre}) : {len(chap.points)} points générés")
                    _sauvegarder_etat(etat)
                    _rendre_markdown(etat)

                # Étape 2 : questions de compréhension
                _verifier_interruption(etat)
                chap.questions = generer_questions(texte, etat.modele_ollama, etat.langue_fiche, etat.nb_questions)
                _fin_etape()
                chap.etape = "termine"
                _journaliser(etat, f"Chapitre {chap.index} ({chap.titre}) : {len(chap.questions)} questions générées")
            except (AnnulationDemandee, PauseDemandee):
                raise
            except Exception as e:
                chap.etape = "erreur"
                msg = f"Chapitre {chap.index} ({chap.titre}) : {e}"
                etat.erreurs.append(msg)
                _journaliser(etat, msg)

            etat.temps_ecoule_secondes += time.time() - temps_debut_session
            temps_debut_session = time.time()
            _sauvegarder_etat(etat)
            _rendre_markdown(etat)

        en_erreur = [c for c in etat.chapitres if c.etape == "erreur"]
        etat.statut = StatutJob.ERREUR if en_erreur and all(
            c.etape != "termine" for c in etat.chapitres
        ) else StatutJob.TERMINE
        _journaliser(etat, f"Fiche d'étude terminée — {len(en_erreur)} chapitre(s) en erreur")
        _sauvegarder_etat(etat)

    except AnnulationDemandee:
        etat.temps_ecoule_secondes += time.time() - temps_debut_session
        etat.statut = StatutJob.ANNULE
        _journaliser(etat, f"Annulation — étape {etat.etapes_completees}/{etat.total_etapes}")
        _sauvegarder_etat(etat)
    except PauseDemandee:
        etat.temps_ecoule_secondes += time.time() - temps_debut_session
        etat.statut = StatutJob.EN_PAUSE
        _journaliser(etat, f"Mise en pause — étape {etat.etapes_completees}/{etat.total_etapes}")
        _sauvegarder_etat(etat)
    except Exception as e:
        etat.temps_ecoule_secondes += time.time() - temps_debut_session
        etat.statut = StatutJob.ERREUR
        etat.erreurs.append(f"Erreur fatale du job : {e}")
        _journaliser(etat, f"Erreur fatale : {e}")
        _sauvegarder_etat(etat)
    finally:
        supprimer_job_registre(etat.job_id)


# ── Démarrage / reprise ───────────────────────────────────────────────────────

def demarrer_etude(
    source_path: str,
    chapitres_selectionnes: list[int],
    modele: str,
    langue_fiche: str = "français",
    nb_points: int = 5,
    nb_questions: int = 3,
    extracteur: str = "pymupdf4llm",
) -> EtatJobEtude:
    """
    Enfile un job de fiche d'étude et retourne son état initial.
    Si une fiche existe déjà pour ce fichier avec les mêmes options, les
    chapitres déjà terminés sont conservés tels quels (mode ajout/reprise) ;
    seuls les chapitres sélectionnés non terminés sont (re)générés.
    """
    tous_chapitres = chapitres_avec_contenu(source_path, extracteur)
    par_index = {c["index"]: c for c in tous_chapitres}
    introuvables = [i for i in chapitres_selectionnes if i not in par_index]
    if introuvables:
        raise ValueError(f"Chapitre(s) inconnu(s) : {introuvables}")

    chemin_sortie = build_output_path(source_path, modele)
    existant = lire_statut_etude(source_path)
    memes_options = (
        existant is not None
        and existant.chemin_sortie == chemin_sortie
        and existant.langue_fiche == langue_fiche
        and existant.nb_points == nb_points
        and existant.nb_questions == nb_questions
    )
    conserves: dict[int, FicheChapitre] = (
        {c.index: c for c in existant.chapitres if c.etape in ("termine", "questions")}
        if memes_options else {}
    )

    # Chapitres du nouveau job : les terminés conservés + la sélection courante
    indices = sorted(set(chapitres_selectionnes) | set(conserves))
    chapitres = [
        conserves.get(i) or FicheChapitre(index=i, titre=par_index[i]["titre"])
        for i in indices
    ]
    completees = sum(
        2 if c.etape == "termine" else (1 if c.etape == "questions" and c.points else 0)
        for c in chapitres
    )

    etat = EtatJobEtude(
        job_id=str(uuid.uuid4()),
        chemin_source=source_path,
        chemin_sortie=chemin_sortie,
        modele_ollama=modele,
        langue_fiche=langue_fiche,
        nb_points=nb_points,
        nb_questions=nb_questions,
        statut=StatutJob.EN_ATTENTE,
        chapitres=chapitres,
        etapes_completees=completees,
        total_etapes=2 * len(chapitres),
        temps_debut=time.time(),
        estimation_temps_total_secondes=(2 * len(chapitres) - completees) * SECONDES_PAR_ETAPE_ESTIME,
    )
    _journaliser(
        etat,
        f"Fiche d'étude : {len(chapitres)} chapitre(s), dont {len(conserves)} conservé(s) d'un run précédent",
    )
    _journaliser(etat, "Job ajouté à la file d'attente")
    _sauvegarder_etat(etat)
    enregistrer_job(etat.job_id)

    contenus = {i: par_index[i].get("contenu", "") for i in indices if i in par_index}
    soumettre_travail(etat.job_id, lambda: _executer_etude(etat, contenus))
    return etat
