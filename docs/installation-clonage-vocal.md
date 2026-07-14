# Installation du clonage vocal (moteur `openvoice`)

Guide d'installation et de configuration du moteur de clonage vocal
**OpenVoice V2 + MeloTTS**, qui alimente la carte « Voix clonées » du
Laboratoire et le moteur TTS `openvoice`.

> Le dossier réel d'installation (`backend/tts_modeles/openvoice/`) est
> **gitignoré** (poids de modèles, venv, échantillons personnels) — ce
> document tracké en est la référence reproductible. Une copie de ces
> instructions est aussi déposée dans `backend/tts_modeles/openvoice/README.md`
> lors de l'installation.
>
> **Statut : installé et validé de bout en bout le 14/7/2026** (macOS arm64).

## Pourquoi un venv Python séparé

Le venv backend principal (`backend/venv/`) tourne en **Python 3.13**.
OpenVoice V2 et MeloTTS visent Python 3.9/3.10 et ne s'installent pas en 3.13.
Le clonage tourne donc dans un **venv Python 3.10 dédié**
(`backend/tts_modeles/openvoice/venv_openvoice/`), invoqué en **sous-processus**
par le backend :
- `app/services/voix_clonage_runner.py` → `openvoice_extract.py` (extraction
  de l'embedding de locuteur à partir de l'échantillon micro) ;
- `app/services/tts.py` → `openvoice_synthesize.py` (synthèse MeloTTS FR +
  conversion de timbre vers la voix clonée).

Le backend principal ne charge **jamais** PyTorch/OpenVoice dans son propre
process — coût nul au démarrage pour les utilisateurs qui n'utilisent que
Piper/Kokoro.

## Prérequis

- **conda** (anaconda3) — Python 3.10 n'étant pas disponible en standalone
  sur la machine, on crée l'environnement avec conda.
- ~3-4 Go d'espace disque (PyTorch + dictionnaire unidic + checkpoints).
- Accès réseau (Hugging Face, GitHub, torch.hub) pour l'installation.

## Étapes d'installation

```bash
cd backend/tts_modeles/openvoice
source /opt/anaconda3/etc/profile.d/conda.sh
conda create -p ./venv_openvoice python=3.10 -y

# 1) PyTorch (roue macOS arm64 standard)
./venv_openvoice/bin/pip install torch

# 2) MeloTTS (TTS de base) — tire la plupart des dépendances
./venv_openvoice/bin/pip install git+https://github.com/myshell-ai/MeloTTS.git

# 3) OpenVoice SANS ses dépendances : ses pins (av==10, faster-whisper==0.9,
#    numpy==1.22, gradio==3.48) sont trop vieux et cassent la compilation ou
#    l'écosystème. On installe les deps réellement utiles nous-mêmes (étape 4).
./venv_openvoice/bin/pip install --no-deps git+https://github.com/myshell-ai/OpenVoice.git

# 4) Dépendances runtime d'OpenVoice absentes de MeloTTS, en versions modernes
#    (faster-whisper 1.x apporte une roue av précompilée — évite de compiler
#    PyAV/ffmpeg à la main)
./venv_openvoice/bin/pip install whisper-timestamped faster-whisper wavmark

# 5) Re-figer les versions cassées par l'étape 4 :
#    - numpy repasse en 2.x → librosa/MeloTTS cassent → repin 1.26.4
#    - setuptools 82 a retiré pkg_resources, requis par librosa 0.9.1 → <81
./venv_openvoice/bin/pip install 'numpy==1.26.4' 'setuptools<81'

# 6) Dictionnaire unidic requis par MeloTTS (chargé même en usage FR seul, ~500 Mo)
./venv_openvoice/bin/python -m unidic download

# 7) Pré-cache du modèle silero-vad (sinon torch.hub demande une confirmation
#    interactive impossible en sous-processus)
./venv_openvoice/bin/python -c "import torch; torch.hub.load('snakers4/silero-vad','silero_vad',onnx=True,trust_repo=True)"

# 8) Ressources nltk pour la synthèse ANGLAISE (g2p_en) — nltk 3.10 a renommé
#    le tagger en *_eng
./venv_openvoice/bin/python -c "import nltk; nltk.download('averaged_perceptron_tagger_eng'); nltk.download('cmudict')"
```

## Téléchargement des checkpoints

Depuis le dépôt Hugging Face `myshell-ai/OpenVoiceV2`, dans `checkpoints/`.
On télécharge le converter + les embeddings de locuteur de base des trois
langues supportées (français, anglais, espagnol) :

