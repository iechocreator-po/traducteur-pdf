"""
Documents complexes : corrections automatiques de la détection (page titre, table des matières) et
réglage manuel des régions page par page (mode de chaque demi-page, gouttière, bords de colonnes,
illustrations retirées). Aucune donnée réelle : pages synthétiques et PDF générés.
"""

import json
import os

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from app.services import reconstruction as R
from app.services import scan_colonnes as S
from tests.test_scan_colonnes import PAPIER, _pdf_trois_colonnes, page_synthetique

client = TestClient(app)


# ── Corrections automatiques ─────────────────────────────────────────────────

def _page_titre(largeur=820, hauteur=900):
    """Page de titre : deux lignes de gros mots décalés (leurs espaces ne s'alignent pas, comme dans un vrai
    titre centré), du blanc partout ailleurs. Sans la règle, la détection en tirait une colonne étroite."""
    img = Image.new("L", (largeur, hauteur), PAPIER)
    d = ImageDraw.Draw(img)
    for y, depart in ((150, 220), (700, 265)):
        for k in range(3):
            x = depart + k * 120
            for lettre in range(4):
                d.rectangle([x + lettre * 23, y, x + lettre * 23 + 19, y + 40], fill=20)
    return img


def test_page_titre_lue_en_pleine_largeur_et_signalee():
    """Cycle I p. 1 : quelques grosses lettres formaient une « colonne » étroite qui coupait le titre."""
    mise = S.analyser_mise_en_page(_page_titre(), double_page=False)
    moitie = mise["moities"][0]
    assert moitie["pleine_largeur"]
    assert moitie["alertes"] and "page titre" in moitie["alertes"][0]


def test_page_de_texte_sans_alerte():
    img, _ = page_synthetique()
    moitie = S.analyser_mise_en_page(img, double_page=False)["moities"][0]
    assert len(moitie["colonnes"]) == 3 and moitie["alertes"] == [] and moitie["mode"] == "auto"


def _profils_table_des_matieres(lignes=100, largeur=400):
    """Colonne de titres (20–150) ; numéros de page (180–230) sur une ligne sur cinq ; bande au bord (385–400)."""
    p = np.zeros((lignes, largeur))
    p[:, 20:150] = 0.2
    p[::5, 180:230] = 0.05
    p[::5, 385:400] = 0.05
    return p


def test_numeros_de_table_des_matieres_rattaches_a_leur_colonne():
    journal = []
    sortie = S._rattacher_encre_clairsemee(_profils_table_des_matieres(), [(20, 150)], journal)
    assert sortie == [(20, 230)]
    assert journal and "table des matières" in journal[0]


def test_bande_clairsemee_au_bord_ou_loin_jamais_rattachee():
    """Ombre de reliure, bord d'une illustration : collés au bord de la demi-page. Trop loin : autre chose."""
    p = _profils_table_des_matieres()
    p[:, 180:230] = 0
    assert S._rattacher_encre_clairsemee(p, [(20, 150)], []) == [(20, 150)]
    p[::5, 260:300] = 0.05            # 110 px après la colonne : au-delà de ECART_MAX_RATTACHEMENT
    assert S._rattacher_encre_clairsemee(p, [(20, 150)], []) == [(20, 150)]


# ── Réglage manuel : forme enregistrée ───────────────────────────────────────

def test_normaliser_reglage_ne_garde_que_ce_qui_change_quelque_chose():
    assert S.normaliser_reglage(None) is None
    assert S.normaliser_reglage({"moities": [{"mode": "auto"}, {"mode": "auto"}]}) is None
    assert S.normaliser_reglage({"moities": [{"mode": "pirate"}]}) is None
    assert S.normaliser_reglage({"moities": [{"mode": "colonnes", "colonnes": [[10, 15]]}]}) is None  # trop étroite
    r = S.normaliser_reglage({"gouttiere": 400.0, "moities": [{"mode": "image"}, {"mode": "colonnes",
                              "colonnes": [[600, 800], [420, 580], "x"]}],
                              "illustrations_retirees": [[1, 2, 3, 4], [1, "a", 3, 4]], "autre": 1})
    assert r == {"gouttiere": 400, "moities": [{"mode": "image"}, {"mode": "colonnes", "colonnes": [[420, 580], [600, 800]]}],
                 "illustrations_retirees": [[1, 2, 3, 4]]}


