"""
Tests des routes API, via le client de test FastAPI (aucun serveur réel nécessaire).
"""

import os
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
    assert flags["teaser_export_pdf"] is True


def test_tts_audio_sert_le_wav(tmp_path):
    wav = tmp_path / "doc_audio.wav"
    wav.write_bytes(b"RIFF----WAVE")
    reponse = client.get("/api/tts/audio", params={"chemin_wav": str(wav)})
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "audio/wav"
    assert reponse.content.startswith(b"RIFF")


def test_tts_audio_introuvable():
    reponse = client.get("/api/tts/audio", params={"chemin_wav": "/inexistant.wav"})
    assert reponse.status_code == 404


def test_tts_audio_extension_invalide(tmp_path):
    fichier = tmp_path / "notes.txt"
    fichier.write_text("x")
    reponse = client.get("/api/tts/audio", params={"chemin_wav": str(fichier)})
    assert reponse.status_code == 422


# ── Voix clonées ──────────────────────────────────────────────────────────────

def _wav_bytes(duree_secondes=3.5, frequence=8000):
    import io
    import wave
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(frequence)
        wav.writeframes(b"\x00\x00" * int(frequence * duree_secondes))
    return tampon.getvalue()


def test_capturer_voix_puis_lister(monkeypatch):
    from app.services import voix_clonage_runner
    monkeypatch.setattr(voix_clonage_runner, "venv_openvoice_disponible", lambda: False)

    reponse = client.post(
        "/api/voix-clonees/capturer",
        data={"nom": "Ma voix"},
        files={"fichier": ("echantillon.wav", _wav_bytes(), "audio/wav")},
    )
    assert reponse.status_code == 200
    id_voix = reponse.json()["id_voix"]

    reponse = client.get("/api/voix-clonees")
    voix = reponse.json()["voix"]
    assert len(voix) == 1
    assert voix[0]["id"] == id_voix
    assert voix[0]["nom"] == "Ma voix"
    # venv non disponible → le traitement démarré échoue immédiatement en erreur
    assert voix[0]["statut"] == "erreur"


def test_capturer_voix_echantillon_trop_court():
    reponse = client.post(
        "/api/voix-clonees/capturer",
        data={"nom": "Ma voix"},
        files={"fichier": ("echantillon.wav", _wav_bytes(duree_secondes=1.0), "audio/wav")},
    )
    assert reponse.status_code == 422


def test_capturer_voix_fichier_invalide():
    reponse = client.post(
        "/api/voix-clonees/capturer",
        data={"nom": "Ma voix"},
        files={"fichier": ("echantillon.wav", b"pas un wav", "audio/wav")},
    )
    assert reponse.status_code == 422


def test_supprimer_voix_clonee(monkeypatch):
    from app.services import voix_clonage_runner
    monkeypatch.setattr(voix_clonage_runner, "venv_openvoice_disponible", lambda: False)

    reponse = client.post(
        "/api/voix-clonees/capturer",
        data={"nom": "Ma voix"},
        files={"fichier": ("echantillon.wav", _wav_bytes(), "audio/wav")},
    )
    id_voix = reponse.json()["id_voix"]

    reponse = client.delete(f"/api/voix-clonees/{id_voix}")
    assert reponse.status_code == 200
    assert client.get("/api/voix-clonees").json()["voix"] == []


def test_supprimer_voix_clonee_introuvable():
    reponse = client.delete("/api/voix-clonees/inconnu")
    assert reponse.status_code == 404


# ── Upload de documents (T1/T2) ───────────────────────────────────────────────

def _pdf_bytes() -> bytes:
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Bonjour")
    c.save()
    return buf.getvalue()


def test_upload_pdf_valide():
    reponse = client.post("/api/upload", files={"fichier": ("cours.pdf", _pdf_bytes(), "application/pdf")})
    assert reponse.status_code == 200
    data = reponse.json()
    assert data["type"] == "PDF"
    assert data["chemin"].endswith("/cours.pdf")


def test_upload_md_puis_reinjectable_dans_chapitres():
    """Le chemin retourné par /upload doit repartir tel quel dans le flux existant."""
    contenu = b"# Chapitre 1\n\ntexte\n\n# Chapitre 2\n\nsuite\n"
    up = client.post("/api/upload", files={"fichier": ("notes.md", contenu, "text/markdown")})
    assert up.status_code == 200
    chemin = up.json()["chemin"]
    assert chemin.endswith(".md")

    chap = client.post("/api/chapitres", json={"chemin_md": chemin})
    assert chap.status_code == 200
    assert len(chap.json()["chapitres"]) == 2


def test_upload_anti_evasion_du_nom():
    reponse = client.post(
        "/api/upload",
        files={"fichier": ("../../../etc/passwd", _pdf_bytes(), "application/pdf")},
    )
    assert reponse.status_code == 200
    from app.services import uploads
    reel = os.path.realpath(reponse.json()["chemin"])
    racine = os.path.realpath(uploads.DOSSIER_UPLOADS)
    assert os.path.commonpath([reel, racine]) == racine


def test_upload_contenu_prime_sur_extension():
    # Nommé .md mais contient un PDF → stocké en .pdf.
    reponse = client.post("/api/upload", files={"fichier": ("piege.md", _pdf_bytes(), "text/markdown")})
    assert reponse.json()["chemin"].endswith(".pdf")


def test_upload_binaire_non_md_rejete():
    reponse = client.post("/api/upload", files={"fichier": ("x.md", b"\xff\xfe\x00parasite", "text/plain")})
    assert reponse.status_code == 422


def test_upload_vide_rejete():
    reponse = client.post("/api/upload", files={"fichier": ("vide.md", b"", "text/plain")})
    assert reponse.status_code == 422


def test_upload_origine_tierce_refusee():
    reponse = client.post(
        "/api/upload",
        files={"fichier": ("x.pdf", _pdf_bytes(), "application/pdf")},
        headers={"origin": "https://evil.example"},
    )
    assert reponse.status_code == 403
