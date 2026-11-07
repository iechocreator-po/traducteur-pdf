"""
Découpe d'un PDF en sections (Laboratoire). PDF synthétiques seulement : chaque page porte son numéro,
ce qui permet de vérifier que chaque page arrive dans la bonne section, et une seule fois.
"""

import hashlib
import os
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import decoupe, uploads
from app.services.decoupe import DecoupeInvalide, SectionsExistantes


def _pdf(dossier, nb_pages=12, nom="Livre.pdf", toc=None, **options_save) -> str:
    chemin = str(dossier / nom)
    doc = pymupdf.open()
    for n in range(1, nb_pages + 1):
        doc.new_page().insert_text((72, 72), f"page-{n}-fin")
    if toc:
        doc.set_toc(toc)
    doc.save(chemin, **options_save)
    doc.close()
    return chemin


def _empreinte(chemin) -> str:
    with open(chemin, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _textes(chemin) -> list[str]:
    doc = pymupdf.open(chemin)
    try:
        return [p.get_text().strip() for p in doc]
    finally:
        doc.close()


def _flags_off(monkeypatch, *noms):
    from app.api import routes
    origine = routes.charger_flags
    monkeypatch.setattr(routes, "charger_flags", lambda: {**origine(), **{n: False for n in noms}})


# ── Calcul des sections ──────────────────────────────────────────────────────

def test_bornes_tombent_juste_et_derniere_section_plus_courte():
    assert decoupe.bornes_sections(12, 4) == [(1, 4), (5, 8), (9, 12)]
    assert decoupe.bornes_sections(12, 5) == [(1, 5), (6, 10), (11, 12)]
    assert decoupe.bornes_sections(3, 1) == [(1, 1), (2, 2), (3, 3)]
    assert decoupe.bornes_sections(12, 11) == [(1, 11), (12, 12)]


@pytest.mark.parametrize("taille", [0, -1, 2.5, "4", True, None, decoupe.PAGES_PAR_SECTION_MAX + 1])
def test_bornes_refusent_une_taille_invalide(taille):
    with pytest.raises(DecoupeInvalide, match="entier"):
        decoupe.bornes_sections(12, taille)


@pytest.mark.parametrize("taille", [12, 13, 5000])
def test_bornes_refusent_une_taille_qui_ne_decoupe_rien(taille):
    with pytest.raises(DecoupeInvalide, match="moins de 12 pages"):
        decoupe.bornes_sections(12, taille)


def test_bornes_refusent_un_document_d_une_page():
    with pytest.raises(DecoupeInvalide, match="qu'une page"):
        decoupe.bornes_sections(1, 1)


def test_bornes_plafonnent_le_nombre_de_sections():
    assert len(decoupe.bornes_sections(decoupe.NB_MAX_SECTIONS, 1)) == decoupe.NB_MAX_SECTIONS
    with pytest.raises(DecoupeInvalide, match=r"au moins 2 pages"):
        decoupe.bornes_sections(decoupe.NB_MAX_SECTIONS + 1, 1)


# ── Découpe ──────────────────────────────────────────────────────────────────

def test_decouper_garde_toutes_les_pages_dans_l_ordre_et_l_original_intact(tmp_path):
    pdf = _pdf(tmp_path, 12)
    avant = _empreinte(pdf)
    r = decoupe.decouper(pdf, 5)

    assert _empreinte(pdf) == avant
    assert [s["nom"] for s in r["sections"]] == [
        "Livre_section_01_p01-05.pdf", "Livre_section_02_p06-10.pdf", "Livre_section_03_p11-12.pdf"]
    assert [s["pages"] for s in r["sections"]] == [5, 5, 2]
    assert r["pages"] == 12 and r["nb_sections"] == 3 and r["remplaces"] == 0

    textes = []
    for s in r["sections"]:
        assert os.path.dirname(s["chemin"]) == str(tmp_path)
        assert s["octets"] == os.path.getsize(s["chemin"])
        textes += _textes(s["chemin"])
    assert textes == [f"page-{n}-fin" for n in range(1, 13)]
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


def test_noms_tries_dans_l_ordre_des_pages_au_dela_de_cent_sections(tmp_path):
    pdf = _pdf(tmp_path, 120)
    noms = [s["nom"] for s in decoupe.decouper(pdf, 1)["sections"]]
    assert noms[0] == "Livre_section_001_p001-001.pdf" and noms[-1] == "Livre_section_120_p120-120.pdf"
    assert sorted(noms) == noms


def test_planifier_n_ecrit_rien(tmp_path):
    pdf = _pdf(tmp_path, 12)
    plan = decoupe.planifier(pdf, 5)
    assert plan["nb_sections"] == 3 and plan["existants"] == [] and plan["autres_decoupes"] == []
    assert os.listdir(tmp_path) == ["Livre.pdf"]


def test_relance_identique_refusee_sans_toucher_aux_fichiers(tmp_path):
    pdf = _pdf(tmp_path, 12)
    r = decoupe.decouper(pdf, 5)
    premiere = r["sections"][0]["chemin"]
    with open(premiere, "wb") as f:
        f.write(b"modifie par l'utilisateur")

    assert len(decoupe.planifier(pdf, 5)["existants"]) == 3
    with pytest.raises(SectionsExistantes) as e:
        decoupe.decouper(pdf, 5)
    assert len(e.value.noms) == 3
    with open(premiere, "rb") as f:
        assert f.read() == b"modifie par l'utilisateur"


def test_relance_avec_remplacer_reecrit_et_le_dit(tmp_path):
    pdf = _pdf(tmp_path, 12)
    premiere = decoupe.decouper(pdf, 5)["sections"][0]["chemin"]
    with open(premiere, "wb") as f:
        f.write(b"abime")

    r = decoupe.decouper(pdf, 5, remplacer=True)
    assert r["remplaces"] == 3
    assert _textes(premiere)[0] == "page-1-fin"


def test_autre_taille_ne_touche_pas_a_la_decoupe_precedente(tmp_path):
    pdf = _pdf(tmp_path, 12)
    anciens = [s["chemin"] for s in decoupe.decouper(pdf, 5)["sections"]]
    empreintes = [_empreinte(c) for c in anciens]

    plan = decoupe.planifier(pdf, 4)
    assert plan["existants"] == [] and len(plan["autres_decoupes"]) == 3

    decoupe.decouper(pdf, 4)
    assert [_empreinte(c) for c in anciens] == empreintes


def test_autres_decoupes_ne_prend_pas_les_fichiers_d_un_autre_document(tmp_path):
    pdf = _pdf(tmp_path, 12, nom="Livre.pdf")
    _pdf(tmp_path, 2, nom="Livre_section_01_p01-05_traduit.pdf")
    _pdf(tmp_path, 2, nom="Autre_section_01_p01-05.pdf")
    assert decoupe.planifier(pdf, 5)["autres_decoupes"] == []


def test_signets_suivent_leur_section_et_restent_valides(tmp_path):
    pdf = _pdf(tmp_path, 12, toc=[[1, "Un", 1], [2, "Un.1", 3], [1, "Deux", 7], [2, "Deux.1", 8], [3, "Deux.1.a", 11]])
    sections = decoupe.decouper(pdf, 5)["sections"]

    def toc(i):
        doc = pymupdf.open(sections[i]["chemin"])
        try:
            return doc.get_toc()
        finally:
            doc.close()

    assert toc(0) == [[1, "Un", 1], [2, "Un.1", 3]]
    assert toc(1) == [[1, "Deux", 2], [2, "Deux.1", 3]]
    assert toc(2) == [[1, "Deux.1.a", 1]]     # niveau ramené à 1 : un signet de niveau 3 seul serait refusé


def test_pdf_protege_refuse_sans_rien_ecrire(tmp_path):
    pdf = _pdf(tmp_path, 4, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret2")
    with pytest.raises(DecoupeInvalide, match="mot de passe"):
        decoupe.decouper(pdf, 2)
    assert os.listdir(tmp_path) == ["Livre.pdf"]


def test_pdf_a_restrictions_sans_mot_de_passe_se_decoupe(tmp_path):
    pdf = _pdf(tmp_path, 4, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="proprio", user_pw="")
    r = decoupe.decouper(pdf, 3)
    assert [p for s in r["sections"] for p in _textes(s["chemin"])] == [f"page-{n}-fin" for n in range(1, 5)]


def test_faux_pdf_refuse(tmp_path):
    faux = tmp_path / "faux.pdf"
    faux.write_text("ceci n'est pas un PDF")
    with pytest.raises(DecoupeInvalide, match="illisible"):
        decoupe.decouper(str(faux), 2)


def test_echec_en_route_retire_ce_que_ce_lancement_a_cree(tmp_path, monkeypatch):
    pdf = _pdf(tmp_path, 12)
    ecrire = decoupe.ecrire_binaire_atomique
    appels = []

    def panne_au_second(chemin, contenu):
        appels.append(chemin)
        if len(appels) == 2:
            raise OSError("disque plein")
        ecrire(chemin, contenu)

    monkeypatch.setattr(decoupe, "ecrire_binaire_atomique", panne_au_second)
    with pytest.raises(DecoupeInvalide, match=r"Section 2 .*disque plein"):
        decoupe.decouper(pdf, 5)
    assert os.listdir(tmp_path) == ["Livre.pdf"]


def test_echec_d_un_remplacement_garde_les_fichiers_deja_la(tmp_path, monkeypatch):
    pdf = _pdf(tmp_path, 12)
    anciens = [s["chemin"] for s in decoupe.decouper(pdf, 5)["sections"]]

    def panne(chemin, contenu):
        if chemin == anciens[1]:
            raise OSError("disque plein")

    monkeypatch.setattr(decoupe, "ecrire_binaire_atomique", panne)
    with pytest.raises(DecoupeInvalide):
        decoupe.decouper(pdf, 5, remplacer=True)
    assert all(os.path.exists(c) for c in anciens)


def test_purge_des_uploads_garde_un_dossier_decoupe(tmp_path, monkeypatch):
    """Importé par le navigateur, le PDF est découpé dans uploads/<hash>/ : la purge ne doit pas tout emporter."""
    racine = tmp_path / "uploads_purge"
    decoupe_faite, sans_decoupe = racine / "aaa", racine / "bbb"
    decoupe_faite.mkdir(parents=True)
    sans_decoupe.mkdir()
    pdf = _pdf(decoupe_faite, 4)
    decoupe.decouper(pdf, 2)
    _pdf(sans_decoupe, 2)
    il_y_a_un_an = time.time() - 365 * 86400
    for dossier in (decoupe_faite, sans_decoupe):
        for nom in os.listdir(dossier):
            os.utime(dossier / nom, (il_y_a_un_an, il_y_a_un_an))

    monkeypatch.setattr(uploads, "DOSSIER_UPLOADS", str(racine))
    assert uploads.purger_uploads_anciens(references=set()) == 1
    assert decoupe_faite.exists() and not sans_decoupe.exists()


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


def test_route_plan_puis_decoupe_puis_conflit_puis_remplacement(client, tmp_path):
    pdf = _pdf(tmp_path, 12)
    avant = _empreinte(pdf)

    r = client.get("/api/labo/decoupe/plan", params={"chemin_pdf": pdf, "pages_par_section": 5})
    assert r.status_code == 200
    assert r.json()["nb_sections"] == 3 and r.json()["sections"][-1]["pages"] == 2
    assert os.listdir(tmp_path) == ["Livre.pdf"]

    corps = {"chemin_pdf": pdf, "pages_par_section": 5}
    r = client.post("/api/labo/decoupe", json=corps)
    assert r.status_code == 200
    sections = r.json()["sections"]
    assert sum(len(_textes(s["chemin"])) for s in sections) == 12
    assert _empreinte(pdf) == avant

    r = client.post("/api/labo/decoupe", json=corps)
    assert r.status_code == 409 and "Remplacer" in r.json()["detail"]
    assert len(client.get("/api/labo/decoupe/plan", params={"chemin_pdf": pdf, "pages_par_section": 5}).json()["existants"]) == 3

    r = client.post("/api/labo/decoupe", json={**corps, "remplacer": True})
    assert r.status_code == 200 and r.json()["remplaces"] == 3


@pytest.mark.parametrize("taille", [0, -3, 2.5, "4", True, None, 100_001])
def test_route_refuse_une_taille_invalide_a_la_creation(client, tmp_path, taille):
    pdf = _pdf(tmp_path, 12)
    r = client.post("/api/labo/decoupe", json={"chemin_pdf": pdf, "pages_par_section": taille})
    assert r.status_code == 422
    assert os.listdir(tmp_path) == ["Livre.pdf"]


def test_route_taille_superieure_au_document(client, tmp_path):
    pdf = _pdf(tmp_path, 12)
    for r in (client.post("/api/labo/decoupe", json={"chemin_pdf": pdf, "pages_par_section": 50}),
              client.get("/api/labo/decoupe/plan", params={"chemin_pdf": pdf, "pages_par_section": 50})):
        assert r.status_code == 422 and "12 pages" in r.json()["detail"]


def test_route_plan_taille_hors_bornes(client, tmp_path):
    pdf = _pdf(tmp_path, 12)
    for taille in (0, -1, "abc"):
        assert client.get("/api/labo/decoupe/plan", params={"chemin_pdf": pdf, "pages_par_section": taille}).status_code == 422


def test_route_garde_de_chemin(client, tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("# titre")
    cas = [("relatif.pdf", 422), (str(md), 422), (str(tmp_path / "absent.pdf"), 404)]
    for chemin, attendu in cas:
        assert client.post("/api/labo/decoupe", json={"chemin_pdf": chemin, "pages_par_section": 2}).status_code == attendu
        assert client.get("/api/labo/decoupe/plan", params={"chemin_pdf": chemin, "pages_par_section": 2}).status_code == attendu


def test_route_pdf_protege(client, tmp_path):
    pdf = _pdf(tmp_path, 4, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret2")
    r = client.post("/api/labo/decoupe", json={"chemin_pdf": pdf, "pages_par_section": 2})
    assert r.status_code == 422 and "mot de passe" in r.json()["detail"]


def test_route_flag_off(client, tmp_path, monkeypatch):
    pdf = _pdf(tmp_path, 4)
    _flags_off(monkeypatch, "decoupe_pdf")
    assert client.post("/api/labo/decoupe", json={"chemin_pdf": pdf, "pages_par_section": 2}).status_code == 403
    assert client.get("/api/labo/decoupe/plan", params={"chemin_pdf": pdf, "pages_par_section": 2}).status_code == 403
    assert os.listdir(tmp_path) == ["Livre.pdf"]


def test_flag_actif_par_defaut():
    from app.config.feature_flags import FLAGS_PAR_DEFAUT
    assert FLAGS_PAR_DEFAUT["decoupe_pdf"] is True
