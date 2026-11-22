"""
Tests du service d'extraction PDF.
On utilise un petit PDF généré à la volée pour ne dépendre d'aucun fichier externe.
"""

import glob
import os
import re

import pytest
from reportlab.pdfgen import canvas

from app.services.pdf_extractor import decouper_en_chunks, extraire_texte


@pytest.fixture
def pdf_simple(tmp_path):
    """Crée un petit PDF de test avec du texte connu."""
    chemin = tmp_path / "test.pdf"
    c = canvas.Canvas(str(chemin))
    c.drawString(100, 750, "Hello world, this is a test document.")
    c.showPage()
    c.save()
    return str(chemin)


@pytest.fixture
def pdf_avec_image(tmp_path):
    """Crée un petit PDF de test avec une image intégrée."""
    from PIL import Image

    chemin_image = tmp_path / "source.png"
    Image.new("RGB", (200, 200), color=(255, 0, 0)).save(chemin_image)

    chemin = tmp_path / "test_image.pdf"
    c = canvas.Canvas(str(chemin))
    c.drawString(100, 750, "Texte avec une image.")
    c.drawImage(str(chemin_image), 100, 400, width=200, height=200)
    c.showPage()
    c.save()
    return str(chemin)


def test_extraire_texte_retourne_le_contenu(pdf_simple):
    texte = extraire_texte(pdf_simple)
    assert "Hello world" in texte


def test_extraire_texte_pdf_inexistant_leve_une_erreur():
    with pytest.raises(Exception):
        extraire_texte("/chemin/qui/n_existe/pas.pdf")


def test_decouper_en_chunks_respecte_la_taille_max():
    texte = "Paragraphe un.\n\n" * 500  # texte volontairement long
    chunks = decouper_en_chunks(texte, taille_max=100)
    assert len(chunks) > 1
    for chunk in chunks:
        # Un peu de marge car on ne coupe jamais en plein milieu d'un paragraphe
        assert len(chunk) < 200


def test_decouper_en_chunks_texte_court_donne_un_seul_chunk():
    texte = "Un texte court."
    chunks = decouper_en_chunks(texte, taille_max=3000)
    assert len(chunks) == 1
    assert chunks[0] == texte


# ── Extracteur Tesseract (OCR) ────────────────────────────────────────────────

def test_tesseract_indisponible_leve_une_erreur_claire(monkeypatch):
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor.shutil, "which", lambda _: None)
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="brew install tesseract"):
        pdf_extractor.extraire_texte("/fake/doc.pdf", "tesseract")


def test_langues_tesseract_croise_avec_les_modeles_installes(monkeypatch):
    from unittest.mock import MagicMock
    from app.services import pdf_extractor

    res = MagicMock()
    res.stdout = "List of available languages (3):\neng\nfra\nosd\n"
    monkeypatch.setattr(pdf_extractor.subprocess, "run", lambda *a, **k: res)
    assert pdf_extractor._langues_tesseract() == "eng+fra"


def test_langues_tesseract_retombe_sur_eng_en_cas_d_echec(monkeypatch):
    from app.services import pdf_extractor

    def echec(*a, **k):
        raise OSError("binaire introuvable")
    monkeypatch.setattr(pdf_extractor.subprocess, "run", echec)
    assert pdf_extractor._langues_tesseract() == "eng"


def test_extracteur_tesseract_liste_dans_la_config():
    from app.config.feature_flags import EXTRACTEURS_PDF
    ids = [e["id"] for e in EXTRACTEURS_PDF]
    assert "tesseract" in ids


