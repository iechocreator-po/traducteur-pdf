"""
Agent d'analyse préliminaire d'un PDF.
Partie déterministe : extraction texte, comptage pages, estimation chunks/durée.
Partie LLM : langue détectée + recommandation (appel direct Ollama, texte libre).
"""

import re

import requests

from app.models.schemas import ResultatAnalyse
from app.config.settings import CHAPITRE_SOUS_CHUNK_TAILLE_MAX
from app.services.pdf_extractor import compter_pages, extraire_texte, decouper_en_chunks, extraire_toc_pdf
from app.services.translation_runner import SECONDES_PAR_CHUNK_ESTIME

OLLAMA_URL = "http://localhost:11434/api/generate"
NB_PAGES_ANALYSE_DEFAUT = 5

# Au-delà de cette part de caractères illisibles, la couche texte est jugée
# corrompue (police sans table ToUnicode, ex. export Aperçu/Quartz).
SEUIL_TEXTE_CORROMPU = 0.3


def _ratio_texte_corrompu(texte: str) -> float:
    """Part de caractères illisibles (contrôle ou U+FFFD) parmi les non-blancs."""
    significatifs = [c for c in texte if not c.isspace()]
    if not significatifs:
        return 0.0
    illisibles = sum(1 for c in significatifs if c == "�" or ord(c) < 32)
    return illisibles / len(significatifs)


def _appel_llm(prompt: str, modele: str = "llama3.1") -> str:
    """Appel direct à Ollama, retourne du texte libre."""
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": modele, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1}},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"(LLM inaccessible : {e})"


def analyser_pdf(chemin_pdf: str, nb_pages: int = NB_PAGES_ANALYSE_DEFAUT) -> ResultatAnalyse:
    """
    Analyse un PDF ou document source : déterministe pour les métriques, LLM pour la langue et
    la recommandation.
    """
    texte_complet = extraire_texte(chemin_pdf)
    nb_pages_total = compter_pages(chemin_pdf)
    texte_extractible = bool(texte_complet.strip())

    # Découpe AVEC LA MÊME TAILLE que le moteur unifié (CHAPITRE_SOUS_CHUNK_TAILLE_MAX),
    # sinon on compte ~2× moins de morceaux que le nombre réellement traduit et l'ETA
    # est fausse d'autant. L'estimation par morceau reste grossière (elle est recalculée
    # depuis le débit réel dès le 1er morceau traduit) mais n'est plus systématiquement
    # sous-évaluée d'un facteur ~n.
    chunks = (
        decouper_en_chunks(texte_complet, taille_max=CHAPITRE_SOUS_CHUNK_TAILLE_MAX)
        if texte_extractible else []
    )
    nb_chunks = len(chunks)
    estimation_temps = nb_chunks * SECONDES_PAR_CHUNK_ESTIME

    if not texte_extractible:
        return ResultatAnalyse(
            nb_pages_analysees=min(nb_pages, nb_pages_total),
            texte_extractible=False,
            avertissements=["Aucun texte extrait — le PDF est probablement scanné (image)."],
            recommandation="Utiliser l'extracteur Tesseract (OCR) avant de tenter une traduction.",
            estimation_nb_chunks=0,
            estimation_temps_secondes=0,
        )

    ratio_corrompu = _ratio_texte_corrompu(texte_complet)
    if ratio_corrompu > SEUIL_TEXTE_CORROMPU:
        return ResultatAnalyse(
            nb_pages_analysees=min(nb_pages, nb_pages_total),
            texte_extractible=False,
            avertissements=[
                f"Couche texte corrompue ({ratio_corrompu:.0%} de caractères illisibles) — "
                "police sans table ToUnicode (ex. PDF ré-enregistré par Aperçu) ou scan."
            ],
            recommandation="Utiliser l'extracteur Tesseract (OCR), ou repartir du PDF original.",
            estimation_nb_chunks=0,
            estimation_temps_secondes=0,
        )

    # Extrait pour le LLM (~2000 caractères suffisent pour la langue et la qualité)
    extrait = texte_complet[:2000]

    prompt = (
        "Analyse cet extrait de document PDF.\n"
        "Réponds en 2 lignes seulement :\n"
        "Ligne 1 — LANGUE: <la langue du texte>\n"
        "Ligne 2 — RECOMMANDATION: <une phrase courte sur la qualité du texte et "
        "si la traduction automatique est conseillée>\n\n"
        f"Extrait :\n{extrait}"
    )

    reponse_llm = _appel_llm(prompt)

    # Parsing simple des deux lignes
    langue_detectee = None
    recommandation = "Texte extractible, traduction automatique possible."
    avertissements: list[str] = []

    for ligne in reponse_llm.splitlines():
        if ligne.upper().startswith("LANGUE:") or "LANGUE:" in ligne.upper():
            partie = ligne.split(":", 1)[-1].strip()
            if partie:
                langue_detectee = partie
        elif ligne.upper().startswith("RECOMMANDATION:") or "RECOMMANDATION:" in ligne.upper():
            partie = ligne.split(":", 1)[-1].strip()
            if partie:
                recommandation = partie

    if nb_chunks > 50:
        avertissements.append(
            f"Document volumineux ({nb_chunks} sections) — la traduction peut prendre du temps."
        )

    # Nb de chapitres : signets PDF si présents, sinon titres Markdown du texte extrait
    toc = extraire_toc_pdf(chemin_pdf)
    nb_chapitres = len(toc) if toc else len(re.findall(r"^#{1,6}\s+", texte_complet, re.MULTILINE))

    return ResultatAnalyse(
        nb_pages_analysees=min(nb_pages, nb_pages_total),
        texte_extractible=True,
        langue_detectee=langue_detectee,
        avertissements=avertissements,
        recommandation=recommandation,
        estimation_nb_chunks=nb_chunks,
        estimation_temps_secondes=estimation_temps,
        nb_chapitres=nb_chapitres,
    )
