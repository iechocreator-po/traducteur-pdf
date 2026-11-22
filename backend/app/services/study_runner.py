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
from app.services import metriques, stats
from app.services.persistance import ecrire_texte_atomique
from app.models.schemas import EtatJobEtude, FicheChapitre, StatutJob
from app.services.etude import (
    calculer_nb_points,
    calculer_nb_questions,
    condenser_texte,
    consolider_points,
    generer_points,
    generer_questions,
)
from app.services.pdf_extractor import chapitres_avec_contenu, decouper_en_chunks
from app.services.job_manager import (
    enregistrer_job,
    est_annule,
    est_en_pause,
    soumettre_travail,
    supprimer_job_registre,
)

# Tâches d'un chapitre (FicheChapitre.a_faire), toujours faites dans cet ordre : le quiz « sections » se bâtit sur
# les points quand ils existent.
TACHE_POINTS = "points"
TACHE_QUIZ = "quiz"


class AnnulationDemandee(Exception):
    """Levée quand l'annulation du job est demandée pendant la génération."""


class PauseDemandee(Exception):
    """Levée quand la mise en pause du job est demandée pendant la génération."""


def _base_depuis_source(chemin: str) -> str:
    """Base commune d'un PDF ou d'un fichier _converti_xx.md (même règle que la traduction)."""
    base, _ = os.path.splitext(chemin)
    return re.sub(r"_converti_[a-z]{0,4}$", "", base)


# Stratégies de génération de fiche. La valeur est écrite dans le NOM DU FICHIER
# et dans l'état : deux fiches du même document produites autrement doivent
# pouvoir coexister, sinon on ne peut pas les comparer.
STRATEGIE_CONDENSATION = "condensation"
STRATEGIE_SECTIONS = "sections"
STRATEGIES = (STRATEGIE_CONDENSATION, STRATEGIE_SECTIONS)
# Défaut passé à « sections » le 19/8, après comparaison sur Chapter 9 : mêmes
# 12 points mais couvrant TOUT le chapitre (condensation restait bloquée sur les
# 20 premiers pour cent), questions typées et concrètes, et 203 s contre 400 s.
# « condensation » reste disponible et inchangée.
STRATEGIE_PAR_DEFAUT = STRATEGIE_SECTIONS


def build_output_path(
    source_path: str, modele: str = "", strategie: str = STRATEGIE_PAR_DEFAUT
) -> str:
    """
    Chemin de la fiche pour ce couple (modèle, stratégie).

    ⚠️ Le suffixe était `modele[:2]` — DEUX caractères, exactement le défaut
    corrigé côté traduction par la feature 338 : `qwen2.5` et `qwen3` donnent
    tous deux « qw », donc la même fiche. Le suffixe est désormais le slug
    complet du modèle, suivi de la stratégie.

    ⚠️ CONSULTE LE DISQUE, à dessein : une fiche produite avant ce changement
    garde son nom historique (`_fiche_ll.md`). Sans ce repli elle deviendrait
    orpheline et serait regénérée de zéro.
    """
    from app.services.translation_runner import suffixe_modele

    base = _base_depuis_source(source_path)
    if not modele:
        return f"{base}_fiche.md"

    nouveau = f"{base}_fiche_{suffixe_modele(modele)}_{strategie}.md"
    if os.path.exists(nouveau):
        return nouveau

    # Repli lié à CONDENSATION, jamais à « la stratégie par défaut du moment ».
    # Les fiches produites avant que la stratégie n'entre dans le nom l'ont
    # forcément été par condensation — c'était la seule qui existait. Ancrer ce
    # repli sur STRATEGIE_PAR_DEFAUT ferait renvoyer une de ces fiches à qui
    # demande « sections » le jour où le défaut change, c'est-à-dire aujourd'hui.
    if strategie == STRATEGIE_CONDENSATION:
        sans_strategie = f"{base}_fiche_{suffixe_modele(modele)}.md"
        if os.path.exists(sans_strategie):
            return sans_strategie
        # Repli 2 : nom historique à deux caractères.
        ancien = f"{base}_fiche_{modele[:2]}.md"
        if os.path.exists(ancien):
            return ancien
    return nouveau


def _chemin_etat(chemin_sortie: str) -> str:
    base, _ = os.path.splitext(chemin_sortie)
    return f"{base}.state.json"


def _sauvegarder_etat(etat: EtatJobEtude) -> None:
    """Écriture atomique — même exposition que la traduction (principe cible ③)."""
    ecrire_texte_atomique(
        _chemin_etat(etat.chemin_sortie), etat.model_dump_json(indent=2)
    )


