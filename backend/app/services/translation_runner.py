"""
Orchestre un job complet de traduction de PDF/Markdown, exécuté par le worker
unique de la file d'attente (un seul job à la fois pour ne pas saturer Ollama).

Moteur UNIFIÉ (2026-07-18) : il n'existe plus qu'un seul chemin d'exécution.
Un document est TOUJOURS traité comme une liste ordonnée de chapitres :
- s'il a des titres `#`, ce sont ses chapitres (signets PDF ou titres Markdown) ;
- s'il n'en a pas, on fabrique un **chapitre implicite** « Document entier »
  couvrant tout le texte.

Propriétés clés (voir aussi le CLAUDE.md du produit) :
- **Progression au grain du sous-morceau** : chaque chapitre est sous-découpé,
  et `derniere_section_completee` avance à CHAQUE sous-morceau — la barre ne
  reste jamais figée pendant un gros chapitre.
- **Chapitre = unité atomique d'écriture** : un chapitre n'est écrit dans la
  sortie que si TOUS ses sous-morceaux réussissent ; sinon rien n'est écrit et
  le chapitre reste re-sélectionnable (aucun trou au milieu du fichier).
- **Reprise = rejeu à cache chaud** : le cache (`cache_traduction.py`) est
  indexé par CONTENU et ne contient jamais les sous-morceaux échoués. Rejouer
  un chapitre fait revenir instantanément le bon travail et n'envoie chez
  Ollama que les trous. La reprise « additive » (poursuivre de nouveaux
  chapitres une autre fois) exclut simplement ceux déjà dans `chapitres_traduits`.
- **Statut honnête** : un job avec ≥1 chapitre en échec finit `erreur`, jamais
  `termine`. `OllamaIndisponible` arrête proprement le job en `erreur` (donc
  reprenable), sans brûler les chapitres restants en placeholders.
"""

import datetime
import glob as _glob
import os
import re
import time
import uuid

from app.models.schemas import EtatJob, StatutJob, Langue
from app.config.settings import (
    CHAPITRE_SOUS_CHUNK_TAILLE_MAX,
    RATIO_TRADUCTION_SUSPECT,
    CONTROLE_QUALITE_LONGUEUR_MIN,
)
from app.services.pdf_extractor import (
    extraire_texte,
    decouper_en_chunks,
    compter_pages,
    extraire_urls,
    chapitres_avec_contenu,
)
from app.services.translator import (
    traduire_texte,
    OllamaIndisponible,
    AppelInterrompu,
)
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

SECONDES_PAR_CHUNK_ESTIME = 10  # estimation initiale avant d'avoir des mesures réelles

# Statuts depuis lesquels une reprise (resume=true) est acceptée. ANNULE et
# ERREUR y figurent : une sortie propre (aucun placeholder) jusqu'aux chapitres
# terminés permet de reprendre là où on en était.
STATUTS_REPRENABLES = (
    StatutJob.EN_PAUSE,
    StatutJob.EN_COURS,
    StatutJob.ERREUR,
    StatutJob.ANNULE,
)


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