def _double_page():
    g, _ = page_synthetique()
    d, _ = page_synthetique()
    double = Image.new("RGB", (g.width + d.width + 40, g.height), (PAPIER,) * 3)
    double.paste(g, (0, 0))
    double.paste(d, (g.width + 40, 0))
    return double, g.width


def test_reglage_manuel_prioritaire_sur_la_detection():
    img, largeur_gauche = _double_page()
    reglage = {"gouttiere": largeur_gauche + 30,
               "moities": [{"mode": "colonnes", "colonnes": [[50, 400], [300, 2000]]}, {"mode": "ignorer"}]}
    mise = S.analyser_mise_en_page(img, double_page=True, reglage=reglage)
    assert mise["gouttiere"] == largeur_gauche + 30
    gauche, droite = mise["moities"]
    assert gauche["mode"] == "colonnes"
    assert gauche["colonnes"] == [(50, 400), (300, largeur_gauche + 30)]     # bornée à la demi-page
    assert droite["mode"] == "ignorer" and droite["colonnes"] == [] and not droite["pleine_largeur"]


def test_reglage_d_un_autre_decoupage_ignore_et_gouttiere_absurde_ignoree():
    img, _ = _double_page()
    auto = S.analyser_mise_en_page(img, double_page=True)
    mise = S.analyser_mise_en_page(img, double_page=True, reglage={"gouttiere": 5, "moities": [{"mode": "image"}]})
    assert mise["gouttiere"] == auto["gouttiere"]
    assert [m["mode"] for m in mise["moities"]] == ["auto", "auto"]


def test_demi_page_en_image_n_est_pas_lue_et_devient_une_illustration():
    img, _ = _double_page()
    reglage = {"moities": [{"mode": "image"}, {"mode": "auto"}]}
    mise = S.analyser_mise_en_page(img, double_page=True, reglage=reglage)
    assert mise["moities"][0]["colonnes"] == []
    illus = R.illustrations_page(img, mise, reglage)
    assert len(illus) == 1
    x0, y0, x1, y1 = illus[0]
    assert 30 <= x0 < 60 and x1 <= mise["gouttiere"] and y0 >= 20      # l'encre, pas la demi-page entière
    assert R.cadres_texte(mise, illus) and all(c[0] >= mise["gouttiere"] for c in R.cadres_texte(mise, illus))


def test_illustration_retiree_n_est_plus_recopiee(monkeypatch):
    img, _ = _double_page()
    monkeypatch.setattr(R, "detecter_illustrations", lambda image: [(100, 100, 400, 400), (900, 100, 1200, 400)])
    mise = S.analyser_mise_en_page(img, double_page=True)
    assert len(R.illustrations_page(img, mise, None)) == 2
    reglage = {"illustrations_retirees": [[102, 98, 401, 399]]}       # même zone, à quelques pixels près
    assert R.illustrations_page(img, mise, reglage) == [(900, 100, 1200, 400)]


# ── Enregistrement, routes, caches ───────────────────────────────────────────

def _pdf(tmp_path):
    pdf = tmp_path / "livre.pdf"
    _pdf_trois_colonnes(pdf)
    return str(pdf)