def test_relier_toc_apparie_par_mots_distinctifs_sans_grab_du_titre_vide():
    """
    Régression du bug « Half-Title partout » : un heading Markdown sans mot
    distinctif (« * * * ») ne doit JAMAIS capter le contenu d'un signet, et
    chaque signet doit matcher son vrai chapitre par ses mots distinctifs.
    """
    from app.services.pdf_extractor import relier_toc_a_markdown

    toc = [
        {"index": 0, "titre": "Half-Title", "niveau": 1, "page": 1},
        {"index": 1, "titre": "Chapter 1: Spherical Cows", "niveau": 1, "page": 8},
        {"index": 2, "titre": "Chapter 6: Stages of Sight", "niveau": 1, "page": 120},
    ]
    # Le séparateur « * * * » (aucun mot distinctif) précède les vrais chapitres.
    chapitres_md = [
        {"index": 0, "titre": "* * *", "niveau": 6, "contenu": "SEPARATEUR", "ligne_debut": 0, "ligne_fin": 1},
        {"index": 1, "titre": "**Spherical Cows**", "niveau": 5, "contenu": "CONTENU CH1", "ligne_debut": 2, "ligne_fin": 9},
        {"index": 2, "titre": "**Stages of Sight**", "niveau": 5, "contenu": "CONTENU CH6", "ligne_debut": 10, "ligne_fin": 20},
    ]
    relies = relier_toc_a_markdown(toc, chapitres_md)
    par_index = {c["index"]: c for c in relies}
    # Le « * * * » n'a JAMAIS été attribué à un signet.
    assert "SEPARATEUR" not in {c["contenu"] for c in relies}
    # Chaque vrai chapitre a SON contenu (pas celui d'un autre).
    assert par_index[1]["contenu"] == "CONTENU CH1"
    assert par_index[2]["contenu"] == "CONTENU CH6"
    # Half-Title n'a pas de mot distinctif correspondant → contenu vide (pas de grab).
    assert par_index[0]["contenu"] == ""


# ── Extraction d'images (flag "extraction_images_pdf") ────────────────────────

def test_extraction_images_off_par_defaut_aucun_tag(pdf_avec_image, monkeypatch):
    """Flag off : comportement strictement inchangé, aucun tag image.

    On force le flag OFF explicitement plutôt que de dépendre du défaut ambiant :
    un feature_flags.json local (dev/validation) peut activer le flag et ce test
    doit rester déterministe quel que soit cet override.
    """
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: False)
    texte = extraire_texte(pdf_avec_image)
    assert "![" not in texte
    base, _ = os.path.splitext(pdf_avec_image)
    assert not os.path.isdir(f"{base}_images")


def test_extraction_images_actif_produit_un_tag_et_le_fichier(pdf_avec_image, monkeypatch):
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: True)

    texte = pdf_extractor.extraire_texte(pdf_avec_image)
    m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", texte)
    assert m, "aucun tag d'image trouvé dans le markdown"

    chemin_relatif = m.group(1)
    dossier_source = os.path.dirname(pdf_avec_image)
    assert os.path.isfile(os.path.join(dossier_source, chemin_relatif))
    # Chemin relatif court, pas le chemin absolu brut renvoyé par la lib.
    assert not os.path.isabs(chemin_relatif)


def test_nettoyage_texte_image_retire_les_marqueurs():
    """
    Régression du bug rapporté : quand pymupdf4llm ne parvient pas à capturer
    l'image elle-même, il laisse un texte de secours entouré de marqueurs
    (traduits en français par le modèle, d'où le rapport initial) — ils ne
    doivent jamais fuiter tels quels dans le document traduit/exporté.
    """
    from app.services.pdf_extractor import _RE_TEXTE_IMAGE, _nettoyer_texte_image

    brut = (
        "Texte avant.\n\n"
        "<!-- Start of picture text -->\n"
        "Leyden Jar<br>Capacitor<br>\\ ee<br>Metal coating<br>"
        "<!-- End of picture text -->\n\n"
        "Texte après."
    )
    nettoye = _RE_TEXTE_IMAGE.sub(_nettoyer_texte_image, brut)

    assert "<!-- Start of picture text -->" not in nettoye
    assert "<!-- End of picture text -->" not in nettoye
    assert "<br>" not in nettoye
    assert "Leyden Jar, Capacitor, \\ ee, Metal coating" in nettoye
    assert "Texte avant." in nettoye and "Texte après." in nettoye


def test_nettoyage_texte_image_bloc_vide_ne_laisse_rien():
    from app.services.pdf_extractor import _RE_TEXTE_IMAGE, _nettoyer_texte_image
    brut = "Avant.\n\n<!-- Start of picture text -->\n<!-- End of picture text -->\n\nAprès."
    nettoye = _RE_TEXTE_IMAGE.sub(_nettoyer_texte_image, brut)
    assert "picture text" not in nettoye
    assert "Avant." in nettoye and "Après." in nettoye


def test_extraction_images_actif_sans_image_ne_cree_pas_de_dossier(pdf_simple, monkeypatch):
    """Un PDF sans image, même avec le flag actif, ne crée aucun dossier _images."""
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: True)

    pdf_extractor.extraire_texte(pdf_simple)
    base, _ = os.path.splitext(pdf_simple)
    assert not os.path.isdir(f"{base}_images")