def _interruption_pour(state: EtatJob):
    """Callback consulté par le traducteur pendant le backoff — le retry peut
    durer des minutes, pendant lesquelles Pause/Annuler doivent rester vivants."""
    return lambda: est_annule(state.job_id) or est_en_pause(state.job_id)


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

    interruption = _interruption_pour(state)
    traduit = traduire_texte(
        texte, state.modele_ollama, state.langue_source.value, state.langue_cible.value,
        termes_a_conserver=termes, interruption=interruption,
    )
    ratio = len(traduit) / max(len(texte), 1)

    if len(texte) >= CONTROLE_QUALITE_LONGUEUR_MIN and ratio < RATIO_TRADUCTION_SUSPECT:
        _journaliser(state, f"{etiquette} : traduction suspecte (ratio {ratio:.2f}) — nouvelle tentative")
        nouvelle = traduire_texte(
            texte, state.modele_ollama, state.langue_source.value, state.langue_cible.value,
            termes_a_conserver=termes, interruption=interruption,
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


# ── Construction des chapitres (structure réelle ou chapitre implicite) ───────

TITRE_IMPLICITE = "Document entier"


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


def _chapitres_ou_implicite(source_path: str, extracteur: str) -> tuple[list[dict], bool]:
    """
    Retourne (chapitres, implicite).
    - Si le document a des titres → ses chapitres, implicite=False.
    - Sinon → un unique chapitre « Document entier » couvrant tout le texte,
      implicite=True (on n'écrira alors pas de marqueur `=== chapitre === `).
    """
    chapitres = chapitres_avec_contenu(source_path, extracteur)
    if chapitres:
        return chapitres, False
    texte = _lire_source_markdown(source_path, extracteur)
    implicite = [{
        "index": 0,
        "titre": TITRE_IMPLICITE,
        "niveau": 0,
        "contenu": texte,
        "ligne_debut": 0,
        "ligne_fin": len(texte.splitlines()),
    }]
    return implicite, True


def _est_couvert_par_ancetre(chap: dict, tous: list[dict], selection: set[int]) -> bool:
    """
    Un chapitre B est « couvert » par un ancêtre A sélectionné si A précède B,
    A est d'un niveau supérieur (moins de #) et la ligne de début de B tombe dans
    la plage [ligne_debut, ligne_fin) de A. Le contenu d'un chapitre incluant
    déjà celui de ses sous-chapitres, cela évite de traduire deux fois le même
    texte quand parent ET enfant sont sélectionnés.
    """
    for autre in tous:
        if autre["index"] >= chap["index"]:
            break
        if autre["index"] in selection and autre["niveau"] < chap["niveau"]:
            if autre["ligne_debut"] < chap["ligne_debut"] < autre["ligne_fin"]:
                return True
    return False


def _nb_sous_chunks(chap: dict) -> int:
    return len(decouper_en_chunks(chap["contenu"], taille_max=CHAPITRE_SOUS_CHUNK_TAILLE_MAX))


# ── En-tête du fichier de sortie ──────────────────────────────────────────────

def _ecrire_entete(output_path: str, modele: str, langue_source: Langue, langue_cible: Langue) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(
            f"<!-- modèle : {modele} | source : {langue_source.value}"
            f" → {langue_cible.value} | chapitres traduits : -->\n"
        )


# Marqueur des placeholders écrits par l'ANCIEN moteur « sections ». Le moteur
# unifié n'en écrit jamais ; on le garde uniquement pour DÉTECTER les sorties
# legacy trouées lors d'une reprise (voir _a_des_trous).
MARQUEUR_ECHEC = "[ERREUR DE TRADUCTION"


def _a_des_trous(state: EtatJob, output_path: str) -> bool:
    """
    Vrai si la sortie existante contient des brèches à recoudre : chapitres/sections
    en échec dans l'état, ou placeholder legacy dans le fichier. Dans ce cas la
    reprise réécrit tout DANS L'ORDRE (rejeu à cache chaud) plutôt que d'ajouter en
    fin de fichier — sinon un chapitre du milieu recousu se retrouverait à la fin.
    """
    if state.chapitres_echoues or state.sections_echouees:
        return True
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            return MARQUEUR_ECHEC in f.read()
    except OSError:
        return False


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


# ── Moteur unifié ─────────────────────────────────────────────────────────────

def _executer_traduction(state: EtatJob, chapitres: list[dict], implicite: bool) -> None:
    """
    Traduit la liste ordonnée `chapitres`, en append dans le fichier de sortie.
    Progression au grain du sous-morceau, un chapitre écrit seulement s'il réussit
    entièrement. Vérifie pause/annulation avant chaque sous-morceau.
    """
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

    session_debut = time.time()
    base_ecoule = state.temps_ecoule_secondes  # figé pendant la boucle (pas de O(n²))
    cache = cache_traduction.charger_cache(output_path)
    unites_faites = 0  # sous-morceaux comptabilisés (chapitres déjà parcourus ce run)

    def _maj_eta() -> None:
        elapsed = base_ecoule + (time.time() - session_debut)
        if state.derniere_section_completee > 0:
            pace = elapsed / state.derniere_section_completee
            state.estimation_temps_total_secondes = pace * max(state.total_sections, 1)

    try:
        for chap in chapitres:
            if est_annule(state.job_id):
                raise AnnulationDemandee
            sous_chunks = decouper_en_chunks(chap["contenu"], taille_max=CHAPITRE_SOUS_CHUNK_TAILLE_MAX)
            nb_sc = len(sous_chunks)
            _journaliser(state, f"Début chapitre {chap['index']} — {chap['titre']} ({nb_sc} morceau·x)")
            sauvegarder_etat(state)

            parties = []
            chapitre_ok = True
            for j, sous_chunk in enumerate(sous_chunks):
                # Pause / annulation vérifiées AVANT chaque sous-morceau (un seul endroit).
                if est_annule(state.job_id):
                    raise AnnulationDemandee
                if est_en_pause(state.job_id):
                    state.derniere_section_completee = unites_faites + j
                    state.temps_ecoule_secondes = base_ecoule + (time.time() - session_debut)
                    state.statut = StatutJob.EN_PAUSE
                    _journaliser(state, f"Mise en pause — chapitre {chap['index']}, morceau {j + 1}/{nb_sc}")
                    sauvegarder_etat(state)
                    supprimer_job_registre(state.job_id)
                    return

                try:
                    partie = _traduire_avec_controle(
                        sous_chunk, state, cache,
                        f"Chapitre {chap['index']} ({chap['titre']}), partie {j + 1}/{nb_sc}",
                    )
                    parties.append(partie)
                    cache_traduction.sauvegarder_cache(output_path, cache)
                except (OllamaIndisponible, AppelInterrompu):
                    # Panne réseau/Ollama ou interruption pendant le backoff : on
                    # ARRÊTE le job ici. Ne rien écrire pour ce chapitre — la
                    # reprise le rejouera intégralement à cache chaud.
                    raise
                except Exception as e:
                    msg = f"Erreur chapitre {chap['index']} ({chap['titre']}), partie {j + 1} : {e}"
                    state.erreurs.append(msg)
                    journaliser_erreur(output_path, msg)
                    if chap["index"] not in state.chapitres_echoues:
                        state.chapitres_echoues.append(chap["index"])
                    chapitre_ok = False
                    break  # on n'écrit pas un chapitre troué ; il reste re-sélectionnable

                # Succès du sous-morceau → progression fine
                state.derniere_section_completee = unites_faites + j + 1
                state.mots_traduits += len(sous_chunk.split())
                _maj_eta()
                sauvegarder_etat(state)

            if chapitre_ok:
                traduit = "\n\n".join(parties)
                with open(output_path, "a", encoding="utf-8") as f:
                    if not implicite:
                        f.write(f"\n<!-- === chapitre {chap['index']} : {chap['titre']} === -->\n\n")
                    f.write(traduit + "\n")
                if chap["index"] not in state.chapitres_traduits:
                    state.chapitres_traduits.append(chap["index"])
                # Un chapitre qui réussit après un échec antérieur quitte la liste des échoués.
                if chap["index"] in state.chapitres_echoues:
                    state.chapitres_echoues.remove(chap["index"])

            unites_faites += nb_sc
            state.derniere_section_completee = unites_faites  # aligne la barre après le chapitre
            state.temps_ecoule_secondes = base_ecoule + (time.time() - session_debut)
            _journaliser(state, f"Fin chapitre {chap['index']} — {chap['titre']}")
            if not implicite:
                _mettre_a_jour_entete_chapitres(output_path, state.chapitres_traduits, state)
            sauvegarder_etat(state)

        _annexer_liens_source(state)
        state.temps_ecoule_secondes = base_ecoule + (time.time() - session_debut)
        # Une seule brèche disqualifie le job : `termine` doit rester une promesse
        # fiable. On bascule sur chapitres_echoues (jamais erreurs/avertissements,
        # qui portent aussi les avertissements qualité inoffensifs).
        if state.chapitres_echoues:
            state.statut = StatutJob.ERREUR
            _journaliser(
                state,
                f"Traduction terminée avec {len(state.chapitres_echoues)} chapitre(s) en échec : "
                f"{sorted(state.chapitres_echoues)} — relancer avec « Reprendre » pour recoudre",
            )
        else:
            state.statut = StatutJob.TERMINE
            _journaliser(state, "Traduction terminée")
        sauvegarder_etat(state)

    except (AnnulationDemandee, AppelInterrompu):
        state.temps_ecoule_secondes = base_ecoule + (time.time() - session_debut)
        state.statut = StatutJob.EN_PAUSE if est_en_pause(state.job_id) else StatutJob.ANNULE
        _journaliser(
            state,
            f"Interruption — {state.derniere_section_completee}/{state.total_sections} morceaux",
        )
        sauvegarder_etat(state)
    except OllamaIndisponible as e:
        # Panne fatale d'Ollama : job en erreur (donc reprenable), sans placeholder.
        state.temps_ecoule_secondes = base_ecoule + (time.time() - session_debut)
        state.statut = StatutJob.ERREUR
        state.erreurs.append(f"Ollama indisponible : {e}")
        _journaliser(state, f"Ollama indisponible — reprise possible : {e}")
        sauvegarder_etat(state)
    except Exception as e:
        msg = f"Erreur fatale du job : {e}"
        state.erreurs.append(msg)
        journaliser_erreur(output_path, msg)
        state.statut = StatutJob.ERREUR
        state.temps_ecoule_secondes = base_ecoule + (time.time() - session_debut)
        _journaliser(state, f"Erreur fatale : {e}")
        sauvegarder_etat(state)
    finally:
        supprimer_job_registre(state.job_id)


# ── Point d'entrée : démarrer ou reprendre ────────────────────────────────────

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
    """
    Démarre (ou reprend) un job de traduction en arrière-plan. Retourne le job_id.

    Trois modes, un seul moteur :
    - **Reprise** (`resume=True`, état existant reprenable) : termine la sélection
      d'origine (`chapitres_selectionnes` persisté), en excluant les chapitres déjà
      faits. Rejeu à cache chaud pour recoudre les trous.
    - **Ajout** (état existant, `chapitres_selectionnes` fourni, pas de resume) :
      poursuit avec de nouveaux chapitres, en append.
    - **Neuf** (sinon) : traduit la sélection donnée, ou tout le document
      (`chapitres_selectionnes=None` → tous les chapitres / le chapitre implicite).
    """
    output_path = build_output_path(source_path, modele)

    # Registre de la Bibliothèque — le statut réel reste lu depuis le .state.json
    from app.services.bibliotheque import enregistrer_document
    enregistrer_document(
        chemin_source=source_path,
        chemin_sortie=output_path,
        modele=modele,
        langue_source=langue_source.value,
        langue_cible=langue_cible.value,
    )

    tous_chapitres, implicite = _chapitres_ou_implicite(source_path, extracteur)
    tous_index = {c["index"] for c in tous_chapitres}
    existing = _trouver_etat_existant(source_path)

    reprendre = bool(resume and existing and existing.statut in STATUTS_REPRENABLES)
    ajout = bool(
        not resume and existing and chapitres_selectionnes is not None
        and existing.chapitres_traduits
    )

    if reprendre:
        state = existing
        run_selection = set(state.chapitres_selectionnes or tous_index)
        state.job_id = str(uuid.uuid4())
        state.statut = StatutJob.EN_ATTENTE
        if _a_des_trous(state, output_path):
            # Trous à recoudre → on réécrit tout DANS L'ORDRE. Le cache chaud
            # (indexé par contenu) fait revenir les chapitres déjà bons
            # instantanément ; seuls les trous repartent chez Ollama.
            _ecrire_entete(output_path, modele, langue_source, langue_cible)
            state.chapitres_traduits = []
            state.chapitres_echoues = []
            state.sections_echouees = []
            _journaliser(state, "Rejeu à cache chaud — recoud les trous, dans l'ordre")
        elif state.chapitres_traduits:
            # Sortie propre (chapitres déjà faits, aucun trou) → on poursuit en append.
            _journaliser(
                state,
                f"Reprise additive — déjà faits : {sorted(state.chapitres_traduits)}",
            )
        else:
            # Rien de fait et pas de trou (ex. pause avant le 1er chapitre) →
            # redémarrage propre de la sélection.
            _ecrire_entete(output_path, modele, langue_source, langue_cible)
            _journaliser(state, "Reprise — redémarrage propre de la sélection")

    elif ajout:
        state = existing
        run_selection = set(chapitres_selectionnes)
        # Mémorise l'union pour qu'une future reprise connaisse toute la portée voulue.
        state.chapitres_selectionnes = sorted(
            set(state.chapitres_selectionnes or []) | run_selection
        )
        state.job_id = str(uuid.uuid4())
        state.statut = StatutJob.EN_ATTENTE
        state.temps_ecoule_secondes = 0.0  # timing propre au nouveau run
        _journaliser(
            state,
            f"Ajout de chapitres {sorted(run_selection)} — déjà faits : {sorted(state.chapitres_traduits)}",
        )
        if not os.path.exists(output_path):
            _ecrire_entete(output_path, modele, langue_source, langue_cible)

    else:
        run_selection = (
            set(chapitres_selectionnes) if chapitres_selectionnes is not None else set(tous_index)
        )
        try:
            nb_pages = compter_pages(source_path) if not source_path.lower().endswith(".md") else 0
        except Exception:
            nb_pages = 0
        state = EtatJob(
            job_id=str(uuid.uuid4()),
            chemin_pdf=source_path,
            chemin_sortie=output_path,
            langue_source=langue_source,
            langue_cible=langue_cible,
            modele_ollama=modele,
            statut=StatutJob.EN_ATTENTE,
            derniere_section_completee=0,
            total_sections=0,
            total_pages=nb_pages,
            total_mots=0,
            mots_traduits=0,
            temps_debut=time.time(),
            chapitres_selectionnes=sorted(run_selection),
        )
        _ecrire_entete(output_path, modele, langue_source, langue_cible)

    # Chapitres réellement à traduire ce run : dans la sélection, pas déjà faits,
    # et non couverts par un ancêtre sélectionné (évite de traduire deux fois).
    deja = set(state.chapitres_traduits)
    chapitres_a_traduire = [
        c for c in tous_chapitres
        if c["index"] in run_selection
        and c["index"] not in deja
        and not _est_couvert_par_ancetre(c, tous_chapitres, run_selection)
    ]

    total_sc = sum(_nb_sous_chunks(c) for c in chapitres_a_traduire)
    state.total_sections = total_sc
    state.derniere_section_completee = 0
    state.total_mots = sum(len(c["contenu"].split()) for c in chapitres_a_traduire)
    state.mots_traduits = 0
    state.temps_debut = time.time()
    state.estimation_temps_total_secondes = (
        estimation_temps_total or total_sc * SECONDES_PAR_CHUNK_ESTIME
    )

    _journaliser(
        state,
        f"Traduction de {len(chapitres_a_traduire)} chapitre(s) "
        f"{[c['index'] for c in chapitres_a_traduire]} — {total_sc} morceau·x",
    )
    _journaliser(state, "Job ajouté à la file d'attente")
    enregistrer_job(state.job_id)
    sauvegarder_etat(state)

    soumettre_travail(
        state.job_id,
        lambda: _executer_traduction(state, chapitres_a_traduire, implicite),
    )
    return state.job_id


# ── Récupération au démarrage ─────────────────────────────────────────────────

def recuperer_jobs_interrompus() -> int:
    """
    Appelée au démarrage du backend. Le registre mémoire des jobs est vide après
    un redémarrage : tout job dont le .state.json est resté `en_cours` a donc été
    coupé net (serveur tué/crashé en plein run). On le bascule `en_pause` pour
    qu'il redevienne visible et reprenable depuis « Nouveau document ».
    Retourne le nombre de jobs récupérés.
    """
    from app.services.bibliotheque import lister_documents

    recuperes = 0
    try:
        documents = lister_documents()
    except Exception:
        return 0

    for doc in documents:
        if doc.get("statut") != StatutJob.EN_COURS.value:
            continue
        etat = charger_etat(doc["chemin_sortie"])
        if etat is None or etat.statut != StatutJob.EN_COURS:
            continue
        etat.statut = StatutJob.EN_PAUSE
        _journaliser(etat, "Interrompu par un arrêt du serveur — reprise possible")
        sauvegarder_etat(etat)
        recuperes += 1
    return recuperes


def lire_statut(job_id: str, chemin: str) -> EtatJob | None:
    return _trouver_etat_existant(chemin)


def check_resume(chemin: str) -> EtatJob | None:
    return _trouver_etat_existant(chemin)