def test_route_reglage_enregistre_a_cote_du_pdf_puis_efface(tmp_path):
    pdf = _pdf(tmp_path)
    r = client.put("/api/labo/scan-colonnes/reglage-page",
                   json={"chemin_pdf": pdf, "page": 1, "reglage": {"moities": [{"mode": "colonnes", "colonnes": [[40, 300]]}]}})
    assert r.status_code == 200, r.text
    fichier = S.chemins_sortie(pdf)["reglages"]
    assert json.load(open(fichier))["pages"]["1"]["moities"][0]["colonnes"] == [[40, 300]]
    geo = client.get("/api/labo/scan-colonnes/geometrie", params={"chemin_pdf": pdf, "page": 1, "double_page": "non"}).json()
    assert geo["moities"][0]["mode"] == "colonnes" and geo["moities"][0]["colonnes"] == [[40, 300]]
    assert geo["reglage"] and geo["largeur"] > geo["hauteur"]

    r = client.put("/api/labo/scan-colonnes/reglage-page", json={"chemin_pdf": pdf, "page": 1, "reglage": None})
    assert r.status_code == 200 and r.json()["reglage"] is None
    assert json.load(open(fichier))["pages"] == {}
    geo = client.get("/api/labo/scan-colonnes/geometrie", params={"chemin_pdf": pdf, "page": 1, "double_page": "non"}).json()
    assert geo["reglage"] is None and geo["moities"][0]["mode"] == "auto"


def test_route_reglage_refusee_pendant_un_nettoyage_et_hors_document(tmp_path):
    pdf = _pdf(tmp_path)
    assert client.put("/api/labo/scan-colonnes/reglage-page",
                      json={"chemin_pdf": pdf, "page": 9, "reglage": None}).status_code == 422
    sorties = S.chemins_sortie(pdf)
    S._sauvegarder_etat({"job_id": "reg-1", "statut": "en_cours", "chemin_etat": sorties["etat"]})
    S.enregistrer_job("reg-1")
    try:
        r = client.put("/api/labo/scan-colonnes/reglage-page",
                       json={"chemin_pdf": pdf, "page": 1, "reglage": {"moities": [{"mode": "ignorer"}]}})
    finally:
        S.supprimer_job_registre("reg-1")
    assert r.status_code == 409
    assert not os.path.exists(sorties["reglages"])


def test_reinitialiser_garde_les_reglages_manuels(tmp_path):
    pdf = _pdf(tmp_path)
    S.enregistrer_reglage_page(pdf, 1, {"moities": [{"mode": "ignorer"}]})
    S.reinitialiser(pdf)
    assert S.reglage_page(pdf, 1) == {"moities": [{"mode": "ignorer"}]}


def test_page_refaite_quand_son_reglage_change(tmp_path, monkeypatch):
    pdf = _pdf(tmp_path)
    appels = []

    def faux_traitement(image, langue, double_page, reglage):
        appels.append(reglage)
        return {"blocs": [["para", "x"]], "avertissements": []}

    monkeypatch.setattr(S, "traiter_page", faux_traitement)
    dossier = str(tmp_path / "pages")
    os.makedirs(dossier)
    S._traiter_et_ecrire_page(pdf, 1, dossier, "eng", False)
    S._traiter_et_ecrire_page(pdf, 1, dossier, "eng", False)
    assert appels == [None]                               # même réglage : repris du cache
    S.enregistrer_reglage_page(pdf, 1, {"moities": [{"mode": "ignorer"}]})
    S._traiter_et_ecrire_page(pdf, 1, dossier, "eng", False)
    assert appels == [None, {"moities": [{"mode": "ignorer"}]}]


def test_geometrie_de_reconstruction_refaite_pour_la_page_retouchee(tmp_path):
    pdf = _pdf(tmp_path)
    dossier = str(tmp_path / "illus")
    m1 = R.preparer_geometrie(pdf, dossier, [1], False)
    assert m1["pages"]["1"]["cadres"] and m1["pages"]["1"]["regions"] is None
    S.enregistrer_reglage_page(pdf, 1, {"moities": [{"mode": "image"}]})
    m2 = R.preparer_geometrie(pdf, dossier, [1], False)
    page = m2["pages"]["1"]
    assert page["regions"] == {"moities": [{"mode": "image"}]}
    assert page["cadres"] == [] and len(page["illustrations"]) == 1
