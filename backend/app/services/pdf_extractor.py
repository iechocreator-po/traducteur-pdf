"""
Service d'extraction de contenu PDF.
Logique pure, sans dépendance à une interface ou à Ollama — facile à tester.
"""

import base64
import glob as _glob
import os
import re
import shutil
import subprocess

import pdfplumber
import pymupdf4llm

from app.config.feature_flags import est_active


def extraire_texte(chemin_pdf: str, extracteur: str = "pymupdf4llm") -> str:
    """Extrait tout le texte d'un PDF en utilisant l'extracteur choisi."""
    if extracteur == "pymupdf4llm":
        return _extraire_avec_pymupdf4llm(chemin_pdf)
    if extracteur == "marker":
        return _extraire_avec_marker(chemin_pdf)
    if extracteur == "tesseract":
        return _extraire_avec_tesseract(chemin_pdf)
    if extracteur in ("llamaparse", "unstructured"):
        raise NotImplementedError(
            f"L'extracteur '{extracteur}' n'est pas encore implémenté."
        )
    raise ValueError(f"Extracteur inconnu : '{extracteur}'")


_RE_IMAGE_BASE64 = re.compile(r"!\[([^\]]*)\]\(data:image/(\w+);base64,([A-Za-z0-9+/=]+)\)")

# pymupdf4llm essaie d'extraire le texte natif présent DANS une zone image
# (utile pour un schéma légendé) et l'entoure de ces marqueurs — déjà présent
# aujourd'hui (flag off compris, force_text=True par défaut dans la librairie),
# mais invisible tant qu'aucune UI n'affichait le Markdown brut. Visible
# seulement quand l'image elle-même n'a pas pu être capturée (zone vide au
# rendu) : sans nettoyage, ce marqueur fuit tel quel jusque dans le document
# traduit et l'export HTML.
_RE_TEXTE_IMAGE = re.compile(
    r"<!-- Start of picture text -->\n?(.*?)<!-- End of picture text -->\n?",
    re.DOTALL,
)


def _nettoyer_texte_image(m: re.Match) -> str:
    morceaux = [p.strip() for p in m.group(1).split("<br>") if p.strip()]
    return ", ".join(morceaux) + "\n\n" if morceaux else ""


def _extraire_avec_pymupdf4llm(chemin_pdf: str) -> str:
    """
    Convertit un PDF en Markdown avec pymupdf4llm. Si le flag
    "extraction_images_pdf" est actif, demande à la librairie d'EMBARQUER les
    images en base64 (embed_images=True) plutôt que de les écrire elle-même
    sur disque (write_images=True) : sa propre écriture de fichiers construit
    le chemin de sauvegarde à partir d'une version assainie (espaces/parenthèses
    retirés) du nom du PDF source, mais SAUVEGARDE sous le nom d'origine —
    un PDF au nom contenant un espace (très courant) fait donc planter
    l'extraction (bug vérifié dans pymupdf4llm/helpers/utils.py:md_path).
    On écrit donc les images nous-mêmes, sous un nom qu'on choisit et qu'on
    maîtrise entièrement (<base>_images/img-N.<ext>), référencées dans le
    Markdown par un chemin relatif court. On nettoie aussi le texte de secours
    « picture text » (voir _RE_TEXTE_IMAGE) — fonctionnalité de mode avancé,
    ce nettoyage reste scopé au flag et ne touche pas le chemin flag off.
    """
    if not est_active("extraction_images_pdf"):
        return pymupdf4llm.to_markdown(chemin_pdf)
    texte = pymupdf4llm.to_markdown(chemin_pdf, embed_images=True, image_format="png")
    texte = _ecrire_images_embarquees(texte, chemin_pdf)
    return _RE_TEXTE_IMAGE.sub(_nettoyer_texte_image, texte)


