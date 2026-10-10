"""
Tests du nettoyage de PDF scannés à colonnes (services/scan_colonnes.py, Laboratoire).

Trois niveaux : fonctions pures (géométrie sur une page synthétique, texte, réparation des
titres), un test bout en bout sur un vrai PDF fabriqué avec Tesseract (ignoré si Tesseract
est absent), et les routes.
"""

import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from app.services import scan_colonnes as S

client = TestClient(app)
avec_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract absent")


# ── Page synthétique : 3 colonnes de « lignes de mots » sur papier beige ─────

PAPIER = 231


def page_synthetique(x_debut=40, largeur_col=230, ecart=30, hauteur=900, boite=None, titre=None):
    """Une page de 3 colonnes. Chaque ligne = des petits blocs noirs séparés par des espaces
    (assez pour ressembler à du texte au profil d'encre, pas pour être lisibles)."""
    largeur = x_debut * 2 + 3 * largeur_col + 2 * ecart
    import random
    alea = random.Random(7)   # largeurs de mots variables : les espaces ne s'alignent pas d'une ligne à l'autre
    img = Image.new("L", (largeur, hauteur), PAPIER)
    d = ImageDraw.Draw(img)
    colonnes = []
    for c in range(3):
        a = x_debut + c * (largeur_col + ecart)
        colonnes.append((a, a + largeur_col))
        y = 30
        while y < hauteur - 40:
            x = a
            while x < a + largeur_col - 30:
                w = alea.randint(10, 30)
                d.rectangle([x, y, min(x + w, a + largeur_col), y + 5], fill=20)   # un « mot »
                x += w + alea.randint(5, 8)
            y += 14
    if boite:
        a, y0, y1 = boite
        d.rectangle([colonnes[0][0] - 8, y0, colonnes[0][1] + 8, y1], fill=255)
    return img, colonnes


def test_plages():
    assert S.plages([0, 1, 1, 1, 0, 1, 0, 1, 1], 2) == [(1, 4), (7, 9)]
    assert S.plages([0, 0], 1) == []


def test_detecter_colonnes_trouve_les_trois():
    import numpy as np
    img, attendues = page_synthetique()
    gris = np.array(img)
    trouvees = S.detecter_colonnes(gris, 0, gris.shape[1])
    assert len(trouvees) == 3
    for (a, b), (ea, eb) in zip(trouvees, attendues):
        assert abs(a - ea) <= 12 and abs(b - eb) <= 12


def test_une_illustration_ne_fausse_pas_la_detection_des_colonnes():
    """Régression mesurée : la moitié basse d'une page droite était une illustration, la
    mesure y trouvait UNE colonne. Seules les bandes qui ressemblent à du texte votent."""
    import numpy as np
    img, _ = page_synthetique()
    d = ImageDraw.Draw(img)
    d.rectangle([0, 450, img.width, img.height], fill=40)   # grande image sombre en bas
    gris = np.array(img)
    assert len(S.detecter_colonnes(gris, 0, gris.shape[1])) == 3


def test_gouttiere_au_centre_de_la_double_page():
    import numpy as np
    g, _ = page_synthetique()
    d_, _ = page_synthetique()
    double = Image.new("L", (g.width + d_.width + 40, g.height), PAPIER)
    double.paste(g, (0, 0))
    double.paste(d_, (g.width + 40, 0))
    gris = np.array(double)
    gx = S.detecter_gouttiere(gris)
    assert g.width - 20 <= gx <= g.width + 60   # au milieu de l'espace vide entre les deux pages


def test_encadre_blanc_isole_comme_segment():
    import numpy as np
    img, cols = page_synthetique(boite=(0, 300, 420))
    a, b = cols[0]
    segs = S.decouper_segments(np.array(img)[:, a:b])
    genres = [g for g, _, _ in segs]
    assert genres == ["texte", "encadre", "texte"]
    _, y0, y1 = segs[1]
    assert abs(y0 - 300) <= 8 and abs(y1 - 420) <= 8


def test_bande_haute_signalee_comme_candidat_titre():
    import numpy as np
    img, cols = page_synthetique()
    a, b = cols[0]
    d = ImageDraw.Draw(img)
    d.rectangle([a, 290, b, 335], fill=PAPIER)          # dégage la place du titre
    d.rectangle([a, 300, a + 30, 322], fill=10)       # gros numéro de section
    d.rectangle([a + 40, 306, b - 20, 318], fill=10)
    segment = np.array(img)[:, a:b]
    bandes = S.bandes_titres(segment)
    assert any(abs(p - 300) <= 6 for p, _ in bandes)


# ── Texte ────────────────────────────────────────────────────────────────────

def mot(txt, c=90, w=30, h=20):
    return dict(l=0, t=0, w=w, h=h, c=c, txt=txt)


def ligne(texte, gauche=0, droite=200, haut=0):
    return dict(mots=[mot(t) for t in texte.split()], gauche=gauche, droite=droite, haut=haut)


