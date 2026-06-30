"""
Service d'extraction de contenu PDF.
Logique pure, sans dépendance à une interface ou à Ollama — facile à tester.
"""

import pdfplumber
import pymupdf4llm


def extraire_texte(chemin_pdf: str, extracteur: str = "pymupdf4llm") -> str:
    """Extrait tout le texte d'un PDF en utilisant l'extracteur choisi."""
    if extracteur == "pymupdf4llm":
        return pymupdf4llm.to_markdown(chemin_pdf)
    if extracteur in ("marker", "llamaparse", "unstructured"):
        raise NotImplementedError(
            f"L'extracteur '{extracteur}' n'est pas encore implémenté."
        )
    raise ValueError(f"Extracteur inconnu : '{extracteur}'")


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

    # Fusionne les blocs jusqu'à taille_max, sans jamais couper un tableau
    chunks: list[str] = []
    chunk_actuel = ""

    for bloc in blocs:
        separateur = "\n\n" if chunk_actuel else ""
        candidat = chunk_actuel + separateur + bloc

        est_tableau = any(l.strip().startswith("|") for l in bloc.splitlines())

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