def _ecrire_images_embarquees(texte: str, chemin_pdf: str) -> str:
    """Remplace chaque image base64 embarquée par pymupdf4llm par un fichier
    écrit dans <base>_images/, référencé par un chemin relatif court."""
    base, _ = os.path.splitext(chemin_pdf)
    dossier_images = f"{base}_images"
    nom_dossier = os.path.basename(dossier_images)
    compteur = {"n": 0}

    def _remplacer(m: re.Match) -> str:
        alt, extension, donnees_b64 = m.group(1), m.group(2), m.group(3)
        os.makedirs(dossier_images, exist_ok=True)
        nom_fichier = f"img-{compteur['n']}.{extension}"
        compteur["n"] += 1
        with open(os.path.join(dossier_images, nom_fichier), "wb") as f:
            f.write(base64.b64decode(donnees_b64))
        return f"![{alt}]({nom_dossier}/{nom_fichier})"

    return _RE_IMAGE_BASE64.sub(_remplacer, texte)


_MARKER_CONVERTER = None


def _obtenir_marker_converter():
    """Charge le convertisseur Marker (et ses modèles) une seule fois, à la demande."""
    global _MARKER_CONVERTER
    if _MARKER_CONVERTER is None:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        _MARKER_CONVERTER = PdfConverter(artifact_dict=create_model_dict())
    return _MARKER_CONVERTER


def _extraire_avec_marker(chemin_pdf: str) -> str:
    """
    Convertit un PDF en Markdown avec la librairie Marker.
    Marker retourne aussi un dict d'images (3e valeur, ignorée ci-dessous) —
    contrairement à Tesseract, ce n'est pas une limite technique mais un choix
    de portée délibéré : l'extraction d'images (flag "extraction_images_pdf")
    ne couvre pour l'instant que l'extracteur pymupdf4llm.
    """
    from marker.output import text_from_rendered

    converter = _obtenir_marker_converter()
    rendered = converter(chemin_pdf)
    texte, _, _ = text_from_rendered(rendered)
    return texte


def tesseract_disponible() -> bool:
    """Vrai si le binaire tesseract est installé sur la machine."""
    return shutil.which("tesseract") is not None


def _langues_tesseract() -> str:
    """
    Croise les langues de l'app (anglais/français/espagnol) avec les modèles
    Tesseract réellement installés. Retourne une spec du type « eng+fra ».
    """
    try:
        res = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=10,
        )
        installees = set(res.stdout.strip().splitlines()[1:])
    except Exception:
        return "eng"
    souhaitees = [langue for langue in ("eng", "fra", "spa") if langue in installees]
    return "+".join(souhaitees) or "eng"


def _extraire_avec_tesseract(chemin_pdf: str, dpi: int = 300) -> str:
    """
    OCR page par page : rendu de chaque page en image (PyMuPDF) puis
    reconnaissance par le binaire tesseract. À utiliser pour les PDF sans
    couche texte exploitable (scans, exports Aperçu à police corrompue).
    """
    if not tesseract_disponible():
        raise RuntimeError(
            "Tesseract n'est pas installé. Installe-le avec : brew install tesseract"
        )
    import fitz

    langues = _langues_tesseract()
    pages_texte = []
    with fitz.open(chemin_pdf) as doc:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            res = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", langues],
                input=pix.tobytes("png"),
                capture_output=True,
                timeout=300,
            )
            if res.returncode != 0:
                erreur = res.stderr.decode("utf-8", errors="replace")[:200]
                raise RuntimeError(f"Échec OCR de la page {i + 1} : {erreur}")
            pages_texte.append(res.stdout.decode("utf-8", errors="replace").strip())
    return "\n\n".join(pages_texte)


def extraire_urls(chemin_pdf: str) -> list[str]:
    """
    Extrait les URLs présentes dans les annotations de liens du PDF
    (liens cliquables), indépendamment du texte brut.
    """
    urls = []
    with pdfplumber.open(chemin_pdf) as pdf:
        for page in pdf.pages:
            annotations = page.annots or []
            for annot in annotations:
                uri = annot.get("uri")
                if uri:
                    urls.append(uri)
    return urls


def _est_tag_image(ligne: str) -> bool:
    """Vrai si la ligne est un tag Markdown d'image ![alt](chemin), seule sur sa ligne."""
    return bool(re.match(r"^!\[.*\]\(.*\)\s*$", ligne.strip()))


