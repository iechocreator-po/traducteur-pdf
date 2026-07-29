"""
Tests de la durabilité des écritures d'état (principe cible ③).

Ces tests corrompent RÉELLEMENT les fichiers, exactement comme le ferait une
coupure pendant un `open(chemin, "w")` : troncature au milieu d'un objet JSON.
C'est la reproduction fidèle des défauts F1, F2 et F13 de l'audit du 27/7 — pas
une simulation par monkeypatch d'une exception.
"""

import json
import os

import pytest

from app.services import persistance


@pytest.fixture(autouse=True)
def journal_propre():
    persistance.reinitialiser_corruptions()
    yield
    persistance.reinitialiser_corruptions()


def _tronquer(chemin: str) -> None:
    """Laisse le fichier dans l'état où une coupure d'alimentation le laisserait."""
    contenu = open(chemin, encoding="utf-8").read()
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(contenu[: len(contenu) // 2])


# ── Écriture atomique ────────────────────────────────────────────────────────

def test_ecriture_atomique_ne_laisse_aucun_fichier_temporaire(tmp_path):
    cible = tmp_path / "etat.json"
    persistance.ecrire_json_atomique(str(cible), {"a": 1})

    assert json.loads(cible.read_text()) == {"a": 1}
    # Aucun .tmp résiduel : un répertoire qui se remplit de déchets serait un bug
    # à part entière sur un dossier de traductions.
    assert [p.name for p in tmp_path.iterdir()] == ["etat.json"]


def test_ecriture_atomique_preserve_l_ancien_contenu_si_la_serialisation_echoue(tmp_path):
    """
    Le point d'une écriture atomique : un échec en cours de route ne détruit pas
    ce qui existait. Avec `open("w")`, l'ancien contenu était déjà perdu au
    moment où l'erreur survenait.
    """
    cible = tmp_path / "etat.json"
    persistance.ecrire_json_atomique(str(cible), {"bon": "contenu"})

    class NonSerialisable:
        pass

    with pytest.raises(TypeError):
        persistance.ecrire_json_atomique(str(cible), {"x": NonSerialisable()})

    assert json.loads(cible.read_text()) == {"bon": "contenu"}
    assert [p.name for p in tmp_path.iterdir()] == ["etat.json"]


def test_ecriture_atomique_cree_le_repertoire_manquant(tmp_path):
    cible = tmp_path / "sous" / "dossier" / "etat.json"
    persistance.ecrire_json_atomique(str(cible), {"a": 1})
    assert json.loads(cible.read_text()) == {"a": 1}


# ── Lecture tolérante ────────────────────────────────────────────────────────

def test_lecture_tolerante_met_en_quarantaine_et_ne_leve_pas(tmp_path):
    cible = tmp_path / "etat.json"
    persistance.ecrire_json_atomique(str(cible), {"chapitres": [1, 2, 3]})
    _tronquer(str(cible))

    resultat = persistance.lire_json_tolerant(str(cible), defaut={"vide": True})

    assert resultat == {"vide": True}
    # Le fichier corrompu est mis de côté, pas supprimé : c'est la seule trace de
    # ce qui avait été fait.
    quarantaines = [p.name for p in tmp_path.iterdir() if ".corrompu-" in p.name]
    assert len(quarantaines) == 1
    # Et il n'est plus en travers du chemin : l'appel suivant repart proprement.
    assert not cible.exists()


def test_la_corruption_est_journalisee_donc_constatable(tmp_path):
    """
    F2 : le cache se perdait « en silence ». La perte reste possible, mais elle
    doit être CONSTATABLE — c'est ce qui alimente GET /api/scheduler/sante.
    """
    cible = tmp_path / "cache.json"
    persistance.ecrire_json_atomique(str(cible), {"cle": "valeur"})
    _tronquer(str(cible))

    persistance.lire_json_tolerant(str(cible), defaut={})

    corruptions = persistance.corruptions_rencontrees()
    assert len(corruptions) == 1
    assert corruptions[0]["chemin"] == str(cible)
    assert "JSON invalide" in corruptions[0]["raison"]


def test_fichier_absent_est_silencieux(tmp_path):
    """Absence ≠ corruption : au premier lancement, rien n'existe et c'est normal."""
    resultat = persistance.lire_json_tolerant(str(tmp_path / "jamais_ecrit.json"), defaut=[])
    assert resultat == []
    assert persistance.corruptions_rencontrees() == []


# ── Les trois défauts de l'audit, reproduits ─────────────────────────────────

def test_F1_un_etat_tronque_ne_fait_plus_tomber_toute_la_bibliotheque(tmp_path, monkeypatch):
    """
    F1 — le défaut le plus grave de l'audit : un seul .state.json tronqué faisait
    remonter un JSONDecodeError jusqu'à `GET /api/bibliotheque`, qui renvoyait
    500. TOUS les documents disparaissaient des deux frontends à la fois, alors
    que le travail était intact sur le disque.
    """
    from app.models.schemas import EtatJob, Langue, StatutJob
    from app.services import bibliotheque, job_manager

    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", str(tmp_path / "biblio.json"))

    # Deux documents : le premier aura un état corrompu, le second est sain.
    for nom in ("abime", "sain"):
        sortie = tmp_path / f"{nom}_traduit.md"
        sortie.write_text(f"# {nom}\n", encoding="utf-8")
        job_manager.sauvegarder_etat(EtatJob(
            job_id=f"job-{nom}",
            chemin_pdf=str(tmp_path / f"{nom}.pdf"),
            chemin_sortie=str(sortie),
            langue_source=Langue.ANGLAIS,
            langue_cible=Langue.FRANCAIS,
            modele_ollama="llama3.1",
            statut=StatutJob.TERMINE,
        ))
        bibliotheque.enregistrer_document(
            chemin_source=str(tmp_path / f"{nom}.pdf"),
            chemin_sortie=str(sortie),
            modele="llama3.1",
            langue_source="anglais",
            langue_cible="français",
        )

    _tronquer(job_manager.chemin_fichier_etat(str(tmp_path / "abime_traduit.md")))

    documents = bibliotheque.lister_documents()

    # Le document sain est TOUJOURS là — c'est tout l'enjeu.
    noms = {d["nom"] for d in documents}
    assert "sain.pdf" in noms
    # Le document abîmé reste listé aussi : sa sortie existe sur disque, seul son
    # état a été perdu. Le travail reste donc atteignable.
    assert "abime.pdf" in noms
    assert next(d for d in documents if d["nom"] == "abime.pdf")["statut"] == "termine"


def test_F13_un_fichier_de_planification_abime_ne_tue_plus_le_planificateur(tmp_path, monkeypatch):
    """
    F13 — vérifié en exécutant le code le 28/7 : un scheduled_jobs.json tronqué
    faisait lever `GET /scheduled`, `GET /scheduled/tous` ET le tick de
    surveillance. La boucle attrapait, imprimait et continuait de tourner : plus
    aucun job planifié ne partait jamais, sans que rien ne le signale.
    """
    from datetime import datetime, timedelta, timezone

    from app.services import scheduler

    monkeypatch.setattr(scheduler, "_FICHIER_JOBS", str(tmp_path / "scheduled.json"))
    scheduler.planifier_job(
        chemin_source="/fake/doc.pdf",
        langue_source="anglais",
        langue_cible="français",
        modele_ollama="llama3.1",
        extracteur_pdf="pymupdf4llm",
        executer_a=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    _tronquer(str(tmp_path / "scheduled.json"))

    # Les trois entrées qui levaient auparavant.
    assert scheduler.lister_jobs_planifies() == []
    assert scheduler.lister_tous_jobs() == []
    scheduler._tick()  # ne lève pas

    # Et le planificateur repart : on peut replanifier immédiatement après.
    scheduler.planifier_job(
        chemin_source="/fake/autre.pdf",
        langue_source="anglais",
        langue_cible="français",
        modele_ollama="llama3.1",
        extracteur_pdf="pymupdf4llm",
        executer_a=datetime.now(timezone.utc) + timedelta(hours=3),
    )
    assert len(scheduler.lister_jobs_planifies()) == 1


def test_F2_un_cache_abime_est_signale_au_lieu_de_disparaitre(tmp_path):
    """F2 — la perte de cache reste possible, mais plus silencieuse."""
    from app.services import cache_traduction

    sortie = str(tmp_path / "doc_traduit.md")
    cache_traduction.sauvegarder_cache(sortie, {"cle1": "traduction déjà payée"})
    _tronquer(cache_traduction.chemin_fichier_cache(sortie))

    assert cache_traduction.charger_cache(sortie) == {}
    assert len(persistance.corruptions_rencontrees()) == 1
