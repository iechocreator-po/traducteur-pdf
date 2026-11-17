"""
Correctifs côté backend de l'audit de la zone frontend du 16/11/2026.

- Une reprise ou un ajout de chapitres suit les options du DOCUMENT (langues, extracteur), pas celles
  de la requête, qui viennent d'un menu (piège 2).
- /schedule/batch planifie la sélection de chapitres de chaque fichier (sinon : tout le fichier).
- Totaux de la Chronique : None quand rien n'a été mesuré (piège 14).
- Un seul nettoyage (scan multi-colonnes) actif par PDF.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import EtatJob, Langue, StatutJob
from app.services import bibliotheque, job_manager, soumission, translation_runner

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolation(store_isole, monkeypatch):
    soumission.reinitialiser_soumissions()
    # Le job est enregistré comme actif mais jamais exécuté : pas d'Ollama, pas de thread.
    monkeypatch.setattr(translation_runner, "soumettre_travail", lambda job_id, travail: None)
    yield
    soumission.reinitialiser_soumissions()


def _source(tmp_path):
    src = tmp_path / "livre.md"
    src.write_text("# Un\n\nPremier chapitre.\n\n# Deux\n\nSecond chapitre.\n", encoding="utf-8")
    return str(src)


def _etat_espagnol_en_pause(src, extracteur="marker"):
    """Travail anglais → ESPAGNOL, chapitre 0 fait, chapitre 1 restant."""
    sortie = translation_runner.build_output_path(src, "llama3.1")
    with open(sortie, "w", encoding="utf-8") as f:
        f.write("<!-- modèle : llama3.1 | source : anglais → espagnol | chapitres traduits : 0 -->\n"
                f"\n{translation_runner._marqueur_chapitre(0, 'Un')}\n\nPRIMERO\n")
    job_manager.sauvegarder_etat(EtatJob(
        job_id="ancien", chemin_pdf=src, chemin_sortie=sortie, langue_source=Langue.ANGLAIS,
        langue_cible=Langue.ESPAGNOL, modele_ollama="llama3.1", statut=StatutJob.EN_PAUSE,
        chapitres_traduits=[0], chapitres_selectionnes=[0, 1], extracteur_pdf=extracteur))
    return sortie


def _doc_du_registre(sortie):
    return next(d for d in bibliotheque.lister_documents() if d["chemin_sortie"] == sortie)


def test_reprise_garde_les_langues_et_l_extracteur_du_document(tmp_path, monkeypatch):
    src = _source(tmp_path)
    sortie = _etat_espagnol_en_pause(src)
    vus = []
    original = translation_runner._chapitres_ou_implicite
    monkeypatch.setattr(translation_runner, "_chapitres_ou_implicite",
                        lambda chemin, extracteur: vus.append(extracteur) or original(chemin, "pymupdf4llm"))
    # Les menus disent « français » et l'extracteur par défaut : ils ne doivent rien changer.
    soumission.soumettre_traduction(
        source_path=src, langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele="llama3.1",
        extracteur="pymupdf4llm", resume=True, verifier_ollama=False)
    etat = job_manager.charger_etat(sortie)
    assert etat.langue_cible == Langue.ESPAGNOL
    assert etat.extracteur_pdf == "marker"
    assert vus == ["marker"]
    assert _doc_du_registre(sortie)["langue_cible"] == "espagnol"
    assert "→ espagnol" in open(sortie, encoding="utf-8").read()


def test_ajout_de_chapitres_garde_les_langues_du_document(tmp_path):
    src = _source(tmp_path)
    sortie = _etat_espagnol_en_pause(src, extracteur=None)
    soumission.soumettre_traduction(
        source_path=src, langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele="llama3.1",
        extracteur="pymupdf4llm", resume=False, chapitres_selectionnes=[1], verifier_ollama=False)
    etat = job_manager.charger_etat(sortie)
    assert etat.langue_cible == Langue.ESPAGNOL
    # État ancien sans extracteur : celui de la requête est mémorisé pour les reprises suivantes.
    assert etat.extracteur_pdf == "pymupdf4llm"
    assert _doc_du_registre(sortie)["extracteur_pdf"] == "pymupdf4llm"


def test_nouveau_travail_memorise_son_extracteur(tmp_path):
    src = _source(tmp_path)
    soumission.soumettre_traduction(
        source_path=src, langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele="llama3.1",
        extracteur="pymupdf4llm", resume=False, verifier_ollama=False)
    sortie = translation_runner.build_output_path(src, "llama3.1")
    assert job_manager.charger_etat(sortie).extracteur_pdf == "pymupdf4llm"


# ── Planification d'un lot ───────────────────────────────────────────────────

@pytest.fixture
def planificateur(tmp_path, monkeypatch):
    from app.api import routes
    from app.services import scheduler
    monkeypatch.setattr(scheduler, "_FICHIER_JOBS", str(tmp_path / "scheduled_jobs.json"))
    monkeypatch.setattr(routes, "lister_modeles_disponibles", lambda: ["llama3.1:latest"])


def test_lot_planifie_avec_la_selection_de_chaque_fichier(tmp_path, planificateur):
    a = _source(tmp_path)
    b = tmp_path / "autre.md"
    b.write_text("# X\n\nTexte.\n", encoding="utf-8")
    r = client.post("/api/schedule/batch", json={
        "chemins": [a, str(b)], "executer_a": "2030-01-01T23:00:00Z",
        "chapitres_par_chemin": {a: [1]},
    })
    assert r.status_code == 200, r.text
    jobs = {j["chemin_pdf"] if "chemin_pdf" in j else j["chemin_source"]: j for j in r.json()["jobs"]}
    assert jobs[a]["chapitres_selectionnes"] == [1]
    assert jobs[str(b)].get("chapitres_selectionnes") is None


def test_lot_planifie_refuse_une_selection_inconnue_sans_rien_planifier(tmp_path, planificateur):
    from app.services import scheduler
    a = _source(tmp_path)
    r = client.post("/api/schedule/batch", json={
        "chemins": [a], "executer_a": "2030-01-01T23:00:00Z", "chapitres_par_chemin": {a: [9]},
    })
    assert r.status_code == 422
    assert scheduler.lister_tous_jobs() == []


# ── Chronique ────────────────────────────────────────────────────────────────

def test_totaux_none_quand_aucune_ligne_n_est_mesuree():
    from app.services.stats import _total_mesure
    lignes = [{"jetons_sortie": None}, {"jetons_sortie": None}]
    assert _total_mesure(lignes, "jetons_sortie") is None
    assert _total_mesure([{"jetons_sortie": None}, {"jetons_sortie": 5}], "jetons_sortie") == 5
    assert _total_mesure([], "jetons_sortie") is None


# ── Scan multi-colonnes ──────────────────────────────────────────────────────

def test_un_seul_nettoyage_actif_par_pdf(tmp_path, monkeypatch):
    from app.services import scan_colonnes
    monkeypatch.setattr(scan_colonnes, "tesseract_disponible", lambda: True)
    monkeypatch.setattr(scan_colonnes, "langues_tesseract", lambda: ["eng"])
    monkeypatch.setattr(scan_colonnes, "nombre_de_pages", lambda chemin: 3)
    monkeypatch.setattr(scan_colonnes, "soumettre_travail", lambda job_id, travail: None)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    premier = scan_colonnes.demarrer_scan(str(pdf))
    with pytest.raises(scan_colonnes.ScanColonnesEchec, match="déjà en cours"):
        scan_colonnes.demarrer_scan(str(pdf))
    # Job terminé (plus connu du process) : un nouveau scan est permis.
    job_manager.supprimer_job_registre(premier["job_id"])
    assert scan_colonnes.demarrer_scan(str(pdf))["job_id"] != premier["job_id"]
