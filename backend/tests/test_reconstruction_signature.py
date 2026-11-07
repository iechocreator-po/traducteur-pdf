"""
Reconstruction d'un scan traduit (PDF mis en page + HTML interactif) et signature visuelle d'un PDF.
Documents synthétiques : une « double page scannée » dessinée avec PIL (papier, lignes de texte en
colonnes, une illustration colorée), sans OCR ni Ollama.
"""

import base64
import io
import json
import os

import pymupdf
import pytest
from PIL import Image, ImageDraw

from app.services import reconstruction, signature


def _page_scannee(largeur=1600, hauteur=1140, illustration=True) -> Image.Image:
    img = Image.new("RGB", (largeur, hauteur), (232, 228, 220))
    d = ImageDraw.Draw(img)
    colonnes = [(60, 300), (320, 560), (580, 780), (840, 1080), (1100, 1340), (1360, 1560)]
    for a, b in colonnes:
        # une « ligne de texte » : traits fins coupés en mots, ~12 % d'encre comme le scan réel
        # (mesuré sur le Cycle I : le texte n'assombrit la page que d'une vingtaine de niveaux)
        for y in range(60, hauteur - 80, 22):
            for x in range(a, b, 46):
                d.rectangle([x, y, min(b, x + 36), y + 3], fill=(60, 55, 50))
    if illustration:
        d.rectangle([0, 0, 800, 520], fill=(70, 110, 160))
        d.ellipse([200, 100, 600, 450], fill=(180, 90, 40))
    return img


@pytest.fixture
def scan_traduit(tmp_path):
    """PDF scanné (2 pages) + pages du scan + traduction avec son état, comme les écrit l'app."""
    base = tmp_path / "Livre"
    pdf = pymupdf.open()
    for illus in (True, False):
        tampon = io.BytesIO()
        _page_scannee(illustration=illus).save(tampon, format="PNG")
        page = pdf.new_page(width=1600 * 72 / 200, height=1140 * 72 / 200)
        page.insert_image(page.rect, stream=tampon.getvalue())
    pdf.save(f"{base}.pdf")
    pdf.close()

    pages = tmp_path / "Livre_scan_colonnes_pages"
    pages.mkdir()
    (pages / "page-0001.json").write_text(json.dumps({"page": 1, "blocs": [
        ["titre", "1 The Ship"], ["para", "First paragraph of the ship."], ["encadre", "Gain one titan."],
        ["titre", "2 The Sea"], ["para", "Waves everywhere on page one."]]}))
    (pages / "page-0002.json").write_text(json.dumps({"page": 2, "blocs": [
        ["para", "Still the sea on page two."], ["titre", "3 Home"], ["para", "Back home at last."]]}))
    source_md = tmp_path / "Livre_scan_colonnes.md"
    source_md.write_text("## 1 The Ship\n\n...")
    traduit = tmp_path / "Livre_scan_colonnes_traduit_m.md"
    traduit.write_text(
        "<!-- modèle : m -->\n\n"
        "<!-- === chapitre 0 : 1 The Ship === -->\n\n## 1 Le Navire\n\nPremier paragraphe du **navire**.\n\n"
        "> Gagnez un titan ⟦icône⟧.\n\n"
        "<!-- === chapitre 1 : 2 The Sea === -->\n\n## 2 La Mer\n\nDes vagues partout, page un.\n\n"
        "Toujours la mer, page deux.\n\n"
        "<!-- === chapitre 2 : 3 Home === -->\n\n## 3 Le Retour\n\nEnfin à la maison.\n")
    (tmp_path / "Livre_scan_colonnes_traduit_m.state.json").write_text(json.dumps({"chemin_pdf": str(source_md)}))
    return str(traduit)


def test_detecte_l_illustration_et_pas_le_texte():
    rects = reconstruction.detecter_illustrations(_page_scannee())
    assert len(rects) == 1
    x0, y0, x1, y1 = rects[0]
    assert x0 <= 20 and y0 <= 20 and 760 <= x1 <= 840 and 480 <= y1 <= 560
    assert reconstruction.detecter_illustrations(_page_scannee(illustration=False)) == []


