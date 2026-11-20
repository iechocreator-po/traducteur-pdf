"""
Tests du nettoyage de PDF scannés à colonnes (services/scan_colonnes.py, Laboratoire).

Trois niveaux : fonctions pures (géométrie sur une page synthétique, texte, réparation des
titres), un test bout en bout sur un vrai PDF fabriqué avec Tesseract (ignoré si Tesseract
est absent), et les routes.
"""

import json
import os
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


def test_page_droite_rendue_telle_quelle():
    img, _ = page_synthetique()
    rgb = img.convert("RGB")
    assert S.estimer_inclinaison(__import__("numpy").array(img)) == 0.0
    assert S.redresser(rgb) is rgb


@pytest.mark.parametrize("angle", [-4.0, 3.0])
def test_page_photographiee_de_biais_redressee_retrouve_ses_colonnes(angle):
    """Cycle I, section 2, p. 8 : photo penchée de ~4°, aucune colonne trouvée, lignes OCR à travers les colonnes."""
    import numpy as np
    img, attendues = page_synthetique()
    penchee = img.rotate(angle, resample=Image.BICUBIC, fillcolor=PAPIER).convert("RGB")
    gris = np.array(penchee.convert("L"))
    assert abs(S.estimer_inclinaison(gris) + angle) <= 0.3
    droit = np.array(S.redresser(penchee).convert("L"))
    trouvees = S.detecter_colonnes(droit, 0, droit.shape[1])
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


