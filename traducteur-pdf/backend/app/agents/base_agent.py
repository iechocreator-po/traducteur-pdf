"""
Classe de base pour les agents IA du projet.

Un agent (par opposition à un service) utilise un LLM pour *décider* ou
*juger*, plutôt que d'exécuter une logique purement déterministe.

Ce module centralise la configuration commune (modèle par défaut, gestion
d'erreurs uniforme) afin que chaque nouvel agent n'ait pas à la redéfinir.

Exemple d'utilisation dans un agent concret (voir analysis_agent.py) :

    from pydantic_ai import Agent
    from app.agents.base_agent import MODELE_PAR_DEFAUT

    mon_agent = Agent(MODELE_PAR_DEFAUT, system_prompt="...", output_type=MonSchema)
"""

from dataclasses import dataclass

# Modèle Ollama utilisé par défaut par tous les agents.
# Format attendu par pydantic-ai : "ollama:<nom_du_modele>"
MODELE_PAR_DEFAUT = "ollama:llama3.1"


@dataclass
class ResultatAgent:
    """
    Enveloppe générique autour du résultat d'un agent, utile quand on veut
    uniformiser la gestion d'erreurs entre plusieurs agents différents dans
    les routes de l'API.
    """

    succes: bool
    donnees: object | None = None
    erreur: str | None = None

    @classmethod
    def ok(cls, donnees: object) -> "ResultatAgent":
        return cls(succes=True, donnees=donnees)

    @classmethod
    def echec(cls, erreur: str) -> "ResultatAgent":
        return cls(succes=False, erreur=erreur)


def executer_agent_en_securite(fonction_agent, *args, **kwargs) -> ResultatAgent:
    """
    Exécute un appel d'agent en capturant les erreurs courantes (Ollama
    inaccessible, modèle absent, réponse mal formée) pour éviter qu'une
    route API ne plante avec une exception non gérée.
    """
    try:
        resultat = fonction_agent(*args, **kwargs)
        return ResultatAgent.ok(resultat)
    except Exception as e:  # noqa: BLE001 — on veut capturer large ici par design
        return ResultatAgent.echec(str(e))