def test_chaque_paragraphe_traduit_retrouve_sa_page(scan_traduit):
    loc = reconstruction.localiser(scan_traduit)
    with open(scan_traduit, encoding="utf-8") as f:
        par_page = reconstruction.repartir_par_page(loc["dossier_pages"], loc["pages"], f.read())
    textes = {n: " ".join(t for _, t in blocs) for n, blocs in par_page.items()}
    assert "Le Navire" in textes[1] and "page un" in textes[1]
    assert "Toujours la mer" in textes[2] and "Le Retour" in textes[2]
    assert ("encadre", "Gagnez un titan ⟦icône⟧.") in par_page[1]


def test_document_hors_scan_refuse(tmp_path):
    md = tmp_path / "Essai_traduit_m.md"
    md.write_text("bonjour")
    assert reconstruction.info(str(md))["disponible"] is False
    with pytest.raises(reconstruction.ReconstructionImpossible):
        reconstruction.generer_pdf(str(md))


def test_pdf_mis_en_page_garde_format_illustrations_et_texte(scan_traduit):
    r = reconstruction.generer_pdf(scan_traduit)
    assert r["pages"] == 2 and r["illustrations"] == 1 and r["pages_suite"] == []
    doc = pymupdf.open(r["chemin"])
    try:
        assert doc.page_count == 2
        source = pymupdf.open(scan_traduit.replace("_scan_colonnes_traduit_m.md", ".pdf"))
        # même proportions que le scan, agrandi ×ECHELLE_SORTIE
        assert doc[0].rect.width == pytest.approx(source[0].rect.width * reconstruction.ECHELLE_SORTIE, rel=0.01)
        source.close()
        texte = doc[0].get_text()
        assert "Le Navire" in texte and "navire" in texte and "◆" in texte
        assert "Le Retour" in doc[1].get_text()
        assert len(doc[0].get_images()) >= 1
    finally:
        doc.close()


def test_html_interactif_autonome(scan_traduit):
    r = reconstruction.generer_html(scan_traduit, titre="Livre <test>")
    contenu = open(r["chemin"], encoding="utf-8").read()
    assert r["illustrations"] == 1
    assert "<title>Livre &lt;test&gt;</title>" in contenu
    assert "<b>navire</b>" in contenu and "data:image/jpeg;base64," in contenu
    assert 'href="#chap-1"' in contenu and "Page par page" in contenu
    assert "http://" not in contenu.replace("http://www.w3.org", "")   # aucune ressource externe


def _pdf(tmp_path, rotation=0):
    chemin = tmp_path / "contrat.pdf"
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), "Contrat")
        page.set_rotation(rotation)
    doc.save(chemin)
    doc.close()
    return str(chemin)


def _png():
    img = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(10, 80), (150, 10), (290, 70)], fill=(20, 30, 110, 255), width=6)
    t = io.BytesIO()
    img.save(t, format="PNG")
    return t.getvalue()


@pytest.mark.parametrize("rotation", [0, 90])
def test_signature_sur_une_copie(tmp_path, rotation):
    chemin = _pdf(tmp_path, rotation)
    avant = open(chemin, "rb").read()
    r = signature.signer(chemin, _png(), [{"page": 2, "x": 0.6, "y": 0.8, "largeur": 0.3, "hauteur": 0.1}],
                         mention="Signé électroniquement par JP")
    assert r["chemin"].endswith("contrat_signe.pdf") and r["pages"] == [2]
    assert open(chemin, "rb").read() == avant                      # l'original n'est pas touché
    doc = pymupdf.open(r["chemin"])
    try:
        assert len(doc[0].get_images()) == 0 and len(doc[1].get_images()) == 1
        assert "Signé électroniquement par JP" in doc[1].get_text()
        # l'image est bien dans le quart bas-droit de la page AFFICHÉE
        rect = doc[1].get_image_rects(doc[1].get_images()[0][0])[0] * doc[1].rotation_matrix
        assert rect.x0 > doc[1].rect.width * 0.5 and rect.y0 > doc[1].rect.height * 0.7
    finally:
        doc.close()
    # signer à nouveau la copie l'enrichit au lieu d'en créer une troisième
    r2 = signature.signer(r["chemin"], _png(), [{"page": 1, "x": 0.1, "y": 0.1, "largeur": 0.2, "hauteur": 0.1}])
    assert r2["chemin"] == r["chemin"]
    assert not os.path.exists(str(tmp_path / "contrat_signe_signe.pdf"))


