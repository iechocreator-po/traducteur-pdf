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
    Découpe le texte en morceaux d'une taille raisonnable, en essayant de
    couper sur des paragraphes plutôt qu'en plein milieu d'une phrase.
    """
    paragraphes = texte.split("\n\n")
    chunks: list[str] = []
    chunk_actuel = ""

    for paragraphe in paragraphes:
        if len(chunk_actuel) + len(paragraphe) > taille_max and chunk_actuel:
            chunks.append(chunk_actuel.strip())
            chunk_actuel = paragraphe
        else:
            chunk_actuel += "\n\n" + paragraphe if chunk_actuel else paragraphe

    if chunk_actuel.strip():
        chunks.append(chunk_actuel.strip())

    return chunks


def compter_pages(chemin_pdf: str) -> int:
    """Retourne le nombre total de pages du PDF."""
    with pdfplumber.open(chemin_pdf) as pdf:
        return len(pdf.pages)
