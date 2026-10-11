"""
Tests de la récupération des jobs interrompus, TOUTES familles (défaut F7).

Le scénario reproduit : le backend est redémarré pendant qu'un job tourne. Au
démarrage suivant, le registre mémoire est vide — un état persisté qui dit
`en_cours` décrit donc forcément un job que plus personne ne fait avancer.

Avant ce correctif, seule la traduction était récupérée. Un job d'étude coupé
restait `en_cours` pour toujours, ce qui bloquait DÉFINITIVEMENT le panneau
Résumé & Quiz du document : `pollStatutFiche` ne s'arrête que sur un statut
terminal et `majBoutonGenerer` garde « Générer » désactivé tant que le poll
tourne — rechargement de page compris.
"""

import pytest

from app.models.schemas import EtatJob, EtatJobEtude, Langue, StatutJob
from app.services import bibliotheque, job_manager, recuperation, study_runner


@pytest.fixture
def document(tmp_path, monkeypatch):
    """Un document présent dans la Bibliothèque, avec sa traduction sur disque."""
    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", str(tmp_path / "biblio.json"))
    sortie = tmp_path / "livre_ll_traduit.md"
    sortie.write_text("# Livre\n", encoding="utf-8")
    bibliotheque.enregistrer_document(
        chemin_source=str(tmp_path / "livre.pdf"),
        chemin_sortie=str(sortie),
        modele="llama3.1",
        langue_source="anglais",
        langue_cible="français",
    )
    return tmp_path, str(sortie)


def _etat_etude(chemin_sortie: str, statut: StatutJob) -> None:
    study_runner._sauvegarder_etat(EtatJobEtude(
        job_id="fiche-1",
        chemin_source="/fake/livre.pdf",
        chemin_sortie=chemin_sortie,
        modele_ollama="llama3.1",
        langue_fiche="français",
        statut=statut,
    ))


def test_une_fiche_d_etude_coupee_est_debloquee(document):
    """
    F7 : le job passe à `erreur` — un statut TERMINAL, donc `pollStatutFiche`
    s'arrête et le bouton « Générer » redevient cliquable. Basculer en `en_pause`
    laisserait le panneau aussi bloqué qu'avant.
    """
    tmp_path, _ = document
    fiche = str(tmp_path / "livre_ll_traduit_fiche_fr.md")
    _etat_etude(fiche, StatutJob.EN_COURS)

    assert recuperation.recuperer_jobs_etude() == 1

    etat = study_runner._charger_etat(study_runner._chemin_etat(fiche))
    assert etat.statut == StatutJob.ERREUR
    assert "arrêt du serveur" in etat.erreurs[-1]


def test_une_fiche_terminee_n_est_pas_touchee(document):
    tmp_path, _ = document
    fiche = str(tmp_path / "livre_ll_traduit_fiche_fr.md")
    _etat_etude(fiche, StatutJob.TERMINE)

    assert recuperation.recuperer_jobs_etude() == 0
    etat = study_runner._charger_etat(study_runner._chemin_etat(fiche))
    assert etat.statut == StatutJob.TERMINE


def test_un_job_audio_coupe_est_debloque(document):
    """Le TTS n'a ni pause ni reprise : le seul service à rendre est de le DIRE."""
    from app.services.persistance import ecrire_json_atomique, lire_json_tolerant

    tmp_path, _ = document
    chemin_etat = str(tmp_path / "livre_ll_traduit_audio_piper_fr.wav.tts.state.json")
    ecrire_json_atomique(chemin_etat, {
        "statut": "en_cours",
        "chemin_sortie": str(tmp_path / "livre_ll_traduit_audio_piper_fr.wav"),
    })

    assert recuperation.recuperer_jobs_tts() == 1

    etat = lire_json_tolerant(chemin_etat)
    assert etat["statut"] == "erreur"
    assert "arrêt du serveur" in etat["erreur"]


def test_un_job_en_attente_est_aussi_recupere(document):
    """
    `en_attente` compte autant que `en_cours` : la file est vidée au redémarrage,
    donc un job enfilé mais jamais exécuté est perdu de la même façon.
    """
    tmp_path, _ = document
    fiche = str(tmp_path / "livre_ll_traduit_fiche_fr.md")
    _etat_etude(fiche, StatutJob.EN_ATTENTE)

    assert recuperation.recuperer_jobs_etude() == 1


def test_recuperer_tout_isole_les_familles(document, monkeypatch):
    """
    Une famille qui explose ne doit pas empêcher les autres d'être récupérées —
    sinon on remplace un blocage par un autre.
    """
    tmp_path, sortie = document

    job_manager.sauvegarder_etat(EtatJob(
        job_id="trad-1",
        chemin_pdf=str(tmp_path / "livre.pdf"),
        chemin_sortie=sortie,
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele_ollama="llama3.1",
        statut=StatutJob.EN_COURS,
    ))
    _etat_etude(str(tmp_path / "livre_ll_traduit_fiche_fr.md"), StatutJob.EN_COURS)

    def tts_qui_explose():
        raise RuntimeError("boom")

    monkeypatch.setattr(recuperation, "recuperer_jobs_tts", tts_qui_explose)

    resultats = recuperation.recuperer_tout()

    assert resultats["tts"] == 0          # la famille en panne est neutralisée
    assert resultats["traduction"] == 1   # les autres ont bien tourné
    assert resultats["etude"] == 1
    # La traduction redevient reprenable, pas terminale.
    assert job_manager.charger_etat(sortie).statut == StatutJob.EN_PAUSE