def _charger_etat(chemin_etat: str) -> EtatJobEtude | None:
    try:
        with open(chemin_etat, "r", encoding="utf-8") as f:
            return EtatJobEtude.model_validate_json(f.read())
    except Exception:
        return None


def lire_statut_etude(
    chemin_source: str, modele: str = "", strategie: str = "",
) -> EtatJobEtude | None:
    """
    État du job de fiche pour ce document.

    ⚠️ Quand `modele` et `strategie` sont donnés, on ne consulte QUE cette
    fiche-là. Sans eux, on retombe sur l'ancien comportement — « la plus
    récente », quel que soit son modèle ou sa stratégie. C'était le même défaut
    que `_trouver_etat_existant` côté traduction avant la 338 : avec deux fiches
    du même document, l'interface en affichait une au hasard.
    """
    if modele and strategie:
        return _charger_etat(_chemin_etat(build_output_path(chemin_source, modele, strategie)))

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
    # Atomique (piège 6) : la fiche est réécrite en entier après chaque étape ; un arrêt
    # pendant un `open("w")` laissait un .md tronqué, lu tel quel par l'export.
    ecrire_texte_atomique(etat.chemin_sortie, "\n".join(lignes))


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


def _generer_points_selon_strategie(
    chap: FicheChapitre, contenu: str, texte_condense, nb_points: int, etat: EtatJobEtude
) -> list[str]:
    """
    Aiguille entre les deux stratégies de génération des points.

    `condensation` (défaut, historique) : le chapitre trop long est résumé en
    notes, puis les points sont tirés DES NOTES. Simple et peu coûteux, mais
    c'est un résumé de résumé — soupçonné d'être la cause principale des fiches
    jugées trop simplistes le 18/8.

    `sections` : le chapitre est découpé, les points de CHAQUE section sont
    générés depuis le TEXTE RÉEL, puis consolidés. La matière première reste
    ancrée dans le document.

    Les deux coexistent volontairement : le nom de fichier porte la stratégie,
    donc on peut produire les deux fiches d'un même chapitre et les comparer.
    """
    if etat.strategie != STRATEGIE_SECTIONS:
        # `texte_condense` est un CALLABLE : on ne condense qu'ici, donc jamais
        # pour la stratégie « sections ».
        return generer_points(texte_condense(), etat.modele_ollama, etat.langue_fiche, nb_points)

    sections = decouper_en_chunks(contenu, taille_max=ETUDE_CONDENSE_CHUNK)
    _journaliser(
        etat,
        f"Chapitre {chap.index} : stratégie « sections » — {len(sections)} section(s)",
    )
    _sauvegarder_etat(etat)

    # Combien de points par section pour en avoir assez à consolider sans
    # exploser le coût : on vise ~1,5× la cible, réparti, avec un plancher de 2.
    par_section = max(2, round(nb_points * 1.5 / max(1, len(sections))))
    points_par_section = []
    for i, section in enumerate(sections):
        _verifier_interruption(etat)
        points_par_section.append(
            generer_points(section, etat.modele_ollama, etat.langue_fiche, par_section)
        )
        _journaliser(etat, f"  section {i + 1}/{len(sections)} : {len(points_par_section[-1])} point(s)")

    _verifier_interruption(etat)
    return consolider_points(
        points_par_section, etat.modele_ollama, etat.langue_fiche, nb_points
    )