def test_route_annuler_sans_traitement_en_cours(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    r = client.post("/api/labo/scan-colonnes/annuler", params={"chemin_pdf": str(pdf)})
    assert r.status_code == 409


def test_route_annuler_demande_l_annulation_du_job_actif(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    etat = {"job_id": "annul-1", "statut": "en_cours", "chemin_etat": sorties["etat"]}
    S._sauvegarder_etat(etat)
    S.enregistrer_job("annul-1")
    r = client.post("/api/labo/scan-colonnes/annuler", params={"chemin_pdf": str(pdf)})
    assert r.status_code == 200 and r.json()["annulation_demandee"] is True
    assert S.est_annule("annul-1")


def test_reinitialiser_efface_les_sorties_mais_jamais_le_pdf(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    os.makedirs(sorties["pages"])
    (tmp_path / "x_scan_colonnes_pages" / "page-0001.json").write_text("{}")
    (tmp_path / "x_scan_colonnes.md").write_text("# t")
    S._sauvegarder_etat({"job_id": "r1", "statut": "termine", "chemin_etat": sorties["etat"]})
    autre = tmp_path / "x_traduit_ll.md"       # un document voisin : ne doit pas être touché
    autre.write_text("traduction")

    r = client.post("/api/labo/scan-colonnes/reinitialiser", params={"chemin_pdf": str(pdf)})
    assert r.status_code == 200 and len(r.json()["supprimes"]) == 3
    assert pdf.exists() and autre.exists()
    assert not os.path.exists(sorties["markdown"]) and not os.path.exists(sorties["pages"]) and not os.path.exists(sorties["etat"])
    assert S.lire_etat(str(pdf)) is None


def test_reinitialiser_refuse_pendant_un_nettoyage(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    S._sauvegarder_etat({"job_id": "r2", "statut": "en_cours", "chemin_etat": sorties["etat"]})
    S.enregistrer_job("r2")
    try:
        r = client.post("/api/labo/scan-colonnes/reinitialiser", params={"chemin_pdf": str(pdf)})
    finally:
        S.supprimer_job_registre("r2")
    assert r.status_code == 409
    assert os.path.exists(sorties["etat"])


def test_scan_orphelin_apres_redemarrage_devient_annule_et_debloque(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    S._sauvegarder_etat({"job_id": "disparu", "statut": "en_cours", "pages_faites": 3, "chemin_etat": sorties["etat"]})
    etat = S.lire_etat(str(pdf))
    assert etat["statut"] == "annule" and etat["pages_faites"] == 3
    assert json.loads(open(sorties["etat"], encoding="utf-8").read())["statut"] == "annule"   # persisté
    r = client.post("/api/labo/scan-colonnes/reinitialiser", params={"chemin_pdf": str(pdf)})
    assert r.status_code == 200


def test_scan_actif_reste_actif_a_la_lecture(tmp_path):
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    S._sauvegarder_etat({"job_id": "vivant", "statut": "en_cours", "chemin_etat": sorties["etat"]})
    S.enregistrer_job("vivant")
    try:
        assert S.lire_etat(str(pdf))["statut"] == "en_cours"
    finally:
        S.supprimer_job_registre("vivant")


def test_reinitialiser_refuse_si_une_traduction_vient_de_ce_fichier(tmp_path, monkeypatch):
    from app.services import bibliotheque
    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    (tmp_path / "x_scan_colonnes.md").write_text("# t")
    monkeypatch.setattr(bibliotheque, "lister_documents", lambda: [{"chemin_source": sorties["markdown"]}])
    r = client.post("/api/labo/scan-colonnes/reinitialiser", params={"chemin_pdf": str(pdf)})
    assert r.status_code == 409
    assert os.path.exists(sorties["markdown"])


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


def test_annulation_reelle_arrete_entre_deux_pages_et_garde_le_fait(tmp_path, monkeypatch):
    from app.services.job_manager import demander_annulation

    pdf = tmp_path / "long.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    traitees = []

    def faux_traitement(_chemin, numero, *_):
        traitees.append(numero)
        if numero == 1:
            demander_annulation("annul-reel")
        return {"blocs": []}

    monkeypatch.setattr(S, "_traiter_et_ecrire_page", faux_traitement)
    etat = {"job_id": "annul-reel", "chemin_pdf": str(pdf), "chemin_sortie": sorties["markdown"],
            "dossier_pages": sorties["pages"], "chemin_etat": sorties["etat"], "langue_ocr": "eng",
            "double_page": False, "travailleurs": 1, "page_debut": 1, "page_fin": 5, "pages_total": 5, "pages_faites": 0,
            "pages_en_erreur": [], "statut": "en_attente", "temps_ecoule_secondes": 0.0, "demarre_a": 0}
    S.enregistrer_job("annul-reel")
    S._executer(etat)
    assert traitees == [1]
    assert S.lire_etat(str(pdf))["statut"] == "annule" and etat["pages_faites"] == 1
    assert not os.path.exists(sorties["markdown"])      # pas de résultat partiel présenté comme un résultat
    assert not S.job_actif("annul-reel")                # le job ne reste pas enregistré


def test_le_markdown_garde_les_reglages_du_scan_qui_l_a_ecrit(tmp_path, monkeypatch):
    from app.services.job_manager import demander_annulation

    pdf = tmp_path / "doc.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    etat = {"job_id": "ecrit", "chemin_pdf": str(pdf), "chemin_sortie": sorties["markdown"],
            "dossier_pages": sorties["pages"], "chemin_etat": sorties["etat"], "langue_ocr": "eng",
            "double_page": False, "travailleurs": 1, "page_debut": 1, "page_fin": 1, "pages_total": 1, "pages_faites": 0,
            "pages_en_erreur": [], "statut": "en_attente", "temps_ecoule_secondes": 0.0, "demarre_a": 0}
    S.enregistrer_job("ecrit")
    S._executer(etat)
    ecrit = {"page_debut": 1, "page_fin": 1, "double_page": False}
    assert S.lire_etat(str(pdf))["markdown_ecrit"] == ecrit

    # Un nouveau scan arrêté avant la fin : le Markdown reste celui du premier, et ses réglages aussi.
    monkeypatch.setattr(S, "tesseract_disponible", lambda: True)
    monkeypatch.setattr(S, "langues_tesseract", lambda: ["eng"])
    monkeypatch.setattr(S, "soumettre_travail", lambda *_: None)
    monkeypatch.setattr(S, "_traiter_et_ecrire_page", lambda *_: demander_annulation(relance["job_id"]) or {"blocs": []})
    relance = S.demarrer_scan(str(pdf), page_debut=1, page_fin=1, double_page=True)
    assert S.reglages_du_markdown(S.lire_etat(str(pdf))) == ecrit      # pendant : pas encore écrit
    date_md = os.path.getmtime(sorties["markdown"])
    S._executer(relance)
    final = S.lire_etat(str(pdf))
    assert final["statut"] == "annule" and final["double_page"] is True
    assert S.reglages_du_markdown(final) == ecrit
    assert os.path.getmtime(sorties["markdown"]) == date_md


def test_markdown_identique_non_reecrit(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    etat = {"job_id": "a", "chemin_pdf": str(pdf), "chemin_sortie": sorties["markdown"],
            "dossier_pages": sorties["pages"], "chemin_etat": sorties["etat"], "langue_ocr": "eng",
            "double_page": False, "travailleurs": 1, "page_debut": 1, "page_fin": 1, "pages_total": 1, "pages_faites": 0,
            "pages_en_erreur": [], "statut": "en_attente", "temps_ecoule_secondes": 0.0, "demarre_a": 0}
    S.enregistrer_job("a")
    S._executer(etat)
    date_md = os.path.getmtime(sorties["markdown"])
    S.enregistrer_job("b")
    S._executer({**etat, "job_id": "b", "pages_faites": 0, "statut": "en_attente"})
    assert os.path.getmtime(sorties["markdown"]) == date_md


def test_etat_ancien_sans_champ_fiable_seulement_si_termine():
    assert S.reglages_du_markdown(None) is None
    assert S.reglages_du_markdown({"statut": "annule", "page_debut": 5, "page_fin": 7}) is None
    assert S.reglages_du_markdown({"statut": "termine", "page_debut": 5, "page_fin": 7}) == {
        "page_debut": 5, "page_fin": 7, "double_page": None}


def test_route_lancer_refusee_si_le_flag_est_coupe(tmp_path, monkeypatch):
    from app.api import routes

    pdf = tmp_path / "x.pdf"
    _pdf_trois_colonnes(pdf)
    monkeypatch.setattr(routes, "charger_flags", lambda: {"extraction_scan_colonnes": False})
    r = client.post("/api/labo/scan-colonnes", json={"chemin_pdf": str(pdf)})
    assert r.status_code == 403
    assert S.lire_etat(str(pdf)) is None                # rien n'a démarré
    # statut reste lisible : un scan déjà parti doit pouvoir être suivi et arrêté
    assert client.get("/api/labo/scan-colonnes/statut", params={"chemin_pdf": str(pdf)}).status_code == 200


# ── Pictogrammes situés (17/11/2026) ─────────────────────────────────────────

def _mot_situe(txt, c=95.0, h=12, sat=0.0, x=0):
    return dict(l=x * 3, t=0, w=30, h=h, c=c, txt=txt, sat=sat, boite=[x, 100, x + 10, 112])


def test_mot_colore_court_devient_pictogramme_situe():
    L = dict(mots=[_mot_situe("place"), _mot_situe("fff", c=74, sat=0.14, x=20), _mot_situe("symbol", x=40)],
             gauche=0, droite=100, haut=0)
    icones = {"page": 2, "boites": []}
    assert S.texte_ligne(L, icones) == "place ⟦icône:2.1⟧ symbol"
    assert icones["boites"] == [[20, 100, 30, 112]]
    # un vrai mot en couleur, long et sûr, reste un mot
    L2 = dict(mots=[_mot_situe("Labyrinth", sat=0.5)], gauche=0, droite=100, haut=0)
    assert S.texte_ligne(L2, {"page": 2, "boites": []}) == "Labyrinth"


def test_vignette_haute_et_son_reste_fusionnes_en_un_pictogramme():
    """Tableau du Cycle I p. 1 (zone lue à part) : « diated » (13 %, deux fois plus haut) puis « sei » (6 %)."""
    mots = [_mot_situe("TEARS"), _mot_situe("MINOTAUR", x=20), _mot_situe("diated", c=13, h=26, x=40),
            _mot_situe("sei", c=6, h=9, x=60)]
    icones = {"page": 1, "boites": []}
    assert S.texte_ligne(dict(mots=mots, gauche=0, droite=100, haut=0, zone=True), icones) == "TEARS MINOTAUR ⟦icône:1.1⟧"
    assert icones["boites"] == [[40, 100, 70, 112]]
    # dans le texte courant, un mot peu sûr et haut (italique, capitale ornée) reste un mot
    assert S.texte_ligne(dict(mots=mots, gauche=0, droite=100, haut=0)) == "TEARS MINOTAUR diated sei"


def test_sans_page_le_pictogramme_reste_un_marqueur_simple():
    L = dict(mots=[_mot_situe("a"), _mot_situe("M", sat=0.2, x=20)], gauche=0, droite=100, haut=0)
    assert S.texte_ligne(L) == "a ⟦icône⟧"


def test_ancrer_decale_les_boites_dans_la_page():
    L = [dict(mots=[_mot_situe("x", x=5)], gauche=0, droite=10, haut=100)]
    assert S._ancrer(L, 300)[0]["mots"][0]["boite"] == [305, 100, 315, 112]


def test_numero_d_entree_colore_reste_du_texte():
    """« 0 0 2 8 » en gros chiffres brun-rouge (Cycle I) : ni pictogramme ni vignette, même mal lu."""
    mots = [_mot_situe("0", c=84, h=66, sat=0.12), _mot_situe("')", c=6, h=73, sat=0.04, x=10),
            _mot_situe("2", c=90, h=65, sat=0.11, x=20), _mot_situe("8", c=96, h=65, sat=0.1, x=30),
            _mot_situe("As", h=30, x=40), _mot_situe("you", h=30, x=50), _mot_situe("traverse", h=30, x=60)]
    icones = {"page": 3, "boites": []}
    assert S.texte_ligne(dict(mots=mots, gauche=0, droite=100, haut=0), icones).startswith("0 ') 2 8 As")
    assert icones["boites"] == []


# ── Numéros d'entrée et intertitres (17/11/2026) ─────────────────────────────

def _ligne_h(textes_h, gauche=0, droite=200, haut=0):
    return dict(mots=[_mot_situe(t, h=h, x=i * 10) for i, (t, h) in enumerate(textes_h)], gauche=gauche,
                droite=droite, haut=haut)


def _corps(n=3):
    return [_ligne_h([("the", 27), ("Argo", 27), ("cuts", 27), ("through", 27)], haut=100 + 30 * i) for i in range(n)]


def test_numero_d_entree_garde_en_tete_meme_avec_un_seul_mot_long():
    """« 0001 So it begins » : le paragraphe « So it begins » n'a qu'un mot de 3 lettres, il était jeté."""
    lignes = [_ligne_h([("0001", 63), ("So", 27), ("it", 27), ("begins", 27)], droite=60)] + _corps()
    blocs = S.lignes_vers_blocs(lignes, 200, 1000)
    assert blocs[0] == ("entree", "0001")
    assert blocs[1][0] == "para" and blocs[1][1].startswith("So it begins")


def test_numero_d_entree_eclate_et_mal_lu():
    assert S.numero_entree([_mot_situe(t, h=66) for t in ("0", "0", "0)")] + [_mot_situe("6", h=66),
                           _mot_situe("You", h=27)], 27)[0] == "0006"
    assert S.numero_entree([_mot_situe(t, h=66) for t in ("0", "0", "I", "3")] + [_mot_situe("Ok", h=27)], 27)[0] == "0013"
    assert S.numero_entree([_mot_situe(t, h=66) for t in ("0", "')", "0", "8")] + [_mot_situe("A", h=27)], 27)[0] == "0?08"
    assert S.numero_entree([_mot_situe("Despair", h=60)], 27) == (None, 0)


def test_reparer_entrees_par_les_voisins():
    journal = []
    blocs = [("entree", "0006"), ("para", "x"), ("entree", "000?"), ("entree", "0?08"), ("entree", "0009")]
    assert [t for k, t in S.reparer_entrees(blocs, journal) if k == "entree"] == ["0006", "0007", "0008", "0009"]
    assert len(journal) == 2


def test_intertitre_plus_gros_que_le_texte():
    lignes = _corps(2) + [_ligne_h([("HOW", 45), ("TO", 45), ("USE", 45), ("THIS", 45), ("BOOK?", 45)], haut=200)] \
        + _corps(2)
    blocs = S.lignes_vers_blocs(lignes, 200, 1000)
    assert ("intertitre", "HOW TO USE THIS BOOK?") in blocs
    # une ligne ordinaire un peu haute (« metropolis. » 1,42) n'en est pas un, ni une ligne en minuscule
    assert not S.est_intertitre(_ligne_h([("metropolis.", 38)]), 27, "metropolis.")
    assert not S.est_intertitre(_ligne_h([("place", 50), ("X", 50)]), 27, "place X")


def test_markdown_numero_et_intertitre_en_gras_jamais_en_titre():
    md = S.blocs_vers_markdown([("entree", "0001"), ("intertitre", "Fear and Dread"), ("para", "Texte")])
    assert "**0001**" in md and "**Fear and Dread**" in md and "#" not in md


def test_reparer_entrees_numero_lisible_mais_hors_sequence():
    """« 0940 » entre 0039 et 0041, « 00030 » entre 0029 et 0031, « 00?2 » après un « 0071 » lui-même réparé."""
    blocs = [("entree", v) for v in ("0039", "0940", "0041", "0029", "00030", "0031", "0070", "0?01", "00?2", "0073")]
    sortie = [t for _, t in S.reparer_entrees(blocs, [])]
    assert sortie == ["0039", "0040", "0041", "0029", "0030", "0031", "0070", "0071", "0072", "0073"]


def test_mot_en_gros_n_est_pas_un_numero():
    mots = [_mot_situe("“Traitor!”", h=42), _mot_situe("someone", h=42), _mot_situe("shouts.", h=25)]
    assert S.numero_entree(mots, 26) == (None, 0)


def test_ligne_decalee_sous_le_gros_numero_continue_le_paragraphe():
    lignes = [_ligne_h([("0070", 63), ("As", 27), ("you", 27), ("turn", 27), ("the", 27), ("corner,", 27)], droite=200),
              dict(_ligne_h([("attacked", 27), ("by", 27), ("the", 27), ("Horned", 27), ("Guard!", 27)]), gauche=60)] \
        + _corps()
    blocs = S.lignes_vers_blocs(lignes, 200, 1000)
    assert blocs[0] == ("entree", "0070")
    assert blocs[1][1].startswith("As you turn the corner, attacked by the Horned Guard!")


def test_numero_suivi_d_un_mot_haut_entre_guillemets():
    mots = [_mot_situe(t, h=66) for t in ("0", "I", "1", "0")] + [_mot_situe("“Phaedra,”", h=60), _mot_situe("says", h=27)]
    assert S.numero_entree(mots, 27) == ("0110", 4)


def test_renvoi_seul_garde():
    """« See 0068. » seul dans un encadré d'une ligne : un mot de 3 lettres et un renvoi, pas du bruit."""
    assert S.lignes_vers_blocs([_ligne_h([("See", 27), ("0068.", 27)])], 200, 1000) == [("para", "See 0068.")]
    assert S.lignes_vers_blocs([_ligne_h([("ee", 27), ("a", 27)])], 200, 1000) == []


def test_intertitre_seul_dans_son_segment_reconnu_avec_le_corps_de_la_page():
    seul = [_ligne_h([("HOW", 45), ("TO", 45), ("USE", 45), ("THIS", 45), ("BOOK?", 45)])]
    assert S.lignes_vers_blocs(seul, 200, 1000) == [("para", "HOW TO USE THIS BOOK?")]
    assert S.lignes_vers_blocs(seul, 200, 1000, corps_defaut=27) == [("intertitre", "HOW TO USE THIS BOOK?")]


def test_intertitre_en_capitales_seuil_plus_bas():
    assert S.est_intertitre(_ligne_h([("HOW", 38), ("TO", 38), ("USE", 38)]), 27, "HOW TO USE THIS BOOK?")
    assert not S.est_intertitre(_ligne_h([("How", 38), ("to", 38)]), 27, "How to use")


def test_element_de_liste_nouveau_paragraphe():
    lignes = [_ligne_h([("-", 27), ("3", 27), ("tokens:", 27), ("You", 27), ("cannot", 27), ("perform", 27)], haut=0),
              _ligne_h([("-", 27), ("4+", 27), ("tokens:", 27), ("Your", 27), ("Titan", 27), ("dies.", 27)], haut=30)]
    blocs = S.lignes_vers_blocs(lignes, 200, 1000)
    assert [t for _, t in blocs] == ["- 3 tokens: You cannot perform", "- 4+ tokens: Your Titan dies."]


def test_titre_courant_sans_numero_en_bas_de_page():
    assert S.est_pied_de_page("6 Truth of the Labyrinth Main Story", 1122, 1160)
    assert S.est_pied_de_page("Truth of the Labyrinth Main Story", 1120, 1160)       # numéro perdu par l'OCR
    assert not S.est_pied_de_page("Truth of the Labyrinth Main Story", 1090, 1160)   # au-dessus : du texte


# ── Redressement hors du scan (#569) et pages redressées signalées (#570) ────

def _page_penchee(angle: float, echelle: float = 1.0):
    """Page synthétique (agrandie à `echelle`) tournée de `angle` degrés, en RGB."""
    img, _ = page_synthetique()
    if echelle != 1.0:
        img = img.resize((round(img.width * echelle), round(img.height * echelle)), Image.BICUBIC)
    return img.rotate(angle, resample=Image.BICUBIC, fillcolor=PAPIER).convert("RGB")


def test_redressement_mesure_une_image_rendue_a_300_dpi():
    """L'OCR classique rend à 300 dpi : les seuils (taille d'une lettre) sont réglés pour 200 dpi, la mesure se
    fait donc sur une copie ramenée à 200 dpi. Sans cela, les lettres agrandies sortent du masque."""
    droite = _page_penchee(0.0, 1.5)
    image, angles = S.redresser_et_mesurer(droite, 300)
    assert image is droite and angles == [0.0]
    penchee = _page_penchee(-4.0, 1.5)
    image, angles = S.redresser_et_mesurer(penchee, 300)
    assert image.size == penchee.size and abs(angles[0] - 4.0) <= 0.3


def test_ocr_classique_lit_la_page_redressee(tmp_path, monkeypatch):
    """#569 : l'extracteur Tesseract classique passait la page penchée telle quelle à l'OCR."""
    import io

    import numpy as np
    import pymupdf

    from app.services import pdf_extractor

    penchee = _page_penchee(-4.0, 1.5)
    tampon = io.BytesIO()
    penchee.save(tampon, format="PNG")
    pdf = tmp_path / "photo.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=penchee.width * 72 / 300, height=penchee.height * 72 / 300)
        page.insert_image(page.rect, stream=tampon.getvalue())
        doc.save(str(pdf))

    lues = []

    class Resultat:
        returncode, stdout, stderr = 0, b"texte", b""

    def faux_tesseract(_cmd, input, **_):
        lues.append(Image.open(io.BytesIO(input)).convert("L"))
        return Resultat()

    monkeypatch.setattr(pdf_extractor, "tesseract_disponible", lambda: True)
    monkeypatch.setattr(pdf_extractor, "_langues_tesseract", lambda: "eng")
    monkeypatch.setattr(pdf_extractor.subprocess, "run", faux_tesseract)
    assert pdf_extractor.extraire_texte(str(pdf), "tesseract") == "texte"
    lue = lues[0].resize((round(lues[0].width / 1.5), round(lues[0].height / 1.5)), Image.BILINEAR)
    assert S.estimer_inclinaison(np.array(lue)) == 0.0     # l'OCR a reçu une page droite


def _etat_scan(pdf, job_id, fin):
    sorties = S.chemins_sortie(str(pdf))
    return {"job_id": job_id, "chemin_pdf": str(pdf), "chemin_sortie": sorties["markdown"],
            "dossier_pages": sorties["pages"], "chemin_etat": sorties["etat"], "langue_ocr": "eng",
            "double_page": False, "travailleurs": 1, "page_debut": 1, "page_fin": fin, "pages_total": fin,
            "pages_faites": 0, "pages_en_erreur": [], "pages_redressees": [], "statut": "en_attente",
            "temps_ecoule_secondes": 0.0, "demarre_a": 0}


def test_le_scan_note_les_pages_redressees(tmp_path, monkeypatch):
    """#570 : le redressement était invisible ; l'état du scan liste les pages tournées et leurs angles."""
    pdf = tmp_path / "doc.pdf"
    _pdf_trois_colonnes(pdf)
    angles = {1: [0.0], 2: [0.0, 4.2], 3: []}
    monkeypatch.setattr(S, "_traiter_et_ecrire_page",
                        lambda _c, n, *_: {"blocs": [], "redressement": angles[n]})
    etat = _etat_scan(pdf, "redresse", 3)
    S.enregistrer_job("redresse")
    S._executer(etat)
    assert S.lire_etat(str(pdf))["pages_redressees"] == [{"page": 2, "angles": [0.0, 4.2]}]


def test_page_en_cache_sans_angle_mesuree_sans_refaire_l_ocr(tmp_path, monkeypatch):
    """Une page lue avant #570 n'a pas d'angle noté : on le mesure (rendu seul) plutôt que de refaire l'OCR ou de
    taire un redressement."""
    pdf = tmp_path / "doc.pdf"
    _pdf_trois_colonnes(pdf)
    sorties = S.chemins_sortie(str(pdf))
    os.makedirs(sorties["pages"], exist_ok=True)
    reglages = {"langue": "eng", "double_page": False, "algo": S.VERSION_ALGO, "regions": None}
    chemin = os.path.join(sorties["pages"], "page-0001.json")
    with open(chemin, "w") as f:
        json.dump({"blocs": [["texte", "déjà lu"]], "page": 1, "reglages": reglages}, f)
    monkeypatch.setattr(S, "traiter_page", lambda *_a, **_k: pytest.fail("OCR refait"))
    res = S._traiter_et_ecrire_page(str(pdf), 1, sorties["pages"], "eng", False)
    assert res["redressement"] == [0.0, 0.0] and res["blocs"] == [["texte", "déjà lu"]]   # paysage : deux moitiés
    with open(chemin) as f:
        assert json.load(f)["redressement"] == [0.0, 0.0]


def test_symbole_noir_apres_une_quantite_devient_pictogramme():
    """Cycle I section 3 p. 7 (19/11/2026) : Λ, Ω, Ψ, ✕ imprimés en noir après « +1 » étaient lus « A », « Q »,
    « YW », « X » (jusqu'à 96 % de confiance) et traduits comme des lettres."""
    def L(*mots):
        return dict(mots=[_mot_situe(t, c=c, h=h, x=i * 10) for i, (t, c, h) in enumerate(mots)],
                    gauche=0, droite=200, haut=0)
    assert S.texte_ligne(L(("Gain", 96, 27), ("+2", 95, 27), ("A", 96, 30), ("to", 95, 22),
                                       ("add", 96, 27), ("+2.", 95, 27))) == "Gain +2 ⟦icône⟧ to add +2."
    assert S.texte_ligne(L(("subtract", 96, 27), ("-2", 95, 27), ("WY.", 33, 38))) == "subtract -2 ⟦icône⟧"
    assert S.texte_ligne(L(("gains", 96, 27), ("+1", 95, 27), ("Qand", 87, 31), ("+2", 95, 27),
                                       ("Λ.", 89, 30))) == "gains +1 ⟦icône⟧ and +2 ⟦icône⟧"
    assert S.texte_ligne(L(("leader", 96, 27), ("gains", 96, 27), ("+1.Q.", 80, 30),
                                       ("See", 96, 27))) == "leader gains +1 ⟦icône⟧ See"
    # un vrai mot après une quantité reste un mot
    assert S.texte_ligne(L(("Gain", 96, 27), ("+1", 95, 27), ("Priest", 97, 26))) == "Gain +1 Priest"
    assert S.texte_ligne(L(("Lose", 96, 27), ("-1", 95, 27), ("Titan.", 96, 27))) == "Lose -1 Titan."


def test_rangees_de_tirage_regroupees_en_un_tableau():
    """Cycle I section 3 p. 5 : une rangée par petit encadré (clé, grand blanc, libellé, case) → un bloc « tirage »."""
    def rangee(cle, libelle, case=None, y=0):
        mots = [dict(l=90, t=40, w=33, h=30, c=80, txt=cle, sat=0.0, boite=[914, y, 925, y + 10])]
        x = 260
        for t in libelle.split():
            mots.append(dict(l=x, t=45, w=len(t) * 15, h=26, c=96, txt=t, sat=0.0, boite=[970, y, 990, y + 9]))
            x += len(t) * 15 + 10
        if case:
            mots.append(dict(l=595, t=29, w=61, h=62, c=45, txt=case, sat=0.0, boite=[1082, y, 1102, y + 20]))
        return [dict(mots=mots, gauche=30, droite=220, haut=y)]
    lus = [((1, 0, 600), "encadre", rangee("a", "Comes a Savior", y=600), 235),
           ((1, 0, 640), "encadre", rangee("1-2", "Mazed Forest", "@", y=640), 235),
           ((1, 0, 680), "encadre", rangee("7", "Words of Love", "S", y=680), 235),
           ((1, 0, 720), "encadre", rangee("Q", "Till Death", y=720), 235),
           ((1, 0, 760), "texte", [dict(mots=[mot("Following"), mot("rumors")], gauche=0, droite=200, haut=760)], 235)]
    sortie = S._regrouper_tirages(lus, None, "eng")
    assert [e[1] for e in sortie] == ["tirage", "texte"]
    assert S._texte_tirage(sortie[0][2], None) == ("| α | Comes a Savior |\n| 1–2 | Mazed Forest | ☐ |\n"
                                                   "| 7 | Words of Love | ☐ |\n| Ω | Till Death |")
    # deux rangées seulement : pas un tableau
    assert [e[1] for e in S._regrouper_tirages(lus[:2], None, "eng")] == ["encadre", "encadre"]
