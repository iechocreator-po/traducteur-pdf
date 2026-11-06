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
