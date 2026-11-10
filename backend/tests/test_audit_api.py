"""
Audit de la zone « api » du 10/11/2026 : un test par constat corrigé. Aucune donnée réelle : sources
synthétiques dans tmp_path, store isolé, moteur jamais exécuté (la file de travaux est neutralisée).
"""

import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.config import feature_flags
from app.main import app
from app.models.schemas import EtatJob, Langue, StatutJob
from app.services import job_manager, soumission, translation_runner

client = TestClient(app)
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def _etat_en_pause(src, statut=StatutJob.EN_PAUSE):
    """Chapitre 0 traduit et écrit, chapitre 1 restant : une sortie propre, reprenable en append."""
    sortie = translation_runner.build_output_path(src, "llama3.1")
    with open(sortie, "w", encoding="utf-8") as f:
        f.write("<!-- modèle : llama3.1 | source : anglais → français | chapitres traduits : 0 -->\n"
                f"\n{translation_runner._marqueur_chapitre(0, 'Un')}\n\nPREMIER TRADUIT\n")
    job_manager.sauvegarder_etat(EtatJob(
        job_id="ancien", chemin_pdf=src, chemin_sortie=sortie, langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS, modele_ollama="llama3.1", statut=statut, chapitres_traduits=[0],
        chapitres_selectionnes=[0, 1]))
    return sortie


def _reprendre(src):
    return soumission.soumettre_traduction(
        source_path=src, langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele="llama3.1",
        extracteur="pymupdf4llm", resume=True, verifier_ollama=False)


# ── Reprise ──────────────────────────────────────────────────────────────────

def test_double_reprendre_ne_tronque_pas_la_traduction(tmp_path):
    src = _source(tmp_path)
    sortie = _etat_en_pause(src)
    job_1, deja_1 = _reprendre(src)
    job_2, deja_2 = _reprendre(src)
    assert (deja_1, deja_2) == (False, True) and job_2 == job_1
    assert "PREMIER TRADUIT" in open(sortie, encoding="utf-8").read()
    assert job_manager.charger_etat(sortie).chapitres_traduits == [0]


def test_reprise_d_un_etat_en_attente_orphelin_ne_repart_pas_de_zero(tmp_path):
    """État resté `en_attente` sans job vivant : c'est une reprise, pas un document neuf."""
    src = _source(tmp_path)
    sortie = _etat_en_pause(src, StatutJob.EN_ATTENTE)
    _reprendre(src)
    assert "PREMIER TRADUIT" in open(sortie, encoding="utf-8").read()
    assert job_manager.charger_etat(sortie).chapitres_traduits == [0]


def test_check_resume_rend_l_etat_du_modele_demande(tmp_path):
    src = _source(tmp_path)
    for modele, statut in (("llama3.1", StatutJob.EN_PAUSE), ("qwen2.5", StatutJob.TERMINE)):
        sortie = translation_runner.build_output_path(src, modele)
        job_manager.sauvegarder_etat(EtatJob(
            job_id=modele, chemin_pdf=src, chemin_sortie=sortie, langue_source=Langue.ANGLAIS,
            langue_cible=Langue.FRANCAIS, modele_ollama=modele, statut=statut))
    for modele in ("llama3.1", "qwen2.5"):
        r = client.post("/api/check-resume", json={"chemin_md": src, "modele_ollama": modele})
        assert r.status_code == 200 and r.json()["modele_ollama"] == modele


# ── Sélection, extracteur, langue, planification ─────────────────────────────

@pytest.mark.parametrize("selection", [[], [-1], [0, 999]])
def test_selection_vide_ou_inconnue_refusee_sans_toucher_a_la_sortie(tmp_path, selection, monkeypatch):
    monkeypatch.setattr("app.services.translator.verifier_ollama_pret", lambda modele: (True, "ok"))
    src = _source(tmp_path)
    sortie = _etat_en_pause(src, StatutJob.TERMINE)
    r = client.post("/api/translate", json={"chemin_md": src, "chapitres_selectionnes": selection})
    assert r.status_code == 422
    assert "PREMIER TRADUIT" in open(sortie, encoding="utf-8").read()


