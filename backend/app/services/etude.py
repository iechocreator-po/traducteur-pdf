"""
Service de génération de fiche d'étude via l'API Ollama locale.
Logique pure d'appel au modèle — pas de gestion d'état ni d'UI ici.

Chaque fonction demande une sortie JSON (format=json d'Ollama), la valide
avec Pydantic et retente une fois si le modèle a produit un JSON invalide
ou incomplet (fréquent avec les petits modèles locaux).
"""

import json

import requests
from pydantic import BaseModel, Field, ValidationError

from app.config.settings import OLLAMA_TIMEOUT
from app.models.schemas import QuestionEtude
from app.services.translator import OLLAMA_URL


class ReponseJsonInvalide(Exception):
    """Levée quand le modèle n'a pas produit le JSON attendu après relance."""


class _ReponsePoints(BaseModel):
    points: list[str] = Field(min_length=1)


class _ReponseQuestions(BaseModel):
    questions: list[QuestionEtude] = Field(min_length=1)


def _appeler_ollama_json(modele: str, system: str, prompt: str) -> str:
    reponse = requests.post(
        OLLAMA_URL,
        json={
            "model": modele,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.3},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    reponse.raise_for_status()
    return reponse.json().get("response", "").strip()


def _generer_valide(modele: str, system: str, prompt: str, schema: type[BaseModel]) -> BaseModel:
    """Appelle Ollama et valide la réponse ; une seconde tentative si JSON invalide."""
    derniere_erreur: Exception | None = None
    for _ in range(2):
        brut = _appeler_ollama_json(modele, system, prompt)
        try:
            return schema.model_validate(json.loads(brut))
        except (json.JSONDecodeError, ValidationError) as e:
            derniere_erreur = e
    raise ReponseJsonInvalide(f"Réponse JSON invalide du modèle après 2 tentatives : {derniere_erreur}")


def generer_points(texte: str, modele: str, langue: str, nb_points: int) -> list[str]:
    """Génère les points à retenir d'un chapitre (liste de phrases complètes)."""
    system = (
        f"Tu es un assistant d'étude rigoureux. Tu réponds UNIQUEMENT en JSON valide, "
        f"en {langue}, sans texte hors du JSON.\n"
        f'Format exact attendu : {{"points": ["…", "…"]}}\n'
        f"RÈGLES :\n"
        f"1. Extrais exactement {nb_points} points essentiels à retenir du texte fourni.\n"
        f"2. Chaque point est une phrase complète, factuelle, fidèle au texte (aucune invention).\n"
        f"3. Classe les points du plus important au moins important.\n"
        f"4. N'utilise que le contenu du texte fourni."
    )
    resultat = _generer_valide(modele, system, texte, _ReponsePoints)
    return resultat.points[:nb_points]


def generer_questions(texte: str, modele: str, langue: str, nb_questions: int) -> list[QuestionEtude]:
    """Génère des questions de compréhension avec leur réponse attendue (corrigé)."""
    system = (
        f"Tu es un assistant d'étude rigoureux. Tu réponds UNIQUEMENT en JSON valide, "
        f"en {langue}, sans texte hors du JSON.\n"
        f'Format exact attendu : {{"questions": [{{"question": "…", "reponse": "…"}}]}}\n'
        f"RÈGLES :\n"
        f"1. Rédige exactement {nb_questions} questions de compréhension sur le texte fourni.\n"
        f"2. Les questions doivent demander de comprendre ou d'expliquer, pas seulement de mémoriser.\n"
        f"3. Chaque réponse est complète, exacte et appuyée uniquement sur le texte fourni.\n"
        f"4. Une question doit pouvoir être répondue sans relire le texte en entier."
    )
    resultat = _generer_valide(modele, system, texte, _ReponseQuestions)
    return resultat.questions[:nb_questions]


def condenser_texte(texte: str, modele: str, langue: str) -> str:
    """
    Condense un morceau de chapitre trop long en notes détaillées.
    Utilisé avant generer_points/generer_questions quand le chapitre dépasse
    le contexte raisonnable du modèle. Sortie en texte libre (pas de JSON).
    """
    system = (
        f"Tu es un assistant d'étude. Rédige en {langue} des notes détaillées et fidèles "
        f"du texte fourni : idées principales, faits, définitions, exemples marquants. "
        f"N'invente rien, n'ajoute aucun commentaire. Réponds directement par les notes."
    )
    reponse = requests.post(
        OLLAMA_URL,
        json={
            "model": modele,
            "system": system,
            "prompt": texte,
            "stream": False,
            "options": {"temperature": 0.3},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    reponse.raise_for_status()
    return reponse.json().get("response", "").strip()