def test_decouper_en_chunks_garde_l_image_avec_du_texte_mais_scinde_par_taille():
    """
    Un tag ![]() ne doit jamais se retrouver ISOLÉ seul dans son chunk (il perdrait
    son contexte de traduction). Mais sa présence ne doit PAS empêcher le découpage
    par taille : sinon un chapitre illustré devient un unique morceau géant
    (~45 k chars → contexte Ollama 32 k → stall mémoire, régression du 2026-07-23).
    Comportement attendu : le tag reste collé à un paragraphe voisin, et le texte
    trop long est bien scindé.
    """
    tag = "![](images/fig.png)"
    para_avant = "Paragraphe avant l'image, assez long pour dépasser la taille max à lui seul."
    para_apres = "Paragraphe après l'image, également assez long pour dépasser la taille max."
    texte = f"{para_avant}\n\n{tag}\n\n{para_apres}"

    chunks = decouper_en_chunks(texte, taille_max=50)

    # Le bloc trop long EST scindé (plus de morceau géant unique).
    assert len(chunks) > 1
    # Le tag apparaît dans exactement un chunk, jamais seul (accompagné de texte).
    chunks_avec_tag = [c for c in chunks if tag in c]
    assert len(chunks_avec_tag) == 1
    assert chunks_avec_tag[0].replace(tag, "").strip(), "le tag image ne doit pas être isolé seul"


def test_decouper_en_chunks_reconnait_le_tag_image():
    from app.services.pdf_extractor import _est_tag_image
    assert _est_tag_image("![](images/x.png)")
    assert _est_tag_image("  ![alt text](chemin/vers/img.png)  ")
    assert not _est_tag_image("Du texte normal.")
    assert not _est_tag_image("[un lien](https://example.com)")


# ── Persistance automatique (une seule extraction PDF) ─────────────────────────

def _compter_appels_extraction(monkeypatch):
    from app.services import pdf_extractor
    compteur = {"n": 0}
    original = pdf_extractor.pymupdf4llm.to_markdown

    def to_markdown_compte(*args, **kwargs):
        compteur["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pdf_extractor.pymupdf4llm, "to_markdown", to_markdown_compte)
    return compteur


def test_flag_actif_extraction_pdf_une_seule_fois(pdf_simple, monkeypatch):
    """
    Avec le flag actif, le premier appel persiste le _converti_*.md ; les
    appels suivants le relisent au lieu de rappeler pymupdf4llm.to_markdown().
    """
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: True)
    compteur = _compter_appels_extraction(monkeypatch)

    pdf_extractor.identifier_chapitres(pdf_simple)
    pdf_extractor.identifier_chapitres(pdf_simple)
    pdf_extractor.identifier_chapitres(pdf_simple)

    assert compteur["n"] == 1
    base, _ = os.path.splitext(pdf_simple)
    assert glob.glob(f"{base}_converti*.md")


def test_flag_inactif_extrait_a_chaque_appel(pdf_simple, monkeypatch):
    """Flag off : comportement inchangé, aucune persistance automatique.

    Flag forcé OFF explicitement (indépendant d'un feature_flags.json local).
    """
    from app.services import pdf_extractor
    monkeypatch.setattr(pdf_extractor, "est_active", lambda nom: False)
    compteur = _compter_appels_extraction(monkeypatch)

    pdf_extractor.identifier_chapitres(pdf_simple)
    pdf_extractor.identifier_chapitres(pdf_simple)

    assert compteur["n"] == 2
    base, _ = os.path.splitext(pdf_simple)
    assert not glob.glob(f"{base}_converti*.md")


def test_relier_toc_progression_monotone():
    """Deux signets qui partagent un mot (« neural ») ne doivent pas matcher le
    même heading : la progression est monotone (chacun matche après le précédent)."""
    from app.services.pdf_extractor import relier_toc_a_markdown

    toc = [
        {"index": 0, "titre": "Chapter 3: Neural Learning", "niveau": 1, "page": 40},
        {"index": 1, "titre": "Chapter 7: Neural Code", "niveau": 1, "page": 130},
    ]
    chapitres_md = [
        {"index": 0, "titre": "**Neural Learning**", "niveau": 5, "contenu": "C3", "ligne_debut": 0, "ligne_fin": 5},
        {"index": 1, "titre": "**Neural Code**", "niveau": 5, "contenu": "C7", "ligne_debut": 6, "ligne_fin": 10},
    ]
    relies = relier_toc_a_markdown(toc, chapitres_md)
    assert relies[0]["contenu"] == "C3"
    assert relies[1]["contenu"] == "C7"  # pas « C3 » à nouveau


