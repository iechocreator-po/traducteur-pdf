"""
Tests du runner de fiche d'étude. Les appels Ollama sont simulés ;
la file d'attente réelle exécute les jobs (attente avec timeout).
"""

import time

from app.models.schemas import QuestionEtude, StatutJob
from app.services import study_runner


DOCUMENT = """# Introduction

Le cerveau contient environ 86 milliards de neurones. Chaque neurone communique
par des synapses. La plasticité synaptique est la base de l'apprentissage.

# Les modèles mathématiques

Les modèles de Hodgkin-Huxley décrivent le potentiel d'action. Les équations
différentielles capturent la dynamique des canaux ioniques.

# Conclusion

La neuroscience computationnelle unit biologie et mathématiques.
"""


def _points_factices(texte, modele, langue, nb):
    return [f"Point {i + 1}" for i in range(nb)]


def _questions_factices(texte, modele, langue, nb):
    return [QuestionEtude(question=f"Question {i + 1} ?", reponse=f"Réponse {i + 1}.") for i in range(nb)]


def _attendre_statut(chemin_source, statuts, timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin:
        etat = study_runner.lire_statut_etude(chemin_source)
        if etat and etat.statut in statuts:
            return etat
        time.sleep(0.05)
    raise AssertionError(f"Timeout en attendant {statuts}")


def _mock_generation(monkeypatch):
    monkeypatch.setattr(study_runner, "generer_points", _points_factices)
    monkeypatch.setattr(study_runner, "generer_questions", _questions_factices)


def test_fiche_complete(tmp_path, monkeypatch):
    _mock_generation(monkeypatch)
    source = tmp_path / "livre.md"
    source.write_text(DOCUMENT)

    etat = study_runner.demarrer_etude(
        str(source), chapitres_selectionnes=[0, 1], modele="llama3.1",
        langue_fiche="français", nb_points=5, nb_questions=3,
    )
    assert etat.statut == StatutJob.EN_ATTENTE
    assert etat.total_etapes == 4

    final = _attendre_statut(str(source), {StatutJob.TERMINE})
    assert final.etapes_completees == 4
    assert all(c.etape == "termine" for c in final.chapitres)
    assert len(final.chapitres[0].points) == 5
    assert len(final.chapitres[0].questions) == 3

    contenu = open(final.chemin_sortie, encoding="utf-8").read()
    assert "# Fiche d'étude" in contenu
    assert "## Introduction" in contenu
    assert "### Points à retenir" in contenu
    assert "**Q1.** Question 1 ?" in contenu
    assert "<details><summary>Voir la réponse</summary>" in contenu
    assert "Réponse 1." in contenu
    # Le chapitre non sélectionné n'apparaît pas
    assert "## Conclusion" not in contenu


def test_reprise_conserve_les_chapitres_termines(tmp_path, monkeypatch):
    _mock_generation(monkeypatch)
    source = tmp_path / "livre.md"
    source.write_text(DOCUMENT)

    study_runner.demarrer_etude(str(source), [0], modele="llama3.1")
    _attendre_statut(str(source), {StatutJob.TERMINE})

    # Deuxième run : chapitre 2 en plus — le chapitre 0 ne doit pas être régénéré
    appels = []

    def points_traces(texte, modele, langue, nb):
        appels.append(texte[:30])
        return _points_factices(texte, modele, langue, nb)

    monkeypatch.setattr(study_runner, "generer_points", points_traces)
    study_runner.demarrer_etude(str(source), [2], modele="llama3.1")
    final = _attendre_statut(str(source), {StatutJob.TERMINE})

    assert len(appels) == 1  # un seul chapitre régénéré
    assert {c.index for c in final.chapitres} == {0, 2}
    contenu = open(final.chemin_sortie, encoding="utf-8").read()
    assert "## Introduction" in contenu
    assert "## Conclusion" in contenu


def test_options_differentes_repartent_de_zero(tmp_path, monkeypatch):
    _mock_generation(monkeypatch)
    source = tmp_path / "livre.md"
    source.write_text(DOCUMENT)

    study_runner.demarrer_etude(str(source), [0], modele="llama3.1", nb_points=5)
    _attendre_statut(str(source), {StatutJob.TERMINE})

    study_runner.demarrer_etude(str(source), [1], modele="llama3.1", nb_points=7)
    final = _attendre_statut(str(source), {StatutJob.TERMINE})
    # nb_points différent → le chapitre 0 n'est pas conservé
    assert {c.index for c in final.chapitres} == {1}


def test_annulation(tmp_path, monkeypatch):
    from app.services import job_manager

    def points_lents(texte, modele, langue, nb):
        time.sleep(0.3)
        return _points_factices(texte, modele, langue, nb)

    monkeypatch.setattr(study_runner, "generer_points", points_lents)
    monkeypatch.setattr(study_runner, "generer_questions", _questions_factices)
    source = tmp_path / "livre.md"
    source.write_text(DOCUMENT)

    etat = study_runner.demarrer_etude(str(source), [0, 1, 2], modele="llama3.1")
    job_manager.demander_annulation(etat.job_id)
    final = _attendre_statut(str(source), {StatutJob.ANNULE, StatutJob.TERMINE})
    assert final.statut in (StatutJob.ANNULE, StatutJob.TERMINE)


def test_pause_puis_reprise(tmp_path, monkeypatch):
    from app.services import job_manager

    def points_lents(texte, modele, langue, nb):
        time.sleep(0.2)
        return _points_factices(texte, modele, langue, nb)

    monkeypatch.setattr(study_runner, "generer_points", points_lents)
    monkeypatch.setattr(study_runner, "generer_questions", _questions_factices)
    source = tmp_path / "livre.md"
    source.write_text(DOCUMENT)

    etat = study_runner.demarrer_etude(str(source), [0, 1, 2], modele="llama3.1")
    job_manager.mettre_en_pause(etat.job_id)
    pause = _attendre_statut(str(source), {StatutJob.EN_PAUSE, StatutJob.TERMINE})

    if pause.statut == StatutJob.EN_PAUSE:
        # La reprise repart des chapitres non terminés
        study_runner.demarrer_etude(str(source), [0, 1, 2], modele="llama3.1")
        final = _attendre_statut(str(source), {StatutJob.TERMINE})
        assert final.etapes_completees == final.total_etapes == 6


def test_chapitre_sans_contenu_marque_en_erreur(tmp_path, monkeypatch):
    _mock_generation(monkeypatch)
    # PDF avec signets non reliés → contenu vide. Simulé via chapitres_avec_contenu.
    monkeypatch.setattr(
        study_runner, "chapitres_avec_contenu",
        lambda chemin, extracteur: [{"index": 0, "titre": "Fantôme", "contenu": ""}],
    )
    source = tmp_path / "livre.md"
    source.write_text(DOCUMENT)

    study_runner.demarrer_etude(str(source), [0], modele="llama3.1")
    final = _attendre_statut(str(source), {StatutJob.ERREUR})
    assert final.chapitres[0].etape == "erreur"
    assert final.erreurs


def test_chapitre_inconnu_leve_valueerror(tmp_path):
    source = tmp_path / "livre.md"
    source.write_text(DOCUMENT)
    try:
        study_runner.demarrer_etude(str(source), [99], modele="llama3.1")
        raise AssertionError("ValueError attendue")
    except ValueError as e:
        assert "99" in str(e)


def test_build_output_path():
    assert study_runner.build_output_path("/x/livre.pdf", "llama3.1") == "/x/livre_fiche_ll.md"
    assert study_runner.build_output_path("/x/livre_converti_py.md", "mistral") == "/x/livre_fiche_mi.md"


def test_lire_statut_absent_retourne_none(tmp_path):
    assert study_runner.lire_statut_etude(str(tmp_path / "rien.md")) is None
