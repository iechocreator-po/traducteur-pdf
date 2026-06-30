"""
Service de traduction via l'API Ollama locale.
Logique pure d'appel au modèle — pas de gestion d'état ni d'UI ici.
"""

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


def traduire_texte(texte: str, modele: str, langue_source: str, langue_cible: str) -> str:
    """Envoie un texte à Ollama et retourne la traduction."""
    prompt = (
        f"Traduis le texte suivant de {langue_source} vers {langue_cible}. "
        f"Traduis INTÉGRALEMENT, mot à mot, sans rien résumer, sans rien omettre. "
        f"RÈGLES ABSOLUES pour le formatage Markdown : "
        f"— Conserve EXACTEMENT tous les symboles Markdown : #, ##, ###, **, *, _, `, ```, |, >, -, [ ], ( ) "
        f"— Ne traduis PAS les URLs, les noms de fichiers, ni le code dans les blocs ``` "
        f"— Les cellules de tableaux (séparées par |) doivent rester alignées "
        f"— INTERDIT : ne commence PAS par 'Voici la traduction', 'Bien sûr', ou toute phrase d'introduction. "
        f"Réponds UNIQUEMENT avec le texte traduit, rien avant, rien après.\n\n"
        f"{texte}"
    )

    reponse = requests.post(
        OLLAMA_URL,
        json={
            "model": modele,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3},
        },
        timeout=300,
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
