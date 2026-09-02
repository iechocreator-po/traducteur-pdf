"""
Tests du runner de traduction UNIFIÉ : file d'attente séquentielle, annulation,
contrôle qualité anti-résumé, cache, reprise (additive et rejeu à cache chaud)
et récupération des jobs interrompus.

Le moteur traite tout document comme une liste de chapitres (un chapitre
implicite « Document entier » s'il n'a pas de titres), avec une progression au
grain du sous-morceau. Ici chaque source a des sections `#` assez courtes pour
qu'un chapitre = un sous-morceau, donc « chapitre » et « unité de progression »
coïncident et les comptes restent lisibles.

Ollama est remplacé par de fausses fonctions de traduction (monkeypatch).
"""

import threading
import time


from app.models.schemas import Langue, StatutJob
from app.services import translation_runner
from app.services.job_manager import charger_etat, demander_annulation, sauvegarder_etat
from app.services.translator import OllamaIndisponible, OllamaErreurApplicative


def _ecrire_source_md(tmp_path, nom: str, nb_sections: int = 4) -> str:
    """Crée un Markdown de nb_sections sections d'environ 800 caractères chacune :
    court assez pour qu'un chapitre = un sous-morceau (decouper_en_chunks à 1500),
    long assez (≥ 200) pour activer le contrôle qualité anti-résumé."""
    contenu = "".join(
        f"# Section {i}\n\n" + ("mot " * 200) + "\n\n" for i in range(nb_sections)
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


def test_progression_avance_au_grain_du_sous_morceau(tmp_path, monkeypatch):
    """
    Cœur de l'incident « ça tourne sans fin » : un gros chapitre (plusieurs
    sous-morceaux) doit faire AVANCER la progression morceau par morceau, pas
    seulement à la fin du chapitre.
    """
    # Un seul chapitre, volontairement gros → plusieurs sous-morceaux.
    contenu = "# Gros chapitre\n\n" + "\n\n".join("mot " * 200 for _ in range(6))
    source = tmp_path / "doc_gros.md"
    source.write_text(contenu, encoding="utf-8")

    from app.services.pdf_extractor import chapitres_avec_contenu, decouper_en_chunks
    from app.config.settings import CHAPITRE_SOUS_CHUNK_TAILLE_MAX
    chap = chapitres_avec_contenu(str(source))[0]
    nb_sous_chunks = len(decouper_en_chunks(chap["contenu"], taille_max=CHAPITRE_SOUS_CHUNK_TAILLE_MAX))
    assert nb_sous_chunks > 1  # sinon le test ne prouve rien

    vues = []

    def traducteur(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
        # Enregistre la progression telle que la voit un client qui poll.
        etat = charger_etat(translation_runner.build_output_path(str(source), "llama3.1"))
        if etat:
            vues.append(etat.derniere_section_completee)
        time.sleep(0.02)
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    _, sortie = _demarrer(str(source))
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat.statut == StatutJob.TERMINE
    assert etat.total_sections == nb_sous_chunks
    # La progression a pris des valeurs intermédiaires strictement croissantes
    # AVANT d'atteindre le total (elle n'a pas sauté de 0 à nb d'un coup).
    intermediaires = [v for v in vues if 0 < v < nb_sous_chunks]
    assert intermediaires, f"aucune progression intermédiaire observée : {vues}"


def test_demarrer_traduction_enregistre_le_document_dans_le_store(tmp_path, monkeypatch):
    """
    Feature 328 (double écriture du registre) : `store.enregistrer_document`
    doit être appelé au lancement, comme `bibliotheque.enregistrer_document` —
    seul moyen pour `bibliotheque._charger` de disposer un jour d'un filet de
    récupération pour ce document.
    """
    from app.services import store

    source = tmp_path / "doc.md"
    source.write_text("# Section 0\n\n" + "mot " * 200, encoding="utf-8")

    def traducteur(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    _, sortie = _demarrer(str(source))
    _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})

    doc = store.lire_document(sortie)
    assert doc is not None, "le document aurait dû être enregistré dans le store aussi"
    assert doc["chemin_source"] == str(source)
    assert doc["modele"] == "llama3.1"


def test_document_sans_titre_traduit_en_chapitre_implicite(tmp_path, monkeypatch):
    """Un document sans aucun titre `#` est traité comme un chapitre implicite
    couvrant tout le texte, et se termine normalement."""
    source = tmp_path / "doc_plat.md"
    source.write_text("mot " * 200 + "\n\nautre paragraphe " * 30, encoding="utf-8")

    def traducteur(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    _, sortie = _demarrer(str(source))
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat.statut == StatutJob.TERMINE
    assert etat.total_sections >= 1
    contenu = open(sortie, encoding="utf-8").read()
    assert "MOT" in contenu.upper()
    # Pas de marqueur de chapitre pour un chapitre implicite.
    assert "=== chapitre" not in contenu


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
    # 1 chapitre = 1 sous-morceau → 1 appel initial + 1 retry
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
    """Un chapitre en échec suffit à disqualifier le job : jamais « termine »."""
    def traduction_qui_echoue_chapitre_1(texte, modele, langue_source, langue_cible,
                                         termes_a_conserver=None, interruption=None):
        if "Section 1" in texte:
            raise OllamaErreurApplicative("modèle inconnu")
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traduction_qui_echoue_chapitre_1)

    source = _ecrire_source_md(tmp_path, "doc_echec.md")
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat.statut == StatutJob.ERREUR
    assert etat.chapitres_echoues == [1]
    # Le chapitre troué n'est PAS écrit (reste re-sélectionnable), les autres oui.
    assert 1 not in etat.chapitres_traduits
    contenu = open(sortie, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu  # pas de placeholder
    assert "SECTION 0" in contenu.upper()
    assert "SECTION 1" not in contenu.upper()


def test_ollama_indisponible_arrete_le_job_sans_bruler_les_chapitres(tmp_path, monkeypatch):
    """
    Régression de l'incident des 306 pages : Ollama tombe au chapitre 1, et
    l'ancienne boucle remplissait les suivants de placeholders en 1 ms avant de
    déclarer « termine ». Le job doit s'arrêter net et rester reprenable.
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
    # Seul le chapitre 0 est fait → la reprise repartira du chapitre 1.
    assert etat.chapitres_traduits == [0]
    assert etat.derniere_section_completee == 1
    contenu = open(sortie, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu  # rien de brûlé
    # Les chapitres 2 et 3 n'ont même jamais été tentés.
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
    # Le chapitre 0 était fait/en cache → seuls 1, 2 et 3 partent réellement.
    assert len(appels) - appels_apres_panne == 3
    contenu = open(sortie2, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu
    # Chapitre 0 écrit une seule fois (le marqueur ne se répète pas → pas de doublon).
    assert contenu.count("=== chapitre 0 ") == 1


def test_reprise_apres_annulation_repart_du_chapitre_stoppe(tmp_path, monkeypatch):
    """Un job annulé en vol laisse une sortie propre : la reprise poursuit les
    chapitres restants, sans retraduire ni tronquer les précédents."""
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
    # Attend qu'AU MOINS un chapitre soit terminé avant d'annuler (sinon course :
    # l'annulation pourrait tomber avant le 1er chapitre → rien de fait).
    fin = time.time() + 15
    while time.time() < fin:
        try:
            e = charger_etat(sortie)  # peut lire un .state.json mi-écriture
        except Exception:
            e = None
        if e and e.chapitres_traduits:
            break
        time.sleep(0.02)
    assert demander_annulation(job_id) is True
    etat = _attendre_statut(sortie, {StatutJob.ANNULE, StatutJob.TERMINE})
    assert etat.statut == StatutJob.ANNULE
    faits = len(etat.chapitres_traduits)
    assert 0 < faits < etat.total_sections

    # Reprise : le reste part réellement, les chapitres déjà faits viennent du cache.
    lent = False
    sortie2 = _reprendre(source)
    etat2 = _attendre_statut(sortie2, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat2.statut == StatutJob.TERMINE
    assert sorted(etat2.chapitres_traduits) == list(range(6))  # tout est fait au final
    contenu = open(sortie2, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu
    assert contenu.count("=== chapitre 0 ") == 1  # chapitre déjà fait non réécrit
    # La reprise additive ne met en scope que les chapitres restants : ceux déjà
    # faits sont exclus (donc jamais retraduits), pas seulement servis par le cache.
    assert etat2.total_sections == 6 - faits


def test_rejeu_a_cache_chaud_recoud_dans_l_ordre(tmp_path, monkeypatch):
    """Un job « erreur » avec un trou au milieu : la reprise réécrit tout DANS
    L'ORDRE ; l'immense majorité revient du cache, seul le trou coûte un appel."""
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
    assert etat.chapitres_echoues == [1]
    appels_run1 = len(appels)

    echouer = False
    sortie2 = _reprendre(source)
    etat2 = _attendre_statut(sortie2, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat2.statut == StatutJob.TERMINE
    assert etat2.chapitres_echoues == []
    # 4 chapitres rejoués, mais 3 étaient en cache → 1 seul vrai appel.
    assert len(appels) - appels_run1 == 1
    contenu = open(sortie2, encoding="utf-8").read().upper()
    assert translation_runner.MARQUEUR_ECHEC not in contenu
    # Les chapitres sont dans l'ordre, sans doublon (grâce à la réécriture ordonnée).
    assert contenu.index("SECTION 0") < contenu.index("SECTION 1") < contenu.index("SECTION 2")
    assert any("Rejeu à cache chaud" in ligne for ligne in etat2.journal)


def test_rejeu_declenche_par_un_placeholder_legacy(tmp_path, monkeypatch):
    """
    Les sorties de l'ancien moteur « sections » peuvent contenir un placeholder
    sans que l'état ne liste d'échec : la reprise doit quand même détecter le
    trou (via le marqueur dans la sortie) et réécrire proprement.
    """
    def traducteur(texte, modele, langue_source, langue_cible,
                   termes_a_conserver=None, interruption=None):
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    source = _ecrire_source_md(tmp_path, "doc_legacy.md")
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.TERMINE})

    # Simule un état legacy : placeholder dans la sortie, mais rien dans l'état.
    with open(sortie, "a", encoding="utf-8") as f:
        f.write("[ERREUR DE TRADUCTION — section 3]\n\n")
    etat.statut = StatutJob.ERREUR
    etat.chapitres_echoues = []
    etat.sections_echouees = []
    sauvegarder_etat(etat)

    sortie2 = _reprendre(source)
    etat2 = _attendre_statut(sortie2, {StatutJob.TERMINE, StatutJob.ERREUR})

    assert etat2.statut == StatutJob.TERMINE
    contenu = open(sortie2, encoding="utf-8").read()
    assert translation_runner.MARQUEUR_ECHEC not in contenu


def test_ajout_de_chapitres_poursuit_sans_retraduire(tmp_path, monkeypatch):
    """Poursuivre un document déjà partiellement traduit avec de NOUVEAUX
    chapitres n'ajoute que ceux-là (les précédents restent, viennent du cache)."""
    appels = []

    def traducteur(texte, modele, langue_source, langue_cible,
                   termes_a_conserver=None, interruption=None):
        appels.append(texte)
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)

    source = _ecrire_source_md(tmp_path, "doc_ajout.md", nb_sections=4)

    # 1er run : seulement les chapitres 0 et 1.
    translation_runner.demarrer_traduction(
        source_path=source, langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS,
        modele="llama3.1", chapitres_selectionnes=[0, 1],
    )
    sortie = translation_runner.build_output_path(source, "llama3.1")
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat.statut == StatutJob.TERMINE
    assert sorted(etat.chapitres_traduits) == [0, 1]
    appels_run1 = len(appels)

    # 2e run : on ajoute les chapitres 2 et 3 (sans resume) — additif.
    translation_runner.demarrer_traduction(
        source_path=source, langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS,
        modele="llama3.1", chapitres_selectionnes=[2, 3],
    )
    etat2 = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat2.statut == StatutJob.TERMINE
    assert sorted(etat2.chapitres_traduits) == [0, 1, 2, 3]
    # Seuls 2 et 3 partent réellement (2 appels), 0 et 1 ne sont pas retraduits.
    assert len(appels) - appels_run1 == 2
    contenu = open(sortie, encoding="utf-8").read().upper()
    for i in range(4):
        assert f"SECTION {i}" in contenu


def test_recuperer_jobs_interrompus_bascule_en_pause(tmp_path, monkeypatch):
    """Un .state.json resté `en_cours` (serveur tué en plein run) est basculé
    `en_pause` au démarrage, pour redevenir reprenable."""
    from app.services import bibliotheque
    from app.models.schemas import EtatJob

    # Redirige le registre de la Bibliothèque vers un fichier temporaire.
    faux_registre = str(tmp_path / "bibliotheque.json")
    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", faux_registre)

    sortie = tmp_path / "doc_traduit_ll.md"
    sortie.write_text("<!-- entête -->\n", encoding="utf-8")
    state = EtatJob(
        job_id="fige", chemin_pdf=str(tmp_path / "doc.md"), chemin_sortie=str(sortie),
        langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele_ollama="llama3.1",
        statut=StatutJob.EN_COURS, derniere_section_completee=2, total_sections=5,
        total_pages=0, total_mots=100, mots_traduits=40, temps_debut=time.time(),
    )
    sauvegarder_etat(state)
    bibliotheque.enregistrer_document(
        chemin_source=str(tmp_path / "doc.md"), chemin_sortie=str(sortie),
        modele="llama3.1", langue_source="anglais", langue_cible="français",
    )

    recuperes = translation_runner.recuperer_jobs_interrompus()
    assert recuperes == 1

    etat = charger_etat(str(sortie))
    assert etat.statut == StatutJob.EN_PAUSE


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


# ── F3 : la fenêtre de duplication (phase 9, étape D) ────────────────────────

def _fausse_traduction(texte, modele, langue_source, langue_cible,
                       termes_a_conserver=None, interruption=None):
    return texte


def test_F3_un_chapitre_deja_dans_la_sortie_n_est_jamais_reecrit(tmp_path, monkeypatch):
    """
    F3 — LE défaut que la phase 9 vise.

    Le chapitre était ajouté au `.md`, puis `chapitres_traduits` n'était persisté
    qu'au `sauvegarder_etat` SUIVANT. Un arrêt dans cet intervalle laissait le
    chapitre DANS le fichier sans qu'il soit marqué comme fait : la reprise le
    retraduisait et le RÉAJOUTAIT. Duplication silencieuse, à chaque chapitre.

    On reproduit exactement cet état — sortie complète, état en retard — puis on
    reprend. Le fichier ne doit pas doubler.
    """
    monkeypatch.setattr(translation_runner, "traduire_texte", _fausse_traduction)
    source = _ecrire_source_md(tmp_path, "doc.md", nb_sections=3)

    _, sortie = _demarrer(source)
    _attendre_statut(sortie, {StatutJob.TERMINE})

    contenu_avant = open(sortie, encoding="utf-8").read()
    marqueurs_avant = contenu_avant.count("<!-- === chapitre")
    assert marqueurs_avant == 3

    # ── On fabrique la fenêtre de crash : l'état « oublie » le dernier chapitre,
    # alors que la sortie le contient déjà.
    etat = charger_etat(sortie)
    etat.chapitres_traduits = [0, 1]          # le chapitre 2 n'est plus marqué
    etat.statut = StatutJob.EN_PAUSE
    sauvegarder_etat(etat)

    # ── Reprise.
    translation_runner.demarrer_traduction(
        source_path=source,
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele="llama3.1",
        resume=True,
    )
    _attendre_statut(sortie, {StatutJob.TERMINE})

    contenu_apres = open(sortie, encoding="utf-8").read()
    assert contenu_apres.count("<!-- === chapitre") == 3, (
        "un chapitre a été réécrit alors qu'il était déjà dans la sortie"
    )
    assert contenu_apres.count("<!-- === chapitre 2 ") == 1


def test_le_contenu_et_l_etat_partent_ensemble_dans_le_store(tmp_path, monkeypatch):
    """
    Étape D : `ecrire_chapitre_et_etat` écrit les deux en UNE transaction. Après
    une traduction, le store doit contenir autant de chapitres que la sortie, et
    un état qui les déclare tous faits — jamais l'un sans l'autre.
    """
    from app.services import store

    monkeypatch.setattr(translation_runner, "traduire_texte", _fausse_traduction)
    source = _ecrire_source_md(tmp_path, "doc.md", nb_sections=3)

    _, sortie = _demarrer(source)
    _attendre_statut(sortie, {StatutJob.TERMINE})

    chapitres = store.lire_chapitres(sortie)
    assert len(chapitres) == 3
    # Les chapitres ressortent dans l'ordre du document.
    assert [c["index_chapitre"] for c in chapitres] == [0, 1, 2]

    import json
    etat_store = json.loads(store.lire_etat(sortie))
    assert sorted(etat_store["chapitres_traduits"]) == [0, 1, 2]


# ── Étape E : le .md devient un export régénéré ──────────────────────────────

def test_E_la_sortie_est_regeneree_depuis_le_store_sans_doublon(tmp_path, monkeypatch):
    """
    Le `.md` cesse d'être construit par ajouts pour devenir un export du store.
    La régénération est naturellement idempotente : réécrire deux fois donne le
    même fichier, là où l'ancien append doublait.
    """
    monkeypatch.setattr(translation_runner, "traduire_texte", _fausse_traduction)
    source = _ecrire_source_md(tmp_path, "doc.md", nb_sections=3)

    _, sortie = _demarrer(source)
    _attendre_statut(sortie, {StatutJob.TERMINE})

    contenu = open(sortie, encoding="utf-8").read()
    assert contenu.count("<!-- === chapitre") == 3
    # L'en-tête reflète les chapitres faits.
    assert "chapitres traduits : 0, 1, 2" in contenu.split("\n", 1)[0]

    # Régénérer à nouveau ne change rien.
    etat = charger_etat(sortie)
    assert translation_runner._regenerer_sortie(sortie, etat, implicite=False) is True
    assert open(sortie, encoding="utf-8").read() == contenu


def test_E_un_document_d_avant_la_phase_9_n_est_JAMAIS_ecrase(tmp_path):
    """
    LE garde-fou de l'étape E. Un document traduit avant la phase 9 n'a son
    contenu QUE dans le `.md` — le store ignore ses chapitres. Le régénérer
    depuis une base vide produirait un fichier vide et DÉTRUIRAIT la traduction.
    """
    from app.models.schemas import EtatJob, StatutJob as SJ

    sortie = tmp_path / "ancien_traduit_ll.md"
    contenu_original = (
        "<!-- modèle : llama3.1 | source : anglais → français | chapitres traduits : 0, 1 -->\n"
        "\n<!-- === chapitre 0 : Un === -->\n\ntexte du chapitre 0\n"
        "\n<!-- === chapitre 1 : Deux === -->\n\ntexte du chapitre 1\n"
    )
    sortie.write_text(contenu_original, encoding="utf-8")

    etat = EtatJob(
        job_id="job-legacy",
        chemin_pdf=str(tmp_path / "ancien.pdf"),
        chemin_sortie=str(sortie),
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele_ollama="llama3.1",
        statut=SJ.EN_PAUSE,
        chapitres_traduits=[0, 1],
    )

    # Le store ne connaît rien de ce document.
    assert translation_runner._regenerer_sortie(str(sortie), etat, implicite=False) is False
    # Et le fichier est INTACT.
    assert sortie.read_text(encoding="utf-8") == contenu_original


def test_E_un_store_incomplet_ne_declenche_pas_la_regeneration(tmp_path):
    """
    Variante plus insidieuse : le store connaît UNE PARTIE des chapitres. Une
    régénération produirait un fichier amputé — pire qu'un doublon, parce que
    silencieuse. On exige que le store couvre tout `chapitres_traduits`.
    """
    from app.models.schemas import EtatJob, StatutJob as SJ
    from app.services import store

    sortie = tmp_path / "partiel_traduit_ll.md"
    original = "en-tete\n\n<!-- === chapitre 0 : Un === -->\n\nc0\n\n<!-- === chapitre 1 : Deux === -->\n\nc1\n"
    sortie.write_text(original, encoding="utf-8")

    # Le store n'a QUE le chapitre 0.
    store.ecrire_chapitre_et_etat(str(sortie), 0, "Un", "c0", 0, "{}")

    etat = EtatJob(
        job_id="job-partiel", chemin_pdf=str(tmp_path / "p.pdf"), chemin_sortie=str(sortie),
        langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS,
        modele_ollama="llama3.1", statut=SJ.EN_PAUSE, chapitres_traduits=[0, 1],
    )
    assert translation_runner._regenerer_sortie(str(sortie), etat, implicite=False) is False
    assert sortie.read_text(encoding="utf-8") == original


def test_E_l_annexe_des_liens_survit_a_la_regeneration(tmp_path):
    """
    L'annexe est ajoutée APRÈS tous les chapitres. Une régénération naïve
    l'effacerait ; `_annexer_liens_source` ne la reconstruirait que si la source
    est encore un PDF lisible — on ne parie pas là-dessus.
    """
    from app.models.schemas import EtatJob, StatutJob as SJ
    from app.services import store

    sortie = tmp_path / "avec_annexe_traduit_ll.md"
    annexe = f"\n\n---\n\n{translation_runner.TITRE_ANNEXE_LIENS}\n\n- <https://exemple.org>\n"
    sortie.write_text("en-tete\n\ncontenu\n" + annexe, encoding="utf-8")

    store.ecrire_chapitre_et_etat(str(sortie), 0, "Un", "contenu du chapitre", 0, "{}")
    etat = EtatJob(
        job_id="j", chemin_pdf=str(tmp_path / "a.pdf"), chemin_sortie=str(sortie),
        langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS,
        modele_ollama="llama3.1", statut=SJ.EN_PAUSE, chapitres_traduits=[0],
    )
    assert translation_runner._regenerer_sortie(str(sortie), etat, implicite=False) is True

    apres = sortie.read_text(encoding="utf-8")
    assert translation_runner.TITRE_ANNEXE_LIENS in apres
    assert "https://exemple.org" in apres
    assert "contenu du chapitre" in apres


def test_E_l_annexe_du_milieu_n_avale_pas_les_chapitres_suivants(tmp_path):
    """
    RÉGRESSION jumelle de celle de la migration : `_extraire_annexe_liens`
    renvoyait tout jusqu'à la FIN du fichier. Sur un document dont l'annexe est
    au milieu (traduction en plusieurs passes), elle emportait les chapitres
    suivants — qui auraient été réinjectés à la régénération, donc dupliqués.
    """
    md = tmp_path / "doc.md"
    md.write_text(
        "<!-- en-tête -->\n"
        "\n<!-- === chapitre 0 : Un === -->\n\nc0\n"
        f"\n\n---\n\n{translation_runner.TITRE_ANNEXE_LIENS}\n\n- <https://exemple.org>\n"
        "\n<!-- === chapitre 1 : Deux === -->\n\nc1\n",
        encoding="utf-8",
    )
    annexe = translation_runner._extraire_annexe_liens(str(md))

    assert translation_runner.TITRE_ANNEXE_LIENS in annexe
    assert "exemple.org" in annexe
    assert "chapitre 1" not in annexe, "l'annexe a emporté le chapitre suivant"
    assert "c1" not in annexe


# ── Titre traduit dans la table des matières (feature bilbao 348) ───────────
# Option A retenue : le titre affiché vient du corps déjà traduit (pas d'appel
# LLM en plus), le marqueur garde le titre SOURCE pour l'alignement de la
# relecture comparative (feature 297). Trois angles couverts sur une vraie
# exécution du pipeline, pas seulement les tests unitaires de pdf_extractor :
# le cas normal, la persistance à travers une régénération depuis le store, et
# un arrêt impromptu qui laisse un document incomplet sur disque.

def test_titre_traduit_disponible_apres_une_traduction_reelle(tmp_path, monkeypatch):
    """Le titre affiché vient du corps traduit, sans appel LLM supplémentaire."""
    def traducteur(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)
    source = _ecrire_source_md(tmp_path, "doc_titres.md", nb_sections=2)
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat.statut == StatutJob.TERMINE

    from app.services.pdf_extractor import identifier_chapitres
    chapitres = identifier_chapitres(sortie)

    assert len(chapitres) == 2
    for i, chap in enumerate(chapitres):
        assert chap["titre"] == f"Section {i}"           # source, inchangé (feature 297)
        assert chap["titre_traduit"] == f"SECTION {i}"    # traduit, tiré du corps


def test_titre_traduit_survit_a_la_regeneration_depuis_le_store(tmp_path, monkeypatch):
    """
    Persistance à travers un redémarrage : après une traduction, forcer la
    réécriture du `.md` DEPUIS LE STORE (étape E, ce qui se produit après un
    redémarrage de l'app) ne doit rien changer au titre affiché — la lecture
    du titre traduit ne dépend pas du chemin d'écriture (append vs export).
    """
    def traducteur(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", traducteur)
    source = _ecrire_source_md(tmp_path, "doc_regen.md", nb_sections=2)
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat.statut == StatutJob.TERMINE

    assert translation_runner._regenerer_sortie(sortie, etat, implicite=False) is True

    from app.services.pdf_extractor import identifier_chapitres
    chapitres = identifier_chapitres(sortie)
    assert [c["titre_traduit"] for c in chapitres] == ["SECTION 0", "SECTION 1"]


def test_titre_traduit_apres_un_arret_impromptu(tmp_path, monkeypatch):
    """
    Même scénario que test_ollama_indisponible_arrete_le_job_sans_bruler_les_chapitres :
    Ollama meurt au chapitre 1, le job finit en ERREUR, seul le chapitre 0 est
    sur disque. Lire les titres d'un document INCOMPLET (chapitres 1 à 3
    absents, pas seulement vides) ne doit ni planter ni inventer un titre pour
    ce qui n'a jamais été écrit.
    """
    def ollama_mort(texte, modele, langue_source, langue_cible, termes_a_conserver=None, interruption=None):
        if "Section 1" in texte:
            raise OllamaIndisponible("Ollama injoignable, budget épuisé")
        return texte.upper()

    monkeypatch.setattr(translation_runner, "traduire_texte", ollama_mort)
    source = _ecrire_source_md(tmp_path, "doc_arret.md")  # 4 sections par défaut
    _, sortie = _demarrer(source)
    etat = _attendre_statut(sortie, {StatutJob.TERMINE, StatutJob.ERREUR})
    assert etat.statut == StatutJob.ERREUR
    assert etat.chapitres_traduits == [0]

    from app.services.pdf_extractor import identifier_chapitres
    chapitres = identifier_chapitres(sortie)  # ne doit lever aucune exception

    assert len(chapitres) == 1  # seul le chapitre 0 a un marqueur dans le fichier
    assert chapitres[0]["titre"] == "Section 0"
    assert chapitres[0]["titre_traduit"] == "SECTION 0"


def test_titre_traduit_absent_sur_un_fichier_tronque_par_un_crash(tmp_path):
    """
    `_ecrire_chapitre` ajoute au fichier avec un simple `open(..., "a")`, pas
    une écriture atomique (voir sa docstring : le `.md` reste la source de
    vérité jusqu'à l'étape E) — un crash en plein milieu de la ligne de titre
    est un scénario réel, pas hypothétique. Reconstitué à la main ici : le
    fichier s'arrête net après « # » sans le reste du titre.
    """
    sortie = tmp_path / "doc_tronque_ll.md"
    sortie.write_text(
        "<!-- en-tête -->\n"
        "\n<!-- === chapitre 0 : Un === -->\n\n# ",
        encoding="utf-8",
    )

    from app.services.pdf_extractor import identifier_chapitres
    chapitres = identifier_chapitres(str(sortie))  # ne doit pas lever

    assert chapitres[0]["titre"] == "Un"         # le marqueur, lui, est intact
    assert chapitres[0]["titre_traduit"] is None  # jamais un titre coupé en deux