def _executer_etude(etat: EtatJobEtude, contenus: dict[int, str]) -> None:
    """Exécuté par le worker de la file. Fait, chapitre par chapitre, les tâches de `a_faire` (« points » puis
    « quiz ») ; vérifie pause/annulation entre les étapes."""
    if est_annule(etat.job_id):
        etat.statut = StatutJob.ANNULE
        _journaliser(etat, "Job annulé avant démarrage")
        _sauvegarder_etat(etat)
        supprimer_job_registre(etat.job_id)
        return

    etat.statut = StatutJob.EN_COURS
    etat.debut_session = time.time()
    etat.estimation_maj_a = etat.debut_session   # le décompte part du vrai début, pas de la mise en file
    _journaliser(etat, "Démarrage de la fiche d'étude")
    _sauvegarder_etat(etat)
    temps_debut_session = time.time()
    session_debut_stats = time.time()
    compteurs = metriques.demarrer()   # jetons et durées réels de CE passage (stats.py)
    caracteres_source = sum(len(contenus.get(c.index, "")) for c in etat.chapitres if c.a_faire)
    traites = {c.index for c in etat.chapitres if c.a_faire}
    etat.echecs_passage = []

    def _fin_etape() -> None:
        # Rythme observé de CE passage (comme « Vos traductions ») : il remplace l'estimation de la Chronique dès
        # la première étape faite.
        etat.etapes_completees += 1
        maintenant = time.time()
        session = maintenant - etat.debut_session
        restantes = max(0, etat.total_etapes - etat.etapes_completees)
        etat.estimation_restante_secondes = session / etat.etapes_completees * restantes
        etat.estimation_maj_a = maintenant
        etat.estimation_temps_total_secondes = etat.temps_ecoule_secondes + session + etat.estimation_restante_secondes

    try:
        for chap in sorted(etat.chapitres, key=lambda c: c.index):
            if not chap.a_faire:
                continue
            contenu = contenus.get(chap.index, "")
            if not contenu.strip():
                chap.etape = "erreur"
                chap.a_faire = []
                etat.echecs_passage.append(chap.index)
                msg = f"Chapitre {chap.index} ({chap.titre}) : contenu introuvable dans le document"
                etat.erreurs.append(msg)
                _journaliser(etat, msg)
                _sauvegarder_etat(etat)
                continue

            try:
                _verifier_interruption(etat)
                # Condensation PARESSEUSE : la stratégie « sections » n'en a pas
                # besoin pour les points, et la déclencher ici la ferait payer
                # pour rien — plusieurs appels Ollama sur un chapitre de livre.
                _condense: list[str] = []

                def texte_condense() -> str:
                    if not _condense:
                        _condense.append(_texte_pour_generation(chap, contenu, etat))
                    return _condense[0]

                # Étape « points » : points à retenir (régénérés si demandé, sinon absents)
                if TACHE_POINTS in chap.a_faire:
                    chap.etape = "points"
                    _sauvegarder_etat(etat)
                    nb_pts = etat.nb_points or calculer_nb_points(len(contenu))
                    chap.points = _generer_points_selon_strategie(
                        chap, contenu, texte_condense, nb_pts, etat
                    )
                    chap.a_faire.remove(TACHE_POINTS)
                    _fin_etape()
                    _journaliser(etat, f"Chapitre {chap.index} ({chap.titre}) : {len(chap.points)} points générés")
                    _sauvegarder_etat(etat)
                    _rendre_markdown(etat)

                # Étape « quiz » : questions de compréhension
                if TACHE_QUIZ in chap.a_faire:
                    _verifier_interruption(etat)
                    chap.etape = "questions"
                    _sauvegarder_etat(etat)
                    # Un chapitre de 55 000 caractères ne tient pas dans un prompt de
                    # questions : il faut une matière de taille maîtrisée.
                    #
                    # « condensation » utilise le texte condensé, comme avant.
                    # « sections » part de ses POINTS CONSOLIDÉS — ils sont ancrés dans
                    # le texte réel et portent la substance du chapitre. Ça supprime la
                    # condensation de son chemin : elle n'en avait besoin QUE pour les
                    # questions, et c'est ce qui la rendait 13 % plus lente (454 s
                    # contre 400 s sur Chapter 9). Mesuré aussi : les deux stratégies
                    # produisaient des questions presque identiques, parce qu'elles
                    # lisaient toutes deux le même texte condensé — le gain de
                    # « sections » ne les atteignait pas.
                    # Quiz seul, sans points (l'utilisateur ne les a pas demandés) : « sections » retombe sur le texte
                    # condensé, faute de points sur lesquels bâtir.
                    depuis_points = (
                        etat.strategie == STRATEGIE_SECTIONS and bool(chap.points)
                    )
                    texte_q = "" if depuis_points else texte_condense()
                    nb_q = etat.nb_questions or calculer_nb_questions(len(contenu))
                    # Les points sont transmis : les questions ne doivent ni les
                    # reformuler, ni porter deux fois sur le même fait.
                    chap.questions = generer_questions(
                        texte_q, etat.modele_ollama, etat.langue_fiche, nb_q,
                        points=chap.points, depuis_les_points=depuis_points,
                    )
                    chap.a_faire.remove(TACHE_QUIZ)
                    _fin_etape()
                    _journaliser(etat, f"Chapitre {chap.index} ({chap.titre}) : {len(chap.questions)} questions générées")
                chap.etape = "termine"
            except (AnnulationDemandee, PauseDemandee):
                raise
            except Exception as e:
                # Ce qui existait (points d'un passage précédent…) reste affiché ; la tâche ratée n'est pas
                # reportée : l'utilisateur la relance depuis la fiche du chapitre.
                chap.etape = "erreur"
                chap.a_faire = []
                etat.echecs_passage.append(chap.index)
                msg = f"Chapitre {chap.index} ({chap.titre}) : {e}"
                etat.erreurs.append(msg)
                _journaliser(etat, msg)

            etat.temps_ecoule_secondes += time.time() - temps_debut_session
            temps_debut_session = time.time()
            _sauvegarder_etat(etat)
            _rendre_markdown(etat)

        # Le statut juge CE passage : tout ce qu'il a traité a échoué → erreur. Un échec d'un passage précédent
        # ne fait pas échouer celui-ci.
        en_erreur = etat.echecs_passage
        etat.statut = StatutJob.ERREUR if traites and set(en_erreur) >= traites else StatutJob.TERMINE
        etat.estimation_restante_secondes = 0.0
        etat.estimation_maj_a = time.time()
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
        # L'historique ne doit JAMAIS faire échouer un traitement : stats.py avale ses propres erreurs.
        stats.enregistrer_etude(etat, compteurs, session_debut_stats, caracteres_source)
        metriques.terminer()
        supprimer_job_registre(etat.job_id)


