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

# ── Retry des appels Ollama ───────────────────────────────────────────────────
# Une panne d'Ollama (arrêt, redémarrage, Mac réveillé après une mise en veille)
# ne doit jamais faire perdre des sections : on réessaie avec un backoff
# exponentiel, puis on abandonne PROPREMENT le job (statut erreur) plutôt que de
# brûler les sections restantes avec des placeholders.
#
# Le budget est en horloge MURALE, pas en nombre de tentatives : une
# ConnectionError échoue en 1 ms (~30 tentatives dans le budget) alors qu'un
# Timeout coûte OLLAMA_TIMEOUT secondes (~3 tentatives). Seul le temps a du sens.
#
# 30 min suffisent même pour une veille de 8 h : quand le Mac dort, le process
# Python dort aussi (time.sleep ne s'écoule pas). Le budget ne couvre pas la
# veille, il couvre les minutes ÉVEILLÉES où Ollama n'est pas encore revenu.
OLLAMA_RETRY_DELAI_INITIAL = 2.0
OLLAMA_RETRY_FACTEUR = 2.0
OLLAMA_RETRY_DELAI_MAX = 60.0
OLLAMA_RETRY_BUDGET_SECONDES = 1800
OLLAMA_RETRY_JITTER = 0.2

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

# ── Upload de documents ───────────────────────────────────────────────────────

# Taille max (octets) d'un fichier uploadé via POST /api/upload. Un PDF scanné
# peut être volumineux, mais on borne pour éviter qu'un client ne remplisse le
# disque. Le contrôle se fait au fil de l'écriture (pas de lecture en RAM).
TAILLE_MAX_UPLOAD_OCTETS = 200 * 1024 * 1024

# Nombre de jours après lequel un dossier d'upload ABANDONNÉ (aucune traduction
# produite, non référencé en Bibliothèque) est purgé au démarrage du backend.
UPLOADS_RETENTION_JOURS = 30