def decouper_en_chunks(texte: str, taille_max: int = 3000) -> list[str]:
    """
    Découpe le Markdown en chunks en respectant les frontières structurelles :
    - Ne coupe jamais à l'intérieur d'un bloc de code (```), d'un tableau (|)
      ou d'un tag d'image (![]())
    - Préfère couper avant un titre (#) ou entre deux paragraphes
    - Si un bloc dépasse taille_max seul, il est conservé tel quel (non tronqué)
    """
    import re

    blocs: list[str] = []
    bloc_courant: list[str] = []
    dans_code = False

    for ligne in texte.splitlines():
        # Suivi des blocs de code fencés
        if ligne.strip().startswith("```"):
            dans_code = not dans_code

        est_titre = not dans_code and re.match(r"^#{1,6} ", ligne)

        if est_titre and bloc_courant:
            blocs.append("\n".join(bloc_courant))
            bloc_courant = [ligne]
        else:
            bloc_courant.append(ligne)

    if bloc_courant:
        blocs.append("\n".join(bloc_courant))

    # Sous-découpe les blocs trop gros sur les frontières de paragraphes,
    # sauf s'ils contiennent du code ou un tableau (jamais coupés).
    blocs_affines: list[str] = []
    for bloc in blocs:
        # Seuls le CODE (```) et les TABLEAUX (|) doivent rester entiers : les
        # sous-découper casserait leur structure. Un tag image, lui, occupe une
        # ligne isolée (![](...)) — le découpage par paragraphes (\n\n) ne le
        # coupe jamais en deux, donc un bloc qui en contient PEUT être sous-
        # découpé. L'inclure ici gardait tout chapitre illustré en UN SEUL
        # morceau géant (45 k chars / ~13 k tokens → contexte Ollama 32k →
        # stall mémoire). Régression corrigée le 2026-07-23. L'étape de fusion
        # ci-dessous garde quand même le tag image collé à son paragraphe voisin.
        contient_code_ou_tableau = "```" in bloc or any(
            ligne.strip().startswith("|") for ligne in bloc.splitlines()
        )
        if len(bloc) <= taille_max or contient_code_ou_tableau:
            blocs_affines.append(bloc)
            continue
        for paragraphe in bloc.split("\n\n"):
            if paragraphe.strip():
                blocs_affines.append(paragraphe)
    blocs = blocs_affines

    # Fusionne les blocs jusqu'à taille_max, sans jamais couper un tableau
    chunks: list[str] = []
    chunk_actuel = ""

    for bloc in blocs:
        separateur = "\n\n" if chunk_actuel else ""
        candidat = chunk_actuel + separateur + bloc

        est_tableau = any(
            ligne.strip().startswith("|") or _est_tag_image(ligne) for ligne in bloc.splitlines()
        )

        if chunk_actuel and len(candidat) > taille_max and not est_tableau:
            chunks.append(chunk_actuel.strip())
            chunk_actuel = bloc
        else:
            chunk_actuel = candidat

    if chunk_actuel.strip():
        chunks.append(chunk_actuel.strip())

    return chunks


def compter_pages(chemin_pdf: str) -> int:
    """Retourne le nombre total de pages du PDF."""
    with pdfplumber.open(chemin_pdf) as pdf:
        return len(pdf.pages)


def extraire_toc_pdf(chemin_pdf: str) -> list[dict] | None:
    """
    Extrait la table des matières intégrée (signets PDF) via pymupdf.
    Retourne None si le PDF n'a pas de signets ou si chemin_pdf est un .md.
    Chaque entrée : {index, titre, niveau, page}.
    """
    if not chemin_pdf.lower().endswith(".pdf"):
        return None
    try:
        import fitz
        doc = fitz.open(chemin_pdf)
        toc = doc.get_toc()
        if not toc:
            return None
        return [
            {"index": i, "titre": titre, "niveau": niveau, "page": page}
            for i, (niveau, titre, page) in enumerate(toc)
        ]
    except Exception:
        return None