# ── titre_traduit (feature bilbao 348) ───────────────────────────────────────
# Le corps d'un chapitre traduit commence, par construction, par son titre —
# déjà traduit puisque tout le corps est passé chez Ollama. On l'expose sans
# appel LLM supplémentaire. Ces tests couvrent le cas normal ET les anomalies
# qu'un arrêt impromptu (écriture non atomique interrompue) peut produire.

def _texte_marque(titre_source: str, corps_traduit: str, index: int = 0) -> str:
    return f"<!-- === chapitre {index} : {titre_source} === -->\n\n{corps_traduit}\n"


def test_chapitres_depuis_marqueurs_expose_le_titre_traduit_du_corps():
    from app.services.pdf_extractor import chapitres_depuis_marqueurs

    texte = _texte_marque("Chapter 9: From Structure to Function", "# Chapitre 9 : de la structure à la fonction\n\nLe reste du texte traduit.")
    chapitres = chapitres_depuis_marqueurs(texte)

    assert len(chapitres) == 1
    # Le marqueur garde le titre SOURCE (alignement feature 297) — inchangé.
    assert chapitres[0]["titre"] == "Chapter 9: From Structure to Function"
    # Le titre traduit vient du corps, pas du marqueur.
    assert chapitres[0]["titre_traduit"] == "Chapitre 9 : de la structure à la fonction"


def test_chapitres_depuis_marqueurs_titre_traduit_absent_si_le_corps_ne_commence_pas_par_un_titre():
    from app.services.pdf_extractor import chapitres_depuis_marqueurs

    # Chapitre implicite ou traduction qui a perdu la mise en forme du titre :
    # la première ligne non vide n'est pas un « # ».
    texte = _texte_marque("Introduction", "Ceci commence directement par du texte, sans titre.")
    chapitres = chapitres_depuis_marqueurs(texte)

    assert chapitres[0]["titre"] == "Introduction"  # repli : le titre source reste exact
    assert chapitres[0]["titre_traduit"] is None     # jamais une chaîne vide ou du charabia


def test_chapitres_depuis_marqueurs_ignore_un_titre_plus_loin_dans_le_corps():
    """
    Ne reproduit PAS le bug qui a motivé le marqueur source (feature 327) :
    un « # » injecté plus loin par Ollama (ex. un séparateur mal préfixé) ne
    doit jamais être pris pour le titre — seule la première ligne compte.
    """
    from app.services.pdf_extractor import chapitres_depuis_marqueurs

    texte = _texte_marque(
        "Un",
        "Un paragraphe d'abord.\n\n# * * *\n\nPuis la suite du chapitre.",
    )
    chapitres = chapitres_depuis_marqueurs(texte)
    assert chapitres[0]["titre_traduit"] is None


def test_chapitres_depuis_marqueurs_titre_traduit_absent_sur_corps_vide_ou_tronque():
    """
    Simule ce qu'un arrêt impromptu peut laisser sur disque : l'append n'est
    PAS atomique (voir _ecrire_chapitre) — un crash en plein milieu de la
    ligne de titre est possible. Ni crash, ni titre garbled : repli propre.
    """
    from app.services.pdf_extractor import chapitres_depuis_marqueurs

    for corps_tronque in ("", "   \n\n  ", "#", "# "):
        texte = _texte_marque("Un", corps_tronque)
        chapitres = chapitres_depuis_marqueurs(texte)
        assert chapitres[0]["titre"] == "Un"
        assert chapitres[0]["titre_traduit"] is None, repr(corps_tronque)


