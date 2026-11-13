"""
Audit de la zone « extraction » du 13/11/2026 : un test par constat corrigé. Aucune donnée réelle : textes,
images et dossiers synthétiques dans tmp_path (uploads isolés par conftest).
"""

import os
import random
import time

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services import pdf_extractor as P
from app.services import scan_colonnes as S
from app.services import translation_runner, uploads


# ── Découpage en morceaux ────────────────────────────────────────────────────

PARAGRAPHE = " ".join(["Lorem ipsum dolor sit amet consectetur."] * 12)
TABLEAU = "| a | b |\n|---|---|\n| 1 | 2 |"
CODE = "```python\nx = 1\n\ny = 2\n```"


def test_un_tableau_ne_garde_plus_toute_la_section_en_un_morceau():
    section = "# Titre\n\n" + "\n\n".join([PARAGRAPHE] * 30) + f"\n\n{TABLEAU}\n\n{CODE}\n\n{PARAGRAPHE}"
    morceaux = P.decouper_en_chunks(section)
    assert len(morceaux) > 3 and max(map(len, morceaux)) < 3000 + len(TABLEAU) + len(CODE)
    assert any(TABLEAU in m for m in morceaux)              # le tableau reste entier
    assert any(CODE in m for m in morceaux)                 # le code aussi, ligne vide comprise
    assert sum(m.count("Lorem") for m in morceaux) == section.count("Lorem")


# ── Chapitres ────────────────────────────────────────────────────────────────

def _toc(*titres):
    return [{"index": i, "titre": t, "niveau": 1, "page": i + 1} for i, t in enumerate(titres)]


def _relier(titres, markdown):
    return [c["contenu"] for c in P.relier_toc_a_markdown(_toc(*titres), P._extraire_chapitres(markdown))]


@pytest.mark.parametrize("titres,markdown,attendus", [
    (("Chapter 1", "Chapter 2"),
     "# Chapter 1\n\nTexte un.\n\n# Chapter 2\n\nTexte deux.\n", ["Texte un.", "Texte deux."]),
    (("Chapter 1: Origins", "Chapter 2", "Chapter 3: Ends"),
     "# Chapter One: Origins\n\nA.\n\n# CHAPTER TWO\n\nTexte B.\n\n# Chapter Three: Ends\n\nC.\n", ["A.", "Texte B.", "C."]),
    (("Graphs", "Exercises", "Trees", "Exercises"),
     "# Graphs\n\nG.\n\n# Exercises\n\nE1 a b.\n\n# Trees\n\nT.\n\n# Exercises\n\nE2 a b c.\n",
     ["G.", "E1 a b.", "T.", "E2 a b c."]),
])
def test_chaque_signet_recoit_son_texte(titres, markdown, attendus):
    contenus = _relier(titres, markdown)
    assert all(attendu in contenu for attendu, contenu in zip(attendus, contenus)), contenus


def test_l_entree_de_la_table_des_matieres_perd_contre_le_vrai_chapitre():
    long = "mot " * 200
    md = f"# Alpha Story\n\n# Beta Story\n\n# Alpha Story\n\n{long}\n\n# Beta Story\n\n{long}\n"
    assert [len(c) > 500 for c in _relier(("Alpha Story", "Beta Story"), md)] == [True, True]


def test_une_section_que_rien_ne_relie_rejoint_le_chapitre_precedent():
    contenus = _relier(("Alpha Story", "Beta Story"), "# Alpha Story\n\nA.\n\n# * * *\n\nPerdu.\n\n# Beta Story\n\nB.\n")
    assert "Perdu." in contenus[0] and "B." in contenus[1]


def test_un_diese_dans_un_bloc_de_code_n_est_pas_un_chapitre():
    md = "# Installer\n\n```bash\n# installer les deps\npip install x\n```\n\n# Suite\n\nok\n"
    chapitres = P._extraire_chapitres(md)
    assert [c["titre"] for c in chapitres] == ["Installer", "Suite"]
    assert "pip install x\n```" in chapitres[0]["contenu"]


# ── _converti : l'extracteur demandé, écrit d'un bloc ────────────────────────

def test_le_converti_de_l_extracteur_demande(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "est_active", lambda nom: False)
    pdf = tmp_path / "livre.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    (tmp_path / "livre_converti_py.md").write_text("TEXTE PYMUPDF", encoding="utf-8")
    (tmp_path / "livre_converti_te.md").write_text("TEXTE TESSERACT", encoding="utf-8")
    for extracteur, attendu in (("tesseract", "TEXTE TESSERACT"), ("pymupdf4llm", "TEXTE PYMUPDF")):
        assert P._lire_source(str(pdf), extracteur) == attendu
        assert translation_runner._lire_source_markdown(str(pdf), extracteur) == attendu
    monkeypatch.setattr(P, "extraire_texte", lambda chemin, extracteur: "EXTRAIT PAR MARKER")
    assert P._lire_source(str(pdf), "marker") == "EXTRAIT PAR MARKER"   # pas de _converti_ma : on extrait