# ── Démarrage / reprise ───────────────────────────────────────────────────────

def _taches_heritees(chap: FicheChapitre) -> list[str]:
    """Tâches restantes d'un chapitre mis en pause. Les états d'avant `a_faire` (22/11/2026) n'en ont pas : on les
    déduit de l'étape, sans migration (« questions » avec des points = seul le quiz manque)."""
    if chap.a_faire or chap.etape in ("termine", "erreur"):
        return list(chap.a_faire)
    if chap.etape == "questions" and chap.points:
        return [TACHE_QUIZ]
    return [TACHE_POINTS, TACHE_QUIZ]


def planifier_etude(
    source_path: str,
    chapitres_selectionnes: list[int],
    modele: str,
    langue_fiche: str = "français",
    strategie: str = STRATEGIE_PAR_DEFAUT,
    avec_points: bool = True,
    avec_quiz: bool = True,
    regenerer: bool = False,
    extracteur: str = "pymupdf4llm",
) -> tuple[list[FicheChapitre], dict[int, str]]:
    """
    Décide, chapitre par chapitre, ce qu'un passage doit générer — sans rien lancer (sert aussi à l'estimation).

    La fiche (une par document × modèle × stratégie) GARDE tous ses chapitres d'un passage à l'autre, quelles que
    soient les options : changer le nombre de questions ou ne demander que le quiz n'efface plus les fiches déjà
    faites (avant le 22/11/2026, toute option différente repartait de zéro). Pour chaque chapitre sélectionné :
    « points » si demandés et absents (ou `regenerer`), « quiz » de même. Une fiche en pause reprend en plus ses
    tâches restantes. Seule une langue de fiche différente repart de zéro (les deux langues ne se mélangent pas).
    """
    if not (avec_points or avec_quiz):
        raise ValueError("Rien à générer : choisis les points clés, le quiz, ou les deux.")
    tous_chapitres = chapitres_avec_contenu(source_path, extracteur)
    par_index = {c["index"]: c for c in tous_chapitres}
    introuvables = [i for i in chapitres_selectionnes if i not in par_index]
    if introuvables:
        raise ValueError(f"Chapitre(s) inconnu(s) : {introuvables}")

    chemin_sortie = build_output_path(source_path, modele, strategie)
    existant = lire_statut_etude(source_path, modele, strategie)
    if existant is not None and existant.statut in (StatutJob.EN_COURS, StatutJob.EN_ATTENTE):
        raise ValueError("Une génération est déjà en cours pour cette fiche : attends sa fin ou mets-la en pause.")
    garder = (
        existant is not None
        and existant.chemin_sortie == chemin_sortie
        and existant.langue_fiche == langue_fiche
    )
    reprise = garder and existant.statut == StatutJob.EN_PAUSE
    fiche: dict[int, FicheChapitre] = {}
    for c in (existant.chapitres if garder else []):
        copie = c.model_copy(deep=True)
        copie.a_faire = _taches_heritees(copie) if reprise else []
        fiche[c.index] = copie

    for i in chapitres_selectionnes:
        chap = fiche.get(i) or FicheChapitre(index=i, titre=par_index[i]["titre"])
        taches = set(chap.a_faire)
        if avec_points and (regenerer or not chap.points):
            taches.add(TACHE_POINTS)
        if avec_quiz and (regenerer or not chap.questions):
            taches.add(TACHE_QUIZ)
        chap.a_faire = [t for t in (TACHE_POINTS, TACHE_QUIZ) if t in taches]
        fiche[i] = chap

    chapitres = sorted(fiche.values(), key=lambda c: c.index)
    contenus = {c.index: par_index[c.index].get("contenu", "") for c in chapitres if c.a_faire and c.index in par_index}
    return chapitres, contenus


