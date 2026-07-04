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

    # Le job échu passe à « declenche », le futur reste planifié
    restants = scheduler.lister_jobs_planifies()
    assert [j["id"] for j in restants] == [futur["id"]]
    tous = scheduler._charger()
    statuts = {j["id"]: j["statut"] for j in tous}
    assert statuts[echu["id"]] == "declenche"


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
