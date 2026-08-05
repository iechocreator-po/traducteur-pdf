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


@pytest.fixture(autouse=True)
def preflight_neutralise(monkeypatch):
    """
    Le planificateur passe désormais par `soumettre_traduction`, donc par le
    preflight Ollama (principe cible ⑨ — c'est tout l'objet du correctif F9).
    On le neutralise ici : ces tests portent sur la logique de planification,
    pas sur la disponibilité d'Ollama, et sans ça chaque test taperait sur le
    réseau.
    """
    monkeypatch.setattr(
        "app.services.translator.verifier_ollama_pret", lambda modele: (True, "ok")
    )
    from app.services import soumission

    soumission.reinitialiser_soumissions()


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


def test_echec_de_lancement_laisse_le_job_replanifie_avec_un_compteur(monkeypatch):
    """
    Un déclenchement raté ne tue plus le job (F9). Il reste `planifie` avec une
    tentative consommée, donc le tick suivant le réessaiera — au lieu de le
    laisser à `declenche`, un cul-de-sac dont rien ne sortait et que rien ne
    récupérait au démarrage.
    """
    def demarrage_qui_echoue(**kwargs):
        raise RuntimeError("Ollama injoignable")

    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction", demarrage_qui_echoue
    )
    echu = _planifier(datetime.now(timezone.utc) - timedelta(minutes=1))
    scheduler._verifier_et_declencher()

    tous = scheduler._charger()
    assert [j["id"] for j in tous] == [echu["id"]]
    assert tous[0]["statut"] == "planifie"
    assert tous[0]["tentatives"] == 1
    assert "Ollama injoignable" in tous[0]["derniere_erreur"]


def test_echecs_repetes_finissent_par_abandonner_explicitement(monkeypatch):
    """
    Après MAX_TENTATIVES, le job passe à `abandonne` — un état terminal, mais
    explicite et visible, jamais un silence.
    """
    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Ollama figé")),
    )
    _planifier(datetime.now(timezone.utc) - timedelta(minutes=1))

    for _ in range(scheduler.MAX_TENTATIVES):
        scheduler._verifier_et_declencher()

    job = scheduler._charger()[0]
    assert job["statut"] == "abandonne"
    assert job["tentatives"] == scheduler.MAX_TENTATIVES
    # Un job abandonné n'est plus repris par les ticks suivants.
    scheduler._verifier_et_declencher()
    assert scheduler._charger()[0]["tentatives"] == scheduler.MAX_TENTATIVES


def test_echeance_trop_vieille_expire_au_lieu_de_partir_en_silence(monkeypatch):
    """
    Rattrapage BORNÉ (principe cible ⑪) : rallumer le Mac après trois semaines
    ne doit pas lancer d'un coup toutes les planifications oubliées.
    """
    lancements = []
    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction",
        lambda **kwargs: lancements.append(kwargs) or "job-1",
    )
    retard = timedelta(hours=scheduler.RATTRAPAGE_MAX_HEURES + 1)
    _planifier(datetime.now(timezone.utc) - retard)
    scheduler._verifier_et_declencher()

    job = scheduler._charger()[0]
    assert job["statut"] == "expire"
    assert lancements == []
    assert "Échéance dépassée" in job["derniere_erreur"]


def test_echeance_recente_est_bien_rattrapee(monkeypatch):
    """Sous la borne, le rattrapage reste le bon comportement — et il marche."""
    lancements = []
    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction",
        lambda **kwargs: lancements.append(kwargs) or "job-1",
    )
    _planifier(datetime.now(timezone.utc) - timedelta(hours=1))
    scheduler._verifier_et_declencher()

    assert len(lancements) == 1
    # Job parti → retiré de la liste, pas de fantôme.
    assert scheduler._charger() == []


def test_le_planificateur_passe_par_le_preflight_ollama(monkeypatch):
    """
    F9 : `_lancer_job` contournait le preflight en appelant le moteur en direct.
    Il doit désormais emprunter le point d'entrée unique, donc être bloqué quand
    Ollama n'est pas prêt.
    """
    monkeypatch.setattr(
        "app.services.translator.verifier_ollama_pret",
        lambda modele: (False, "llama-server ne répond plus"),
    )
    lancements = []
    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction",
        lambda **kwargs: lancements.append(kwargs) or "job-1",
    )
    _planifier(datetime.now(timezone.utc) - timedelta(minutes=1))
    scheduler._verifier_et_declencher()

    # Le moteur n'a JAMAIS été appelé : le garde a joué.
    assert lancements == []
    job = scheduler._charger()[0]
    assert job["statut"] == "planifie"
    assert job["tentatives"] == 1
