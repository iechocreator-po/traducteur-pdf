"""
Service de traduction via l'API Ollama locale.
Logique pure d'appel au modèle — pas de gestion d'état ni d'UI ici.
"""

import random
import time
from typing import Callable

import requests

from app.config.settings import (
    OLLAMA_TIMEOUT,
    OLLAMA_NUM_CTX,
    OLLAMA_RETRY_DELAI_INITIAL,
    OLLAMA_RETRY_FACTEUR,
    OLLAMA_RETRY_DELAI_MAX,
    OLLAMA_RETRY_BUDGET_SECONDES,
    OLLAMA_RETRY_JITTER,
)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


class OllamaIndisponible(Exception):
    """
    Ollama est injoignable et le budget de retry est épuisé.
    FATALE pour le job : l'appelant doit arrêter proprement et laisser la reprise
    repartir de la section perdue, surtout pas continuer avec des placeholders.
    """


class OllamaErreurApplicative(Exception):
    """
    Ollama répond mais refuse la requête (4xx : modèle inconnu, requête invalide).
    Attendre n'y changera rien → aucun retry, et l'erreur reste locale à la section.
    """


class AppelInterrompu(Exception):
    """L'appelant a demandé pause/annulation pendant l'attente entre deux tentatives."""


def _attendre(delai: float, interruption: Callable[[], bool] | None) -> None:
    """
    Dort `delai` secondes en surveillant l'interruption. Le sommeil est découpé
    car pause et annulation ne sont testées qu'entre les chunks par l'appelant :
    sans ça, un clic sur Pause resterait invisible pendant tout le backoff.
    """
    echeance = time.monotonic() + delai
    while time.monotonic() < echeance:
        if interruption and interruption():
            raise AppelInterrompu
        time.sleep(min(0.5, echeance - time.monotonic()))


def appeler_ollama(
    payload: dict,
    timeout: int = OLLAMA_TIMEOUT,
    interruption: Callable[[], bool] | None = None,
) -> dict:
    """
    POST vers Ollama avec retry exponentiel sur les pannes réseau.

    Distingue deux familles d'erreurs :
    - réseau / 5xx (Ollama absent, tué en plein vol, en train de charger un
      modèle) → réessayable : c'est transitoire par nature ;
    - 4xx → définitive : la requête elle-même est invalide.
    """
    delai = OLLAMA_RETRY_DELAI_INITIAL
    debut = time.monotonic()
    derniere: Exception | None = None

    while True:
        if interruption and interruption():
            raise AppelInterrompu
        try:
            reponse = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
            reponse.raise_for_status()
            return reponse.json()
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code is not None and 400 <= code < 500:
                raise OllamaErreurApplicative(
                    f"Ollama a refusé la requête (HTTP {code})"
                ) from e
            derniere = e  # 5xx : Ollama est vivant mais en vrac → réessayable
        except (requests.RequestException, ValueError) as e:
            # ValueError couvre le JSONDecodeError d'une réponse tronquée.
            derniere = e

        ecoule = time.monotonic() - debut
        attente = delai * (1 + random.uniform(-OLLAMA_RETRY_JITTER, OLLAMA_RETRY_JITTER))
        # Budget vérifié AVANT de dormir, contre l'attente RÉELLE (jitter inclus) :
        # ainsi le temps total passé en attente ne dépasse jamais le budget.
        if ecoule + attente > OLLAMA_RETRY_BUDGET_SECONDES:
            raise OllamaIndisponible(
                f"Ollama injoignable depuis {ecoule:.0f}s (budget "
                f"{OLLAMA_RETRY_BUDGET_SECONDES}s épuisé) — dernière erreur : {derniere}"
            )
        _attendre(attente, interruption)
        delai = min(delai * OLLAMA_RETRY_FACTEUR, OLLAMA_RETRY_DELAI_MAX)


def traduire_texte(
    texte: str,
    modele: str,
    langue_source: str,
    langue_cible: str,
    termes_a_conserver: list[str] | None = None,
    interruption: Callable[[], bool] | None = None,
) -> str:
    """
    Envoie un texte à Ollama et retourne la traduction.
    termes_a_conserver : termes du glossaire à recopier tels quels (jamais traduits).
    interruption : callback consulté pendant le backoff (pause/annulation).
    """
    system = (
        f"You are a professional literal translator. Traduis de {langue_source} vers {langue_cible}.\n"
        f"RÈGLES STRICTES :\n"
        f"1. Traduis INTÉGRALEMENT chaque mot, sans rien résumer, omettre, ni paraphraser.\n"
        f"2. N'inclus JAMAIS le texte original dans ta réponse.\n"
        f"3. N'ajoute AUCUNE phrase d'introduction ni de conclusion.\n"
        f"4. N'ajoute AUCUN résumé, commentaire ou explication.\n"
        f"5. Commence ta réponse DIRECTEMENT par le premier mot traduit.\n"
        f"6. Conserve EXACTEMENT tous les symboles Markdown : #, ##, ###, **, *, _, `, ```, |, >, -, [ ], ( ).\n"
        f"7. Ne traduis PAS les URLs, noms de fichiers, ni le code dans les blocs ```."
    )
    if termes_a_conserver:
        liste = ", ".join(f"« {t} »" for t in termes_a_conserver)
        system += (
            f"\n8. Ne traduis JAMAIS ces termes, recopie-les EXACTEMENT tels quels : {liste}."
        )

    data = appeler_ollama(
        {
            "model": modele,
            "system": system,
            "prompt": texte,
            "stream": False,
            "options": {"temperature": 0.3, "num_ctx": OLLAMA_NUM_CTX},
        },
        interruption=interruption,
    )
    return data.get("response", "").strip()


def lister_modeles_disponibles() -> list[str]:
    """Récupère la liste des modèles installés localement via l'API Ollama."""
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=5)
        r.raise_for_status()
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []
