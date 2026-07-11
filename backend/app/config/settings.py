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

# ── Fiche d'étude ─────────────────────────────────────────────────────────────

# Taille max (caractères) d'un chapitre envoyé tel quel au modèle pour générer
# points et questions. Au-delà, le chapitre est d'abord condensé en notes
# (le contexte par défaut d'Ollama est ~4096 tokens ≈ 16 000 caractères).
ETUDE_CONTEXTE_MAX = 10000

# Taille (caractères) des morceaux condensés un par un pour les longs chapitres.
ETUDE_CONDENSE_CHUNK = 8000

# ── Contrôle qualité ──────────────────────────────────────────────────────────

# Ratio longueur traduit/source en dessous duquel une traduction est suspecte
# (le modèle a probablement résumé au lieu de traduire). Une tentative de plus
# est faite, puis un avertissement est ajouté au job si le ratio reste bas.
RATIO_TRADUCTION_SUSPECT = 0.5

# Longueur minimale (caractères) du texte source pour appliquer le contrôle :
# les très petits chunks (titres, lignes isolées) peuvent légitimement raccourcir.
CONTROLE_QUALITE_LONGUEUR_MIN = 200
