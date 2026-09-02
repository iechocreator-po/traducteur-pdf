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


def _questions_factices(texte, modele, langue, nb, points=None, depuis_les_points=False):
    # `points` est transmis par le runner depuis le 18/8 : les questions
    # reçoivent les points déjà retenus pour ne pas les reformuler.
    return [QuestionEtude(question=f"Question {i + 1} ?", reponse=f"Réponse {i + 1}.") for i in range(nb)]


def _attendre_statut(chemin_source, statuts, timeout=15.0):
    # 15 s et non 5 : ces tests attendent un thread worker en arriere-plan, et
    # 5 s suffisaient a faire echouer test_fiche_complete quand la machine etait
    # chargee (Ollama + serveurs en parallele). Un test instable erode la
    # confiance dans toute la suite ; il ne mesure pas une vitesse ici.
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
    # « sections » étant le défaut depuis le 19/8, la consolidation est sur le
    # chemin normal : sans ce faux, les tests taperaient sur Ollama pour de vrai.
    monkeypatch.setattr(study_runner, "consolider_points",
                        lambda listes, m, l, n: [p for section in listes for p in section][:n])


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


def test_build_output_path_porte_le_modele_ET_la_strategie(tmp_path):
    """
    Le suffixe etait `modele[:2]` — deux caracteres, exactement le defaut
    corrige cote traduction par la 338 : qwen2.5 et qwen3 donnaient tous deux
    « qw ». Il porte desormais le slug complet du modele et la strategie, pour
    que deux fiches du meme document puissent coexister et etre comparees.
    """
    base = str(tmp_path / "livre")
    assert study_runner.build_output_path(f"{base}.pdf", "llama3.1").endswith(
        "livre_fiche_llama3-1_sections.md"), "le défaut est « sections » depuis le 19/8"
    assert study_runner.build_output_path(f"{base}.pdf", "llama3.1", "condensation").endswith(
        "livre_fiche_llama3-1_condensation.md")
    assert study_runner.build_output_path(f"{base}.pdf", "llama3.1", "sections").endswith(
        "livre_fiche_llama3-1_sections.md")
    # Deux modeles d'une meme famille ne se marchent plus dessus.
    assert (study_runner.build_output_path(f"{base}.pdf", "qwen2.5")
            != study_runner.build_output_path(f"{base}.pdf", "qwen3"))
    # Le suffixe _converti_xx de la source est toujours retire.
    assert study_runner.build_output_path(f"{base}_converti_py.md", "mistral").endswith(
        "livre_fiche_mistral_sections.md")


def test_une_fiche_d_avant_le_changement_garde_son_nom(tmp_path):
    """
    Repli : sans lui, une fiche existante deviendrait orpheline et serait
    regeneree de zero — plusieurs minutes d'Ollama pour rien.
    """
    base = str(tmp_path / "livre")
    ancienne = tmp_path / "livre_fiche_ll.md"
    ancienne.write_text("# fiche historique\n", encoding="utf-8")

    # Le repli est ancré sur CONDENSATION, jamais sur « le défaut du moment » :
    # ces fiches ont forcément été produites par condensation, c'était la seule
    # stratégie qui existait. Depuis que « sections » est le défaut, ancrer le
    # repli dessus aurait renvoyé une fiche de condensation à qui demande
    # « sections ».
    assert study_runner.build_output_path(f"{base}.pdf", "llama3.1", "condensation") == str(ancienne)
    # Et « sections » — désormais le défaut — prend bien un nom neuf.
    assert study_runner.build_output_path(f"{base}.pdf", "llama3.1").endswith(
        "livre_fiche_llama3-1_sections.md")


def test_lire_statut_ne_melange_pas_deux_strategies(tmp_path, monkeypatch):
    """
    `lire_statut_etude` prenait la fiche la PLUS RECENTE, quel que soit son
    modele ou sa strategie — l'interface en affichait donc une au hasard des
    qu'il y en avait deux. Meme defaut que `_trouver_etat_existant` avant la 338.
    """
    from app.models.schemas import EtatJobEtude, StatutJob as SJ

    source = str(tmp_path / "livre.pdf")
    for strategie, job in (("condensation", "job-cond"), ("sections", "job-sect")):
        sortie = study_runner.build_output_path(source, "llama3.1", strategie)
        study_runner._sauvegarder_etat(EtatJobEtude(
            job_id=job, chemin_source=source, chemin_sortie=sortie,
            modele_ollama="llama3.1", langue_fiche="français", statut=SJ.TERMINE,
        ))

    assert study_runner.lire_statut_etude(source, "llama3.1", "condensation").job_id == "job-cond"
    assert study_runner.lire_statut_etude(source, "llama3.1", "sections").job_id == "job-sect"


