"""
Schémas de données (Pydantic) utilisés par l'API et les services.
Centraliser ici évite de dupliquer les définitions entre les routes,
les services et les agents.
"""

from enum import Enum

from pydantic import BaseModel, Field


class Langue(str, Enum):
    ANGLAIS = "anglais"
    FRANCAIS = "français"
    ESPAGNOL = "espagnol"


class StatutJob(str, Enum):
    EN_ATTENTE = "en_attente"
    EN_COURS = "en_cours"
    EN_PAUSE = "en_pause"
    TERMINE = "termine"
    ERREUR = "erreur"


class DemandeTraduction(BaseModel):
    """Requête envoyée par le frontend pour démarrer une traduction."""

    chemin_pdf: str = Field(..., description="Chemin absolu vers le fichier PDF source")
    langue_source: Langue = Langue.ANGLAIS
    langue_cible: Langue = Langue.FRANCAIS
    modele_ollama: str = Field(default="llama3.1", description="Nom du modèle Ollama à utiliser")


class SectionTraduite(BaseModel):
    """Une section traduite, avec ses métadonnées."""

    index: int
    texte_source: str
    texte_traduit: str
    urls_trouvees: list[str] = Field(default_factory=list)


class EtatJob(BaseModel):
    """État persistant d'un job de traduction, pour permettre la pause/reprise."""

    job_id: str
    chemin_pdf: str
    chemin_sortie: str
    langue_source: Langue
    langue_cible: Langue
    modele_ollama: str
    statut: StatutJob
    derniere_section_completee: int = 0
    total_sections: int = 0


class ResultatAnalyse(BaseModel):
    """Résultat de l'analyse préliminaire d'un PDF (avant traduction complète)."""

    nb_pages_analysees: int
    texte_extractible: bool
    langue_detectee: str | None = None
    avertissements: list[str] = Field(default_factory=list)
    recommandation: str
