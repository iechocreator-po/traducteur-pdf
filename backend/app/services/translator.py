"""
Service de traduction via l'API Ollama locale.
Logique pure d'appel au modèle — pas de gestion d'état ni d'UI ici.
"""

import random
import re
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


# Un tag image Markdown : ![alt](chemin). Le chemin est un nom de fichier qu'on
# ne veut SURTOUT pas envoyer au modèle (gaspillage de tokens + risque qu'il
# traduise/altère le chemin). On le masque par une sentinelle avant l'appel.
_RE_TAG_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _masquer_images(texte: str) -> tuple[str, list[str]]:
    """
    Remplace chaque tag image par une sentinelle neutre `⟦IMGk⟧`. Ainsi Ollama ne
    reçoit jamais le chemin de l'image (ni l'alt) : il ne peut ni le traduire ni
    l'altérer. Retourne (texte_masqué, liste des tags dans l'ordre d'apparition).
    """
    tags: list[str] = []

    def _sub(m: re.Match) -> str:
        tags.append(m.group(0))
        return f"⟦IMG{len(tags) - 1}⟧"

    return _RE_TAG_IMAGE.sub(_sub, texte), tags


def _restaurer_images(traduit: str, tags: list[str]) -> str:
    """
    Remet chaque tag image d'origine à la place de sa sentinelle. Filet de
    sécurité : si le modèle a malgré tout perdu/altéré une sentinelle, le tag
    correspondant est réinséré en fin de texte — on ne perd JAMAIS une image.
    """
    for k, tag in enumerate(tags):
        sentinelle = f"⟦IMG{k}⟧"
        if sentinelle in traduit:
            traduit = traduit.replace(sentinelle, tag)
        else:
            traduit = traduit.rstrip() + "\n\n" + tag
    return traduit


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

    Les tags images `![](...)` sont masqués par une sentinelle avant l'appel (le
    chemin n'est jamais envoyé au modèle) puis restaurés à l'identique.
    """
    texte_a_traduire, tags_images = _masquer_images(texte)

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
    if tags_images:
        system += (
            "\nMARQUEURS IMAGES : recopie EXACTEMENT tout marqueur de la forme "
            "⟦IMG0⟧, ⟦IMG1⟧, … — ne les traduis pas, ne les déplace pas, ne les supprime pas."
        )

    data = appeler_ollama(
        {
            "model": modele,
            "system": system,
            "prompt": texte_a_traduire,
            "stream": False,
            "options": {"temperature": 0.3, "num_ctx": OLLAMA_NUM_CTX},
        },
        interruption=interruption,
    )
    return _restaurer_images(data.get("response", "").strip(), tags_images)


def lister_modeles_disponibles() -> list[str]:
    """Récupère la liste des modèles installés localement via l'API Ollama."""
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=5)
        r.raise_for_status()
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def verifier_ollama_pret(modele: str, timeout: int = 60) -> tuple[bool, str]:
    """
    Preflight : Ollama peut-il RÉELLEMENT traduire maintenant ?

    Au-delà d'un simple ping /api/tags, envoie une VRAIE mini-traduction avec les
    params exacts d'un job (num_ctx compris). Un llama-server figé (modèle chargé
    mais bloqué, ~3 % CPU) ne répond pas à ceci alors que /api/tags répondrait
    encore. Appeler ce preflight AVANT de lancer un job évite de figer 10 min sur
    un Ollama en vrac : on échoue proprement (503) avec une consigne claire.

    Ce premier appel réchauffe aussi le modèle (chargement en mémoire) — d'où le
    timeout large qui couvre un démarrage à froid.

    Retour : (pret, message). `message` décrit la cause quand pas prêt.
    """
    payload = {
        "model": modele,
        "system": "Traduis de anglais vers français. Réponds uniquement la traduction.",
        "prompt": "The cat sleeps.",
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": OLLAMA_NUM_CTX},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        if r.json().get("response", "").strip():
            return True, "ok"
        return False, "Ollama a répondu vide au test de traduction — état anormal."
    except requests.Timeout:
        return False, (
            f"Ollama n'a pas répondu en {timeout}s au test de traduction "
            "(llama-server probablement figé). Redémarrer Ollama, puis réessayer : "
            "killall ollama ; open -a Ollama"
        )
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", "?")
        return False, f"Ollama a refusé le test (HTTP {code}) — modèle « {modele} » installé ?"
    except requests.RequestException as e:
        return False, f"Ollama injoignable ({e}). Est-il lancé ? (open -a Ollama)"
