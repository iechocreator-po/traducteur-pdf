"""
Export d'un document traduit NATIF (hors Scan multi-colonnes) : HTML interactif et PDF A4.
Traduction synthétique à marqueurs de chapitre, sans Ollama.
"""

import json
import re

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import export_document

client = TestClient(app)


@pytest.fixture
def traduction(tmp_path):
    dossier_images = tmp_path / "Livre_images"
    dossier_images.mkdir()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(dossier_images / "img-0.png")
    (tmp_path / "secret.png").write_bytes((dossier_images / "img-0.png").read_bytes())
    md = tmp_path / "Livre_traduit_m.md"
    md.write_text(
        "<!-- modèle : m | source : anglais → français -->\n\n"
        "<!-- === chapitre 0 : Title Page === -->\n\n\n"
        "<!-- === chapitre 1 : Chapter One === -->\n\n"
        "###### **1.1 La méthode**\n\n"
        "Un texte avec _italique_, **gras**, x<sup>2</sup> et <script>alert(1)</script>.\n\n"
        "[clic](javascript:alert(1)) et [site](https://exemple.org)\n\n"
        "![](Livre_images/img-0.png)\n\n"
        "![](Livre_images/img-0.png)\n\n"
        "![](../secret.png)\n\n"
        "<!-- non-traduit -->\nLeft in English.\n\n"
        "```\nif a < b: **pas du gras**\n```\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "<!-- === chapitre 2 : Chapter Two === -->\n\n"
        "###### **2.1 Suite**\n\n###### **2.1.1 Détail**\n\nDernier paragraphe recherché.\n",
        encoding="utf-8",
    )
    return md


def test_markdown_echappe_avant_la_liste_blanche():
    html, _ = export_document.markdown_en_html(
        "<img src=x onerror=alert(1)> <sup>2</sup> [a](javascript:x) `<b>`", lambda _r: None, "c")
    assert "<img" not in html and "&lt;img" in html
    assert "<sup>2</sup>" in html
    assert 'href="javascript' not in html
    assert "<code>&lt;b&gt;</code>" in html
    cellule, _ = export_document.markdown_en_html("| a<br>b | <br onclick=x> |", lambda _r: None, "c")
    assert "a<br>b" in cellule and "<br onclick" not in cellule


def test_profondeur_des_titres_par_numerotation():
    titres = []
    export_document.markdown_en_html("###### **5.4 A**\n\n###### **5.4.1 B**\n\n###### Sans numéro", lambda _r: None,
                                     "c", titres)
    assert [t[0] for t in titres] == [3, 4, 4]


def test_html_interactif(traduction):
    r = export_document.generer_html(str(traduction), titre="Livre <test>")
    assert r["mode"] == "document" and r["chapitres"] == 2      # le chapitre vide (Title Page) est sauté
    assert r["illustrations"] == 2 and r["original"] == 0         # pas d'état : pas de version d'origine
    page = open(r["chemin"], encoding="utf-8").read()
    assert r["chemin"].endswith("Livre_traduit_m_interactif.html")
    assert "Livre &lt;test&gt;" in page and "<script>alert" not in page
    assert 'id="regard" aria-pressed="false" hidden' in page      # bouton masqué sans original
    images = json.loads(re.search(r'<script type="application/json" id="images">(.*?)</script>', page, re.S).group(1))
    assert len(images) == 1                                       # la même image n'est encodée qu'une fois
    assert "image introuvable : ../secret.png" in page            # jamais hors du dossier de la traduction
    assert 'class="non-traduit"' in page
    assert "<pre><code>if a &lt; b: **pas du gras**</code></pre>" in page
    assert "<th>A</th>" in page
    # Titre traduit d'un chapitre = son premier intertitre, montré en h2 et pas répété ; les suivants au sommaire.
    assert ">1.1 La méthode</h2>" in page and 'href="#c2-t1">2.1.1 Détail' in page


def test_pdf_pagine(traduction):
    r = export_document.generer_pdf(str(traduction), titre="Livre")
    assert r["chemin"].endswith("Livre_traduit_m_mise_en_page.pdf") and r["illustrations"] == 2
    doc = pymupdf.open(r["chemin"])
    assert doc.page_count == r["pages"] >= 3                     # titre + sommaire, puis un chapitre par page
    assert doc[0].rect.width == pytest.approx(595, abs=1)
    signets = [(niv, titre) for niv, titre, _p in doc.get_toc()]
    assert signets == [(1, "Sommaire"), (1, "1.1 La méthode"), (1, "2.1 Suite"), (2, "2.1.1 Détail")]
    texte = "".join(p.get_text() for p in doc)
    assert "Dernier paragraphe recherché." in texte
    assert "<script>alert(1)</script>" in texte                  # échappé : montré comme du texte, jamais interprété


def test_pied_de_page_garde_les_caracteres_hors_latin1(traduction):
    """insert_text en Helvetica rendait « ’ », « œ », « — » et « … » en « · » dans le pied (revue du 24/11/2026)."""
    titre = "L’art de la guerre — une œuvre ancienne, " + "x" * 40
    r = export_document.generer_pdf(str(traduction), titre=titre)
    pied = pymupdf.open(r["chemin"])[1].get_text()
    assert "L’art de la guerre — une œuvre" in pied and "…" in pied and "·" not in pied


def test_routes_basculent_sur_l_export_document(traduction):
    info = client.get("/api/reconstruction/info", params={"chemin_sortie": str(traduction)}).json()
    assert info["disponible"] is True and info["mode"] == "document"
    r = client.post("/api/reconstruction/html", params={"chemin_sortie": str(traduction)})
    assert r.status_code == 200 and r.json()["mode"] == "document"
    rep = client.get("/api/telecharger", params={"chemin": r.json()["chemin"]})
    assert rep.status_code == 200 and b"Sommaire" in rep.content
