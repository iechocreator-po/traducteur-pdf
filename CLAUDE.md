# Traducteur PDF

Application locale de traduction de documents PDF, propulsée par [Ollama](https://ollama.com)
(modèles LLM open source exécutés 100% sur ta machine — aucune donnée envoyée vers le cloud).

## Démarrage de session — features en attente

Depuis R24 (2026-07-20), **bilbao** (`../feature-factory/`) est l'unique
source de vérité des features/roadmap de ce produit — pas de
`docs/features-roadmap.md` local. À chaque nouvelle session sur toledo,
présenter d'emblée la liste des features/idées/questions dont le statut
n'est ni `Livre` ni `Rejete`, lue directement dans l'export JSON (pas besoin
que bilbao tourne) :

```bash
python3 -c "
import json
d = json.load(open('../feature-factory/data/toledo/features.json'))
for f in d['features']:
    if f['statut'] not in ('Livre', 'Rejete'):
        print(f['id'], f['statut'], f['votes'], f['titre'])
"
```

Pour marquer une feature complétée, en ajouter une nouvelle, ou assembler une
release note, passer par l'API de bilbao (`npm start` dans
`feature-factory/`, 127.0.0.1:4600) — jamais éditer `features.json` à la main
(régénéré à chaque mutation, toute édition manuelle serait écrasée).

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
└── docs/             # Documentation (release notes, décisions) — features/roadmap gérées par bilbao, voir plus bas
```

Le frontend ne fait **que** des appels HTTP vers l'API locale — aucune logique métier
n'existe côté interface. Ça permet de remplacer ou faire évoluer l'UI sans toucher au backend.

L'interface web suit la **refonte « Workflow »** (design retenu dans
`toledo_v2/handoff_iTraducteur/`, décisions dans `docs/refonte-workflow-decisions.md`) :
une barre supérieure (thème, mode avancé, statuts) et **3 modules** organisés par flux
de travail, chargés depuis `frontend/js/` (`commun.js` + un fichier par module) :
- **Vos traductions** (`module-import.js`, section « Vos traductions ») : liste
  **tous** les documents du registre (`GET /api/bibliotheque`) — en cours, en pause,
  interrompus ET terminés — pour les gérer sans passer par la Bibliothèque (qui, elle,
  est réservée aux résumés/quiz/export). Chaque ligne affiche la progression **« N/M
  chapitres · X/Y morceaux »** (`lister_documents` expose `chapitres_traduits` et
  `chapitres_selectionnes`). Actions selon l'état : **Pause** (en cours), **Reprendre**
  (arrêté/troué), **➕ Chapitres** et **Supprimer** (ce dernier **masqué pendant un job**
  en cours/en file). **➕ Chapitres** ouvre un sélecteur de chapitres **inline, directement
  sous le document** (pas de saut vers le lot, pas de re-analyse OCR) : les chapitres déjà
  traduits sont **verrouillés** (« ✓ déjà traduit »), on ne coche que les nouveaux, et le
  bouton **« Traduire N chapitres »** lance le flux **additif** (`chapitres_selectionnes`,
  options issues du **registre**). Si tout est déjà traduit, le sélecteur l'indique et
  aucun lancement n'est possible (fini le « terminé » silencieux). **Lancer une traduction
  retire le document du lot** (il vit désormais ici) — plus de doublon lot ↔ Vos traductions.
- **Nouveau document** (`module-import.js`) : lot multi-fichiers — **import par
  navigateur** (bouton « Parcourir » + glisser-déposer, `televerser()` → `POST /api/upload`)
  ou, en mode avancé, ajout par **chemin absolu** (flux historique conservé). Le navigateur
  ne révélant jamais le chemin disque d'un fichier, l'upload envoie les octets ; le backend
  les écrit dans `backend/uploads/<hash-contenu>/` (`services/uploads.py`) et retourne un
  chemin absolu réinjecté tel quel dans le flux existant. Puis : analyse auto (qualité /
  durée / chapitres), réglages du lot (langues ; extracteur et modèle en mode avancé),
  lancement en lot (file séquentielle backend), planification. La gestion des traductions
  existantes se fait dans la section **« Vos traductions »** (décrite plus haut) :
  Pause / Reprendre (`POST /api/job/{job_id}/pause`, `POST /api/translate` `resume=true`,
  options issues du **registre**), **➕ Chapitres** (flux additif) et **Supprimer**
  (`DELETE /api/bibliotheque` → retire du registre, fichiers disque conservés).
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

### Extraction d'images du PDF (flag `extraction_images_pdf`, off par défaut)

En cours de validation (2026-07-21, branche `feat/extraction-images-pdf`) —
flag **off par défaut**, à activer via `bilbao.features.json` ou
`feature_flags.json` local une fois validé en usage réel prolongé.

- **Extraction** (`pdf_extractor._extraire_avec_pymupdf4llm`) : demande à
  `pymupdf4llm.to_markdown()` d'**embarquer** les images en base64
  (`embed_images=True`) plutôt que de les écrire elle-même sur disque
  (`write_images=True`) — cette dernière option **plante** dès que le nom du
  PDF contient un espace, très courant (bug vérifié dans
  `pymupdf4llm/helpers/utils.py:md_path`, qui sanitize le nom pour la
  référence Markdown mais sauvegarde sous le nom non sanitisé). Le service
  décode lui-même le base64 et écrit les fichiers dans
  `<base>_images/img-N.png`, référencés dans le Markdown par un chemin
  relatif court — nommage entièrement maîtrisé par nous, sans dépendre du
  comportement interne (fragile) de la librairie. Marker et Tesseract
  restent hors scope (dégradation propre = pas d'images).
- **Persistance automatique** (`pdf_extractor.convertir_et_sauvegarder`,
  appelée par `_lire_source()`, factorisée avec `/convert`) : jusqu'ici,
  `_lire_source()`/`_lire_source_markdown()` ne faisaient QUE lire un
  `_converti_*.md` s'il existait déjà (créé manuellement via `/convert`) —
  sinon ré-extraction à la volée, jamais sauvegardée. Chaque clic de
  chapitre en Bibliothèque et chaque `demarrer_traduction()`
  (reprise/ajout compris) relançait donc l'extraction PDF complète. Avec le
  flag actif, le premier appel persiste `.md` + images ; les appels
  suivants retombent sur le `glob` déjà en place, sans jamais rappeler
  `pymupdf4llm`. Flag off → comportement historique inchangé (aucune
  persistance, ré-extraction à chaque appel comme avant).
- **Traduction** : aucune modification de `translation_runner.py` — les
  tags `![](...)` traversent le moteur unifié comme du texte normal (le
  prompt système préserve déjà `[ ]`/`( )`, `translator.py:126`, vérifié en
  conditions réelles). `decouper_en_chunks()` **sous-découpe normalement**
  un bloc contenant un tag image (par paragraphes `\n\n`), et l'étape de
  fusion garde le tag collé à son paragraphe voisin (jamais isolé seul). ⚠️
  **Ne PAS** re-traiter un tag image comme une frontière inséparable au même
  titre qu'un tableau/bloc de code : le tag est une ligne isolée que le
  découpage ne coupe jamais en deux, alors qu'un tableau/code cassé est
  irréparable. L'ancienne version le faisait et transformait tout chapitre
  illustré en un morceau géant (voir le Fix du 2026-07-23 plus bas).
- **Texte de secours « picture text » nettoyé** (`_RE_TEXTE_IMAGE`,
  `_nettoyer_texte_image`) : pymupdf4llm essaie, par défaut (`force_text=True`,
  **déjà le cas flag off**, pas une option qu'on active), d'extraire le texte
  natif présent dans une zone image (schéma légendé) et l'entoure de
  `<!-- Start/End of picture text -->` — visible seulement quand l'image
  elle-même n'a pas pu être capturée. Sans nettoyage, ce marqueur fuit tel
  quel jusque dans le document traduit (le LLM le traduit même, d'où des
  `<!-- Début/Fin du texte de l'image -->` observés en français) et l'export
  HTML. Nettoyé en texte lisible (`, `-joint), **scopé au flag actif**
  uniquement (le chemin flag off garde le comportement historique, marqueur
  brut compris — pas notre problème à corriger hors du flag).
