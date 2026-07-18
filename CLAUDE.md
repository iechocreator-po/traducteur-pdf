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
- **Nouveau document** (`module-import.js`) : lot multi-fichiers — **import par
  navigateur** (bouton « Parcourir » + glisser-déposer, `televerser()` → `POST /api/upload`)
  ou, en mode avancé, ajout par **chemin absolu** (flux historique conservé). Le navigateur
  ne révélant jamais le chemin disque d'un fichier, l'upload envoie les octets ; le backend
  les écrit dans `backend/uploads/<hash-contenu>/` (`services/uploads.py`) et retourne un
  chemin absolu réinjecté tel quel dans le flux existant. Puis : analyse auto (qualité /
  durée / chapitres), réglages du lot (langues ; extracteur et modèle en mode avancé),
  lancement en lot (file séquentielle backend), planification. Une section **« Reprendre
  une traduction »** (IIFE dédiée dans `module-import.js`, distincte du `lot` en mémoire)
  liste les jobs **non terminés** via `GET /api/jobs/reprenables` — donc **persistante**
  (survit au reload et aux sessions). Contrôles par document selon l'état : job **en cours**
  → **Pause** (`POST /api/job/{job_id}/pause`, `job_id` exposé par le registre) ; job **arrêté**
  → **Reprendre** (`POST /api/translate` `resume=true`, options issues du **registre** et non
  des menus) **et** **➕ Chapitres** (`window.toledoImport.ajouterEtOuvrirChapitres` renvoie le
  document dans le `lot` avec le sélecteur de chapitres ouvert → flux **additif** pour traduire
  de nouveaux chapitres). Tous : **Supprimer** (`DELETE /api/bibliotheque` → retire du registre,
  fichiers disque conservés).
- **Planification** (`scheduler.py`) : la liste « Traductions planifiées » (`GET /api/scheduled/tous`)
  a un bouton **Retirer** sur **chaque** ligne quel que soit le statut (`DELETE /api/scheduled/{id}`
  → `supprimer_job`, suppression réelle et non simple passage en `annule`). Un job **déclenché
  avec succès est auto-purgé** de la liste (`_lancer_job`) : la traduction est ensuite suivie
  dans « Reprendre »/la Bibliothèque, plus de « Déclenché » fantôme qui subsiste après la fin.
- **Bibliothèque** (`module-bibliotheque.js`) : documents traduits (`GET /api/bibliotheque`,
  registre alimenté par `translation_runner`), lecture chapitre par chapitre
  (`POST /api/chapitres/contenu`), barre audio TTS (`GET /api/tts/audio`), panneau IA
  « points clés + quiz » servi par le backend Étude (`services/etude.py` +
  `services/study_runner.py`, routes `POST /api/etude`, `GET /api/etude/statut`,
  sortie `<base>_fiche_<xx>.md`, même file d'attente séquentielle que la traduction).
  Les chapitres portent des **cases à cocher** (`chapitresCoches`, mode avancé) : la
  génération de fiche traite **toute la sélection** en un job, et l'export reprend les
  chapitres cochés. Les options (modèle, langue) suivent le **document** (`docActif.modele`
  / `docActif.langue_cible`) et non les menus de l'Import — sinon un changement de menu
  ferait diverger les options et le backend Étude effacerait silencieusement les fiches
  déjà générées (`study_runner.py`, comparaison `memes_options`). `ficheParChapitre` est
  reconstruit depuis `etat.chapitres` à chaque poll (jamais accumulé). Un document au statut
  `erreur` reste **lisible**, avec un bandeau « Reprendre » (`/translate` `resume=true`).
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

### Import par navigateur (`POST /api/upload`)

L'API est **path-based** (chemins absolus), mais un navigateur ne révèle jamais le chemin
disque d'un fichier choisi/déposé. `POST /api/upload` (`services/uploads.py`) reçoit donc
les octets en `multipart`, les valide **par contenu** (`%PDF-` ou UTF-8 décodable — jamais
par l'extension client), assainit le nom (`assainir_nom` : anti-évasion `../`, whitelist
Unicode qui préserve les accents, troncature en octets) et écrit dans
`backend/uploads/<sha256[:16]>/<nom>` — dossier **indexé sur le contenu** pour rester
idempotent (ré-uploader le même fichier réutilise cache et reprise plutôt que de retraduire).
La réponse rend un chemin absolu que le frontend réinjecte tel quel ; **l'extension du nom
retourné est donc structurante** (`estMarkdown`, routage `/analyser` vs `/chapitres`).

- **Garde de chemin centralisée** : `api/validation.py` (`valider_chemin_source` /
  `resoudre_source`) remplace les 7 `os.path.exists` des routes — chemin absolu obligatoire,
  extension dans l'allowlist `(.pdf, .md)`, `os.path.isfile` (404 sur un dossier au lieu d'un
  500). **Pas** de whitelist `backend/uploads/` : les deux flux (upload **et** chemin absolu
  du mode avancé) coexistent volontairement — c'est écrit dans la docstring pour prévenir un
  « durcissement » qui casserait le flux historique.
- **Modèle de menace** : app 100 % locale, protégée par le CORS restrictif (`main.py`). Seul
  delta introduit par l'upload : `multipart/form-data` est CORS-safelisted (POST sans
  préflight depuis un site tiers), d'où la garde `Origin` (`verifier_origine_upload`, 403 si
  origine présente hors allowlist). `ORIGINES_LOCALES` est partagée entre `main.py` et
  `validation.py` pour éviter la dérive.
