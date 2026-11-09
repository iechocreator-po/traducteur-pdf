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


# ── Zones dessinées à la main (niveau 3) ─────────────────────────────────────

from app.services import translator as T                                   # noqa: E402
from tests.test_scan_colonnes import ligne                                  # noqa: E402


def test_normaliser_zones():
    r = S.normaliser_reglage({"zones": [
        {"type": "titre", "rect": [300, 50, 100, 90]},          # coins inversés : remis dans l'ordre
        {"type": "pirate", "rect": [0, 0, 100, 100]},           # type inconnu
        {"type": "texte", "rect": [0, 0, 5, 100]},              # trop étroite
        {"type": "conserver", "rect": [0, 0, "a", 100]},
        "x",
    ]})
    assert r == {"zones": [{"type": "titre", "rect": [100, 50, 300, 90]}]}


def test_zone_rognee_a_la_page():
    img, _ = page_synthetique()
    mise = S.analyser_mise_en_page(img, double_page=False, reglage={"zones": [
        {"type": "ignorer", "rect": [-50, 800, 5000, 5000]}, {"type": "titre", "rect": [5000, 0, 6000, 50]}]})
    assert mise["zones"] == [{"type": "ignorer", "rect": [0, 800, img.width, img.height]}]


def _ocr_factice(appels):
    """OCR sans Tesseract : une ligne dont le texte dit d'où vient le recadrage (zone ou colonne)."""
    def ocr(image, langue, y_decalage=0, zone=False):
        appels.append((image.size, y_decalage, zone))
        return [ligne(f"Lu{image.width}x{image.height} depuis {y_decalage} mots", 0, image.width, y_decalage)]
    return ocr


def test_zones_lues_a_leur_place_selon_leur_type(monkeypatch):
    img, cols = page_synthetique()
    monkeypatch.setattr(S, "ocr_titre", lambda image, langue: ("", 0.0))
    appels = []
    monkeypatch.setattr(S, "ocr_lignes", _ocr_factice(appels))
    a, b = cols[1]
    reglage = {"zones": [{"type": "conserver", "rect": [a, 600, b, 640]},
                         {"type": "titre", "rect": [a, 300, b, 340]},
                         {"type": "ignorer", "rect": [cols[2][0], 30, cols[2][1], 200]},
                         {"type": "illustration", "rect": [cols[0][0], 500, cols[0][1], 700]}]}
    blocs = S.traiter_page(img, "eng", double_page=False, reglage=reglage)["blocs"]
    largeur = b - a
    titre = ("titre", f"Lu{largeur}x40 depuis 300 mots")
    conserve = ("conserve", f"Lu{largeur}x40 depuis 600 mots")
    assert titre in blocs and conserve in blocs
    i_titre, i_conserve = blocs.index(titre), blocs.index(conserve)
    assert i_titre < i_conserve
    # Ordre de lecture : tout ce qui est lu dans la 1re colonne vient avant le titre (2e colonne, en haut),
    # et le texte de la 3e colonne après le texte conservé.
    avant = [t for k, t in blocs[:i_titre] if k == "para"]
    apres = [t for k, t in blocs[i_conserve + 1:] if k == "para"]
    assert avant and apres
    # Les zones ignorer / illustration ne sont jamais lues : aucun recadrage à leur taille.
    assert all(taille != (cols[2][1] - cols[2][0], 170) and taille != (cols[0][1] - cols[0][0], 200)
               for taille, _, _ in appels)
    # Une zone garde ses lignes de chiffres (tableau) ; les colonnes, non.
    assert {(t, e) for t, y, e in appels if y in (300, 600)} == {((largeur, 40), True)}


def test_contenu_d_une_zone_masque_pour_les_colonnes():
    img, cols = page_synthetique()
    mise = S.analyser_mise_en_page(img, double_page=False)
    zone = {"type": "ignorer", "rect": [cols[1][0], 100, cols[1][1], 400]}
    gris, image = S._masquer_zones(mise["gris"], img, [zone])
    assert gris[100:400, cols[1][0]:cols[1][1]].min() == 255
    assert mise["gris"][100:400, cols[1][0]:cols[1][1]].min() < 100          # l'original n'est pas touché
    assert min(image.crop((cols[1][0], 100, cols[1][1], 400)).getdata()) == 255


def test_zone_vide_signalee(monkeypatch):
    img, cols = page_synthetique()
    monkeypatch.setattr(S, "ocr_titre", lambda image, langue: ("", 0.0))
    monkeypatch.setattr(S, "ocr_lignes", lambda image, langue, y_decalage=0, zone=False: [])
    res = S.traiter_page(img, "eng", double_page=False,
                         reglage={"zones": [{"type": "titre", "rect": [cols[0][0], 50, cols[0][1], 90]}]})
    assert any("aucun texte lu" in a for a in res["avertissements"])


