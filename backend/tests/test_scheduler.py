"""
Tests du planificateur de traductions différées.
La persistance est redirigée vers un fichier temporaire et le déclenchement
réel des traductions est remplacé par une fausse fonction (monkeypatch).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import scheduler


@pytest.fixture(autouse=True)
def fichier_jobs_temporaire(tmp_path, monkeypatch):
    """Isole chaque test dans son propre scheduled_jobs.json."""
    monkeypatch.setattr(scheduler, "_FICHIER_JOBS", str(tmp_path / "scheduled_jobs.json"))


def _planifier(executer_a: datetime, chemin: str = "/fake/doc.pdf") -> dict:
    return scheduler.planifier_job(
        chemin_source=chemin,
        langue_source="anglais",
        langue_cible="français",
        modele_ollama="llama3.1",
        extracteur_pdf="pymupdf4llm",
        executer_a=executer_a,
    )


def test_planifier_puis_lister(tmp_path):
    job = _planifier(datetime.now(timezone.utc) + timedelta(hours=2))

    jobs = scheduler.lister_jobs_planifies()
    assert len(jobs) == 1
    assert jobs[0]["id"] == job["id"]
    assert jobs[0]["statut"] == "planifie"


def test_annuler_job_planifie():
    job = _planifier(datetime.now(timezone.utc) + timedelta(hours=2))

    assert scheduler.annuler_job(job["id"]) is True
    assert scheduler.lister_jobs_planifies() == []
    # Une seconde annulation échoue : le job n'est plus « planifie »
    assert scheduler.annuler_job(job["id"]) is False


def test_annuler_job_inconnu():
    assert scheduler.annuler_job("id-inexistant") is False


def test_declenchement_d_un_job_echu(monkeypatch):
    lancements = []

    def faux_demarrage(**kwargs):
        lancements.append(kwargs)
        return "job-id-factice"

    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction", faux_demarrage
    )

    echu = _planifier(datetime.now(timezone.utc) - timedelta(minutes=1))
    futur = _planifier(datetime.now(timezone.utc) + timedelta(hours=2), chemin="/fake/futur.pdf")

    scheduler._verifier_et_declencher()

    assert len(lancements) == 1
    assert lancements[0]["source_path"] == "/fake/doc.pdf"

    # Le job échu, une fois déclenché AVEC SUCCÈS, est retiré de la liste (plus
    # de « Déclenché » fantôme) ; le futur reste planifié.
    restants = scheduler.lister_jobs_planifies()
    assert [j["id"] for j in restants] == [futur["id"]]
    tous = scheduler._charger()
    assert [j["id"] for j in tous] == [futur["id"]]  # echu purgé après lancement
    assert echu["id"] not in {j["id"] for j in tous}


def test_date_naive_traitee_comme_utc(monkeypatch):
    lancements = []
    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction",
        lambda **kwargs: lancements.append(kwargs) or "job-id",
    )

    # Date naive (sans fuseau) dans le passé UTC : doit être déclenchée
    _planifier(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5))
    scheduler._verifier_et_declencher()

    assert len(lancements) == 1


def test_lister_tous_jobs_inclut_annules_et_declenches():
    job_a = _planifier(datetime.now(timezone.utc) + timedelta(hours=2), "/fake/a.pdf")
    _planifier(datetime.now(timezone.utc) + timedelta(hours=3), "/fake/b.pdf")
    scheduler.annuler_job(job_a["id"])

    tous = scheduler.lister_tous_jobs()
    assert len(tous) == 2
    assert {j["statut"] for j in tous} == {"annule", "planifie"}
    # La vue filtrée ne montre que les planifiés
    assert len(scheduler.lister_jobs_planifies()) == 1


def test_supprimer_job_quel_que_soit_le_statut():
    """supprimer_job retire l'entrée même si elle est annulée ou déclenchée."""
    planifie = _planifier(datetime.now(timezone.utc) + timedelta(hours=2))
    annule = _planifier(datetime.now(timezone.utc) + timedelta(hours=3), chemin="/fake/b.pdf")
    scheduler.annuler_job(annule["id"])

    # Un job annulé (non « planifie ») : annuler_job le refuse, supprimer_job non.
    assert scheduler.annuler_job(annule["id"]) is False
    assert scheduler.supprimer_job(annule["id"]) is True
    assert scheduler.supprimer_job(planifie["id"]) is True
    assert scheduler._charger() == []
    # Suppression d'un id inconnu → False.
    assert scheduler.supprimer_job("inconnu") is False


def test_echec_de_lancement_laisse_le_job_dans_la_liste(monkeypatch):
    """Si le déclenchement échoue, le job n'est PAS purgé (reste visible/effaçable)."""
    def demarrage_qui_echoue(**kwargs):
        raise RuntimeError("Ollama injoignable")

    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction", demarrage_qui_echoue
    )
    echu = _planifier(datetime.now(timezone.utc) - timedelta(minutes=1))
    scheduler._verifier_et_declencher()

    tous = scheduler._charger()
    assert [j["id"] for j in tous] == [echu["id"]]
    assert tous[0]["statut"] == "declenche"  # marqué déclenché, mais conservé