def test_symbole_de_jeu_devient_marqueur():
    assert S.nettoyer_mot(mot("@§.", w=40, h=20)) == S.MARQUEUR_ICONE
    assert S.nettoyer_mot(mot("word")) == "word"
    assert S.nettoyer_mot(mot(",")) == ","


def test_i_mal_lu_redevient_i_mais_pas_une_icone_large():
    assert S.nettoyer_mot(mot("|", w=4, h=20), "bring") == "I"
    assert S.nettoyer_mot(mot("“{", w=12, h=20), "think") == "“I"
    assert S.nettoyer_mot(mot("&.", w=30, h=20), "The") == S.MARQUEUR_ICONE


def test_icones_consecutives_fusionnees():
    L = dict(mots=[mot("Gain"), mot("@", w=30), mot("§", w=30), mot("now")], gauche=0, droite=100, haut=0)
    assert S.texte_ligne(L).count(S.MARQUEUR_ICONE) == 1


def test_pied_de_page_seulement_en_bord_de_page():
    assert S.est_pied_de_page("92 Inward Odyssey", 1100, 1160)
    assert S.est_pied_de_page("Inward Odyssey 93", 20, 1160)
    assert not S.est_pied_de_page("92 Inward Odyssey", 600, 1160)      # même texte, milieu de page
    assert not S.est_pied_de_page("A line of ordinary prose, long enough to be a sentence here.", 1100, 1160)


def test_paragraphes_coupes_sur_retrait_et_cesure_recollee():
    lignes = [
        ligne("The first sentence runs on with the exten-", 0, 200, 0),
        ligne("sion of the word.", 0, 80, 10),
        ligne("A new paragraph starts indented here", 30, 200, 20),
    ]
    blocs = S.lignes_vers_blocs(lignes, 200, 1000)
    assert [k for k, _ in blocs] == ["para", "para"]
    assert "extension of the word." in blocs[0][1]
    assert blocs[1][1].startswith("A new paragraph")


def test_pied_de_page_ecarte_du_texte():
    lignes = [ligne("Real body text of the column goes here", 0, 200, 100),
              ligne("92 Inward Odyssey", 0, 100, 1120)]
    blocs = S.lignes_vers_blocs(lignes, 200, 1160)
    assert len(blocs) == 1 and "Odyssey" not in blocs[0][1]


# ── Réparation des titres ────────────────────────────────────────────────────

def test_numero_isole_repare_quand_ses_voisins_le_confirment():
    blocs = [("titre", "9 Days"), ("titre", "10 Memory"), ("titre", "1 Garden"), ("titre", "12 Next")]
    journal = []
    res = S.reparer_titres(blocs, journal)
    assert [t for _, t in res] == ["9 Days", "10 Memory", "11 Garden", "12 Next"]
    assert len(journal) == 1 and "confirmé par 10 et 12" in journal[0]


def test_titres_numerotes_par_plages_ne_sont_pas_abimes():
    """Régression mesurée sur le document complet : « 3-4 » lu « 34 » est correct tel quel ;
    l'ancienne règle « la séquence attendue » le réécrivait en 2."""
    blocs = [("titre", "12 The Pilgrimage"), ("titre", "34 Dream of the Maw"), ("titre", "56 Stewards"),
             ("titre", "78 The Feast"), ("titre", "95 Toate")]
    journal = []
    assert S.reparer_titres(blocs, journal) == blocs
    assert journal == []


def test_aucun_voisin_aucune_reparation():
    assert S.reparer_titres([("titre", "95 Alpha"), ("titre", "1 Beta")], []) == [("titre", "95 Alpha"), ("titre", "1 Beta")]


def test_numero_d_entree_espace_recolle():
    lignes = [ligne("0 2 8 1 As the Argo closes in on the cliff", 0, 200, 0)]
    assert S.lignes_vers_blocs(lignes, 200, 1000)[0][1].startswith("0281 As the Argo")
    # un texte ordinaire commençant par un chiffre n'est pas touché
    lignes = [ligne("3 apples were eaten by the crew", 0, 200, 0)]
    assert S.lignes_vers_blocs(lignes, 200, 1000)[0][1].startswith("3 apples")


def test_titre_sur_deux_lignes_complete():
    blocs = [("titre", "7 Blood of the"), ("para", "Mother You have arrived on a small island.")]
    journal = []
    res = S.reparer_titres(blocs, journal)
    assert res[0] == ("titre", "7 Blood of the Mother")
    assert res[1][1].startswith("You have arrived")
    assert journal


def test_titre_complet_n_absorbe_pas_le_paragraphe_suivant():
    res = S.reparer_titres([("titre", "2 Phos Dance"), ("para", "The Argo is abuzz.")], [])
    assert res[1] == ("para", "The Argo is abuzz.")


def test_markdown():
    md = S.blocs_vers_markdown([("titre", "1 Un"), ("para", "Corps."), ("encadre", "Règle.")])
    assert "## 1 Un" in md and "\n> Règle." in md and "Corps." in md


# ── Bout en bout : un vrai PDF, un vrai Tesseract ────────────────────────────

