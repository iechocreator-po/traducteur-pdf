"""
Service de traduction via l'API Ollama locale.
Logique pure d'appel au modèle — pas de gestion d'état ni d'UI ici.
"""

import requests

from app.config.settings import OLLAMA_TIMEOUT

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


def traduire_texte(texte: str, modele: str, langue_source: str, langue_cible: str) -> str:
    """Envoie un texte à Ollama et retourne la traduction."""
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
    data = reponse.json()
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