# Le numéro de la table des matières est souvent sur une ligne à part dans le PDF : le titre traduit l'a perdu.
@pytest.mark.parametrize("source, traduit, attendu", [
    ("20 Multiprocessing and Vectorization", "Multitraitement et vectorisation", "20 Multitraitement et vectorisation"),
    ("Part 5: High-Performance Computing Recipes", "Recettes de calcul", "Part 5: Recettes de calcul"),
    ("1.2 Scope", "Portée", "1.2 Portée"),
    ("IV A Portfolio Approach", "Une approche de portefeuille", "IV Une approche de portefeuille"),
    ("Chapter 9: From Structure to Function", "Chapitre 9 : de la structure", "Chapitre 9 : de la structure"),
    ("20 Multiprocessing", "20. Multitraitement", "20. Multitraitement"),
    ("I Robot", "Moi, robot", "Moi, robot"),
    ("Preamble", "Préambule", "Préambule"),
    ("3 Labeling", None, None),
])
def test_numeroter_titre_traduit(source, traduit, attendu):
    from app.services.pdf_extractor import numeroter_titre_traduit
    assert numeroter_titre_traduit(source, traduit) == attendu


def test_chapitres_depuis_marqueurs_garde_le_numero_de_la_table_des_matieres():
    from app.services.pdf_extractor import chapitres_depuis_marqueurs
    texte = _texte_marque("20 Multiprocessing and Vectorization", "### Multitraitement et vectorisation\n\nCorps.")
    assert chapitres_depuis_marqueurs(texte)[0]["titre_traduit"] == "20 Multitraitement et vectorisation"


# Bibliothèque et « Nouveau document » montrent la même liste : titres sans emphase, intertitre écarté, niveau source.
def test_titre_traduit_sans_emphase_markdown():
    from app.services.pdf_extractor import chapitres_depuis_marqueurs
    texte = _texte_marque("Chapter 1: Spherical Cows", "### Chapter 1: **Vaches sphériques**\n\nCorps.")
    assert chapitres_depuis_marqueurs(texte)[0]["titre_traduit"] == "Chapter 1: Vaches sphériques"


def test_titre_traduit_ecarte_un_intertitre_du_corps():
    from app.services.pdf_extractor import chapitres_depuis_marqueurs
    texte = _texte_marque("Series", "### **Also available in the Bloomsbury Sigma series:**\n\nCorps.")
    assert chapitres_depuis_marqueurs(texte)[0]["titre_traduit"] is None


def test_aligner_niveaux_sur_source_si_index_et_titre_concordent():
    from app.services.pdf_extractor import aligner_niveaux_sur_source
    traduits = [{"index": 0, "titre": "Part 1", "niveau": 6}, {"index": 1, "titre": "2 Data", "niveau": 6},
                {"index": 2, "titre": "Autre", "niveau": 3}]
    source = [{"index": 0, "titre": "Part 1", "niveau": 1}, {"index": 1, "titre": "2 Data", "niveau": 2},
              {"index": 2, "titre": "Différent", "niveau": 1}]
    assert [c["niveau"] for c in aligner_niveaux_sur_source(traduits, source)] == [1, 2, 3]


# ── Sous-chapitres couverts par une partie traduite d'un bloc ────────────────
def _source_partie():
    contenu = ("# Part Four\n\nIntro.\n\n### Breaks\n\nAlpha.\n\n###### 17.1 MOTIVATION\n\nA.\n\n"
               "### Entropy\n\nBeta.\n\n### Index\n\nZ.\n")
    lignes = contenu.splitlines()
    debut_b, debut_e, debut_i = (lignes.index(t) for t in ("### Breaks", "### Entropy", "### Index"))
    return [
        {"index": 26, "titre": "Part 4: Four", "niveau": 1, "contenu": contenu,
         "ligne_debut": 0, "ligne_fin": len(lignes)},
        {"index": 27, "titre": "17 Breaks", "niveau": 2, "contenu": "\n".join(lignes[debut_b:debut_e]),
         "ligne_debut": debut_b, "ligne_fin": debut_e},
        {"index": 28, "titre": "18 Entropy", "niveau": 2, "contenu": "\n".join(lignes[debut_e:debut_i]),
         "ligne_debut": debut_e, "ligne_fin": debut_i},
    ]


def _traduit_partie(titres_niveau_3=("### Ruptures", "### Entropie", "### Index")):
    from app.services.pdf_extractor import chapitres_depuis_marqueurs
    r, e, i = titres_niveau_3
    texte = ("<!-- === chapitre 26 : Part 4: Four === -->\n\n# Partie quatre\n\nIntro.\n\n"
             f"{r}\n\nAlpha FR.\n\n###### 17.1 MOTIVATION\n\nA.\n\n# code isolé\n\n{e}\n\nBêta FR.\n\n{i}\n\nZ.\n")
    return chapitres_depuis_marqueurs(texte)


