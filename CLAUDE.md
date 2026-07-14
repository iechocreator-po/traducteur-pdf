# Traducteur PDF

Application locale de traduction de documents PDF, propulsée par [Ollama](https://ollama.com)
(modèles LLM open source exécutés 100% sur ta machine — aucune donnée envoyée vers le cloud).

## Architecture

```
traducteur-pdf/
├── backend/          # API FastAPI (Python) — toute la logique métier et les agents IA
│   ├── app/
│   │   ├── api/          # Routes HTTP
│   │   ├── services/      # Logique déterministe (extraction PDF, traduction, jobs)
│   │   ├── agents/        # Logique pilotée par LLM (analyse, décisions)
│   │   ├── models/        # Schémas de données (Pydantic)
│   │   └── config/        # Feature flags
│   └── tests/         # Tests automatisés (pytest)
├── frontend/         # Interface web (HTML/CSS/JS), aucune dépendance de build
└── docs/             # Documentation et roadmap des fonctionnalités
```

Le frontend ne fait **que** des appels HTTP vers l'API locale — aucune logique métier
n'existe côté interface. Ça permet de remplacer ou faire évoluer l'UI sans toucher au backend.

L'interface web suit la **refonte « Workflow »** (design retenu dans
`toledo_v2/handoff_iTraducteur/`, décisions dans `docs/refonte-workflow-decisions.md`) :
une barre supérieure (thème, mode avancé, statuts) et **3 modules** organisés par flux
de travail, chargés depuis `frontend/js/` (`commun.js` + un fichier par module) :
- **Nouveau document** (`module-import.js`) : lot multi-fichiers — ajout par chemin,
  analyse auto (qualité / durée / chapitres), réglages du lot (langues ; extracteur et
  modèle en mode avancé), lancement en lot (file séquentielle backend), planification.