def identifier_chapitres(chemin: str, extracteur: str = "pymupdf4llm") -> list[dict]:
    """
    Identifie tous les chapitres (titres # à ######) dans un PDF ou Markdown.
    Si chemin est un PDF et qu'un fichier _converti_*.md existe, l'utilise pour éviter
    une re-extraction. Retourne une liste de dicts {index, titre, niveau, contenu}.
    """
    texte = _lire_source(chemin, extracteur)
    return _extraire_chapitres(texte)


def convertir_et_sauvegarder(chemin_pdf: str, extracteur: str = "pymupdf4llm") -> tuple[str, str]:
    """
    Extrait le Markdown d'un PDF et le sauvegarde dans un _converti_*.md à côté
    de la source (en-tête `<!-- extracteur : ... -->` + annexe des liens
    cliquables du PDF). Retourne (chemin_sortie, contenu_md_brut).
    Partagée par POST /convert et la persistance automatique de _lire_source().
    """
    contenu_md = extraire_texte(chemin_pdf, extracteur)
    base, _ = os.path.splitext(chemin_pdf)
    suffixe = extracteur[:2] if extracteur else ""
    chemin_sortie = f"{base}_converti_{suffixe}.md" if suffixe else f"{base}_converti.md"
    with open(chemin_sortie, "w", encoding="utf-8") as f:
        f.write(f"<!-- extracteur : {extracteur} -->\n\n")
        f.write(contenu_md)
        # Annexe les liens cliquables du PDF (souvent perdus par l'extraction texte)
        try:
            uniques = list(dict.fromkeys(extraire_urls(chemin_pdf)))
            if uniques:
                f.write("\n\n---\n\n## Liens du document original\n\n")
                for url in uniques:
                    f.write(f"- <{url}>\n")
        except Exception:
            pass  # Non critique — la conversion reste valide sans l'annexe
    return chemin_sortie, contenu_md