def test_deplier_chapitres_couverts_retrouve_les_sous_chapitres_dans_le_bloc():
    from app.services.pdf_extractor import deplier_chapitres_couverts
    resultat = deplier_chapitres_couverts(_traduit_partie(), _source_partie(), [27, 28])
    assert [c["index"] for c in resultat] == [26, 27, 28]
    partie, breaks, entropy = resultat
    assert breaks["titre_traduit"] == "17 Ruptures" and breaks["niveau"] == 2
    assert breaks["contenu"].startswith("### Ruptures") and "Alpha FR." in breaks["contenu"]
    assert "Bêta FR." not in breaks["contenu"]
    # Fin au titre suivant du même niveau : l'index (non couvert) n'entre pas dans le dernier sous-chapitre.
    assert "Bêta FR." in entropy["contenu"] and "Z." not in entropy["contenu"]
    # La partie garde tout son texte, et ses sous-chapitres sont imbriqués dans sa plage (export sans doublon).
    assert "Alpha FR." in partie["contenu"]
    for enfant in (breaks, entropy):
        assert partie["ligne_debut"] < enfant["ligne_debut"] < partie["ligne_fin"]


def test_deplier_chapitres_couverts_renonce_si_un_titre_manque_dans_la_traduction():
    from app.services.pdf_extractor import deplier_chapitres_couverts
    traduits = _traduit_partie(("### Ruptures", "Entropie", "### Index"))  # le modèle a perdu un « ### »
    assert [c["index"] for c in deplier_chapitres_couverts(traduits, _source_partie(), [27, 28])] == [26]


def test_deplier_chapitres_couverts_sans_couverts_ne_change_rien():
    from app.services.pdf_extractor import deplier_chapitres_couverts
    traduits = _traduit_partie()
    assert deplier_chapitres_couverts(traduits, _source_partie(), []) is traduits


def test_signet_borne_au_signet_suivant_de_meme_niveau_meme_si_le_markdown_est_plus_profond():
    # « Index » est un ### dans le Markdown mais un signet de niveau 1 : Part 5 ne doit pas l'englober.
    from app.services.pdf_extractor import _extraire_chapitres, relier_toc_a_markdown
    markdown = ("# Computing Recipes\n\nIntro.\n\n### Multiprocessing Vectorization\n\nCorps.\n\n"
                "### Index\n\nAbsolute return.\n\n## Liens du document original\n\n- http://x\n")
    toc = [{"index": 0, "titre": "Part 5: Computing Recipes", "niveau": 1, "page": 1},
           {"index": 1, "titre": "20 Multiprocessing Vectorization", "niveau": 2, "page": 2},
           {"index": 2, "titre": "Index", "niveau": 1, "page": 3}]
    part, chap, index = relier_toc_a_markdown(toc, _extraire_chapitres(markdown))
    assert "Corps." in part["contenu"] and "Absolute return." not in part["contenu"]
    assert part["ligne_fin"] == index["ligne_debut"]
    assert "Absolute return." not in chap["contenu"]
    # Rien n'est perdu : la section qui suit l'Index (ex-contenu de Part 5) rejoint l'Index.
    assert "Absolute return." in index["contenu"] and "http://x" in index["contenu"]


def test_deplier_chapitres_couverts_bloc_traduit_avant_le_bornage_des_signets():
    # Bloc traduit quand la partie englobait encore l'Index : un titre ### de plus que la source bornée.
    from app.services.pdf_extractor import deplier_chapitres_couverts
    source = _source_partie()
    partie = source[0]
    coupe = partie["contenu"].index("### Index")
    lignes_partie = partie["contenu"][:coupe].count("\n")
    source[0] = {**partie, "contenu": partie["contenu"][:coupe], "ligne_fin": lignes_partie}
    source.append({"index": 29, "titre": "Index", "niveau": 1, "contenu": "### Index\n\nZ.",
                   "ligne_debut": lignes_partie, "ligne_fin": lignes_partie + 3})
    resultat = deplier_chapitres_couverts(_traduit_partie(), source, [27, 28])
    assert [c["index"] for c in resultat] == [26, 27, 28]
    assert "Bêta FR." in resultat[2]["contenu"] and "Z." not in resultat[2]["contenu"]
