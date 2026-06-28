"""
Agent d'analyse préliminaire d'un PDF, avant de lancer une traduction complète.
Contrairement aux services (déterministes), cet agent utilise un LLM pour
juger de la qualité du contenu extrait et émettre une recommandation.

Implémente la feature roadmap #5 : "analyser les 5 premières pages et valider
s'il y a un problème avant de lancer la traduction."
"""

from pydantic_ai import Agent

from app.agents.base_agent import MODELE_PAR_DEFAUT
from app.models.schemas import ResultatAnalyse
from app.services.pdf_extractor import compter_pages, extraire_texte

NB_PAGES_ANALYSE_DEFAUT = 5

PROMPT_SYSTEME = """\
Tu es un assistant qui analyse des extraits de documents PDF avant leur traduction.
Examine le texte fourni et détermine :
- s'il semble correctement extrait (pas de charabia, pas de caractères corrompus)
- la langue probable du texte
- s'il y a des signes que le PDF est scanné (image) plutôt que du texte natif
- toute autre anomalie qui pourrait nuire à une traduction automatique

Réponds de façon concise et structurée selon le format demandé.
"""

agent_analyse = Agent(
    MODELE_PAR_DEFAUT,
    system_prompt=PROMPT_SYSTEME,
    output_type=ResultatAnalyse,
)


def analyser_pdf(chemin_pdf: str, nb_pages: int = NB_PAGES_ANALYSE_DEFAUT) -> ResultatAnalyse:
    """
    Extrait les premières pages du PDF et utilise l'agent pour détecter
    d'éventuels problèmes avant de lancer la traduction complète.
    """
    texte_complet = extraire_texte(chemin_pdf)
    nb_pages_total = compter_pages(chemin_pdf)

    # On ne garde qu'un échantillon correspondant approximativement aux nb_pages demandées
    # (l'extraction actuelle ne distingue pas les pages dans la chaîne concaténée,
    # donc on utilise une heuristique simple sur la longueur du texte).
    texte_extractible = bool(texte_complet.strip())

    if not texte_extractible:
        return ResultatAnalyse(
            nb_pages_analysees=min(nb_pages, nb_pages_total),
            texte_extractible=False,
            avertissements=["Aucun texte n'a pu être extrait. Le PDF est probablement scanné (image)."],
            recommandation="Effectuer une OCR avant de tenter une traduction.",
        )

    resultat = agent_analyse.run_sync(
        f"Voici un extrait du document à analyser :\n\n{texte_complet[:6000]}"
    )
    analyse: ResultatAnalyse = resultat.output
    analyse.nb_pages_analysees = min(nb_pages, nb_pages_total)
    analyse.texte_extractible = True
    return analyse