def _lire_source(chemin: str, extracteur: str) -> str:
    """Lit le Markdown depuis un .md, cherche un _converti_*.md pour un PDF, ou extrait."""
    if chemin.lower().endswith(".md"):
        with open(chemin, "r", encoding="utf-8") as f:
            return f.read()
    base, _ = os.path.splitext(chemin)
    candidats = _glob.glob(f"{_glob.escape(base)}_converti*.md")
    if candidats:
        with open(candidats[0], "r", encoding="utf-8") as f:
            return f.read()
    if est_active("extraction_images_pdf"):
        # Persiste le .md (+ images) au premier appel, comme /convert le fait déjà
        # manuellement — les appels suivants retombent sur le glob ci-dessus, sans
        # jamais rappeler l'extraction PDF. Best-effort : une erreur d'écriture ne
        # doit pas empêcher la lecture, juste renoncer au cache.
        try:
            chemin_sortie, _ = convertir_et_sauvegarder(chemin, extracteur)
            with open(chemin_sortie, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    return extraire_texte(chemin, extracteur)


# Mots ignorés lors de l'appariement TOC↔Markdown : trop courants pour être
# distinctifs (un titre de signet « Chapter 6: Stages of Sight » doit matcher sur
# « stages », « sight », pas sur « chapter »). Inclut les nombres écrits, car la
# TOC dit « Chapter 6 » là où l'OCR écrit « CHAPTER SIX » (6 ≠ six).
_MOTS_VIDES_TITRE = {
    "chapter", "chapitre", "part", "partie", "the", "of", "and", "a", "an", "to",
    "in", "on", "for", "from", "le", "la", "les", "de", "des", "du", "et",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty",
}


def _mots_distinctifs(titre: str) -> set[str]:
    """Mots significatifs d'un titre (minuscules, sans ponctuation, sans mots
    vides ni nombres seuls) — servent de clé d'appariement robuste."""
    mots = re.sub(r"[^\w\s]", " ", titre).lower().split()
    return {m for m in mots if len(m) > 2 and m not in _MOTS_VIDES_TITRE and not m.isdigit()}


def relier_toc_a_markdown(toc: list[dict], chapitres_md: list[dict]) -> list[dict]:
    """
    Relie chaque entrée de la TOC PDF (signets, titres propres) au chapitre
    Markdown extrait qui porte son CONTENU.

    Robustesse (l'ancienne version prenait le 1er titre « contenant » l'autre, ce
    qui faisait matcher tous les chapitres non trouvés sur le premier titre vide
    « * * * » → contenu du Half-Title partout) :
    - appariement sur les **mots distinctifs** du titre (ignore « chapter », les
      nombres, les mots vides) ;
    - les headings Markdown sans mot distinctif (« * * * », « Notes ») sont ignorés ;
    - progression **monotone** (chaque signet matche APRÈS le précédent), et à
      égalité de recouvrement on préfère le heading au **contenu le plus long**
      (le vrai chapitre, pas son entrée dans la table des matières).
    Les signets sans correspondance conservent un contenu vide.
    """
    mds = [
        {**c, "_mots": _mots_distinctifs(c["titre"])}
        for c in chapitres_md
    ]

    chapitres_relies = []
    pos_min = 0  # index Markdown minimal autorisé (monotone)
    for entree in toc:
        mots_toc = _mots_distinctifs(entree["titre"])
        meilleur = None
        meilleur_pos = pos_min
        meilleur_cle = (0, 0)  # (recouvrement, longueur contenu)
        for j in range(pos_min, len(mds)):
            chap_md = mds[j]
            if not chap_md["_mots"]:
                continue  # heading sans mot distinctif (séparateurs, « Notes »…)
            recouvrement = len(mots_toc & chap_md["_mots"])
            if recouvrement == 0:
                continue
            cle = (recouvrement, len(chap_md["contenu"]))
            if cle > meilleur_cle:
                meilleur, meilleur_pos, meilleur_cle = chap_md, j, cle
        if meilleur is not None:
            pos_min = meilleur_pos + 1  # le prochain signet matche plus loin
        chapitres_relies.append({
            "index": entree["index"],
            "titre": entree["titre"],
            "niveau": entree["niveau"],
            "page": entree.get("page"),
            "contenu": meilleur["contenu"] if meilleur else "",
            "ligne_debut": meilleur["ligne_debut"] if meilleur else 0,
            "ligne_fin": meilleur["ligne_fin"] if meilleur else 0,
        })
    return chapitres_relies


def chapitres_avec_contenu(chemin: str, extracteur: str = "pymupdf4llm") -> list[dict]:
    """
    Retourne tous les chapitres d'un PDF ou Markdown avec leur contenu.
    Priorité aux signets PDF (mêmes index que la route /chapitres), reliés au
    Markdown par titre ; fallback sur les titres Markdown.
    """
    toc_pdf = extraire_toc_pdf(chemin) if chemin.lower().endswith(".pdf") else None
    chapitres_md = identifier_chapitres(chemin, extracteur)
    if toc_pdf:
        return relier_toc_a_markdown(toc_pdf, chapitres_md)
    return chapitres_md


def _extraire_chapitres(texte: str) -> list[dict]:
    """
    Découpe le Markdown en chapitres selon les titres # à ######.
    Le contenu d'un chapitre inclut tous ses sous-titres : il se termine
    au prochain titre de niveau égal ou supérieur (≤ niveau courant).
    ligne_debut et ligne_fin sont inclus pour détecter les relations ancêtre/descendant.
    """
    lignes = texte.splitlines()
    debuts: list[tuple[int, int, str]] = []

    for i, ligne in enumerate(lignes):
        m = re.match(r"^(#{1,6})\s+(.+)", ligne)
        if m:
            debuts.append((i, len(m.group(1)), m.group(2).strip()))

    chapitres = []
    for idx, (num_ligne, niveau, titre) in enumerate(debuts):
        # Cherche le prochain titre de même niveau ou supérieur (moins de #)
        fin = len(lignes)
        for num_ligne_suivant, niveau_suivant, _ in debuts[idx + 1:]:
            if niveau_suivant <= niveau:
                fin = num_ligne_suivant
                break
        contenu = "\n".join(lignes[num_ligne:fin])
        chapitres.append({
            "index": idx,
            "titre": titre,
            "niveau": niveau,
            "contenu": contenu,
            "ligne_debut": num_ligne,
            "ligne_fin": fin,
        })
    return chapitres