- **Affichage Bibliothèque** : nouvelle route `GET /api/image?chemin=...`
  (même garde que `/tts/audio` — chemin absolu, extension allowlistée dans
  `EXTENSIONS_IMAGE`, `api/validation.py`). `rendreContenu()`
  (`module-bibliotheque.js`) détecte une ligne `![alt](chemin)` et crée un
  `<img>` via `document.createElement` (jamais `innerHTML`), chemin résolu
  contre `dirname(docActif.chemin_sortie)`. **Fonctionnalité de mode avancé** :
  gated par `featureFlags.extraction_images_pdf === true` **ET**
  `document.documentElement.classList.contains("avance")` — pas seulement
  le flag.
- **Export HTML du document traduit** (bouton `#doc-exporter` dans
  `lecture-bandeau`, même double condition flag+mode avancé, distinct de
  `exporterFicheHtml()` qui exporte la fiche IA résumé/quiz) : charge tous
  les chapitres traduits (`construireDocumentHtml()`), convertit chaque
  image en data-URI base64 (`imageEnDataUri()`, fetch + `FileReader`) pour
  un fichier 100 % autonome et portable une fois sorti du serveur local —
  vérifié en l'ouvrant hors serveur. **Chapitres de sommet uniquement**
  (`estChapitreImbrique()`) : `identifier_chapitres()` liste TOUS les
  niveaux de titre (`#` à `######`) comme des « chapitres » distincts, y
  compris les sous-titres dont le contenu est déjà inclus dans celui de leur
  parent (règle de `_extraire_chapitres`, côté backend). Boucler naïvement
  sur tous les chapitres pour construire l'export duplique donc le contenu
  d'un sous-titre : une fois dans la section de son parent, une fois comme
  section à part. `estChapitreImbrique()` reproduit exactement la règle déjà
  utilisée par `translation_runner._est_couvert_par_ancetre()` (même
  algorithme, deux implémentations car un côté Python/backend et l'autre
  JS/frontend) pour ne garder que les chapitres de sommet dans la table des
  matières ET les sections exportées. Nécessite `ligne_debut`/`ligne_fin`
  sur chaque entrée de `POST /chapitres` — ajout **additif** (seul `contenu`,
  lourd, reste exclu de cette route ; ne change rien pour les consommateurs
  existants qui ignorent ces deux champs).