def test_extracteur_inconnu_en_422_et_non_500(tmp_path):
    src = _source(tmp_path)
    for route, corps in (("/api/chapitres", {"chemin_md": src}), ("/api/translate", {"chemin_md": src}),
                         ("/api/convert", {"chemin_pdf": "/x.pdf"})):
        assert client.post(route, json={**corps, "extracteur_pdf": "foo"}).status_code == 422, route


def test_langue_invalide_dit_que_c_est_la_langue(tmp_path):
    r = client.post("/api/translate", json={"chemin_md": _source(tmp_path), "langue_cible": "allemand"})
    assert r.status_code == 422
    assert r.json()["erreur"]["code"] == "langue_invalide"
    assert "chemin" not in r.json()["erreur"]["remediation"]


@pytest.fixture
def planificateur(tmp_path, monkeypatch):
    from app.api import routes
    from app.services import scheduler
    monkeypatch.setattr(scheduler, "_FICHIER_JOBS", str(tmp_path / "scheduled_jobs.json"))
    monkeypatch.setattr(routes, "lister_modeles_disponibles", lambda: ["llama3.1:latest", "qwen2.5:latest"])


@pytest.mark.parametrize("champs,attendu", [
    ({"executer_a": "2030-01-01T23:00:00"}, "fuseau"),
    ({"executer_a": "2020-01-01T23:00:00Z"}, "passée"),
    ({"modele_ollama": "llama3.l-faute"}, "non installé"),
    ({"extracteur_pdf": "nimporte"}, "Extracteur"),
    ({"chapitres_selectionnes": [7]}, "absent"),
    ({"chapitres_selectionnes": []}, "Aucun chapitre"),
])
def test_planification_validee_a_la_creation(tmp_path, planificateur, champs, attendu):
    corps = {"chemin_md": _source(tmp_path), "executer_a": "2030-01-01T23:00:00Z", **champs}
    r = client.post("/api/schedule", json=corps)
    assert r.status_code == 422 and attendu in json.dumps(r.json(), ensure_ascii=False)


def test_planification_valide_acceptee_et_lot_vide_refuse(tmp_path, planificateur):
    ok = client.post("/api/schedule", json={"chemin_md": _source(tmp_path), "executer_a": "2030-01-01T23:00:00Z",
                                            "chapitres_selectionnes": [1]})
    assert ok.status_code == 200 and ok.json()["statut"] == "planifie"
    vide = client.post("/api/schedule/batch", json={"chemins": ["   "], "executer_a": "2030-01-01T23:00:00Z"})
    assert vide.status_code == 422


# ── Garde du serveur local (PC-17) ───────────────────────────────────────────

def test_origine_null_refusee(tmp_path):
    preflight = client.options("/api/translate", headers={
        "Origin": "null", "Access-Control-Request-Method": "POST"})
    assert preflight.headers.get("access-control-allow-origin") is None
    r = client.post("/api/upload", headers={"Origin": "null"}, files={"fichier": ("a.md", b"# A\n")})
    assert r.status_code == 403


def test_post_simple_cross_site_refuse_avant_la_route(tmp_path, monkeypatch):
    """Paramètre d'URL, sans préflight : la route s'exécutait et effaçait le scan."""
    from app.services import scan_colonnes
    appels = []
    monkeypatch.setattr(scan_colonnes, "reinitialiser", lambda chemin: appels.append(chemin) or [])
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    url = f"/api/labo/scan-colonnes/reinitialiser?chemin_pdf={pdf}"
    assert client.post(url, headers={"Origin": "https://attaquant.example"}).status_code == 403
    assert client.post("/api/job/x/pause", headers={"Origin": "https://attaquant.example"}).status_code == 403
    assert appels == []
    assert client.post(url, headers={"Origin": "http://localhost:5500"}).status_code == 200
    assert client.post(url).status_code == 200                       # sans Origin : curl, client Swift
    assert len(appels) == 2


