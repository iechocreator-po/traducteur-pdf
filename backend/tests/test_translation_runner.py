"""
Tests du runner de traduction : file d'attente séquentielle, annulation,
contrôle qualité anti-résumé et cache de chunks.
Ollama est remplacé par des fausses fonctions de traduction (monkeypatch).
"""

import threading
import time


from app.models.schemas import Langue, StatutJob
from app.services import translation_runner
from app.services.job_manager import charger_etat, demander_annulation


def _ecrire_source_md(tmp_path, nom: str, nb_sections: int = 4) -> str:
    """Crée un fichier Markdown avec nb_sections sections d'environ 2000 caractères
    chacune, pour que decouper_en_chunks (taille_max=3000) produise un chunk par section."""
    contenu = "".join(
        f"# Section {i}\n\n" + ("mot " * 500) + "\n\n" for i in range(nb_sections)
    )
    chemin = tmp_path / nom
    chemin.write_text(contenu, encoding="utf-8")
    return str(chemin)


def _attendre_statut(chemin_sortie: str, statuts: set[StatutJob], timeout: float = 15.0):
    """Attend que l'état du job atteigne un des statuts donnés. Échoue après timeout."""
    fin = time.time() + timeout
    while time.time() < fin:
        try:
            etat = charger_etat(chemin_sortie)
        except Exception:
            etat = None  # fichier d'état en cours d'écriture
        if etat and etat.statut in statuts:
            return etat
        time.sleep(0.02)
    raise AssertionError(f"Timeout en attendant {statuts} pour {chemin_sortie}")


def _demarrer(chemin_source: str) -> tuple[str, str]:
    """Lance un job de traduction complète et retourne (job_id, chemin_sortie)."""
    job_id = translation_runner.demarrer_traduction(
        source_path=chemin_source,
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele="llama3.1",
    )
    return job_id, translation_runner.build_output_path(chemin_source, "llama3.1")