def test_markdown_du_texte_conserve_et_relu_par_la_reconstruction():
    md = S.blocs_vers_markdown([("para", "Avant."), ("conserve", "Nom Propre"), ("para", "Après.")])
    assert "<!-- conserver -->\nNom Propre\n<!-- /conserver -->" in md
    assert R._blocs_traduits(md) == [("para", "Avant."), ("para", "Nom Propre"), ("para", "Après.")]


def test_texte_conserve_jamais_envoye_au_modele(monkeypatch):
    vus = []

    def faux_ollama(payload, interruption=None):
        vus.append(payload["prompt"])
        return {"response": payload["prompt"].replace("Hello world.", "Bonjour le monde.")}

    monkeypatch.setattr(T, "appeler_ollama", faux_ollama)
    bloc = "<!-- conserver -->\nThe Unknown Studio\n<!-- /conserver -->"
    sortie = T.traduire_texte(f"Hello world.\n\n{bloc}\n\nHello world.", "m", "anglais", "français")
    assert "Unknown" not in vus[0] and "⟦IMG0⟧" in vus[0]
    assert sortie == f"Bonjour le monde.\n\n{bloc}\n\nBonjour le monde."


def test_morceau_entierement_conserve_sans_appel_au_modele(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError("le modèle ne doit pas être appelé")

    monkeypatch.setattr(T, "appeler_ollama", interdit)
    bloc = "<!-- conserver -->\nCREDITS\n<!-- /conserver -->"
    assert T.traduire_texte(bloc, "m", "anglais", "français") == bloc


def test_zones_et_illustrations_detectees(monkeypatch):
    img, _ = _double_page()
    monkeypatch.setattr(R, "detecter_illustrations", lambda image: [(100, 100, 400, 400), (900, 100, 1200, 400)])
    reglage = {"zones": [{"type": "titre", "rect": [80, 80, 420, 420]},            # recouvre la 1re détection
                         {"type": "illustration", "rect": [900, 500, 1100, 700]}]}
    mise = S.analyser_mise_en_page(img, double_page=True, reglage=reglage)
    illus = R.illustrations_page(img, mise, reglage)
    assert illus == [(900, 100, 1200, 400), (900, 500, 1100, 700)]
    geo = S.geometrie_publique({"mise": mise, "illustrations": illus}, reglage)
    assert geo["illustrations"] == [[900, 100, 1200, 400]]            # la zone manuelle n'y est pas en double
    assert [z["type"] for z in geo["zones"]] == ["titre", "illustration"]


def test_geometrie_de_reconstruction_avec_zone_illustration(tmp_path):
    pdf = _pdf(tmp_path)
    S.enregistrer_reglage_page(pdf, 1, {"zones": [{"type": "illustration", "rect": [40, 40, 240, 240]}]})
    page = R.preparer_geometrie(pdf, str(tmp_path / "illus"), [1], False)["pages"]["1"]
    assert {"rect": [40, 40, 240, 240], "fichier": "page-0001-illus-1.jpg"} in page["illustrations"]


def test_apercu_en_edition_omet_les_zones(monkeypatch):
    img, cols = page_synthetique()
    reglage = {"zones": [{"type": "conserver", "rect": [cols[0][0], 100, cols[0][1], 300]}]}
    normal = Image.open(__import__("io").BytesIO(S.apercu_page(img, False, reglage))).convert("RGB")
    edition = Image.open(__import__("io").BytesIO(S.apercu_page(img, False, reglage, edition=True))).convert("RGB")
    x, y = cols[0][0] + 2, 200          # sur le bord de la zone : turquoise d'un côté, page de l'autre
    assert normal.getpixel((x, y)) == S.COULEURS_ZONES["conserver"]
    assert edition.getpixel((x, y)) != S.COULEURS_ZONES["conserver"]


def test_zone_texte_garde_les_lignes_de_chiffres():
    """Corps d'un tableau (« 1-3 1-2 ») : écarté comme du bruit dans une colonne, gardé dans une zone « texte »."""
    lignes = [ligne("Story I Story II", 0, 300, 10), ligne("1-3 1-2", 0, 300, 40)]
    assert S.lignes_vers_blocs(lignes, 300, 1000, filtrer=False) == [("para", "Story I Story II 1-3 1-2")]


def _rangee(cellules, haut):
    """Ligne OCR dont les mots sont séparés par de grands blancs (cellules d'un tableau)."""
    mots = [dict(l=i * 200, t=0, w=40, h=20, c=90, txt=c) for i, c in enumerate(cellules)]
    return dict(mots=mots, gauche=0, droite=len(cellules) * 200, haut=haut)


def test_zone_texte_en_tableau_une_rangee_par_paragraphe(monkeypatch):
    img, cols = page_synthetique()
    monkeypatch.setattr(S, "ocr_lignes", lambda image, langue, y_decalage=0, zone=False:
                        [_rangee(["1-3", "1-2", "Fated"], y_decalage), _rangee(["4-6", "3-4", "Plight"], y_decalage + 30)]
                        if zone else [])
    monkeypatch.setattr(S, "ocr_titre", lambda image, langue: ("", 0.0))
    blocs = S.traiter_page(img, "eng", False, {"zones": [{"type": "texte", "rect": [cols[0][0], 50, cols[2][1], 120]}]})["blocs"]
    assert blocs == [("para", "1-3 1-2 Fated"), ("para", "4-6 3-4 Plight")]
    assert not S._ressemble_a_un_tableau([ligne("Une phrase de prose normale", 0, 300, 0), ligne("et sa suite", 0, 300, 20)])


# ── Titres et encadrés détectés, modifiables dans l'éditeur ──────────────────

def _page_titre_et_encadre():
    """3 colonnes ; un gros titre dans la 1re (y ≈ 300) et un encadré blanc dans la 2e (y 500–620)."""
    img, cols = page_synthetique()
    a, b = cols[0]
    d = ImageDraw.Draw(img)
    d.rectangle([a, 290, b, 335], fill=PAPIER)
    d.rectangle([a, 300, a + 30, 322], fill=10)
    d.rectangle([a + 40, 306, b - 20, 318], fill=10)
    a2, b2 = cols[1]
    d.rectangle([a2 - 8, 500, b2 + 8, 620], fill=255)
    return img, cols


def _detection(dets, type_, x, y):
    return next(dt for dt in dets if dt["type"] == type_ and abs(dt["rect"][0] - x) <= 12 and abs(dt["rect"][1] - y) <= 12)


def test_titre_et_encadre_detectes_exposes_a_l_editeur():
    img, cols = _page_titre_et_encadre()
    geo = S.geometrie_publique(S.geometrie_page(img, double_page=False), None)
    _detection(geo["detections"], "titre", cols[0][0], 297)
    _detection(geo["detections"], "texte", cols[1][0], 500)      # un encadré : présenté comme une zone « texte »
    json.dumps(geo)                                                # sérialisable tel quel


def test_titre_et_encadre_retires_lus_comme_texte_courant(monkeypatch):
    img, cols = _page_titre_et_encadre()
    dets = S.geometrie_page(img, double_page=False)["detections"]
    titre = _detection(dets, "titre", cols[0][0], 297)["rect"]
    encadre = _detection(dets, "texte", cols[1][0], 500)["rect"]
    titres_ocr = []
    monkeypatch.setattr(S, "ocr_titre", lambda image, langue: (titres_ocr.append(image.size) or "1 The Ship", 95.0))
    monkeypatch.setattr(S, "ocr_lignes", _ocr_factice([]))
    avant = S.traiter_page(img, "eng", double_page=False)["blocs"]
    assert ("titre", "1 The Ship") in avant and any(k == "encadre" for k, _ in avant)
    titres_ocr.clear()
    reglage = S.normaliser_reglage({"detections_retirees": [titre, encadre]})
    apres = S.traiter_page(img, "eng", double_page=False, reglage=reglage)["blocs"]
    assert ("titre", "1 The Ship") not in apres and not any(k == "encadre" for k, _ in apres)
    assert not titres_ocr                                          # le titre retiré n'est même plus essayé
    restantes = S.geometrie_page(img, double_page=False, reglage=reglage)["detections"]
    assert not any(R.scan_colonnes.meme_zone(dt["rect"], q) for dt in restantes for q in (titre, encadre))


def test_titre_detecte_recadre_en_zone_manuelle(monkeypatch):
    """Ce que fait l'éditeur : la détection retirée, une zone « titre » à sa place, plus large."""
    img, cols = _page_titre_et_encadre()
    titre = _detection(S.geometrie_page(img, double_page=False)["detections"], "titre", cols[0][0], 297)["rect"]
    monkeypatch.setattr(S, "ocr_titre", lambda image, langue: ("1 The Ship", 95.0))
    monkeypatch.setattr(S, "ocr_lignes", _ocr_factice([]))
    zone = [titre[0], titre[1] - 10, titre[2], titre[3] + 10]
    reglage = S.normaliser_reglage({"detections_retirees": [titre], "zones": [{"type": "titre", "rect": zone}]})
    blocs = S.traiter_page(img, "eng", double_page=False, reglage=reglage)["blocs"]
    assert ("titre", "1 The Ship") not in blocs
    assert ("titre", f"Lu{zone[2] - zone[0]}x{zone[3] - zone[1]} depuis {zone[1]} mots") in blocs


def test_apercu_en_edition_omet_titres_et_encadres():
    img, _ = _page_titre_et_encadre()
    orange = (240, 140, 0)
    pixels = lambda png: set(Image.open(__import__("io").BytesIO(png)).convert("RGB").getdata())   # noqa: E731
    assert orange in pixels(S.apercu_page(img, double_page=False))
    assert orange not in pixels(S.apercu_page(img, double_page=False, edition=True))


def test_normaliser_detections_retirees():
    r = S.normaliser_reglage({"detections_retirees": [[1, 2, 3, 4], "x", [1, 2, 3], [1, 2, 3, True]]})
    assert r == {"detections_retirees": [[1, 2, 3, 4]]}
