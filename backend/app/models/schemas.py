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
    ANNULE = "annule"


class DemandeTraduction(BaseModel):
    """Requête envoyée par le frontend pour démarrer une traduction."""

    chemin_pdf: str = Field(..., description="Chemin absolu vers le fichier PDF source")
    langue_source: Langue = Langue.ANGLAIS
    langue_cible: Langue = Langue.FRANCAIS
    modele_ollama: str = Field(default="llama3.1", description="Nom du modèle Ollama à utiliser")
    extracteur_pdf: str = Field(default="pymupdf4llm", description="Librairie d'extraction PDF")


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
    total_pages: int = 0
    total_mots: int = 0
    mots_traduits: int = 0
    temps_debut: float | None = None
    temps_ecoule_secondes: float = 0.0
    estimation_temps_total_secondes: float | None = None
    erreurs: list[str] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)
    journal: list[str] = Field(default_factory=list)
    chapitres_traduits: list[int] = Field(default_factory=list)
    # Sections/chapitres dont la traduction a échoué. Un job qui en contient
    # finit en `erreur`, jamais en `termine` : une traduction trouée ne doit
    # pas s'annoncer comme un succès. Ces index pilotent aussi le rejeu à
    # cache chaud de la reprise (voir demarrer_traduction).
    sections_echouees: list[int] = Field(default_factory=list)
    chapitres_echoues: list[int] = Field(default_factory=list)


class QuestionEtude(BaseModel):
    """Une question de compréhension et sa réponse attendue (corrigé)."""

    question: str
    reponse: str


class FicheChapitre(BaseModel):
    """Fiche d'étude d'un chapitre : points à retenir et questions de compréhension."""

    index: int
    titre: str
    # Étape de traitement : en_attente → points → questions → termine (ou erreur)
    etape: str = "en_attente"
    points: list[str] = Field(default_factory=list)
    questions: list[QuestionEtude] = Field(default_factory=list)


class EtatJobEtude(BaseModel):
    """État persistant d'un job de génération de fiche d'étude (suivi + reprise)."""

    job_id: str
    chemin_source: str
    chemin_sortie: str
    modele_ollama: str
    langue_fiche: str
    nb_points: int = 5
    nb_questions: int = 3
    statut: StatutJob
    chapitres: list[FicheChapitre] = Field(default_factory=list)
    # 2 étapes par chapitre (points puis questions) — granularité de la progression
    etapes_completees: int = 0
    total_etapes: int = 0
    temps_debut: float | None = None
    temps_ecoule_secondes: float = 0.0
    estimation_temps_total_secondes: float | None = None
    erreurs: list[str] = Field(default_factory=list)
    journal: list[str] = Field(default_factory=list)


class ResultatAnalyse(BaseModel):
    """Résultat de l'analyse préliminaire d'un PDF (avant traduction complète)."""

    nb_pages_analysees: int
    texte_extractible: bool
    langue_detectee: str | None = None
    avertissements: list[str] = Field(default_factory=list)
    recommandation: str
    estimation_nb_chunks: int = 0
    estimation_temps_secondes: int = 0
    nb_chapitres: int = 0
