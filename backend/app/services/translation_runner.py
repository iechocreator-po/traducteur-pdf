"""
Orchestre un job complet de traduction de PDF, exécuté par le worker unique
de la file d'attente (un seul job à la fois pour ne pas saturer Ollama).
Supporte pause/reprise et annulation via des flags mémoire vérifiés entre chunks.
"""

import datetime
import glob as _glob
import os
import re
import time
import uuid

from app.models.schemas import EtatJob, StatutJob, Langue
from app.config.settings import (
    CHUNK_TAILLE_MAX,
    CHAPITRE_SOUS_CHUNK_TAILLE_MAX,
    RATIO_TRADUCTION_SUSPECT,
    CONTROLE_QUALITE_LONGUEUR_MIN,
)
from app.services.pdf_extractor import extraire_texte, decouper_en_chunks, compter_pages, extraire_urls
from app.services.translator import traduire_texte
from app.services import cache_traduction, glossaire
from app.services.job_manager import (
    sauvegarder_etat,
    charger_etat,
    enregistrer_job,
    est_en_pause,
    est_annule,
    soumettre_travail,
    supprimer_job_registre,
    journaliser_erreur,
)

SECONDES_PAR_CHUNK_ESTIME = 10  # used for initial estimate before real timing data


class AnnulationDemandee(Exception):
    """Levée quand l'annulation du job est demandée pendant la traduction."""


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


def _traduire_avec_controle(texte: str, state: EtatJob, cache: dict[str, str], etiquette: str) -> str:
    """
    Traduit un texte avec cache, glossaire et contrôle qualité anti-résumé.
    - Si le texte est déjà dans le cache (même modèle/langues/glossaire), le retourne.
    - Les termes du glossaire présents dans le texte sont imposés au traducteur,
      puis leur présence est vérifiée dans la traduction (avertissement si perdus).
    - Si la traduction est trop courte (ratio < RATIO_TRADUCTION_SUSPECT), le modèle
      a probablement résumé : une seconde tentative est faite, et un avertissement
      est ajouté au job si le ratio reste suspect (résultat alors non mis en cache,
      pour qu'un re-run retente la section).
    """
    termes = glossaire.termes_presents(texte)
    cle = cache_traduction.calculer_cle(
        texte, state.modele_ollama, state.langue_source.value, state.langue_cible.value,
        extra=",".join(termes),
    )
    if cle in cache:
        _journaliser(state, f"{etiquette} : reprise depuis le cache")
        return cache[cle]

    traduit = traduire_texte(
        texte, state.modele_ollama, state.langue_source.value, state.langue_cible.value,
        termes_a_conserver=termes,
    )
    ratio = len(traduit) / max(len(texte), 1)

    if len(texte) >= CONTROLE_QUALITE_LONGUEUR_MIN and ratio < RATIO_TRADUCTION_SUSPECT:
        _journaliser(state, f"{etiquette} : traduction suspecte (ratio {ratio:.2f}) — nouvelle tentative")
        nouvelle = traduire_texte(
            texte, state.modele_ollama, state.langue_source.value, state.langue_cible.value,
            termes_a_conserver=termes,
        )
        nouveau_ratio = len(nouvelle) / max(len(texte), 1)
        if nouveau_ratio > ratio:
            traduit, ratio = nouvelle, nouveau_ratio
        if ratio < RATIO_TRADUCTION_SUSPECT:
            avertissement = (
                f"{etiquette} : traduction possiblement résumée "
                f"(ratio longueur {ratio:.2f} < {RATIO_TRADUCTION_SUSPECT})"
            )
            state.avertissements.append(avertissement)
            journaliser_erreur(state.chemin_sortie, avertissement)
            return traduit

    perdus = glossaire.termes_perdus(termes, traduit)
    if perdus:
        avertissement = (
            f"{etiquette} : terme(s) du glossaire absent(s) de la traduction : "
            f"{', '.join(perdus)}"
        )
        state.avertissements.append(avertissement)
        journaliser_erreur(state.chemin_sortie, avertissement)
        return traduit  # non mis en cache — un re-run retentera la section

    cache[cle] = traduit
    return traduit


TITRE_ANNEXE_LIENS = "## Liens du document original"


def _annexer_liens_source(state: EtatJob) -> None:
    """
    Ajoute en fin de fichier traduit la liste des liens cliquables du PDF source
    (annotations extraites par pdfplumber — le texte traduit peut les avoir perdus).
    Sans effet si la source n'est pas un PDF ou si l'annexe existe déjà (re-run).
    """
    source = state.chemin_pdf
    if not source.lower().endswith(".pdf"):
        return
    try:
        with open(state.chemin_sortie, "r", encoding="utf-8") as f:
            if TITRE_ANNEXE_LIENS in f.read():
                return
        urls = extraire_urls(source)
    except Exception:
        return  # Non critique — la traduction reste valide sans l'annexe

    uniques = list(dict.fromkeys(urls))
    if not uniques:
        return
    with open(state.chemin_sortie, "a", encoding="utf-8") as f:
        f.write(f"\n\n---\n\n{TITRE_ANNEXE_LIENS}\n\n")
        for url in uniques:
            f.write(f"- <{url}>\n")
    _journaliser(state, f"{len(uniques)} lien(s) du PDF source ajoutés en annexe")


