"""
Tests des routes API, via le client de test FastAPI (aucun serveur réel nécessaire).
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.api.routes.lister_modeles_disponibles", return_value=["llama3.1"])
def test_health_check_ok(mock_lister):
    reponse = client.get("/api/health")
    assert reponse.status_code == 200
    assert reponse.json()["ollama_accessible"] == "oui"


@patch("app.api.routes.lister_modeles_disponibles", return_value=[])
def test_health_check_ollama_inaccessible(mock_lister):
    reponse = client.get("/api/health")
    assert reponse.status_code == 200
    assert reponse.json()["ollama_accessible"] == "non"


def test_feature_flags_retourne_un_dictionnaire():
    reponse = client.get("/api/feature-flags")
    assert reponse.status_code == 200
    assert isinstance(reponse.json(), dict)


def test_analyser_document_fichier_introuvable():
    reponse = client.post("/api/analyser", json={"chemin_pdf": "/chemin/inexistant.pdf"})
    assert reponse.status_code == 404


def test_glossaire_lecture_et_ecriture(tmp_path, monkeypatch):
    from app.services import glossaire
    monkeypatch.setattr(glossaire, "_FICHIER_GLOSSAIRE", str(tmp_path / "glossaire.json"))

    reponse = client.get("/api/glossaire")
    assert reponse.status_code == 200
    assert reponse.json() == {"termes": []}

    reponse = client.put("/api/glossaire", json={"termes": [" FastAPI ", "", "fastapi", "Ollama"]})
    assert reponse.status_code == 200
    assert reponse.json() == {"termes": ["FastAPI", "Ollama"]}

    reponse = client.get("/api/glossaire")
    assert reponse.json() == {"termes": ["FastAPI", "Ollama"]}


def test_schedule_batch_planifie_plusieurs_fichiers(tmp_path, monkeypatch):
    from app.services import scheduler
    monkeypatch.setattr(scheduler, "_FICHIER_JOBS", str(tmp_path / "scheduled_jobs.json"))

    doc1 = tmp_path / "doc1.md"
    doc2 = tmp_path / "doc2.md"
    doc1.write_text("# Un")
    doc2.write_text("# Deux")

    reponse = client.post("/api/schedule/batch", json={
        "chemins": [str(doc1), str(doc2)],
        "executer_a": "2030-01-01T23:00:00",
    })
    assert reponse.status_code == 200
    jobs = reponse.json()["jobs"]
    assert len(jobs) == 2
    assert all(j["statut"] == "planifie" for j in jobs)

    reponse = client.get("/api/scheduled/tous")
    assert len(reponse.json()["jobs"]) == 2


def test_schedule_batch_fichier_introuvable(tmp_path, monkeypatch):
    from app.services import scheduler
    monkeypatch.setattr(scheduler, "_FICHIER_JOBS", str(tmp_path / "scheduled_jobs.json"))

    reponse = client.post("/api/schedule/batch", json={
        "chemins": ["/chemin/inexistant.pdf"],
        "executer_a": "2030-01-01T23:00:00",
    })
    assert reponse.status_code == 404


def test_schedule_batch_liste_vide_refusee():
    reponse = client.post("/api/schedule/batch", json={
        "chemins": [],
        "executer_a": "2030-01-01T23:00:00",
    })
    assert reponse.status_code == 422


def test_tts_moteurs_liste_les_moteurs():
    reponse = client.get("/api/tts/moteurs")
    assert reponse.status_code == 200
    moteurs = reponse.json()["moteurs"]
    ids = [m["id"] for m in moteurs]
    assert "piper" in ids and "kokoro" in ids
    for m in moteurs:
        assert "disponible" in m and "voix" in m


def test_tts_generation_refuse_un_non_markdown(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    reponse = client.post("/api/tts", json={
        "chemin_md": str(pdf), "moteur": "piper", "voix": "v",
    })
    assert reponse.status_code == 422


def test_tts_generation_fichier_introuvable():
    reponse = client.post("/api/tts", json={
        "chemin_md": "/chemin/inexistant.md", "moteur": "piper", "voix": "v",
    })
    assert reponse.status_code == 404


def test_tts_extrait_texte_vide_refuse():
    reponse = client.post("/api/tts/extrait", json={
        "texte": "   ", "moteur": "piper", "voix": "v",
    })
    assert reponse.status_code == 422


# ── Fiche d'étude ─────────────────────────────────────────────────────────────

def test_etude_fichier_introuvable():
    reponse = client.post("/api/etude", json={
        "chemin_md": "/chemin/inexistant.md",
        "chapitres_selectionnes": [0],
    })
    assert reponse.status_code == 404


def test_etude_sans_chapitres_rejete(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Titre\n\nContenu.")
    reponse = client.post("/api/etude", json={
        "chemin_md": str(source),
        "chapitres_selectionnes": [],
    })
    assert reponse.status_code == 422


def test_etude_nb_points_hors_bornes_rejete(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Titre\n\nContenu.")
    reponse = client.post("/api/etude", json={
        "chemin_md": str(source),
        "chapitres_selectionnes": [0],
        "nb_points": 0,
    })
    assert reponse.status_code == 422


def test_etude_chapitre_inconnu_rejete(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Titre\n\nContenu.")
    reponse = client.post("/api/etude", json={
        "chemin_md": str(source),
        "chapitres_selectionnes": [42],
    })
    assert reponse.status_code == 422


def test_etude_statut_absent_retourne_null(tmp_path):
    reponse = client.get("/api/etude/statut", params={"chemin_source": str(tmp_path / "rien.md")})
    assert reponse.status_code == 200
    assert reponse.json() is None


# ── Bibliothèque / refonte Workflow ──────────────────────────────────────────

def test_bibliotheque_vide(tmp_path, monkeypatch):
    from app.services import bibliotheque
    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", str(tmp_path / "biblio.json"))
    reponse = client.get("/api/bibliotheque")
    assert reponse.status_code == 200
    assert reponse.json() == {"documents": []}


def test_contenu_chapitre(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Un\n\nContenu du chapitre un.\n\n# Deux\n\nContenu du chapitre deux.")
    reponse = client.post("/api/chapitres/contenu", json={"chemin_md": str(source), "index": 1})
    assert reponse.status_code == 200
    data = reponse.json()
    assert data["titre"] == "Deux"
    assert "Contenu du chapitre deux." in data["contenu"]


def test_contenu_chapitre_index_inconnu(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Un\n\nContenu.")
    reponse = client.post("/api/chapitres/contenu", json={"chemin_md": str(source), "index": 9})
    assert reponse.status_code == 404


def test_contenu_chapitre_fichier_introuvable():
    reponse = client.post("/api/chapitres/contenu", json={"chemin_md": "/inexistant.md", "index": 0})
    assert reponse.status_code == 404


def test_interet_enregistre(tmp_path, monkeypatch):
    from app.services import interet
    log = tmp_path / "interet.log"
    monkeypatch.setattr(interet, "_FICHIER_LOG", str(log))
    reponse = client.post("/api/interet", json={
        "fonctionnalite": "clonage_voix", "email": "jp@example.com",
    })
    assert reponse.status_code == 200
    assert reponse.json() == {"statut": "enregistre"}
    assert "clonage_voix" in log.read_text()


def test_interet_email_invalide():
    reponse = client.post("/api/interet", json={
        "fonctionnalite": "export_pdf", "email": "pas-un-email",
    })
    assert reponse.status_code == 422


def test_interet_fonctionnalite_vide():
    reponse = client.post("/api/interet", json={"fonctionnalite": "  ", "email": "jp@example.com"})
    assert reponse.status_code == 422


def test_flags_teaser_actives():
    reponse = client.get("/api/feature-flags")
    flags = reponse.json()
    assert flags["teaser_voix_personnalisees"] is True
    assert flags["teaser_export_pdf"] is True