def test_hote_etranger_refuse():
    assert client.get("/api/feature-flags", headers={"Host": "attaquant.example"}).status_code == 400
    assert client.get("/api/feature-flags", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/api/feature-flags", headers={"Host": "127.0.0.1:8000"}).status_code == 200


# ── Démarrage : rien ne touche aux données à l'import ────────────────────────

def test_importer_app_main_ne_lance_ni_recuperation_ni_purge_ni_planificateur():
    code = ("import app.services.recuperation as r, app.services.uploads as u, app.services.scheduler as s\n"
            "appels = []\n"
            "r.recuperer_tout = lambda: appels.append('recuperation')\n"
            "u.purger_uploads_anciens = lambda: appels.append('purge')\n"
            "s.demarrer_surveillance = lambda: appels.append('planificateur')\n"
            "import app.main\n"
            "assert appels == [], appels\n"
            "from fastapi.testclient import TestClient\n"
            "with TestClient(app.main.app):\n"
            "    pass\n"
            "assert appels == ['planificateur', 'purge', 'recuperation'], appels\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


# ── Feature flags ────────────────────────────────────────────────────────────

@pytest.fixture
def sources_flags(tmp_path, monkeypatch):
    bilbao, local = tmp_path / "bilbao.features.json", tmp_path / "feature_flags.json"
    monkeypatch.setattr(feature_flags, "CHEMIN_FLAGS_BILBAO", bilbao)
    monkeypatch.setattr(feature_flags, "CHEMIN_FLAGS_DEFAUT", local)
    monkeypatch.delenv("FEATURE_DECOUPE_PDF", raising=False)
    return bilbao, local


def test_flag_en_chaine_ignore(sources_flags):
    bilbao, local = sources_flags
    bilbao.write_text(json.dumps({"flags": {"decoupe_pdf": "false", "signature_pdf": False}}))
    flags = feature_flags.charger_flags()
    assert flags["decoupe_pdf"] is True and flags["signature_pdf"] is False      # "false" ignoré : le défaut tient
    local.write_text(json.dumps({"decoupe_pdf": "false"}))
    assert feature_flags.est_active("decoupe_pdf") is True


@pytest.mark.parametrize("contenu", ["{tronqué", "[1, 2]", "\xff\xfe"])
def test_fichiers_de_flags_invalides_toleres(sources_flags, contenu):
    bilbao, local = sources_flags
    bilbao.write_text(contenu, encoding="latin-1")
    local.write_text(contenu, encoding="latin-1")
    assert feature_flags.charger_flags() == feature_flags.FLAGS_PAR_DEFAUT
    assert client.get("/api/feature-flags").status_code == 200


@pytest.mark.parametrize("valeur,attendu", [("0", False), ("false", False), ("non", False), ("1", True),
                                            (" TRUE ", True), ("yes", True), ("on", True)])
def test_variable_d_environnement_des_flags(sources_flags, monkeypatch, valeur, attendu):
    monkeypatch.setenv("FEATURE_DECOUPE_PDF", valeur)
    assert feature_flags.est_active("decoupe_pdf") is attendu


def test_feature_flags_local_jamais_versionne():
    racine = os.path.dirname(BACKEND)
    r = subprocess.run(["git", "ls-files", "backend/app/config/feature_flags.json"], cwd=racine,
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("pas de dépôt git")
    assert r.stdout.strip() == ""


# ── Réponses d'erreur typées (macOS lit `erreur.code`) ───────────────────────

def test_preflight_en_echec_rend_le_champ_erreur(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.translator.verifier_ollama_pret", lambda modele: (False, "figé"))
    r = client.post("/api/translate", json={"chemin_md": _source(tmp_path)})
    assert r.status_code == 503
    assert r.json()["erreur"]["code"] == "ollama_indisponible" and r.json()["erreur"]["remediation"]