- **Bibliothèque** (`module-bibliotheque.js`) : documents traduits (`GET /api/bibliotheque`,
  registre alimenté par `translation_runner`), lecture chapitre par chapitre
  (`POST /api/chapitres/contenu`), barre audio TTS (`GET /api/tts/audio`), panneau IA
  « points clés + quiz » servi par le backend Étude (`services/etude.py` +
  `services/study_runner.py`, routes `POST /api/etude`, `GET /api/etude/statut`,
  sortie `<base>_fiche_<xx>.md`, même file d'attente séquentielle que la traduction).
  La section « Résumé & Quiz » n'est visible **qu'en mode avancé** ; elle offre un
  **export HTML autonome** (bouton `#ia-exporter`, `exporterFicheHtml()`) reprenant
  infos document + structure des chapitres + résumé + quiz (réponses en `<details>`),
  généré 100 % côté client depuis `docActif`/`chapitres`/`ficheParChapitre` — gated par
  le flag `export_fiche_html`.
- **Laboratoire** (`module-laboratoire.js`) : état système, glossaire, TTS (moteur/voix/
  extrait), **voix clonées** (capture micro → clonage, voir ci-dessous), outils document
  (analyse, conversion, reprise, journal d'erreurs) et **teasers** des fonctionnalités
  futures restantes (export PDF — flag `teaser_export_pdf`, capture d'intérêt via
  `POST /api/interet`, log local). **Le module entier n'est visible qu'en mode avancé**
  (onglet + contenu masqués sinon).

### Mode avancé et feature flags

- **Mode avancé** (`appliquerModeAvance` dans `commun.js`) : bascule `.hidden` sur tous
  les `[data-avance]` et une classe `.avance` sur `<html>` (pour le reflow CSS de la
  grille Bibliothèque). Éléments gated : réglages extracteur/modèle de l'Import, onglet +
  contenu du **Laboratoire**, section **Résumé & Quiz** de la Bibliothèque. `activerModule`
  redirige vers l'Import si on tente d'ouvrir le Laboratoire hors mode avancé. Le **bouton**
  « mode avancé » lui-même est gated par le flag `mode_avance` (off → bouton masqué et
  mode forcé désactivé).
- **Feature flags** (`backend/app/config/feature_flags.py`, `GET /api/feature-flags`,
  global `featureFlags` + événement `flags-charges` côté front). Ordre de priorité
  (bas → haut) : `FLAGS_PAR_DEFAUT` → **`bilbao.features.json`** (racine du repo, artefact
  géré par la console bilbao/feature-factory, à committer) → `feature_flags.json` local
  → variables d'env `FEATURE_<NOM>`. **Contrat d'intégration Bilbao** : `charger_flags()`
  lit et fusionne la clé `flags` de `bilbao.features.json` — c'est ce qui rend tous les
  flags du produit pilotables depuis Bilbao (bilbao émet l'artefact, JP le committe).

### Clonage vocal (moteur `openvoice`)

Le TTS local a un troisième moteur, `openvoice`, à côté de Piper et Kokoro : des voix
**clonées par l'utilisateur** à partir d'un échantillon micro capturé dans le Laboratoire.

- **Capture (frontend)** : `module-laboratoire.js` capture le micro via Web Audio API
  (`AudioContext` + `ScriptProcessorNode`, PCM brut) et encode un WAV côté client
  (`encoderWav`) — **pas** `MediaRecorder` (produirait du webm/opus, incompatible avec
  la validation WAV stricte du backend). Envoi en `multipart/form-data` vers
  `POST /api/voix-clonees/capturer`, puis polling de `GET /api/voix-clonees/statut`
  jusqu'à ce que la voix soit prête (rafraîchit alors le `<select id="tts-voix">` partagé
  avec la Bibliothèque — aucune logique supplémentaire nécessaire côté lecture audio).
- **Registre** : `backend/app/services/voix_clonees.py` — CRUD sur
  `tts_modeles/openvoice/voix_utilisateur/registre.json` (nom, statut, chemins de
  l'échantillon et de l'embedding). Chaque voix a son dossier
  `voix_utilisateur/<id>/` (`echantillon.wav` + `embedding.pth`).
- **Traitement asynchrone** : `voix_clonage_runner.py`, même patron de job que
  `tts_runner.py` (file d'attente unique du `job_manager`, statut persisté). Le traitement
  réel tourne en **sous-processus**, jamais importé directement dans le process FastAPI.
- **Pourquoi un venv séparé** : le moteur de clonage est **OpenVoice V2 + MeloTTS**
  (voix de base multilingue + conversion de timbre), dont les dépendances connues
  (`numpy==1.22.0`, `librosa==0.9.1`, `faster-whisper==0.9.0`…) visent Python 3.9/3.10 —
  incompatibles avec le venv backend principal (Python 3.13). Elles tournent donc dans
  un **venv Python 3.10 dédié**, `backend/tts_modeles/openvoice/venv_openvoice/`
  (non versionné), invoqué en sous-processus par `voix_clonage_runner.py` (extraction
  d'embedding, `openvoice_extract.py`) et `tts.py` (synthèse,
  `openvoice_synthesize.py`). Procédure d'installation et de configuration
  complète (validée de bout en bout) :
  [docs/installation-clonage-vocal.md](docs/installation-clonage-vocal.md)
  (une copie est aussi déposée dans le `README.md` du dossier gitignoré
  `backend/tts_modeles/openvoice/`).
- **Détection de disponibilité** : `tts._openvoice_disponible()` suit le même patron que
  Kokoro (`disponible: false` + message d'`aide` tant que le venv dédié ou les checkpoints
  sont absents) — pas de feature flag dédié, `GET /tts/moteurs` fait foi.
- **Langue de synthèse (FR/EN/ES)** : le timbre cloné est indépendant de la langue
  (OpenVoice V2 est cross-lingual) ; c'est MeloTTS qui porte la langue. Le paramètre
  `langue` traverse `synthetiser()` → `openvoice_synthesize.py`, qui mappe vers le bon
  locuteur MeloTTS + source SE (`ses/{fr,en-us,es}.pth`). La **Bibliothèque** passe
  automatiquement `langue_cible` du document ; le **Laboratoire** affiche un sélecteur de
  langue quand une voix clonée est choisie (`#tts-langue-ligne`). Piper/Kokoro ignorent
  ce paramètre (langue déduite de la voix).
- **Capture** : un **texte de lecture fixe** phonétiquement riche (« La bise et le
  soleil ») est affiché à l'enregistrement pour obtenir un échantillon clair et varié.
- **v1 web uniquement** — l'app macOS n'a pas encore ce module (parité différée, comme
  pour d'autres fonctionnalités du projet).

L'app macOS (Swift/SwiftUI, `macos-app/`) suit le **même design Workflow** : barre
supérieure (navigation 3 modules, pastilles de statut, thème, mode avancé) dans
`ContentView.swift` (+ `AppEnvironment` partagé), et un fichier par module dans
`Views/` (`ImportModuleView` avec drag & drop natif + NSOpenPanel,
`BibliothequeModuleView` avec lecteur AVAudioPlayer lisant le WAV du disque,
`LaboratoireModuleView` avec teasers). Le choix moteur/voix TTS est partagé entre
Laboratoire et Bibliothèque via `@AppStorage`. Le projet Xcode (format 16,
groupes synchronisés) inclut automatiquement les fichiers posés dans `macos-app/`.

## Prérequis

- [Ollama](https://ollama.com) installé et lancé, avec au moins un modèle téléchargé
  (`ollama pull llama3.1`)
- Python 3.10+
- (Optionnel) [Tesseract](https://github.com/tesseract-ocr/tesseract) pour l'extracteur OCR,
  utile pour les PDF scannés ou à couche texte corrompue : `brew install tesseract`
  (+ `brew install tesseract-lang` pour le français/espagnol)

## Installation

```bash
cd backend
python3 -m venv venv
source venv/bin/activate      # sur Windows : venv\Scripts\activate
pip install -r requirements.txt
```

## Lancer l'application

**Backend (API) :**
```bash
cd backend
uvicorn app.main:app --reload --port 8000
```
La documentation interactive de l'API est disponible sur http://localhost:8000/docs

**Frontend :**
Ouvre simplement `frontend/index.html` dans ton navigateur, ou sers-le avec :
```bash
cd frontend
python3 -m http.server 5500
```
puis va sur http://localhost:5500

## Lancer les tests

```bash
cd backend
pytest tests/ -v
```

## Design system

L'interface (web **et** macOS) suit le design system partagé de `2000_DigitalProducts`,
mais **vendoré** (copie locale) car le projet est indépendant et publié sur GitHub :

- **Web** : `frontend/css/tokens.css` (copie de `design-system/tokens.css`) est chargé
  avant `style.css`. Tout le CSS applicatif utilise les variables sémantiques
  (`var(--accent)`, `var(--surface)`, `var(--text)`, `var(--border)`…) — **jamais de hex
  en dur**, sinon invisible en mode sombre. Le mode clair/sombre est automatique
  (suit le système, ou `data-theme` sur `<html>`).
- **macOS** : `macos-app/Theme.swift` traduit les tokens en `Color` dynamiques
  (clair/sombre) exposées via l'énumération `DS` (`DS.accent`, `DS.green`, `DS.red`,
  `DS.amber`, rayons `DS.radius*`). L'accent est appliqué globalement par `.tint(DS.accent)`
  sur la racine. Les surfaces natives (`GroupBox`, matériaux macOS) sont laissées telles
  quelles — elles s'adaptent déjà. Pour une couleur de statut, utiliser `DS.*`, pas
  `.green`/`.red`/`.orange` bruts.

Pour resynchroniser après une évolution du design system : recopier `tokens.css` et
réaligner les valeurs de `Theme.swift`.

## Roadmap

Voir [docs/features-roadmap.md](docs/features-roadmap.md) pour la liste des fonctionnalités
prévues et leur priorité.
