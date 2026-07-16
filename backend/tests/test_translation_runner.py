"""
Tests du runner de traduction : file d'attente séquentielle, annulation,
contrôle qualité anti-résumé et cache de chunks.
Ollama est remplacé par des fausses fonctions de traduction (monkeypatch).
"""

import threading
import time


from app.models.schemas import Langue, StatutJob
from app.services import translation_runner
from app.services.job_manager import charger_etat, demander_annulation, sauvegarder_etat
from app.services.translator import OllamaIndisponible, OllamaErreurApplicative


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

    def fausse_traduction(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
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
    def fausse_traduction(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
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
    def fausse_traduction(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
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

    def traduction_qui_resume(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
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

    def fausse_traduction(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
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


def _reprendre(chemin_source: str) -> str:
    """Relance le même document en mode reprise. Retourne le chemin de sortie."""
    translation_runner.demarrer_traduction(
        source_path=chemin_source,
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele="llama3.1",
        resume=True,
    )
    return translation_runner.build_output_path(chemin_source, "llama3.1")


def test_erreur_applicative_marque_le_job_en_erreur(tmp_path, monkeypatch):
    """Une section en échec suffit à disqualifier le job : jamais « termine »."""
    def traduction_qui_echoue_section_2(texte, modele, langue_source, langue_cible,
                                        termes_a_conserver=None, interruption=None):
        if "Section 1" in texte:
            raise OllamaErreurApplicative("modèle inconnu")
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traduction_qui_echoue_section_2)

    source = _ecrire_source_md(tmp_path, "doc_echec.md")
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat.statut == StatutJob.ERREUR
    assert etat.sections_echouees == [1]
    # Les autres sections sont bien traduites : l'échec reste local.
    assert etat.derniere_section_completee == etat.total_sections
    contenu = open(sortie, encoding="utf-8").read()
    assert "[ERREUR DE TRADUCTION — section 2]" in contenu
    assert "SECTION 0" in contenu.upper()


def test_ollama_indisponible_arrete_le_job_sans_bruler_les_sections(tmp_path, monkeypatch):
    """
    Régression de l'incident des 306 pages : Ollama tombe à la section 2, et
    l'ancienne boucle remplissait les 3 restantes de placeholders en 1 ms avant
    de déclarer « termine ». Le job doit s'arrêter net et rester reprenable.
    """
    appels = []

    def ollama_mort(texte, modele, langue_source, langue_cible,
                    termes_a_conserver=None, interruption=None):
        appels.append(texte)
        if "Section 1" in texte:
            raise OllamaIndisponible("Ollama injoignable, budget épuisé")
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", ollama_mort)

    source = _ecrire_source_md(tmp_path, "doc_panne.md")
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat.statut == StatutJob.ERREUR
    # La section perdue n'est PAS comptée comme faite → la reprise repart d'elle.
    assert etat.derniere_section_completee == 1
    # Aucun placeholder : rien n'a été brûlé.
    contenu = open(sortie, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu
    # Les sections 3 et 4 n'ont même jamais été tentées.
    assert len(appels) == 2


def test_reprise_apres_panne_recoud_sans_retraduire(tmp_path, monkeypatch):
    """Après une panne, la reprise ne retraduit que ce qui manque (cache chaud)."""
    appels = []
    ollama_vivant = False

    def traducteur(texte, modele, langue_source, langue_cible,
                   termes_a_conserver=None, interruption=None):
        appels.append(texte)
        if not ollama_vivant and "Section 1" in texte:
            raise OllamaIndisponible("Ollama injoignable")
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    source = _ecrire_source_md(tmp_path, "doc_reprise.md")
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.ERREUR})
    assert etat.statut == StatutJob.ERREUR
    appels_apres_panne = len(appels)

    ollama_vivant = True
    sortie2 = _reprendre(source)
    etat2 = _attendre_statut(sortie2, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat2.statut == StatutJob.TERMINE
    # La section 0 était en cache → seules 1, 2 et 3 partent réellement.
    assert len(appels) - appels_apres_panne == 3
    contenu = open(sortie2, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu
    assert contenu.count("SECTION 0") == 1  # pas de doublon


def test_reprise_apres_annulation_repart_de_la_section_stoppee(tmp_path, monkeypatch):
    """Un job annulé en vol laisse une sortie propre : la reprise continue depuis
    derniere_section_completee (comme une pause), sans retraduire ni tronquer."""
    appels = []
    lent = True

    def traducteur(texte, modele, langue_source, langue_cible,
                   termes_a_conserver=None, interruption=None):
        appels.append(texte)
        if lent:
            time.sleep(0.15)  # laisse le temps d'annuler en vol
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    source = _ecrire_source_md(tmp_path, "doc_annule_reprise.md", nb_sections=6)
    job_id, sortie = _demarrer(source)
    _attendre_statut(sortie, {StatutJob.EN_COURS})
    assert demander_annulation(job_id) is True
    etat = _attendre_statut(sortie, {StatutJob.ANNULE, StatutJob.TERMINE})
    assert etat.statut == StatutJob.ANNULE
    faites = etat.derniere_section_completee
    assert 0 < faites < etat.total_sections
    appels_avant = len(appels)

    # Reprise : le reste part réellement, les sections déjà faites viennent du cache.
    lent = False
    sortie2 = _reprendre(source)
    etat2 = _attendre_statut(sortie2, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat2.statut == StatutJob.TERMINE
    # Aucun placeholder, sections dans l'ordre, une seule fois chacune.
    contenu = open(sortie2, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu
    assert contenu.count("SECTION 0") == 1
    # On ne retraduit pas ce qui était déjà fait avant l'annulation (cache chaud).
    assert len(appels) - appels_avant <= etat2.total_sections - faites


def test_rejeu_a_cache_chaud_ne_retraduit_que_les_trous(tmp_path, monkeypatch):
    """Un job « erreur » avec des trous : la reprise rejoue depuis 0 mais
    l'immense majorité revient du cache — seuls les trous coûtent un appel."""
    appels = []
    echouer = True

    def traducteur(texte, modele, langue_source, langue_cible,
                   termes_a_conserver=None, interruption=None):
        appels.append(texte)
        if echouer and "Section 1" in texte:
            raise OllamaErreurApplicative("échec transitoire")
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    source = _ecrire_source_md(tmp_path, "doc_rejeu.md")
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.ERREUR})
    assert etat.sections_echouees == [1]
    appels_run1 = len(appels)

    echouer = False
    sortie2 = _reprendre(source)
    etat2 = _attendre_statut(sortie2, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat2.statut == StatutJob.TERMINE
    assert etat2.sections_echouees == []
    # 4 sections rejouées, mais 3 étaient en cache → 1 seul vrai appel.
    assert len(appels) - appels_run1 == 1
    contenu = open(sortie2, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu
    # Les sections sont dans l'ordre, sans doublon.
    assert contenu.index("SECTION 0") < contenu.index("SECTION 1") < contenu.index("SECTION 2")
    assert any("Rejeu à cache chaud" in ligne for ligne in etat2.journal)


def test_rejeu_declenche_par_un_etat_legacy_sans_sections_echouees(tmp_path, monkeypatch):
    """
    Les .state.json d'avant sections_echouees se chargent avec une liste vide :
    sans le repli sur le fichier de sortie, la reprise les croirait intacts et
    laisserait les placeholders en place.
    """
    def traducteur(texte, modele, langue_source, langue_cible,
                   termes_a_conserver=None, interruption=None):
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    source = _ecrire_source_md(tmp_path, "doc_legacy.md")
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.TERMINE})

    # Simule un état legacy : trou dans la sortie, mais rien dans l'état.
    with open(sortie, "a", encoding="utf-8") as f:
        f.write("[ERREUR DE TRADUCTION — section 3]\n\n")
    etat.statut = StatutJob.ERREUR
    etat.sections_echouees = []
    sauvegarder_etat(etat)

    sortie2 = _reprendre(source)
    etat2 = _attendre_statut(sortie2, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat2.statut == StatutJob.TERMINE
    contenu = open(sortie2, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu


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