def _executer_traduction(state: EtatJob, chunks: list[str]) -> None:
    """Exécuté par le worker de la file. Vérifie pause et annulation entre chaque chunk."""
    output_path = state.chemin_sortie

    if est_annule(state.job_id):
        state.statut = StatutJob.ANNULE
        _journaliser(state, "Job annulé avant démarrage")
        sauvegarder_etat(state)
        supprimer_job_registre(state.job_id)
        return

    state.statut = StatutJob.EN_COURS
    _journaliser(state, "Démarrage de la traduction")
    sauvegarder_etat(state)

    start_index = state.derniere_section_completee
    temps_debut_session = time.time()
    cache = cache_traduction.charger_cache(output_path)

    try:
        with open(output_path, "a", encoding="utf-8") as f:
            for i, chunk in enumerate(chunks):
                if i < start_index:
                    continue

                # Vérifie le flag d'annulation avant chaque chunk
                if est_annule(state.job_id):
                    state.temps_ecoule_secondes += time.time() - temps_debut_session
                    state.statut = StatutJob.ANNULE
                    _journaliser(state, f"Annulation — section {i}/{state.total_sections}")
                    sauvegarder_etat(state)
                    return

                # Vérifie le flag de pause avant chaque chunk
                if est_en_pause(state.job_id):
                    elapsed = time.time() - temps_debut_session
                    state.temps_ecoule_secondes += elapsed
                    state.statut = StatutJob.EN_PAUSE
                    _journaliser(state, f"Mise en pause — section {i}/{state.total_sections}")
                    sauvegarder_etat(state)
                    supprimer_job_registre(state.job_id)
                    return

                try:
                    translated = _traduire_avec_controle(
                        chunk, state, cache, f"Section {i + 1}/{state.total_sections}"
                    )
                    f.write(translated + "\n\n")
                    cache_traduction.sauvegarder_cache(output_path, cache)
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

        _annexer_liens_source(state)
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

    if est_annule(state.job_id):
        state.statut = StatutJob.ANNULE
        _journaliser(state, "Job annulé avant démarrage")
        sauvegarder_etat(state)
        supprimer_job_registre(state.job_id)
        return

    state.statut = StatutJob.EN_COURS
    _journaliser(state, "Démarrage de la traduction par chapitres")
    sauvegarder_etat(state)

    temps_debut_session = time.time()
    cache = cache_traduction.charger_cache(output_path)

    try:
        for i, chap in enumerate(chapitres):
            if est_annule(state.job_id):
                raise AnnulationDemandee
            _journaliser(state, f"Début chapitre {i + 1}/{state.total_sections} — {chap['titre']}")
            sauvegarder_etat(state)
            try:
                # Sous-découpe les chapitres en petits morceaux pour limiter le temps par appel Ollama
                sous_chunks = decouper_en_chunks(chap["contenu"], taille_max=CHAPITRE_SOUS_CHUNK_TAILLE_MAX)
                parties_traduites = []
                for j, sous_chunk in enumerate(sous_chunks):
                    if est_annule(state.job_id):
                        raise AnnulationDemandee
                    parties_traduites.append(
                        _traduire_avec_controle(
                            sous_chunk,
                            state,
                            cache,
                            f"Chapitre {chap['index']} ({chap['titre']}), partie {j + 1}/{len(sous_chunks)}",
                        )
                    )
                    cache_traduction.sauvegarder_cache(output_path, cache)
                traduit = "\n\n".join(parties_traduites)
                with open(output_path, "a", encoding="utf-8") as f:
                    f.write(f"\n<!-- === chapitre {chap['index']} : {chap['titre']} === -->\n\n")
                    f.write(traduit + "\n")
            except AnnulationDemandee:
                raise
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

        _annexer_liens_source(state)
        state.statut = StatutJob.TERMINE
        _journaliser(state, "Traduction des chapitres terminée")
        sauvegarder_etat(state)

    except AnnulationDemandee:
        state.temps_ecoule_secondes += time.time() - temps_debut_session
        state.statut = StatutJob.ANNULE
        _journaliser(state, f"Annulation — chapitre {state.derniere_section_completee}/{state.total_sections}")
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
    from app.services.pdf_extractor import chapitres_avec_contenu

    # Signets PDF reliés au Markdown si disponibles, sinon titres Markdown
    tous_chapitres = chapitres_avec_contenu(source_path, extracteur)
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
        state.statut = StatutJob.EN_ATTENTE
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
            statut=StatutJob.EN_ATTENTE,
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
    _journaliser(state, "Job ajouté à la file d'attente")
    enregistrer_job(state.job_id)
    sauvegarder_etat(state)

    soumettre_travail(
        state.job_id,
        lambda: _executer_traduction_chapitres(state, chapitres_a_traduire),
    )
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
        state.statut = StatutJob.EN_ATTENTE
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
            statut=StatutJob.EN_ATTENTE,
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

    _journaliser(state, "Job ajouté à la file d'attente")
    enregistrer_job(state.job_id)
    sauvegarder_etat(state)

    soumettre_travail(state.job_id, lambda: _executer_traduction(state, chunks))

    return state.job_id


def lire_statut(job_id: str, chemin: str) -> EtatJob | None:
    return _trouver_etat_existant(chemin)


def check_resume(chemin: str) -> EtatJob | None:
    return _trouver_etat_existant(chemin)