- **Flag unique** `extraction_images_pdf` (`FLAGS_PAR_DEFAUT`, **off par
  défaut** contrairement aux autres flags — touche l'extraction PDF et le
  chunking envoyé à Ollama, rollout prudent) pilote l'ensemble ci-dessus
  d'un bloc : extraction, persistance auto, affichage/export (eux-mêmes
  soumis en plus au mode avancé).
- **Piège opérationnel rencontré en validant cette feature** : le backend
  local (`uvicorn`, voir `.claude/launch.json`) tourne **sans `--reload`** —
  modifier `pdf_extractor.py`/`routes.py` sans redémarrer le process laisse
  l'ancien code actif indéfiniment, alors que les tests `pytest` (qui
  importent le code frais) donnent l'impression que le correctif est en
  place. Pire, combiné à la persistance automatique ci-dessus : un
  `_converti_*.md` déjà écrit par un run AVANT un correctif d'extraction
  reste lu tel quel par les runs suivants, même après redémarrage du
  serveur — le correctif ne s'applique jamais tant que ce cache précis n'est
  pas supprimé manuellement. Après toute modif de `pdf_extractor.py` en
  test manuel : redémarrer le backend **et** vérifier qu'aucun
  `_converti_*.md` obsolète ne traîne pour le document testé.

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

## Source de données bilbao — features & releases

Les features et la roadmap de ce produit ne sont plus suivies dans un fichier
local : **bilbao** (`feature-factory`) en est l'unique source de vérité, pour
tous les produits du portfolio.

- **Lecture** (pas besoin que bilbao tourne) : `../feature-factory/data/toledo/features.json`
  et `.../releases.json`.
- **Écriture** (marquer une feature complétée, en ajouter une nouvelle,
  assembler une release) : nécessite `npm start` dans `feature-factory/`
  (127.0.0.1:4600) — passer par son API (`GET/POST/PATCH /api/feedbacks`,
  `POST /api/release-notes`). Ne jamais éditer les JSON à la main.

## Fixes — Pause et reprise après redémarrage (23/7/2026)

Trois bugs critiques ont été corrigés pour la robustesse du système de pause/reprise :

1. **Jobs enfilés restaient figés après redémarrage** (`recuperer_jobs_interrompus()`)
   - Cause : fonction ignorait les jobs avec statut `en_attente` (traitait seulement `en_cours`)
   - Fix : traite maintenant `en_attente` aussi → jobs ré-enfilés correctement au démarrage
   - Impact : plus de jobs perdus après un redémarrage du backend

