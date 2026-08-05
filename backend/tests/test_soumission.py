"""
Tests du point d'entrée unique de soumission (principes cibles ⑧ et ⑨).
"""

import pytest

from app.api.erreurs import ErreurMetier
from app.models.schemas import EtatJob, Langue, StatutJob
from app.services import job_manager, soumission


@pytest.fixture(autouse=True)
def registre_propre():
    soumission.reinitialiser_soumissions()
    yield
    soumission.reinitialiser_soumissions()


@pytest.fixture
def ollama_pret(monkeypatch):
    monkeypatch.setattr(
        "app.services.translator.verifier_ollama_pret", lambda modele: (True, "ok")
    )


def _brancher_moteur(monkeypatch, tmp_path, job_id="job-1"):
    """Remplace le moteur par un compteur d'appels, et fige le chemin de sortie."""
    appels = []
    sortie = str(tmp_path / "doc_ll_traduit.md")

    monkeypatch.setattr(
        "app.services.translation_runner.demarrer_traduction",
        lambda **kw: appels.append(kw) or job_id,
    )
    monkeypatch.setattr(
        "app.services.translation_runner.build_output_path",
        lambda source, modele: sortie,
    )
    return appels, sortie


def _etat(sortie: str, statut: StatutJob) -> None:
    job_manager.sauvegarder_etat(EtatJob(
        job_id="job-1",
        chemin_pdf="/fake/doc.pdf",
        chemin_sortie=sortie,
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele_ollama="llama3.1",
        statut=statut,
    ))


def _soumettre(**surcharges):
    params = dict(
        source_path="/fake/doc.pdf",
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele="llama3.1",
        extracteur="pymupdf4llm",
    )
    params.update(surcharges)
    return soumission.soumettre_traduction(**params)


# ── Preflight (⑨) ────────────────────────────────────────────────────────────

def test_ollama_indisponible_leve_une_erreur_typee_avec_remediation(monkeypatch, tmp_path):
    """
    ⑦ + ⑨ : le 503 porte sa consigne dans un CHAMP, pas noyée dans une phrase.
    C'est ce qui permettra à macOS de l'afficher (F4) au lieu de la perdre.
    """
    appels, _ = _brancher_moteur(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.translator.verifier_ollama_pret",
        lambda modele: (False, "llama-server ne répond plus"),
    )

    with pytest.raises(ErreurMetier) as exc:
        _soumettre()

    assert exc.value.status_code == 503
    assert exc.value.erreur.code == "ollama_indisponible"
    assert "killall ollama" in exc.value.erreur.remediation
    # Le moteur n'a jamais été sollicité : rien n'a été enfilé pour rien.
    assert appels == []


# ── Idempotence (⑧) ──────────────────────────────────────────────────────────

def test_deux_soumissions_identiques_ne_creent_qu_un_job(monkeypatch, tmp_path, ollama_pret):
    """
    F10 : un double-clic, deux onglets, ou web + macOS ensemble créaient deux
    jobs sur le même fichier de sortie, le second parti d'un état lu avant le
    premier. Le worker unique empêchait l'écriture concurrente, pas l'incohérence.
    """
    appels, sortie = _brancher_moteur(monkeypatch, tmp_path)

    job_id_1, deja_1 = _soumettre()
    _etat(sortie, StatutJob.EN_COURS)
    job_id_2, deja_2 = _soumettre()

    assert (job_id_1, deja_1) == ("job-1", False)
    assert (job_id_2, deja_2) == ("job-1", True)
    assert len(appels) == 1  # un seul lancement réel


def test_l_ordre_des_chapitres_ne_change_pas_la_cle(tmp_path):
    """Demander les chapitres [3, 1] et [1, 3] est la même demande."""
    cle_a = soumission.calculer_cle_idempotence(
        "/doc.pdf", Langue.ANGLAIS, Langue.FRANCAIS, "llama3.1", [3, 1]
    )
    cle_b = soumission.calculer_cle_idempotence(
        "/doc.pdf", Langue.ANGLAIS, Langue.FRANCAIS, "llama3.1", [1, 3]
    )
    assert cle_a == cle_b


def test_une_portee_differente_est_une_soumission_differente(monkeypatch, tmp_path, ollama_pret):
    """Ajouter de NOUVEAUX chapitres n'est pas un doublon — c'est le flux additif."""
    appels, sortie = _brancher_moteur(monkeypatch, tmp_path)

    _soumettre(chapitres_selectionnes=[1, 2])
    _etat(sortie, StatutJob.EN_COURS)
    _, deja = _soumettre(chapitres_selectionnes=[3, 4])

    assert deja is False
    assert len(appels) == 2


def test_un_job_termine_peut_etre_relance(monkeypatch, tmp_path, ollama_pret):
    """
    L'idempotence ne doit pas devenir une prison : une fois le job fini, la même
    demande est une relance légitime, pas un doublon.
    """
    appels, sortie = _brancher_moteur(monkeypatch, tmp_path)

    _soumettre()
    _etat(sortie, StatutJob.TERMINE)
    _, deja = _soumettre()

    assert deja is False
    assert len(appels) == 2


def test_une_reprise_explicite_n_est_jamais_un_doublon(monkeypatch, tmp_path, ollama_pret):
    """`resume=True` = l'utilisateur DEMANDE de relancer un job existant."""
    appels, sortie = _brancher_moteur(monkeypatch, tmp_path)

    _soumettre()
    _etat(sortie, StatutJob.EN_COURS)
    _, deja = _soumettre(resume=True)

    assert deja is False
    assert len(appels) == 2