def test_signature_refuse_une_image_invalide_ou_un_placement_hors_page(tmp_path):
    chemin = _pdf(tmp_path)
    with pytest.raises(signature.SignatureInvalide):
        signature.signer(chemin, b"pas une image", [{"page": 1, "x": 0, "y": 0, "largeur": 0.2, "hauteur": 0.1}])
    with pytest.raises(signature.SignatureInvalide):
        signature.signer(chemin, _png(), [{"page": 3, "x": 0, "y": 0, "largeur": 0.2, "hauteur": 0.1}])
    with pytest.raises(signature.SignatureInvalide):
        signature.signer(chemin, _png(), [])


def test_route_signature(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app

    chemin = _pdf(tmp_path)
    client = TestClient(app)
    r = client.post("/api/signature", json={
        "chemin_pdf": chemin,
        "signature_png_base64": "data:image/png;base64," + base64.b64encode(_png()).decode(),
        "placements": [{"page": 1, "x": 0.5, "y": 0.5, "largeur": 0.3, "hauteur": 0.1}],
    })
    assert r.status_code == 200, r.text
    assert client.get("/api/telecharger", params={"chemin": r.json()["chemin"]}).status_code == 200
    autre = tmp_path / "page.html"
    autre.write_text("<p>x</p>")
    assert client.get("/api/telecharger", params={"chemin": str(autre)}).status_code == 422
    assert client.get("/api/signature/apercu", params={"chemin_pdf": chemin, "page": 1}).headers["content-type"] == "image/png"


# ── Correctifs de la revue du 06/11/2026 ────────────────────────────────────────────────────────

MENTION_TYPE = "Signé électroniquement par Jean-Pierre Parra le 6 novembre 2026 à 10 h 30"


def _texte_compact(page) -> str:
    return " ".join(page.get_text().split())


@pytest.mark.parametrize("x,y,larg,haut", [
    (0.1, 0.1, 0.286, 0.095),   # largeur et ratio posés par le clic dans l'interface
    (0.1, 0.1, 0.286, 0.3),     # signature haute : la police visée est la plus grande
    (0.1, 0.9, 0.286, 0.095),   # bas de page : la mention passe au-dessus
    (0.8, 0.5, 0.15, 0.05),     # bord droit : la mention revient dans la page
])
@pytest.mark.parametrize("rotation", [0, 90])
def test_mention_toujours_presente_et_complete(tmp_path, x, y, larg, haut, rotation):
    chemin = _pdf(tmp_path, rotation)
    r = signature.signer(chemin, _png(), [{"page": 1, "x": x, "y": y, "largeur": larg, "hauteur": haut}], mention=MENTION_TYPE)
    doc = pymupdf.open(r["chemin"])
    try:
        assert MENTION_TYPE in _texte_compact(doc[0])
    finally:
        doc.close()


def test_mention_impossible_echoue_sans_ecrire(tmp_path):
    chemin = _pdf(tmp_path)
    doc = pymupdf.open()
    doc.new_page(width=60, height=30)        # page minuscule : même à 6 pt, 200 caractères ne tiennent pas
    petit = str(tmp_path / "petit.pdf")
    doc.save(petit)
    doc.close()
    with pytest.raises(signature.SignatureInvalide):
        signature.signer(petit, _png(), [{"page": 1, "x": 0, "y": 0, "largeur": 0.5, "hauteur": 0.3}], mention="m" * 200)
    assert not os.path.exists(str(tmp_path / "petit_signe.pdf"))
    assert os.path.exists(chemin)


def test_signer_l_original_ne_detruit_pas_une_copie_signee(tmp_path):
    chemin = _pdf(tmp_path)
    pl = {"page": 1, "x": 0.1, "y": 0.1, "largeur": 0.2, "hauteur": 0.1}
    r = signature.signer(chemin, _png(), [pl])
    avant = open(r["chemin"], "rb").read()
    with pytest.raises(signature.SignatureInvalide):
        signature.signer(chemin, _png(), [{**pl, "page": 2}])
    assert open(r["chemin"], "rb").read() == avant


def test_png_geant_refuse_sans_decompression(tmp_path):
    img = Image.new("1", (20000, 20000))
    t = io.BytesIO()
    img.save(t, format="PNG")
    with pytest.raises(signature.SignatureInvalide):
        signature.signer(_pdf(tmp_path), t.getvalue(), [{"page": 1, "x": 0, "y": 0, "largeur": 0.2, "hauteur": 0.1}])


def test_signatures_simultanees_sans_erreur_de_temporaire(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    chemin = _pdf(tmp_path)
    pl = [{"page": 1, "x": 0.1, "y": 0.1, "largeur": 0.2, "hauteur": 0.1}]
    copie = signature.signer(chemin, _png(), pl)["chemin"]
    png = _png()
    with ThreadPoolExecutor(4) as pool:
        for f in [pool.submit(signature.signer, copie, png, pl) for _ in range(40)]:
            f.result()
    assert pymupdf.open(copie).page_count == 2
    assert [n for n in os.listdir(tmp_path) if n.endswith(".tmp")] == []


def test_tient_compte_tous_les_cadres():
    from app.services import reconstruction
    cadres = [pymupdf.Rect(40 + i * 180, 40, 210 + i * 180, 800) for i in range(3)]
    html = "<p>" + " ".join(["mot"] * 1200) + "</p>"
    une_colonne = cadres[:1]
    assert not reconstruction._tient(html, 10.0, une_colonne)    # dépasse la première colonne…
    assert reconstruction._tient(html, 10.0, cadres)             # …mais tient dans les trois, sans réduire


# ── Répartition par page : ancres, bornes, plage ─────────────────────────────

def _page_json(dossier, n, blocs):
    (dossier / f"page-{n:04d}.json").write_text(json.dumps({"page": n, "blocs": blocs}))


def test_un_faux_titre_lointain_ne_fait_pas_sauter_les_chapitres():
    # « Chapter one » existe à l'identique très loin : le prendre ferait perdre les trois chapitres suivants.
    cibles = ["Chapter one", "Chapter two", "Chapter three", "Chapter four"]
    scan = ["Chapter on", "Chapter tw", "Chapter thre", "Chapter fou", "Annexe", "Chapter one"]
    assert reconstruction._aligner_titres(cibles, scan) == [0, 1, 2, 3]


def test_alignement_respecte_l_ordre_et_signale_l_absent():
    assert reconstruction._aligner_titres(["Alpha", "Sans rapport", "Gamma"], ["Alpha", "Gamma"]) == [0, None, 1]
    assert reconstruction._aligner_titres(["Gamma", "Alpha"], ["Alpha", "Gamma"]) in ([1, None], [None, 0])


def test_a_total_egal_le_titre_le_plus_proche_gagne():
    assert reconstruction._aligner_titres(["Introduction"], ["Introduction", "Introduction"]) == [0]


def test_chapitre_traduit_seul_ne_s_etale_pas_jusqu_a_la_fin(tmp_path):
    for n, titre in enumerate(("Un", "Deux", "Trois"), start=1):
        _page_json(tmp_path, n, [["titre", titre], ["para", "Texte source " * 20], ["para", "Encore " * 20]])
    # Un seul paragraphe traduit pour deux à la source : passe par la répartition proportionnelle.
    md = "<!-- === chapitre 0 : Un === -->\n\n## Uno\n\n" + "Texto traducido " * 50 + "\n"
    source = [{"titre": t, "niveau": 2} for t in ("Un", "Deux", "Trois")]
    par_page = reconstruction.repartir_par_page(str(tmp_path), [1, 2, 3], md, source)
    assert par_page[2] == [] and par_page[3] == []
    assert [k for k, _ in par_page[1]] == ["titre", "para"]


def test_chapitre_parent_garde_ses_sous_chapitres(tmp_path):
    _page_json(tmp_path, 1, [["titre", "Partie"], ["para", "A" * 100]])
    _page_json(tmp_path, 2, [["titre", "Section"], ["para", "B" * 100]])
    _page_json(tmp_path, 3, [["titre", "Autre partie"], ["para", "C" * 100]])
    md = "<!-- === chapitre 0 : Partie === -->\n\n## Partie\n\n" + "a" * 100 + "\n\n" + "b" * 100 + "\n"
    source = [{"titre": "Partie", "niveau": 1}, {"titre": "Section", "niveau": 2}, {"titre": "Autre partie", "niveau": 1}]
    par_page = reconstruction.repartir_par_page(str(tmp_path), [1, 2, 3], md, source)
    assert ("para", "b" * 100) in par_page[2] and par_page[3] == []


def test_source_regeneree_ignoree_si_elle_ne_correspond_plus(tmp_path):
    _page_json(tmp_path, 1, [["titre", "Un"], ["para", "x"]])
    _page_json(tmp_path, 2, [["titre", "Deux"], ["para", "y"]])
    md = "<!-- === chapitre 0 : Un === -->\n\n## Uno\n\nX\n\n<!-- === chapitre 1 : Deux === -->\n\n## Dos\n\nY\n"
    par_page = reconstruction.repartir_par_page(str(tmp_path), [1, 2], md, [{"titre": "Autre chose", "niveau": 2}])
    assert ("para", "Y") in par_page[2]


def test_repartition_proportionnelle_aux_caracteres(tmp_path):
    _page_json(tmp_path, 1, [["titre", "Un"], ["para", "s" * 100], ["para", "s" * 100]])
    _page_json(tmp_path, 2, [["para", "s" * 200]])
    # 2 paragraphes traduits pour 3 à la source : le 1er (court) en page 1, le 2e (milieu à 62 %) en page 2.
    md = "<!-- === chapitre 0 : Un === -->\n\n## Uno\n\n" + "c" * 100 + "\n\n" + "l" * 100 + "\n\n"
    par_page = reconstruction.repartir_par_page(str(tmp_path), [1, 2], md)
    assert ("para", "c" * 100) in par_page[1] and ("para", "l" * 100) in par_page[2]


def _etat_scan(pdf, **champs):
    from app.services import scan_colonnes
    sorties = scan_colonnes.chemins_sortie(pdf)
    with open(sorties["etat"], "w", encoding="utf-8") as f:
        json.dump({"statut": "termine", "job_id": "fini", **champs}, f)


def test_seules_les_pages_du_dernier_scan_comptent(scan_traduit):
    pdf = scan_traduit.replace("_scan_colonnes_traduit_m.md", ".pdf")
    assert reconstruction.localiser(scan_traduit)["pages"] == [1, 2]
    _etat_scan(pdf, page_debut=2, page_fin=2)
    assert reconstruction.localiser(scan_traduit)["pages"] == [2]


def test_reglage_double_page_du_scan_transmis_et_dans_le_cache(scan_traduit, monkeypatch):
    from app.services import scan_colonnes
    pdf = scan_traduit.replace("_scan_colonnes_traduit_m.md", ".pdf")
    reconstruction.generer_pdf(scan_traduit)

    vus = []
    origine = scan_colonnes.analyser_mise_en_page
    monkeypatch.setattr(scan_colonnes, "analyser_mise_en_page", lambda image, dp=None: vus.append(dp) or origine(image, dp))
    _etat_scan(pdf, double_page=False)
    reconstruction.generer_pdf(scan_traduit)     # le réglage a changé : le cache ne doit pas resservir
    assert vus == [False, False]
    reconstruction.generer_pdf(scan_traduit)     # inchangé : cache
    assert vus == [False, False]


# ── Illustrations ────────────────────────────────────────────────────────────

def _page_jaunie(illustration=False):
    img = _page_scannee(illustration=illustration)
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            if px[x, y] == (232, 228, 220):
                px[x, y] = (235, 215, 170)
    return img


def test_papier_jauni_n_est_pas_une_illustration():
    assert reconstruction.detecter_illustrations(_page_jaunie()) == []


def test_illustration_toujours_trouvee_sur_papier_jauni():
    rects = reconstruction.detecter_illustrations(_page_jaunie(illustration=True))
    assert len(rects) == 1 and rects[0][0] < 40 and rects[0][2] > 760


def test_illustration_replacee_a_sa_position_dans_le_pdf(scan_traduit):
    from app.services import scan_colonnes
    r = reconstruction.generer_pdf(scan_traduit)
    ech = 72 / scan_colonnes.DPI_RENDU * reconstruction.ECHELLE_SORTIE
    doc = pymupdf.open(r["chemin"])
    try:
        rects = doc[0].get_image_rects(doc[0].get_images()[0][0])
        assert len(rects) == 1
        # dessinée dans _page_scannee sur (0, 0, 800, 520) pixels ; la détection travaille par tuiles de 8 px,
        # fermeture morphologique comprise : quatre tuiles de tolérance
        tol = 32 * ech
        assert rects[0].x0 == pytest.approx(0, abs=tol) and rects[0].y0 == pytest.approx(0, abs=tol)
        assert rects[0].x1 == pytest.approx(800 * ech, abs=tol) and rects[0].y1 == pytest.approx(520 * ech, abs=tol)
    finally:
        doc.close()


def test_position_d_une_illustration_dans_le_flux():
    info = {"largeur": 1600, "cadres": [[0, 0, 400, 400], [0, 600, 400, 1000], [800, 0, 1200, 1000]]}
    gauche = reconstruction._position_illustration(info, {"rect": [0, 420, 400, 580]})
    droite = reconstruction._position_illustration(info, {"rect": [800, 0, 1200, 300]})
    assert gauche == pytest.approx(160000 / 720000)     # seul le cadre au-dessus la précède
    assert droite == pytest.approx(320000 / 720000)     # toute la demi-page gauche la précède
    assert reconstruction._position_illustration({"largeur": 100, "cadres": []}, {"rect": [0, 0, 1, 1]}) == 0.0


# ── Sorties : texte jamais coupé, HTML ───────────────────────────────────────

def test_texte_trop_long_echoue_au_lieu_d_etre_coupe(scan_traduit, monkeypatch):
    with open(scan_traduit, "a", encoding="utf-8") as f:
        f.write("\n\n" + "\n\n".join(["Enfin à la maison, encore et encore. " * 40] * 60) + "\n")
    monkeypatch.setattr(reconstruction, "NB_MAX_SUITES", 0)
    with pytest.raises(reconstruction.ReconstructionImpossible, match="ne tient pas"):
        reconstruction.generer_pdf(scan_traduit)
    assert not os.path.exists(reconstruction.localiser(scan_traduit)["sortie_pdf"])


@pytest.mark.parametrize("langue, attendu", [("espagnol", "es"), ("anglais", "en"), ("français", "fr"), (None, "und")])
def test_html_prend_la_langue_du_document(scan_traduit, langue, attendu):
    chemin_etat = os.path.splitext(scan_traduit)[0] + ".state.json"
    etat = json.load(open(chemin_etat, encoding="utf-8"))
    etat["langue_cible"] = langue
    json.dump(etat, open(chemin_etat, "w", encoding="utf-8"))
    contenu = open(reconstruction.generer_html(scan_traduit)["chemin"], encoding="utf-8").read()
    assert f'<html lang="{attendu}">' in contenu


def test_html_une_ancre_par_titre_meme_identique(scan_traduit):
    with open(scan_traduit, "w", encoding="utf-8") as f:
        f.write("<!-- === chapitre 0 : 1 The Ship === -->\n\n## Introduction\n\nUn.\n\n"
                "<!-- === chapitre 1 : 2 The Sea === -->\n\n## Introduction\n\nDeux.\n")
    contenu = open(reconstruction.generer_html(scan_traduit)["chemin"], encoding="utf-8").read()
    assert contenu.count('id="chap-1"') == 1 and contenu.count('id="chap-2"') == 1
    assert 'href="#chap-1"' in contenu and 'href="#chap-2"' in contenu


# ── Signature : rotation et CropBox ──────────────────────────────────────────

@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_signature_placee_au_bon_endroit_avec_cropbox(tmp_path, rotation):
    import numpy as np

    chemin = tmp_path / "recadre.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.set_cropbox(pymupdf.Rect(50, 100, 450, 700))
    page.set_rotation(rotation)
    W, H = page.rect.width, page.rect.height
    doc.save(chemin)
    doc.close()
    tampon = io.BytesIO()
    Image.new("RGB", (300, 300), (255, 0, 0)).save(tampon, format="PNG")
    cote = 120
    x, y = 0.1, 0.2
    r = signature.signer(str(chemin), tampon.getvalue(),
                         [{"page": 1, "x": x, "y": y, "largeur": cote / W, "hauteur": cote / H}], "Signé par JP")

    doc = pymupdf.open(r["chemin"])
    try:
        page = doc[0]
        assert page.rotation == rotation       # rotation d'origine restaurée
        pix = page.get_pixmap(alpha=False)
        px = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        ys, xs = ((px[..., 0] > 200) & (px[..., 1] < 80) & (px[..., 2] < 80)).nonzero()
        assert len(xs), "signature absente de la zone visible"
        assert xs.min() == pytest.approx(x * W, abs=2) and xs.max() + 1 == pytest.approx(x * W + cote, abs=2)
        assert ys.min() == pytest.approx(y * H, abs=2) and ys.max() + 1 == pytest.approx(y * H + cote, abs=2)
        assert "Signé par JP" in _texte_compact(page)
    finally:
        doc.close()


# ── Routes : flags, validation ───────────────────────────────────────────────

def _flags_off(monkeypatch, *noms):
    from app.api import routes
    origine = routes.charger_flags
    monkeypatch.setattr(routes, "charger_flags", lambda: {**origine(), **{n: False for n in noms}})


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_routes_reconstruction(scan_traduit, tmp_path, monkeypatch):
    client = _client()
    assert client.get("/api/reconstruction/info", params={"chemin_sortie": scan_traduit}).json()["disponible"] is True
    assert client.post("/api/reconstruction/docx", params={"chemin_sortie": scan_traduit}).status_code == 404
    r = client.post("/api/reconstruction/pdf", params={"chemin_sortie": scan_traduit})
    assert r.status_code == 200 and r.json()["chemin"].endswith("_mise_en_page.pdf")

    autre = tmp_path / "autre_traduit_m.md"
    autre.write_text("texte")
    r = client.post("/api/reconstruction/html", params={"chemin_sortie": str(autre)})
    assert r.status_code == 409 and "Scan multi-colonnes" in r.json()["detail"]

    _flags_off(monkeypatch, "reconstruction_scan")
    assert client.post("/api/reconstruction/pdf", params={"chemin_sortie": scan_traduit}).status_code == 403


def test_routes_signature_refusent_flag_off_et_entrees_invalides(tmp_path, monkeypatch):
    client = _client()
    pdf = _pdf(tmp_path)
    corps = {"chemin_pdf": pdf, "signature_png_base64": base64.b64encode(_png()).decode(),
             "placements": [{"page": 1, "x": 0.1, "y": 0.1, "largeur": 0.2, "hauteur": 0.1}]}
    assert client.post("/api/signature", json={**corps, "signature_png_base64": "%%%"}).status_code == 422
    assert client.post("/api/signature", json={**corps, "placements": []}).status_code == 422
    assert client.post("/api/signature", json={**corps, "placements": [{**corps["placements"][0], "page": 9}]}).status_code == 422
    assert client.get("/api/signature/apercu", params={"chemin_pdf": pdf, "page": 9}).status_code == 422
    assert client.get("/api/signature/info", params={"chemin_pdf": pdf}).json()["pages"] == 2

    _flags_off(monkeypatch, "signature_pdf")
    assert client.post("/api/signature", json=corps).status_code == 403
    assert client.get("/api/signature/info", params={"chemin_pdf": pdf}).status_code == 403
    assert client.get("/api/signature/apercu", params={"chemin_pdf": pdf}).status_code == 403
    assert client.get("/api/signature/pdfs", params={"chemin_sortie": str(tmp_path / "x.md")}).status_code == 403