def estimer_etude(
    chapitres: list[FicheChapitre], contenus: dict[int, str], modele: str, strategie: str,
    avec_points: bool, avec_quiz: bool,
) -> dict:
    """
    Durée d'un passage planifié, d'après ce que les fiches ont réellement coûté (Chronique, `stats.estimer`).
    Retour : `taches`, `chapitres` et `caracteres` à traiter, plus l'estimation (`statut` « ok » avec
    `secondes`/`min`/`max`/`echantillons`/`portee`, « insuffisant », ou « rien » s'il n'y a rien à faire).
    """
    a_traiter = [c for c in chapitres if c.a_faire]
    resultat = {
        "taches": sum(len(c.a_faire) for c in a_traiter),
        "chapitres": len(a_traiter),
        "caracteres": sum(len(contenus.get(c.index, "")) for c in a_traiter),
    }
    if not a_traiter:
        return {**resultat, "statut": "rien"}
    return {**resultat, **stats.estimer(
        modele, caracteres=resultat["caracteres"] or None, type_="etude",
        strategie=strategie, parties=stats.parties_fiche(avec_points, avec_quiz),
    )}


def decrire_base_estimation(est: dict) -> str | None:
    """Phrase courte qui dit d'où vient une estimation (affichée telle quelle par le frontend)."""
    if est.get("statut") != "ok":
        return None
    portee = {
        "identique": "fiches identiques",
        "meme_strategie": "même stratégie, tous contenus",
        "meme_contenu": "même contenu, toutes stratégies",
        "modele": "toutes fiches de ce modèle",
    }.get(est.get("portee"), "mesures")
    return f"d'après {est['echantillons']} fiche(s) passée(s) — {portee}"


def demarrer_etude(
    source_path: str,
    chapitres_selectionnes: list[int],
    modele: str,
    langue_fiche: str = "français",
    strategie: str = STRATEGIE_PAR_DEFAUT,
    nb_points: int = 5,
    nb_questions: int = 3,
    extracteur: str = "pymupdf4llm",
    avec_points: bool = True,
    avec_quiz: bool = True,
    regenerer: bool = False,
) -> EtatJobEtude:
    """
    Enfile un passage de fiche d'étude et retourne son état initial (voir `planifier_etude` pour ce qui est
    généré). S'il n'y a rien à faire, rien n'est enfilé ni écrit : l'état retourné a `total_etapes == 0`.
    `nb_points` / `nb_questions` valent pour CE passage (0 = automatique, selon la longueur du chapitre).
    """
    chapitres, contenus = planifier_etude(
        source_path, chapitres_selectionnes, modele, langue_fiche, strategie,
        avec_points, avec_quiz, regenerer, extracteur,
    )
    est = estimer_etude(chapitres, contenus, modele, strategie, avec_points, avec_quiz)
    total = est["taches"]
    etat = EtatJobEtude(
        job_id=str(uuid.uuid4()),
        chemin_source=source_path,
        chemin_sortie=build_output_path(source_path, modele, strategie),
        modele_ollama=modele,
        langue_fiche=langue_fiche,
        strategie=strategie,
        nb_points=nb_points,
        nb_questions=nb_questions,
        avec_points=avec_points,
        avec_quiz=avec_quiz,
        statut=StatutJob.EN_ATTENTE if total else StatutJob.TERMINE,
        chapitres=chapitres,
        etapes_completees=0,
        total_etapes=total,
        temps_debut=time.time(),
        # Jamais un chiffre inventé : sans assez de fiches mesurées, pas d'estimation (le rythme réel prendra le
        # relais dès la première étape).
        estimation_temps_total_secondes=est.get("secondes"),
        estimation_restante_secondes=est.get("secondes"),
        estimation_maj_a=time.time() if est.get("secondes") is not None else None,
        estimation_base=decrire_base_estimation(est),
    )
    if not total:
        return etat
    _journaliser(
        etat,
        f"Fiche d'étude : {est['chapitres']} chapitre(s) à traiter, {total} étape(s) "
        f"({'points' if avec_points else ''}{' + ' if avec_points and avec_quiz else ''}{'quiz' if avec_quiz else ''}"
        f"{', régénération' if regenerer else ''})",
    )
    _journaliser(etat, "Job ajouté à la file d'attente")
    _sauvegarder_etat(etat)
    enregistrer_job(etat.job_id)
    soumettre_travail(etat.job_id, lambda: _executer_etude(etat, contenus))
    return etat