def test_deux_jobs_ne_traduisent_jamais_en_parallele(tmp_path, monkeypatch):
    verrou = threading.Lock()
    en_cours = 0
    max_simultanes = 0

    def fausse_traduction(texte, modele, langue_source, langue_cible, termes_a_conserver=None):
        nonlocal en_cours, max_simultanes
        with verrou:
            en_cours += 1
            max_simultanes = max(max_simultanes, en_cours)
        time.sleep(0.05)
        with verrou:
            en_cours -= 1
        return texte

    monkeypatch.setattr(translation_runner, "traduire_texte", fausse_traduction)

    source_a = _ecrire_source_md(tmp_path, "doc_a.md")
    source_b = _ecrire_source_md(tmp_path, "doc_b.md")
    _, sortie_a = _demarrer(source_a)
    _, sortie_b = _demarrer(source_b)

    etat_a = _attendre_statut(sortie_a, {StatutJob.TERMINE, StatutJob.ERREUR})
    etat_b = _attendre_statut(sortie_b, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat_a.statut == StatutJob.TERMINE
    assert etat_b.statut == StatutJob.TERMINE
    assert max_simultanes == 1


def test_annulation_d_un_job_en_cours(tmp_path, monkeypatch):
    def fausse_traduction(texte, modele, langue_source, langue_cible, termes_a_conserver=None):
        time.sleep(0.15)
        return texte

    monkeypatch.setattr(translation_runner, "traduire_texte", fausse_traduction)

    source = _ecrire_source_md(tmp_path, "doc_annule.md", nb_sections=6)
    job_id, sortie = _demarrer(source)

    # Attend que le job démarre vraiment, puis demande l'annulation
    _attendre_statut(sortie, {StatutJob.EN_COURS})
    assert demander_annulation(job_id) is True

    etat = _attendre_statut(sortie, {StatutJob.ANNULE, StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat.statut == StatutJob.ANNULE
    assert etat.derniere_section_completee < etat.total_sections


def test_annulation_d_un_job_en_file_d_attente(tmp_path, monkeypatch):
    def fausse_traduction(texte, modele, langue_source, langue_cible, termes_a_conserver=None):
        time.sleep(0.1)
        return texte

    monkeypatch.setattr(translation_runner, "traduire_texte", fausse_traduction)

    source_a = _ecrire_source_md(tmp_path, "doc_occupe.md")
    source_b = _ecrire_source_md(tmp_path, "doc_en_attente.md")
    _, sortie_a = _demarrer(source_a)
    job_b, sortie_b = _demarrer(source_b)

    # B attend derrière A : l'annulation doit le stopper avant tout travail
    assert demander_annulation(job_b) is True

    etat_b = _attendre_statut(sortie_b, {StatutJob.ANNULE, StatutJob.TERMINE, StatutJob.ERREUR})
    etat_a = _attendre_statut(sortie_a, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat_a.statut == StatutJob.TERMINE
    assert etat_b.statut == StatutJob.ANNULE
    assert etat_b.derniere_section_completee == 0


def test_controle_qualite_retente_puis_avertit(tmp_path, monkeypatch):
    appels = []

    def traduction_qui_resume(texte, modele, langue_source, langue_cible, termes_a_conserver=None):
        appels.append(texte)
        return "trop court"  # ratio très inférieur à RATIO_TRADUCTION_SUSPECT

    monkeypatch.setattr(translation_runner, "traduire_texte", traduction_qui_resume)

    source = _ecrire_source_md(tmp_path, "doc_resume.md", nb_sections=1)
    _, sortie = _demarrer(source)

    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat.statut == StatutJob.TERMINE
    # 1 chunk → 1 appel initial + 1 retry
    assert len(appels) == 2
    assert len(etat.avertissements) == 1
    assert "résumée" in etat.avertissements[0]


def test_cache_evite_de_retraduire_au_re_run(tmp_path, monkeypatch):
    appels = []

    def fausse_traduction(texte, modele, langue_source, langue_cible, termes_a_conserver=None):
        appels.append(texte)
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", fausse_traduction)

    source = _ecrire_source_md(tmp_path, "doc_cache.md")
    _, sortie = _demarrer(source)
    etat1 = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat1.statut == StatutJob.TERMINE
    nb_appels_premier_run = len(appels)
    assert nb_appels_premier_run == etat1.total_sections

    # Re-run du même document : tout doit venir du cache
    _, sortie2 = _demarrer(source)
    etat2 = _attendre_statut(sortie2, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat2.statut == StatutJob.TERMINE
    assert len(appels) == nb_appels_premier_run
    assert any("cache" in ligne for ligne in etat2.journal)


def test_annexe_liens_ajoutee_une_seule_fois(tmp_path, monkeypatch):
    """L'annexe des liens du PDF est ajoutée à la fin, sans doublon au re-run."""
    import time as _time
    from app.models.schemas import EtatJob, StatutJob, Langue

    monkeypatch.setattr(
        translation_runner, "extraire_urls",
        lambda chemin: ["https://a.org", "https://b.org", "https://a.org"],
    )

    sortie = tmp_path / "doc_traduit_ll.md"
    sortie.write_text("Texte traduit.\n")
    state = EtatJob(
        job_id="test", chemin_pdf=str(tmp_path / "doc.pdf"), chemin_sortie=str(sortie),
        langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele_ollama="llama3.1",
        statut=StatutJob.EN_COURS, derniere_section_completee=1, total_sections=1,
        total_pages=1, total_mots=2, mots_traduits=2, temps_debut=_time.time(),
    )

    translation_runner._annexer_liens_source(state)
    contenu = sortie.read_text()
    assert "## Liens du document original" in contenu
    assert contenu.count("https://a.org") == 1  # dédoublonné
    assert "https://b.org" in contenu

    # Re-run : pas de seconde annexe
    translation_runner._annexer_liens_source(state)
    assert sortie.read_text().count("## Liens du document original") == 1


def test_annexe_liens_ignoree_pour_source_markdown(tmp_path, monkeypatch):
    import time as _time
    from app.models.schemas import EtatJob, StatutJob, Langue

    monkeypatch.setattr(
        translation_runner, "extraire_urls",
        lambda chemin: ["https://a.org"],
    )
    sortie = tmp_path / "doc_traduit_ll.md"
    sortie.write_text("Texte traduit.\n")
    state = EtatJob(
        job_id="test", chemin_pdf=str(tmp_path / "doc.md"), chemin_sortie=str(sortie),
        langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele_ollama="llama3.1",
        statut=StatutJob.EN_COURS, derniere_section_completee=1, total_sections=1,
        total_pages=0, total_mots=2, mots_traduits=2, temps_debut=_time.time(),
    )

    translation_runner._annexer_liens_source(state)
    assert "## Liens du document original" not in sortie.read_text()
