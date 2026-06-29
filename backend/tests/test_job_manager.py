"""Tests du gestionnaire d'état de jobs (pause/reprise)."""

from app.models.schemas import EtatJob, Langue, StatutJob
from app.services.job_manager import (
    charger_etat,
    chemin_fichier_etat,
    sauvegarder_etat,
    supprimer_etat,
)


def _etat_exemple(chemin_sortie: str) -> EtatJob:
    return EtatJob(
        job_id="abc123",
        chemin_pdf="/fake/document.pdf",
        chemin_sortie=chemin_sortie,
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele_ollama="llama3.1",
        statut=StatutJob.EN_COURS,
        derniere_section_completee=2,
        total_sections=5,
    )


def test_sauvegarder_puis_charger_etat(tmp_path):
    chemin_sortie = str(tmp_path / "document.txt")
    etat = _etat_exemple(chemin_sortie)

    sauvegarder_etat(etat)
    etat_charge = charger_etat(chemin_sortie)

    assert etat_charge is not None
    assert etat_charge.derniere_section_completee == 2
    assert etat_charge.statut == StatutJob.EN_COURS


def test_charger_etat_retourne_none_si_absent(tmp_path):
    chemin_sortie = str(tmp_path / "inexistant.txt")
    assert charger_etat(chemin_sortie) is None


def test_supprimer_etat(tmp_path):
    chemin_sortie = str(tmp_path / "document.txt")
    etat = _etat_exemple(chemin_sortie)
    sauvegarder_etat(etat)

    supprimer_etat(chemin_sortie)

    assert charger_etat(chemin_sortie) is None


def test_chemin_fichier_etat_a_la_bonne_extension():
    chemin = chemin_fichier_etat("/dossier/document.txt")
    assert chemin == "/dossier/document.state.json"
