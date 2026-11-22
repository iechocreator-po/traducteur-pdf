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
    # Chapitres sans bloc propre dans la sortie, car leur texte est déjà dans celui d'une partie traduite qui les
    # contient (« Part 4 » contient 17, 18, 19). Faits au même titre que `chapitres_traduits` pour l'utilisateur
    # (compteur, ✓ dans « ➕ Chapitres »), mais jamais mêlés à eux : `chapitres_traduits` reste la liste exacte des
    # blocs écrits, que le store et la régénération de la sortie vérifient.
    chapitres_couverts: list[int] = Field(default_factory=list)
    # Portée d'origine du job (indices de chapitres demandés). Persistée pour
    # qu'une future session sache quoi finir lors d'une reprise. None/[] pour
    # les états d'avant l'unification du moteur (traités comme « tout le doc »).
    chapitres_selectionnes: list[int] = Field(default_factory=list)
    # Extracteur qui a produit la liste de chapitres de ce job : les index de
    # `chapitres_selectionnes` / `chapitres_traduits` n'ont de sens qu'avec LUI.
    # Une reprise ou un ajout le réutilise (piège 2). None : état antérieur.
    extracteur_pdf: str | None = None
    # Sections/chapitres dont la traduction a échoué. Un job qui en contient
    # finit en `erreur`, jamais en `termine` : une traduction trouée ne doit
    # pas s'annoncer comme un succès. Ces index pilotent aussi le rejeu à
    # cache chaud de la reprise (voir demarrer_traduction).
    # `sections_echouees` est conservé pour les .state.json d'avant l'unification
    # (le moteur unifié n'utilise plus que `chapitres_echoues`).
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
    # Tâches restantes du passage en cours : « points », « quiz » (l'un, l'autre ou les deux). Vide = rien à faire.
    # Un chapitre peut n'avoir que ses points, ou que son quiz : c'est l'utilisateur qui choisit quoi générer.
    a_faire: list[str] = Field(default_factory=list)


class EtatJobEtude(BaseModel):
    """État persistant d'un job de génération de fiche d'étude (suivi + reprise)."""

    job_id: str
    chemin_source: str
    chemin_sortie: str
    modele_ollama: str
    langue_fiche: str
    nb_points: int = 5
    nb_questions: int = 3
    # Stratégie de génération des points : « condensation » (historique) ou
    # « sections ». Portée aussi par le NOM du fichier, pour que deux fiches du
    # même document produites autrement puissent coexister et être comparées.
    # Valeur par défaut = comportement d'avant, donc les états existants se
    # rechargent sans migration.
    strategie: str = "condensation"
    statut: StatutJob
    chapitres: list[FicheChapitre] = Field(default_factory=list)
    # 2 étapes par chapitre (points puis questions) — granularité de la progression
    etapes_completees: int = 0
    total_etapes: int = 0
    temps_debut: float | None = None
    temps_ecoule_secondes: float = 0.0
    estimation_temps_total_secondes: float | None = None
    # Ce que la DERNIÈRE demande voulait générer (les chapitres déjà faits gardent ce qu'ils ont).
    avec_points: bool = True
    avec_quiz: bool = True
    # Minutage du passage en cours, pour le « reste ≈ » de la Bibliothèque : début réel du passage (pas de la mise
    # en file), durée restante estimée et instant de cette estimation (le frontend décompte depuis cet instant).
    # `estimation_base` dit d'où vient le chiffre initial (Chronique) ; None = pas assez de mesures.
    debut_session: float | None = None
    estimation_restante_secondes: float | None = None
    estimation_maj_a: float | None = None
    estimation_base: str | None = None
    # Chapitres en échec PENDANT ce passage (ceux d'un passage précédent restent marqués « erreur » dans
    # `chapitres`, mais ne doivent pas être annoncés comme un échec de celui-ci).
    echecs_passage: list[int] = Field(default_factory=list)
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
