"""
Paramètres techniques de l'application.
Modifie ces valeurs pour ajuster le comportement sans toucher à la logique métier.
"""

# ── Ollama ────────────────────────────────────────────────────────────────────

# Timeout (secondes) par appel au modèle Ollama.
# Augmenter si les chapitres denses timeout sur CPU.
OLLAMA_TIMEOUT = 600

# Modèle utilisé par défaut si aucun n'est spécifié par l'utilisateur.
OLLAMA_MODELE_DEFAUT = "llama3.1"

# ── Découpage du texte ────────────────────────────────────────────────────────

# Taille max (caractères) d'un chunk en mode traduction complète (tous les chunks).
CHUNK_TAILLE_MAX = 3000

# Taille max (caractères) des sous-chunks lors de la traduction par chapitres.
# Plus petit = appels Ollama plus courts = moins de risque de timeout.
CHAPITRE_SOUS_CHUNK_TAILLE_MAX = 1500