- **Purge** : `purger_uploads_anciens()` (appelée au démarrage) ne supprime QUE les dossiers
  abandonnés (vieux, sans `*_traduit*.md`/`*.state.json`, non référencés en Bibliothèque) —
  les sorties étant écrites à côté de la source, une purge naïve détruirait des traductions.

### Moteur de traduction unifié (fiabilité + reprise + progression)

**Il n'existe qu'UN seul moteur d'exécution** (`translation_runner._executer_traduction`,
unifié le 2026-07-18). Les deux implémentations parallèles d'avant (document entier par
`decouper_en_chunks` vs par chapitres) divergeaient : les correctifs fiabilité ne profitaient
qu'à l'une, et le mode chapitre traînait des bugs (progression figée, ETA en double). Tout
document est désormais traité comme une **liste ordonnée de chapitres** ; s'il n'a aucun titre
`#`, on fabrique un **chapitre implicite** « Document entier » couvrant tout le texte
(`_chapitres_ou_implicite`). Ne JAMAIS réintroduire un second chemin d'exécution.

- **Progression au grain du sous-morceau** : chaque chapitre est sous-découpé
  (`CHAPITRE_SOUS_CHUNK_TAILLE_MAX`), et `derniere_section_completee` avance à **chaque
  sous-morceau** (pas seulement à la fin d'un chapitre) → la barre ne reste jamais figée sur
  un gros chapitre (c'était le cœur du feedback #132). `total_sections` = nombre total de
  sous-morceaux de la portée du run.
- **Chapitre = unité atomique d'écriture** : un chapitre n'est écrit (append) que si TOUS ses
  sous-morceaux réussissent ; sinon rien n'est écrit et il reste re-sélectionnable (aucun
  placeholder, aucun trou au milieu du fichier).
- **Retry réseau** (inchangé) : `translator.appeler_ollama()` distingue réseau/5xx
  (transitoires → backoff, budget **mural** 30 min, `OLLAMA_RETRY_*`) et 4xx (définitives).
  `OllamaIndisponible` (fatale → job `erreur`, reprenable) vs `OllamaErreurApplicative`
  (locale au chapitre). Le callback `interruption` garde Pause/Annuler vivants pendant le backoff.
- **Statut honnête** : un job avec ≥1 `chapitres_echoues` finit `erreur`, **jamais** `termine`
  (bascule sur cette liste seule, pas sur `erreurs`/`avertissements` qui portent aussi les
  avertissements qualité inoffensifs). `sections_echouees` est conservé sur `EtatJob` **pour la
  compat** des `.state.json` d'avant l'unification ; le moteur unifié n'écrit que `chapitres_echoues`.
- **ETA sans O(n²)** : `temps_ecoule_secondes` est figé pendant la boucle ; l'écoulé se calcule
  en variable **locale** (`base_ecoule + (now - session_debut)`) et n'est réécrit dans l'état
  qu'aux points de sortie (pause/annule/fin). Fini la ré-accumulation (ancien item G).
- **Reprise unifiée** (`demarrer_traduction`, `chapitres_selectionnes` persisté sur `EtatJob`) :
  - **Additive** — poursuivre de NOUVEAUX chapitres (état existant, sélection fournie) ou
    reprendre sans trou : on garde la sortie et on **append** les chapitres restants
    (sélection − `chapitres_traduits`).
  - **Rejeu à cache chaud DANS L'ORDRE** — si l'état a des trous (`_a_des_trous` : `chapitres_echoues`,
    `sections_echouees`, ou marqueur `MARQUEUR_ECHEC` legacy dans la sortie) : on réécrit tout
    depuis l'en-tête, dans l'ordre. Le cache (`cache_traduction.py`, indexé par **contenu**, ne
    contenant jamais les morceaux échoués) fait revenir instantanément le bon travail ; seuls les
    trous repartent chez Ollama. La réécriture ordonnée évite qu'un chapitre du milieu recousu
    se retrouve à la fin.
- **Récupération au démarrage** : `recuperer_jobs_interrompus()` (appelée dans `main.py`) parcourt
  le registre Bibliothèque et bascule tout `.state.json` resté `en_cours` → `en_pause` (au
  redémarrage le registre mémoire est vide : un `en_cours` est forcément un job coupé par un
  arrêt/crash serveur). Il redevient ainsi reprenable depuis « Nouveau document ».
- **Endpoints associés** : `GET /api/jobs/reprenables` (documents non terminaux, filtre sur
  `STATUTS_NON_TERMINAUX`) ; `DELETE /api/bibliotheque` (`bibliotheque.retirer_document` : retire
  du registre, **ne touche pas** aux fichiers disque).
- **Perf mesurée** : Ollama ~29 tok/s sur M2 Pro. Le parallélisme des sous-morceaux reste mesuré
  **inutile** (1,04× — Ollama 0.32 sérialise sans `OLLAMA_NUM_PARALLEL`), donc non construit ;
  l'architecture (file d'unités) le rendrait toutefois facile à ajouter. Items encore ouverts :
  anti-sommeil `caffeinate` (item H). L'ETA O(n²) (G) et le sous-découpage des gros blocs (I) sont
  résolus par cette unification. **v1 web** — parité macOS de la section Reprendre différée.

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

<!-- bilbao:managed:start -->
## Géré par bilbao — ne pas éditer à la main
_Bloc régénéré par le cockpit bilbao (2026-07-14). La prose hors marqueurs n'est jamais touchée._

### Roadmap (issue des feedbacks)
- [Livré] Voix TTS personnalisée (9 votes)

### Feature flags actifs
_Aucun feature flag actif._
<!-- bilbao:managed:end -->