def test_lire_statut_absent_retourne_none(tmp_path):
    assert study_runner.lire_statut_etude(str(tmp_path / "rien.md")) is None


# ── Stratégie « sections » (18/8) ────────────────────────────────────────────

def test_la_strategie_sections_lit_le_TEXTE_pas_des_notes(tmp_path, monkeypatch):
    """
    LE point de la stratégie. `condensation` résume le chapitre en notes puis
    tire les points DES NOTES — un résumé de résumé. `sections` génère les
    points de chaque section depuis le texte réel.

    On vérifie donc que `condenser_texte` n'est JAMAIS appelé, et que ce sont
    bien des fragments du texte d'origine qui arrivent au générateur.
    """
    vus = []
    condensations = []

    def points_traces(texte, modele, langue, nb):
        vus.append(texte)
        return [f"point {i}" for i in range(nb)]

    def condense_trace(texte, modele, langue):
        condensations.append(texte)
        return "notes"

    monkeypatch.setattr(study_runner, "generer_points", points_traces)
    monkeypatch.setattr(study_runner, "condenser_texte", condense_trace)
    monkeypatch.setattr(study_runner, "generer_questions", _questions_factices)
    monkeypatch.setattr(study_runner, "consolider_points",
                        lambda listes, m, l, n: [p for s in listes for p in s][:n])

    # Chapitre nettement au-dessus du seuil de condensation.
    contenu = "# Titre\n\n" + ("phrase distinctive alpha. " * 900)
    source = tmp_path / "long.md"
    source.write_text(contenu, encoding="utf-8")

    study_runner.demarrer_etude(
        source_path=str(source), chapitres_selectionnes=[0],
        modele="llama3.1", strategie=study_runner.STRATEGIE_SECTIONS,
    )
    _attendre_statut(str(source), {StatutJob.TERMINE}, timeout=10)

    # LES POINTS viennent du texte réel, découpé en sections.
    assert len(vus) > 1, "le chapitre aurait dû être découpé en plusieurs sections"
    assert not any(v == "notes" for v in vus), "des points ont été tirés de notes condensées"
    # Les sections REASSEMBLEES couvrent le texte d'origine. On ne teste pas
    # chaque section individuellement : le découpeur isole légitimement un titre
    # seul en début de chapitre, et cette section-là ne contient aucune phrase.
    reassemble = " ".join(" ".join(v.split()) for v in vus)
    assert reassemble.count("phrase distinctive alpha") >= 890, (
        "les points n'ont pas vu la totalité du texte d'origine"
    )

    # La condensation SUBSISTE pour les questions, et c'est assumé : un chapitre
    # de 55 000 caractères ne tient pas dans un prompt de questions. Le gain de
    # la stratégie porte sur les POINTS, là où le « trop simpliste » se voyait.
    # Ce qu'on exige : elle n'a PAS servi aux points.


def test_la_strategie_par_defaut_est_desormais_sections(tmp_path, monkeypatch):
    """
    CHANGEMENT DE CONTRAT (19/8) : « sections » devient le défaut. Mesuré sur
    Chapter 9 — mêmes 12 points mais couvrant TOUT le chapitre (condensation
    restait bloquée sur les 20 premiers pour cent) et 203 s contre 400 s.
    Qui ne demande rien ne condense donc plus.
    """
    condensations = []
    monkeypatch.setattr(study_runner, "generer_points", _points_factices)
    monkeypatch.setattr(study_runner, "generer_questions", _questions_factices)
    monkeypatch.setattr(study_runner, "consolider_points",
                        lambda listes, m, l, n: [p for s in listes for p in s][:n])
    monkeypatch.setattr(study_runner, "condenser_texte",
                        lambda t, m, l: condensations.append(t) or "notes")

    contenu = "# Titre\n\n" + ("mot " * 4000)
    source = tmp_path / "long.md"
    source.write_text(contenu, encoding="utf-8")

    study_runner.demarrer_etude(
        source_path=str(source), chapitres_selectionnes=[0], modele="llama3.1",
    )
    _attendre_statut(str(source), {StatutJob.TERMINE})

    assert condensations == [], "le défaut condense encore — « sections » n'est pas actif"