2. **Endpoint `/translate` n'exposait pas le chemin de sortie**
   - Cause : le frontend n'avait pas accès à `chemin_sortie` pour passer au endpoint Pause
   - Fix : `/translate` retourne maintenant `chemin_sortie` en plus de `job_id`
   - Impact : Pause résilient après redémarrage (même sans registre en mémoire)

3. **Endpoint `/pause` cassait après redémarrage du serveur**
   - Cause : cherchait le job_id dans le registre en mémoire (vide après redémarrage) → 404
   - Fix : accepte `chemin_sortie` optionnel en query param, charge l'état depuis le disque si job_id absent
   - Impact : bouton Pause fonctionne même après redémarrage
   - Bonus : bug JavaScript dans module-import.js corrigé (`new URL()` avec URL relative)

## Fix — Chapitre illustré = morceau géant → stall Ollama (23/7/2026)

**Symptôme** : avec `extraction_images_pdf` actif, la traduction d'un chapitre
contenant une image (ex. « Models of the Mind_ Chapter 9 ») restait bloquée à
`0/N` indéfiniment. Ollama (llama-server) chargé mais figé à ~3 % CPU, ne
répondant plus à aucune requête (même un « bonjour » manuel). Mac qui « tourne
pour rien ». `pytest` (code frais) trompeur : chaque appel isolé passait.

**Cause racine** (mesurée) : `decouper_en_chunks()` (`pdf_extractor.py`) traitait
un tag image `![]()` comme une frontière **inséparable** (au même titre qu'un
tableau). Un chapitre de 48 Ko avec 2 images devenait donc **un seul morceau de
45 818 caractères (~13 000 tokens)**. Envoyé à Ollama, il exigeait un contexte
~26 k tokens ; Ollama 0.32 (mis à jour ce jour) charge par défaut un contexte de
**32768** → cache KV énorme → sous pression mémoire (~11 % RAM libre : modèle
8,7 Go + backend + navigateur), llama-server **swappe et se fige** au lieu de
calculer. Avant l'extraction d'images (flag off), pas de tags → morceaux normaux
(~1500 chars) → aucun souci : d'où le « ça marchait avant ».

**Correctif (2 volets complémentaires)** :
1. `decouper_en_chunks()` sous-découpe désormais par paragraphes un bloc
   contenant une image (seuls code ``` et tableaux `|` restent entiers). Le
   chapitre 9 passe de 3 morceaux (dont un de 45 818) à **41 morceaux ≤ 1500
   chars**. L'étape de fusion garde le tag image collé à son paragraphe voisin
   (jamais isolé).
2. `translator.py` force `num_ctx = OLLAMA_NUM_CTX` (`settings.py`, **4096**)
   dans les `options` de l'appel Ollama : suffisant pour des morceaux de ~430
   tokens, cache KV léger, plus de stall — et rend toledo robuste quel que soit
   le défaut de contexte d'Ollama. ⚠️ Si `CHAPITRE_SOUS_CHUNK_TAILLE_MAX`
   augmente un jour, remonter `OLLAMA_NUM_CTX` en conséquence (input+sortie).

**Piège à ne pas retomber dedans** : `num_ctx` **trop petit** tronque un gros
morceau → le modèle produit un **résumé en anglais** au lieu d'une traduction.
C'est pourquoi le vrai correctif est le chunking (petits morceaux), `num_ctx`
seul ne suffit pas.

**Validation** : Chapter 9 traduit **41/41, 0 échec**, sortie en vrai français,
Markdown préservé, RAM stable ~22-29 % (plus d'effondrement), aucun stall. Suite
`pytest` : **222 verts** (test chunking mis à jour pour valider « image jamais
isolée mais bloc découpable » ; 2 tests flag isolés via `monkeypatch` pour ne
plus dépendre du `feature_flags.json` local qui active le flag).

<!-- bilbao:managed:start -->
## Géré par bilbao — ne pas éditer à la main
_Bloc régénéré par le cockpit bilbao (2026-07-14). La prose hors marqueurs n'est jamais touchée._

### Roadmap (issue des feedbacks)
- [Livré] Voix TTS personnalisée (9 votes)

### Feature flags actifs
_Aucun feature flag actif._
<!-- bilbao:managed:end -->