```bash
./venv_openvoice/bin/python - <<'PY'
from huggingface_hub import hf_hub_download
import shutil, os
dest = os.path.abspath('checkpoints')
fichiers = [
    'converter/config.json', 'converter/checkpoint.pth',
    'base_speakers/ses/fr.pth', 'base_speakers/ses/en-us.pth', 'base_speakers/ses/es.pth',
]
for f in fichiers:
    p = hf_hub_download('myshell-ai/OpenVoiceV2', f)
    t = os.path.join(dest, f); os.makedirs(os.path.dirname(t), exist_ok=True)
    shutil.copy(p, t)
PY
```

Structure finale attendue :

```
backend/tts_modeles/openvoice/
├── venv_openvoice/           # venv Python 3.10 (conda), gitignoré
├── checkpoints/
│   ├── converter/
│   │   ├── config.json
│   │   └── checkpoint.pth
│   └── base_speakers/
│       └── ses/
│           ├── fr.pth        # locuteur FR de base
│           ├── en-us.pth     # locuteur EN de base
│           └── es.pth        # locuteur ES de base
└── voix_utilisateur/         # voix clonées de l'utilisateur (registre.json + un dossier/voix)
```

## Langue de synthèse

Le timbre cloné est **indépendant de la langue** (OpenVoice V2 est
cross-lingual). C'est la **synthèse** (MeloTTS) qui porte une langue : elle
doit suivre la langue du texte lu. Mapping (dans `openvoice_synthesize.py`) :

| Langue appli | MeloTTS | Locuteur | Source SE   |
|--------------|---------|----------|-------------|
| `français`   | `FR`    | `FR`     | `fr.pth`    |
| `anglais`    | `EN`    | `EN-US`  | `en-us.pth` |
| `espagnol`   | `ES`    | `ES`     | `es.pth`    |

Côté app : dans la **Bibliothèque**, la langue suit automatiquement
`langue_cible` du document traduit ; dans le **Laboratoire** (extrait de test),
un sélecteur de langue apparaît quand une voix clonée est choisie.

Pour capturer une voix, un **texte de lecture fixe** phonétiquement riche
(« La bise et le soleil ») est affiché à l'enregistrement — un échantillon
clair et varié améliore l'extraction du timbre.

## Vérification

Une fois l'installation faite, `GET /api/tts/moteurs` détecte automatiquement
le moteur `openvoice` comme `disponible: true` (voir `_openvoice_disponible()`
dans `app/services/tts.py`). Test rapide de bout en bout, une voix `termine`
étant présente :

```bash
curl -s -X POST http://localhost:8000/api/tts/extrait \
  -H "Content-Type: application/json" \
  -d '{"texte":"Bonjour, test de ma voix clonée.","moteur":"openvoice","voix":"<NomDeLaVoix>"}' \
  -o extrait.wav && file extrait.wav   # doit être un WAVE audio valide
```

La synthèse CPU prend ~30-40s par extrait — c'est pour cela que la génération
audio d'un chapitre complet passe par la file d'attente asynchrone.

## Utilisation

Laboratoire → carte « Voix clonées » → **＋ Créer une voix** : enregistrer un
échantillon au micro (20-30s recommandées, 3s minimum), le nommer, valider.
L'extraction d'embedding tourne en tâche de fond ; la voix apparaît dans le
sélecteur TTS (Laboratoire **et** Bibliothèque) une fois `termine`.

> ⚠️ La capture micro exige un navigateur avec accès micro autorisé (ne
> fonctionne pas dans un navigateur en bac à sable).

## Pièges rencontrés (pour mémoire)

- `av==10` (pin OpenVoice) ne compile pas contre ffmpeg 7 → contourné par
  `--no-deps` + faster-whisper 1.x (roue `av==17`).
- `librosa 0.9.1` importe `pkg_resources` → `setuptools<81`.
- MeloTTS charge le module japonais au démarrage → `unidic download` requis
  même pour un usage strictement français.
- `silero-vad` via `torch.hub` demande une confirmation interactive →
  pré-cache avec `trust_repo=True`.
- Le source SE français est `base_speakers/ses/fr.pth` (et non `fr_se.pth`).
- Les versions exactes qui fonctionnent sont figées dans
  `backend/tts_modeles/openvoice/requirements.lock.txt` (généré par
  `pip freeze`, local car dans un dossier gitignoré).