def test_les_deux_strategies_produisent_deux_fiches_distinctes(tmp_path, monkeypatch):
    """Elles doivent COEXISTER : c'est toute la raison de les nommer."""
    monkeypatch.setattr(study_runner, "generer_points", _points_factices)
    monkeypatch.setattr(study_runner, "generer_questions", _questions_factices)
    monkeypatch.setattr(study_runner, "consolider_points",
                        lambda listes, m, l, n: [p for s in listes for p in s][:n])

    source = tmp_path / "doc.md"
    source.write_text("# Titre\n\nUn contenu court.\n", encoding="utf-8")

    for strategie in (study_runner.STRATEGIE_CONDENSATION, study_runner.STRATEGIE_SECTIONS):
        study_runner.demarrer_etude(
            source_path=str(source), chapitres_selectionnes=[0],
            modele="llama3.1", strategie=strategie,
        )
        _attendre_statut(str(source), {StatutJob.TERMINE}, timeout=10)

    fiches = sorted(p.name for p in tmp_path.glob("*_fiche_*.md"))
    assert len(fiches) == 2, f"les deux fiches devraient coexister, trouvé : {fiches}"
    assert any("condensation" in f for f in fiches)
    assert any("sections" in f for f in fiches)


def test_la_strategie_sections_ne_condense_plus_JAMAIS(tmp_path, monkeypatch):
    """
    Suite du 19/8. « sections » ne condensait plus pour les POINTS, mais encore
    pour les QUESTIONS — d'où deux conséquences mesurées sur Chapter 9 :
    elle était 13 % plus lente que « condensation » (454 s contre 400 s), et
    ses questions étaient presque identiques aux siennes, puisque les deux
    lisaient le même texte condensé.

    Les questions partent désormais des POINTS CONSOLIDÉS, ancrés dans le texte
    réel. La condensation disparaît complètement de ce chemin.
    """
    condensations = []
    materiaux_questions = []

    def questions_tracees(texte, modele, langue, nb, points=None, depuis_les_points=False):
        materiaux_questions.append({"texte": texte, "depuis_points": depuis_les_points,
                                    "nb_points": len(points or [])})
        return [QuestionEtude(question=f"Q{i} ?", reponse=f"R{i}.") for i in range(nb)]

    monkeypatch.setattr(study_runner, "generer_points",
                        lambda t, m, l, n: [f"point ancré {i}" for i in range(n)])
    monkeypatch.setattr(study_runner, "generer_questions", questions_tracees)
    monkeypatch.setattr(study_runner, "condenser_texte",
                        lambda t, m, l: condensations.append(t) or "notes")
    monkeypatch.setattr(study_runner, "consolider_points",
                        lambda listes, m, l, n: [p for s in listes for p in s][:n])

    # Chapitre largement au-dessus du seuil de condensation.
    source = tmp_path / "long.md"
    source.write_text("# Titre\n\n" + ("phrase alpha. " * 1200), encoding="utf-8")

    study_runner.demarrer_etude(
        source_path=str(source), chapitres_selectionnes=[0],
        modele="llama3.1", strategie=study_runner.STRATEGIE_SECTIONS,
    )
    _attendre_statut(str(source), {StatutJob.TERMINE})

    assert condensations == [], (
        "« sections » condense encore — c'est ce qui la rendait plus lente ET "
        "donnait des questions identiques à celles de « condensation »"
    )
    assert len(materiaux_questions) == 1
    assert materiaux_questions[0]["depuis_points"] is True
    assert materiaux_questions[0]["nb_points"] > 0, "les points n'ont pas été transmis"


def test_la_condensation_garde_son_chemin_pour_les_questions(tmp_path, monkeypatch):
    """La stratégie historique n'est pas touchée : elle condense toujours."""
    condensations = []
    modes = []

    def questions_tracees(texte, modele, langue, nb, points=None, depuis_les_points=False):
        modes.append(depuis_les_points)
        return [QuestionEtude(question="Q ?", reponse="R.") for _ in range(nb)]

    monkeypatch.setattr(study_runner, "generer_points", _points_factices)
    monkeypatch.setattr(study_runner, "generer_questions", questions_tracees)
    monkeypatch.setattr(study_runner, "condenser_texte",
                        lambda t, m, l: condensations.append(t) or "notes")

    source = tmp_path / "long.md"
    source.write_text("# Titre\n\n" + ("mot " * 4000), encoding="utf-8")

    study_runner.demarrer_etude(
        source_path=str(source), chapitres_selectionnes=[0], modele="llama3.1",
        strategie=study_runner.STRATEGIE_CONDENSATION,
    )
    _attendre_statut(str(source), {StatutJob.TERMINE})

    assert condensations, "« condensation » demandée explicitement doit condenser"
    assert modes == [False], "la condensation ne doit pas partir des points"