def test_le_converti_est_ecrit_d_un_seul_bloc(tmp_path, monkeypatch):
    from app.services import persistance
    ecrits = []
    monkeypatch.setattr(persistance, "ecrire_texte_atomique", lambda chemin, texte: ecrits.append((chemin, texte)))
    monkeypatch.setattr(P, "extraire_texte", lambda chemin, extracteur: "# Corps")
    monkeypatch.setattr(P, "extraire_urls", lambda chemin: ["https://a.example"])
    chemin, _ = P.convertir_et_sauvegarder(str(tmp_path / "livre.pdf"), "pymupdf4llm")
    assert chemin.endswith("livre_converti_py.md")
    assert len(ecrits) == 1 and ecrits[0][1].startswith("<!-- extracteur : pymupdf4llm -->")
    assert "# Corps" in ecrits[0][1] and "<https://a.example>" in ecrits[0][1]


# ── Purge des imports ────────────────────────────────────────────────────────

def _vieux_dossier(nom, *fichiers):
    dossier = os.path.join(uploads.DOSSIER_UPLOADS, nom)
    os.makedirs(dossier, exist_ok=True)
    vieux = time.time() - (uploads.UPLOADS_RETENTION_JOURS + 10) * 86400
    for f in fichiers:
        chemin = os.path.join(dossier, f)
        if f.endswith("/"):
            os.makedirs(chemin, exist_ok=True)
        else:
            open(chemin, "w").close()
        os.utime(chemin, (vieux, vieux))
    return dossier


@pytest.mark.parametrize("travail", ["source_scan_colonnes_regions.json", "source_segmente.md",
                                     "source_traduit_ll_fiche_00.md", "source_images/"])
def test_la_purge_garde_tout_travail_produit_a_cote(travail):
    dossier = _vieux_dossier("travail", "source.pdf", travail)
    assert uploads.purger_uploads_anciens(references=set()) == 0
    assert os.path.isdir(dossier)


def test_la_purge_garde_un_etat_seul_et_efface_une_source_seule():
    etat = _vieux_dossier("etat", "source_traduit_ll.state.json")
    seule = _vieux_dossier("seule", "source.pdf")
    assert uploads.purger_uploads_anciens(references=set()) == 1
    assert os.path.isdir(etat) and not os.path.isdir(seule)


# ── Scan : deux colonnes, fond blanc ─────────────────────────────────────────

def _page_deux_colonnes(fond=231, largeur_col=330, ecart=40, x_debut=40, hauteur=900):
    """Page simple à DEUX colonnes de « mots » (blocs noirs de largeurs variables)."""
    alea = random.Random(3)
    largeur = 2 * x_debut + 2 * largeur_col + ecart
    img = Image.new("L", (largeur, hauteur), fond)
    d = ImageDraw.Draw(img)
    colonnes = []
    for c in range(2):
        a = x_debut + c * (largeur_col + ecart)
        colonnes.append((a, a + largeur_col))
        for y in range(30, hauteur - 40, 14):
            x = a
            while x < a + largeur_col - 30:
                w = alea.randint(10, 30)
                d.rectangle([x, y, min(x + w, a + largeur_col), y + 5], fill=20)
                x += w + alea.randint(5, 8)
    return img, colonnes


def test_une_page_simple_a_deux_colonnes_est_decoupee():
    img, colonnes = _page_deux_colonnes()
    trouvees = S.detecter_colonnes(np.array(img), 0, img.width)
    assert len(trouvees) == 2
    for (a, b), (ta, tb) in zip(colonnes, trouvees):
        assert abs(a - ta) <= 15 and abs(b - tb) <= 15


def test_une_colonne_sur_papier_blanc_n_est_pas_un_encadre():
    img, (col, _) = _page_deux_colonnes(fond=255)
    assert [t for t, _, _ in S.decouper_segments(np.array(img)[:, col[0]:col[1]])] == ["texte"]


def test_un_vrai_encadre_blanc_sur_papier_reste_un_encadre():
    img, (col, _) = _page_deux_colonnes()
    ImageDraw.Draw(img).rectangle([col[0], 300, col[1], 500], fill=255)
    segments = S.decouper_segments(np.array(img)[:, col[0]:col[1]])
    assert [t for t, _, _ in segments] == ["texte", "encadre", "texte"]