def _pdf_trois_colonnes(chemin):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(chemin), pagesize=landscape(A4))
    L, H = landscape(A4)
    largeur = (L - 100) / 3
    textes = [
        "Alpha lines of the first column keep going down the page in order",
        "Bravo lines of the second column come after the first column",
        "Charlie lines of the third column close the page at the end",
    ]
    for i, t in enumerate(textes):
        x = 40 + i * (largeur + 10)
        if i == 0:
            c.setFont("Helvetica-Bold", 17)
            c.drawString(x, H - 60, "1 First Section")
        c.setFont("Helvetica", 10)
        y = H - 100
        for _ in range(26):
            c.drawString(x, y, t[:46])
            c.drawString(x, y - 13, t[46:] or "and then it continues")
            y -= 26
    c.save()


@avec_tesseract
def test_pdf_a_trois_colonnes_dans_le_bon_ordre(tmp_path):
    pdf = tmp_path / "trois_colonnes.pdf"
    _pdf_trois_colonnes(pdf)
    doc = S._ouvrir_pdf(str(pdf))
    try:
        res = S.traiter_page(S.rendre_page(doc, 1), "eng", double_page=False)
    finally:
        doc.close()
    texte = " ".join(t for _, t in res["blocs"])
    # l'ordre colonne par colonne : tout Alpha avant tout Bravo avant tout Charlie
    assert 0 <= texte.find("Alpha") < texte.find("Bravo") < texte.find("Charlie")
    assert texte.rfind("Alpha") < texte.find("Bravo")
    assert texte.rfind("Bravo") < texte.find("Charlie")


@avec_tesseract
def test_job_complet_reprise_et_aucun_llm(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    import os

    etat = {"job_id": "t", "chemin_pdf": str(pdf), "chemin_sortie": sorties["markdown"],
            "dossier_pages": sorties["pages"], "chemin_etat": sorties["etat"], "langue_ocr": "eng",
            "double_page": False, "travailleurs": 1, "page_debut": 1, "page_fin": 1, "pages_total": 1, "pages_faites": 0,
            "pages_en_erreur": [], "statut": "en_attente", "temps_ecoule_secondes": 0.0, "demarre_a": 0}
    S.enregistrer_job("t")
    S._executer(etat)
    assert etat["statut"] == "termine"
    assert os.path.exists(sorties["markdown"])
    page = os.path.join(sorties["pages"], "page-0001.json")
    avant = os.path.getmtime(page)
    # relance : la page déjà faite n'est PAS refaite
    S.enregistrer_job("t2")
    etat2 = {**etat, "job_id": "t2", "pages_faites": 0, "statut": "en_attente"}
    S._executer(etat2)
    assert os.path.getmtime(page) == avant


def test_page_en_erreur_ne_rend_pas_le_job_termine(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))

    def echec(*a, **k):
        raise S.ScanColonnesEchec("page illisible")

    monkeypatch.setattr(S, "traiter_page", echec)
    etat = {"job_id": "e", "chemin_pdf": str(pdf), "chemin_sortie": sorties["markdown"],
            "dossier_pages": sorties["pages"], "chemin_etat": sorties["etat"], "langue_ocr": "eng",
            "double_page": False, "travailleurs": 1, "page_debut": 1, "page_fin": 1, "pages_total": 1, "pages_faites": 0,
            "pages_en_erreur": [], "statut": "en_attente", "temps_ecoule_secondes": 0.0, "demarre_a": 0}
    S.enregistrer_job("e")
    S._executer(etat)
    assert etat["statut"] == "erreur"
    assert etat["pages_en_erreur"][0]["page"] == 1


# ── Routes ───────────────────────────────────────────────────────────────────

def test_route_statut_sans_traitement_renvoie_null(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    r = client.get("/api/labo/scan-colonnes/statut", params={"chemin_pdf": str(pdf)})
    assert r.status_code == 200 and r.json() is None


def test_route_apercu_renvoie_un_png(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    r = client.get("/api/labo/scan-colonnes/apercu", params={"chemin_pdf": str(pdf), "page": 1})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_route_apercu_page_hors_document(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    r = client.get("/api/labo/scan-colonnes/apercu", params={"chemin_pdf": str(pdf), "page": 9})
    assert r.status_code == 422


def test_route_refuse_un_non_pdf(tmp_path):
    md = tmp_path / "x.md"
    md.write_text("# t")
    r = client.post("/api/labo/scan-colonnes", json={"chemin_pdf": str(md)})
    assert r.status_code == 422


def test_route_lancer_sans_tesseract(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    monkeypatch.setattr(S, "tesseract_disponible", lambda: False)
    r = client.post("/api/labo/scan-colonnes", json={"chemin_pdf": str(pdf)})
    assert r.status_code == 422 and "Tesseract" in r.json()["detail"]


def test_route_info(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    r = client.get("/api/labo/scan-colonnes/info", params={"chemin_pdf": str(pdf)})
    assert r.status_code == 200 and r.json()["pages"] == 1


def test_le_flag_existe_et_est_actif_par_defaut():
    from app.config.feature_flags import FLAGS_PAR_DEFAUT
    assert FLAGS_PAR_DEFAUT["extraction_scan_colonnes"] is True
