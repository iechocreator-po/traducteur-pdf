"""
Service d'extraction de contenu PDF.
Logique pure, sans dépendance à une interface ou à Ollama — facile à tester.
"""

import glob as _glob
import os
import re
import shutil
import subprocess

import pdfplumber
import pymupdf4llm


def extraire_texte(chemin_pdf: str, extracteur: str = "pymupdf4llm") -> str:
    """Extrait tout le texte d'un PDF en utilisant l'extracteur choisi."""
    if extracteur == "pymupdf4llm":
        return pymupdf4llm.to_markdown(chemin_pdf)
    if extracteur == "marker":
        return _extraire_avec_marker(chemin_pdf)
    if extracteur == "tesseract":
        return _extraire_avec_tesseract(chemin_pdf)
    if extracteur in ("llamaparse", "unstructured"):
        raise NotImplementedError(
            f"L'extracteur '{extracteur}' n'est pas encore implémenté."
        )
    raise ValueError(f"Extracteur inconnu : '{extracteur}'")


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
    """Convertit un PDF en Markdown avec la librairie Marker."""
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


def decouper_en_chunks(texte: str, taille_max: int = 3000) -> list[str]:
    """
    Découpe le Markdown en chunks en respectant les frontières structurelles :
    - Ne coupe jamais à l'intérieur d'un bloc de code (```) ou d'un tableau (|)
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

        est_tableau = any(ligne.strip().startswith("|") for ligne in bloc.splitlines())

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
    return extraire_texte(chemin, extracteur)


def relier_toc_a_markdown(toc: list[dict], chapitres_md: list[dict]) -> list[dict]:
    """
    Relie les entrées de la TOC PDF (signets) aux chapitres Markdown.
    Pour chaque signet, cherche le chapitre Markdown dont le titre correspond
    (correspondance partielle insensible à la casse).
    Les signets sans correspondance Markdown conservent un contenu vide.
    """
    def normaliser(titre: str) -> str:
        return re.sub(r"[^\w\s]", "", titre).lower().strip()

    chapitres_relies = []
    for entree in toc:
        titre_toc = normaliser(entree["titre"])
        meilleur = None
        for chap_md in chapitres_md:
            titre_md = normaliser(chap_md["titre"])
            # Correspondance si le titre du signet est contenu dans le titre Markdown ou vice-versa
            if titre_toc in titre_md or titre_md in titre_toc:
                meilleur = chap_md
                break
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
