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
  durée / chapitres), réglages du lot (langues et modèle ; extracteur en mode avancé),
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
  grille Bibliothèque). Éléments gated : extracteur de l'Import (le **modèle** en est sorti
  le 17/8, voir « Plusieurs modèles Ollama cohabitent »), onglet +
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
- **Endpoints associés** : `DELETE /api/bibliotheque` (`bibliotheque.retirer_document` : retire
  du registre, **ne touche pas** aux fichiers disque). *(`GET /api/jobs/reprenables` a existé un
  temps pour ça mais n'a jamais eu de client — retiré le 5/8/2026 avec le reste du code mort F8,
  voir « Architecture cible » plus bas.)*
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
- **Traduction** : aucune modification de `translation_runner.py`.
  `decouper_en_chunks()` **sous-découpe normalement** un bloc contenant un tag
  image (par paragraphes `\n\n`), et l'étape de fusion garde le tag collé à son
  paragraphe voisin (jamais isolé seul). ⚠️ **Ne PAS** re-traiter un tag image
  comme une frontière inséparable au même titre qu'un tableau/bloc de code : le
  tag est une ligne isolée que le découpage ne coupe jamais en deux, alors qu'un
  tableau/code cassé est irréparable. L'ancienne version le faisait et
  transformait tout chapitre illustré en un morceau géant (voir le Fix du
  2026-07-23 plus bas).
- **Les tags images ne sont JAMAIS envoyés à Ollama** (`translator.py`,
  `_masquer_images`/`_restaurer_images`) : avant l'appel, chaque `![](chemin)`
  est remplacé par une sentinelle neutre `⟦IMGk⟧`, puis restauré à l'identique
  après traduction. Ollama ne voit donc jamais le chemin de fichier — zéro
  token gaspillé, zéro risque qu'il traduise/altère le chemin. Filet de
  sécurité : une sentinelle perdue par le modèle réinsère le tag en fin de
  texte (l'image n'est jamais perdue). Sans image dans le morceau, comportement
  strictement inchangé (pas de masquage, pas de règle système en plus).
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

**Tests manuels bout-en-bout (T1/T2/T3, hors suite pytest)** : `tests/test_pdf_translation.py`
lance un vrai PDF à travers Ollama direct, puis le backend avec/sans extraction d'images
(prérequis : Ollama lancé + backend `uvicorn` sur le port 8000). Doc complète dans
`tests/README_TESTS.md`, dernier rapport dans `tests/RESULTS_2026-07-23.txt` (Chapter 9,
3/3 réussis). Réutilisable sur n'importe quel PDF :
```bash
cd tests
python3 test_pdf_translation.py /chemin/vers/mon/pdf.pdf [--test T1|T2|T3|all] [--timeout 600]
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

**Durcissements ajoutés dans la foulée (23/7/2026)** :
- **Preflight Ollama avant chaque job** (`translator.verifier_ollama_pret`,
  appelé dans la route `/translate`) : au-delà d'un ping `/api/tags`, une VRAIE
  mini-traduction avec les params exacts (num_ctx compris), plafonnée à 60 s. Un
  `llama-server` figé ne répond pas à ce test alors que `/api/tags` répondrait
  encore → `/translate` renvoie **503** avec la consigne de redémarrer Ollama,
  au lieu de figer 10 min à `0/N`. C'est le garde qui aurait évité toute la
  séance de debug.
- **Tags images jamais envoyés à Ollama** (voir la section extraction d'images) :
  masquage `⟦IMGk⟧` avant l'appel, restauration après.
- **Validation end-to-end réelle** (`tests/validate_translation.py` +
  `tests/reference/Chapter9_*_reference.md`, hors suite `pytest`) : compare une
  vraie sortie à une référence golden sur des invariants robustes au
  non-déterminisme d'Ollama (taille dans [50 %,200 %], tags images identiques,
  français vs résumé anglais, 0 échec). Prouvé : accepte la bonne sortie, rejette
  un résumé anglais tronqué. Régénérer la référence via `--save-reference` après
  toute amélioration volontaire de la qualité. `pytest` final : **231 verts**.

<!-- bilbao:managed:start -->
## Pièges vérifiés — durabilité et messagerie (audit du 27/7/2026, addendum différé du 28/7)

Audit complet de la messagerie backend ↔ frontends (web **et** macOS), de la
durabilité sur 12 scénarios d'interruption et de l'architecture cible (12 principes,
13 défauts) :
[frontend/docs/architecture-messagerie.html](frontend/docs/architecture-messagerie.html)
(document autonome, lisible hors ligne, accessible depuis le **Laboratoire →
Documentation technique**). Les cinq pièges ci-dessous ont été **vérifiés en
exécutant le code**, pas seulement par lecture — ne pas les traiter comme des
hypothèses.

⚠️ **Le traitement différé est la zone la moins couverte du système** — c'est la
seule partie qui travaille quand personne ne regarde, et celle qui a le moins de
moyens de signaler qu'elle a échoué (voir le 5ᵉ piège et la section « Le différé
intégré » du document).

- **Un `.state.json` tronqué rend TOUT le travail invisible.** `sauvegarder_etat()`
  (`job_manager.py`) ouvre en `"w"` — troncature puis réécriture — et est appelée
  ≈1× par sous-morceau (41× pour Chapter 9, des centaines pour un livre). Chaque
  appel est une fenêtre de corruption. `charger_etat()` ne garde rien et
  `lister_documents()` (`bibliotheque.py`) non plus : le `JSONDecodeError` remonte
  et **`GET /api/bibliotheque` renvoie 500 — tous les documents disparaissent des
  deux frontends à la fois**. Le travail est intact sur le disque, il devient
  inatteignable. Même exposition non atomique pour `bibliotheque.json`,
  `scheduled_jobs.json` et le cache.
- **Le cache — qui *est* le travail — se perd en silence.** `cache_traduction.py`
  réécrit le fichier **entier** après chaque sous-morceau, et `charger_cache()`
  *avale* la corruption en retournant `{}`. Après une coupure brutale, la reprise
  repart chez Ollama pour du travail déjà payé, **sans jamais signaler la perte**.
  C'est le cache qui porte la traduction entre deux écritures de chapitre : le
  correctif minimal est `tmp` + `os.replace()` (atomique sur APFS), ou un journal
  append-only `.jsonl`.
- **Étude, TTS et clonage ne sont JAMAIS récupérés au démarrage.**
  `recuperer_jobs_interrompus()` (appelée dans `main.py`) ne connaît que la
  traduction. Un job d'étude coupé par un redémarrage reste `en_cours` pour
  toujours : `pollStatutFiche` (`module-bibliotheque.js`) ne s'arrête que sur
  `termine`/`erreur`/`annule` et `majBoutonGenerer` désactive « Générer » tant que
  le poll tourne — **le panneau Résumé & Quiz de ce document est bloqué
  définitivement, y compris après rechargement de page** (`chargerFicheExistante`
  relance le poll). Seule sortie : supprimer le `_fiche_*.state.json` à la main.
  Le TTS a la même absence de récupération, et en plus ni pause ni reprise ni
  cache — relancer repart de zéro.
- **Code mort et routes sans client — la dérive à ne pas rouvrir.** Depuis que les
  fichiers lancés quittent le lot (`module-import.js`), `stage = "lance"` n'est
  **plus jamais assigné** : `pollLot`, `demarrerPolling`, `arreterPolling`,
  `basculerPauseLot` et le bouton `#bouton-pause-lot` sont tous inatteignables
  (~80 lignes). Côté backend, **4 routes n'ont aucun client** :
  `GET /jobs/reprenables`, `GET /job/{id}/statut`, `POST /job/{id}/annuler` et
  `POST /job/{id}/reprendre` (qui renvoie toujours 400). Conséquence notable :
  toute la machinerie d'annulation existe (`demander_annulation`, `est_annule`,
  `AnnulationDemandee`, statut `annule`, branches de rendu) et **aucun bouton
  « Annuler » n'existe dans aucune des deux interfaces** — les deux savent
  afficher un job annulé, aucune ne sait en provoquer un.
  **Code mort retiré depuis le 5/8/2026** (branche `feat/architecture-cible`,
  voir « Architecture cible » plus bas) : les 4 routes et les 5 éléments JS
  ci-dessus, plus leurs deux wrappers Swift. La machinerie d'annulation active
  (`demander_annulation`/`est_annule`/`AnnulationDemandee`) n'a pas bougé —
  toujours aucun bouton « Annuler », c'était la façade HTTP inutilisée qui a
  disparu, pas le mécanisme.

- **Le planificateur est une 5ᵉ famille de jobs, hors de tout le reste** (addendum
  du 28/7/2026). Il n'est pas sur la file du `job_manager` : il a son propre thread
  (`scheduler._boucle_surveillance`, tick de 60 s), son propre fichier
  (`scheduled_jobs.json`, **toutes** les planifications dans un seul fichier, en
  `open("w")`) et trois statuts sans aucun lien avec `StatutJob`
  (`planifie`/`declenche`/`annule`). Deux pièges **vérifiés en exécutant le code** :
  (1) un `scheduled_jobs.json` tronqué fait lever `_charger()` — `GET /scheduled`,
  `GET /scheduled/tous` **et** le tick de surveillance ; la boucle attrape
  l'exception, l'imprime et **continue de tourner**, donc **plus aucun job planifié
  ne se déclenche jamais**, avec pour seul signe une ligne sur stdout toutes les
  60 s et un 500 côté interface (qui ressemble à un bug d'affichage, pas à un
  planificateur mort) ; (2) `_lancer_job()` marque `declenche` **avant** de lancer,
  et **toute** exception de `demarrer_traduction()` — pas seulement un crash —
  laisse le job dans cet état : ni retenté, ni supprimé (la suppression n'a lieu
  qu'en cas de succès), et **rien ne récupère les `declenche` au démarrage**. Un lot
  planifié à 23 h sur un Ollama figé meurt donc en silence, d'autant que
  `_lancer_job()` appelle le moteur en direct et **contourne le preflight** de la
  route `/translate`. Ce qui est correct et à conserver : le rattrapage
  (`executer_a <= maintenant` → un job dont l'heure est passée pendant un arrêt part
  au démarrage) et l'auto-purge après déclenchement réussi.

**Correct par accident, à ne pas « corriger » :** `time.monotonic()` vaut
`mach_absolute_time()` sur macOS, qui **ne compte pas** le temps de veille — le
budget mural de 30 min d'`OLLAMA_RETRY_BUDGET_SECONDES` survit donc à une nuit de
sommeil (vérifié). En revanche `temps_ecoule_secondes`/ETA utilisent `time.time()`
et deviennent absurdes après une veille, et **aucune assertion d'énergie**
(`caffeinate`/`IOPMAssertion`) n'existe nulle part : un job long s'arrête dès que
le Mac s'endort. C'est l'item H, toujours ouvert. ⚠️ Pour le **différé**, l'item H
ne suffira pas : une assertion d'énergie empêche l'endormissement *pendant* le
travail, elle ne réveille pas une machine déjà endormie *pour atteindre* l'heure
planifiée (il faut un réveil programmé, `pmset schedule`/`IOPMSchedulePowerEvent`).
Tant que ça n'existe pas, une traduction programmée la nuit sur un portable ne
partira pas — et l'interface laisse croire le contraire.

**Écarts web ↔ macOS touchant la récupération du travail** (détail dans l'audit) :
`APIService.swift` jette systématiquement le code de statut HTTP
(`let (data, _) = ...`), donc le **503 du preflight Ollama et sa consigne de
redémarrage arrivent sur macOS comme une erreur de décodage générique** ; macOS
n'a **aucune liste de reprise** (lot en mémoire seulement, seul retour = taper le
chemin absolu dans le Laboratoire, lui-même en mode avancé) ; et `reprendre()`
passe `env.modeleChoisi` — le menu *courant* — au lieu du modèle du document, or
`build_output_path()` dérive le nom de sortie de `modele[:2]`, d'où un fichier
fantôme si le menu a changé. Le web ne l'a pas : il renvoie `doc.modele`.
**F4 et F6 sont corrigés depuis le 29/7** (branche `feat/architecture-cible`,
voir plus bas) ; la liste de reprise macOS aussi.

## Pertes de données réelles — quatre pièges vérifiés (29-30/7 et 18/8/2026)

Ces quatre défauts ont **détruit ou amputé du travail pour de vrai**, pas en
théorie. Tous corrigés sur `feat/architecture-cible`, tous couverts par un test
de non-régression. À lire avant de toucher à l'extraction, aux uploads ou à la
migration.

- **Un upload rejeté détruisait les traductions déjà présentes**
  (`services/uploads.py`). Le dossier d'upload est indexé sur le **contenu**
  (sha256), donc ré-uploader un document déjà traduit retombe forcément sur le
  dossier qui contient sa sortie, son cache, son état et ses images. En cas
  d'échec de la validation PDF, le code faisait `shutil.rmtree(dossier)` — il
  détruisait donc un travail sans rapport avec l'upload en cours, pour un
  fichier *identique* à celui déjà accepté. Une traduction complète de Chapter 9
  (9 minutes) a disparu ainsi. ⚠️ **Ne jamais `rmtree` un dossier d'upload** :
  ne retirer que ce que l'upload courant a écrit, et seulement si le fichier
  n'existait pas avant (`deja_present`).

- **pymupdf4llm remplaçait le texte réel par de l'OCR** (`pdf_extractor.py`).
  Depuis la version 1.28, la librairie active Tesseract **d'elle-même** dès
  qu'elle le détecte (`select_ocr_function` teste `pymupdf.get_tessdata()`, qui
  trouve le dossier Homebrew même sans `TESSDATA_PREFIX`). Sur une page
  contenant une figure, elle OCR-ise la page et **remplace son texte**. Mesuré
  sur Chapter 9 : page 4, 1 495 caractères réels (« …the famous mathematician
  **Leonhard Euler**, the field of graph theory was born… ») → 148 caractères
  d'OCR (« Map of K6nigsberg As a graph 35 ®—2 = @ … »). Une page entière du
  livre disparaissait de la traduction, et le charabia partait chez Ollama.
  ⚠️ `use_ocr=OCRMode.NEVER` est **obligatoire** et s'applique aux **deux**
  chemins (flag actif ou non) : c'est une perte de contenu, pas une option
  d'affichage. Notre extracteur `tesseract` reste disponible séparément et
  explicitement, pour les PDF scannés — c'est là que l'OCR a sa place.

- **Les images extraites n'étaient pas les figures du PDF.** `embed_images=True`
  ne rend pas les images du document mais des **rognures de l'analyse de mise en
  page** : sur Chapter 9, un demi-panneau de la figure 21 (395×311) et le simple
  fragment de texte « An example hub » (263×33) pris pour une image, tandis que
  la figure 20 (carte de Königsberg) n'était jamais extraite. On lit désormais
  les images **réellement embarquées** via PyMuPDF (`page.get_images` +
  `extract_image`), assemblées page par page — les deux figures complètes, à
  leur résolution d'origine (500×271 et 500×257).

- **La migration vers le store perdait 90 % d'un livre, en annonçant un succès**
  (`backend/scripts/migrer_vers_store.py`, 18/8). Le corps était tronqué à
  l'**annexe des liens**, supposée en fin de fichier. Elle ne l'est pas : un
  document traduit **en plusieurs passes** la voit suivie d'autres chapitres.
  Mesuré sur un livre de 716 Ko : **7 chapitres migrés sur 22**, 78 043
  caractères au lieu de 691 654 — et le script affichait « 3 documents migrés ».
  Le même défaut existait dans `translation_runner._extraire_annexe_liens()`,
  désormais borné par `_RE_MARQUEUR_CHAPITRE`. ⚠️ **Ne jamais supposer qu'un
  marqueur est en fin de fichier** parce qu'il y est *la première fois*.
  Ce défaut n'a été vu que par un **contrôle aller-retour** (régénérer depuis la
  base et comparer à l'original, au caractère près) — un simple compte de
  documents migrés le déclarait vert, et il cachait un jumeau qui *dupliquait*
  les mêmes chapitres.

**Conséquence sur la référence golden** : `tests/reference/Chapter9_*_reference.md`
encodait ces défauts, donc validait une sortie défectueuse contre une référence
défectueuse. Régénérée le 31/7 (`RESULTS_2026-07-31.txt`) — source 50 950 octets,
traduit 60 019. Après tout correctif d'extraction, **régénérer la référence**,
sinon elle fige le défaut.

## Architecture cible — branche `feat/architecture-cible` (29/7 → 20/8/2026)

Mise en œuvre des 12 principes de l'audit. Plan complet dans
[docs/architecture-cible-plan.md](docs/architecture-cible-plan.md), état dans
[docs/architecture-cible-etat.md](docs/architecture-cible-etat.md).
**Phases 1 à 7 et 9 livrées** ; reste la génération des clients (⑤, feature 328).
`pytest` : **320 verts**.

**F3 est fermé depuis la phase 9** (17-18/8, six étapes) — c'était le dernier
défaut structurel ouvert. Écrire un chapitre et avancer `chapitres_traduits`
sont désormais **une seule transaction** (`store.ecrire_chapitre_et_etat`), et
l'écriture du `.md` est idempotente.

**La lecture est store-primaire depuis le 21/8 (feature 328, terminée) — le
JSON reste écrit indéfiniment, par choix.** Correction au passage : cette
section affirmait « le store est alimenté et la migration faite », ce qui
était faux — vérifié directement, `toledo.db` avait ses 4 tables vides malgré
7 entrées dans `bibliotheque.json`. `scripts/migrer_vers_store.py --appliquer`
n'avait jamais tourné dans cet environnement. Lancée puis vérifiée par un
aller-retour **octet pour octet** (`scripts/verifier_migration_store.py`,
lecture seule — ne jamais utiliser `_regenerer_sortie` pour ça, elle écrit sur
le vrai fichier) : 3 documents réels migrés, 2/3 identiques à l'octet près, le
troisième avec un unique écart d'un saut de ligne au raccord d'une ancienne
annexe insérée en milieu de fichier (traduction en plusieurs passes,
antérieure à la phase 9) — contenu des 22 chapitres et de l'annexe retrouvé
intégralement, donc **pas** une récidive du bug des 90 %, une simple
différence de mise en forme.

Une fois la migration vérifiée, la bascule de lecture a été faite pour l'état
(`job_manager.charger_etat`) et le registre (`bibliotheque._charger`) — mais
**pas de la même façon**. Pour l'état, le store ne fait foi que s'il est **au
moins aussi récent** que le JSON (`store.lire_etat_horodate`, comparé au mtime
du `.state.json`) : comme le JSON est toujours écrit avec succès avant que le
store ne soit tenté (`sauvegarder_etat`), le store peut être en retard si une
de ses écritures a raté en silence — le préférer à l'aveugle aurait ressuscité
une progression périmée, une régression du type F3. Pour le registre, **le
store ne sert que de filet** (repli dégradé si le JSON est vide/illisible),
jamais d'une fusion façon cache : la table `documents` n'a ni `nom`, ni
`cree_a`, ni `qualite` (annotation feature 320), et une fusion « le store
gagne » aurait perdu ces champs en silence dès qu'un document existe des deux
côtés. Le cache, lui, n'a pas eu besoin d'y toucher : sa fusion
`{**json, **store}` (store gagnant sur les clés qu'il connaît) était déjà
correcte, ces trois champs n'existant pas côté cache.

⚠️ **La double écriture JSON + SQLite reste active, pour toujours** — décision
explicite : `.state.json`/`.cache.json` restent un filet lisible à la main,
leur retrait n'apporterait qu'un gain de propreté, aucune garantie nouvelle.
Seule la priorité de LECTURE a basculé.

Nouveaux modules backend, à connaître avant d'en écrire un sixième :

| Module | Rôle |
|---|---|
| `services/persistance.py` | Écriture atomique (`tmp` + `fsync` + `os.replace`) et lecture tolérante (quarantaine `.corrompu-<horodatage>` + journal). Les 8 points d'écriture JSON y passent. |
| `services/soumission.py` | **Point d'entrée UNIQUE** d'une traduction : preflight Ollama + clé d'idempotence. |
| `services/recuperation.py` | Récupération au démarrage des **quatre** familles de jobs (F7). |
| `services/energie.py` | `caffeinate` pendant le travail. Le réveil programmé (`pmset`) exige les droits admin : la commande est **rendue, jamais exécutée**. |
| `api/erreurs.py` | Erreurs typées `{code, message, remediation}`. `detail` est conservé pour compat. |
| `services/store.py` | Store SQLite (WAL, une connexion **par thread**). Porte les morceaux traduits, l'état et le registre. `ecrire_chapitre_et_etat()` fait le tout en une transaction — c'est ce qui ferme F3. |

⚠️ **`PRAGMA journal_mode=WAL` exige un verrou exclusif**, et `busy_timeout` ne
couvre PAS ce conflit-là : sans le `_verrou_ouverture` (module) qui sérialise
l'ouverture, le test à 3 threads échouait `database is locked` **1 fois sur 5**.
Le worker, le planificateur et uvicorn ouvrent tous leur connexion au démarrage,
donc simultanément. Ne pas retirer ce verrou en le croyant redondant.

⚠️ **La connexion est liée au chemin de base**, pas seulement au thread : un
changement de `CHEMIN_BASE` (tests) doit rouvrir, sinon un thread garde la base
précédente. Voir `reinitialiser_pour_tests()`.

Migration : `backend/scripts/migrer_vers_store.py`, **dry-run par défaut**,
`--appliquer` obligatoire pour écrire. ⚠️ Il a perdu 90 % d'un livre avant
correction — voir la section « Pertes de données réelles » : l'annexe des liens
**n'est pas forcément en fin de fichier**.

⚠️ **Ne JAMAIS rappeler `demarrer_traduction()` directement** depuis une route ou
le planificateur : c'est ainsi que le planificateur avait fini par contourner le
preflight (F9). Tout passe par `soumettre_traduction()`.

Changements de contrat : le statut `declenche` du planificateur **n'existe plus**
(`planifie | annule | expire | abandonne`, avec compteur de tentatives et
rattrapage borné à 24 h) ; `POST /translate` renvoie un champ `deja_soumis` ;
nouvelle route `GET /api/scheduler/sante` (dernier tick, prochaine échéance,
échéances dépassées, corruptions rencontrées) ; `GET /api/jobs/events` en SSE.

**`xcodebuild` reste absent sur cette machine** (Command Line Tools seulement,
pas Xcode.app sélectionné — `sudo xcode-select -s` demanderait le mot de passe
admin de JP). Tout le Swift continue de passer par
`swiftc -typecheck -sdk $(xcrun --show-sdk-path)`, ce qui attrape les erreurs de
type mais **pas** les erreurs de projet Xcode ni l'exécution — **builds réels
confirmés par JP dans Xcode le 5/8 puis le 20/8/2026**, voir ci-dessous.

⚠️ Le correctif macOS de la feature 341 (20/8) est **postérieur** à la dernière
confirmation : il n'a passé que `swiftc -typecheck`. Ce filet a déjà laissé
passer **deux** erreurs de build (un fichier absent du projet Xcode le 31/7, un
`import Combine` manquant le 5/8) — donc un build Xcode reste à faire.

### F8 nettoyé, message sur le réveil programmé, et un 2ᵉ piège de typecheck (5/8/2026)

Une vérification par lecture directe du code (pas seulement des docs) a confirmé
que F1, F2, F4, F6, F7, F9, F10, F11, F13 étaient déjà réellement corrigés sur
cette branche. F3 (fenêtre de duplication) reste **atténuée** par ③ mais pas
éliminée — la vraie correction exige le store transactionnel ②, laissé tel
quel (décision explicite : hors périmètre pour l'instant). **F8**, lui, ne
l'était pas : le code mort décrit plus haut était toujours présent — retiré
(4 routes backend, le polling/pause mort du module Import web, les 2 wrappers
Swift correspondants dans `APIService.swift`).

Ajouté dans la foulée, web **et** macOS, un message près du bouton « Planifier
le lot » : le Mac doit rester éveillé à l'heure prévue, le réveil automatique
programmé (`services/energie.py`, `pmset schedule wake`) reste bloqué faute de
droits admin — jusqu'ici l'interface laissait croire le contraire.

**Second piège du même genre que celui du 31/7** (fichier Swift absent du
projet Xcode, voir plus haut) : `VosTraductionsView.swift` déclarait un
`ObservableObject` avec `@Published` sans `import Combine`. Ça passait
`swiftc -typecheck` en compilant tous les fichiers ensemble (résolution
laxiste inter-fichiers) mais cassait le vrai build Xcode
(« does not conform to protocol 'ObservableObject' »). Corrigé — et vérifié
que tous les autres fichiers du projet utilisant `@Published`/`ObservableObject`
importent déjà `Combine`, celui-ci était le seul manquant. **Leçon à retenir** :
`swiftc -typecheck` multi-fichiers ne remplace toujours pas un vrai build Xcode
pour ce genre d'erreur — deuxième fois que ce filet précis laisse passer un
défaut. Build Xcode réel confirmé propre par JP le 5/8/2026, première
confirmation de ce type sur cette branche.

## Plusieurs modèles Ollama cohabitent (feature 338, 17/8/2026)

Ajouter un modèle ne demande **aucun code** : `GET /api/modeles` interroge Ollama
et le menu recopie la liste telle quelle. Aucune liste blanche nulle part —
`OLLAMA_MODELE_DEFAUT` dans `settings.py` est une constante **orpheline**,
utilisée nulle part. Un `ollama pull qwen2.5` suffit.

Mais deux défauts latents mordaient dès qu'un **second** modèle existait, c'est-à-dire
exactement dans le cas d'usage visé — comparer deux modèles sur un même document.

- **Collision des fichiers de sortie.** Le suffixe était `modele[:2]`, deux
  caractères : `llama3.1` et `llama3.2` donnaient tous deux `ll`, `qwen2.5` et
  `qwen3` donnaient `qw`. Deux modèles d'une même famille écrivaient donc dans le
  MÊME `.md`, le MÊME `.state.json` et le MÊME cache. `suffixe_modele()` produit
  désormais un slug lisible (`qwen2-5`, `llama3-1`) — lisible plutôt que haché,
  ces fichiers vivant à côté des documents de l'utilisateur.
  ⚠️ `build_output_path()` **consulte le disque** à dessein : un document traduit
  avant ce changement garde son nom historique `_traduit_ll.md`, sinon sa reprise
  repartirait de zéro dans un fichier neuf et l'ancienne deviendrait orpheline.

- **État de reprise choisi au hasard.** `_trouver_etat_existant()` renvoyait le
  PREMIER `.state.json` trouvé par un glob — un état arbitraire, dans l'ordre du
  système de fichiers. Avec deux modèles, « Reprendre » pouvait poursuivre le
  travail de l'AUTRE, et l'ajout de chapitres mélanger les deux. La fonction prend
  maintenant le modèle et ne consulte que son état, sans repli sur un voisin.

**Le choix du modèle est un réglage ordinaire**, sorti du mode avancé (web et
macOS) : on en change d'un document à l'autre. Le moteur de conversion, lui, y
reste — on n'y touche qu'en cas de PDF récalcitrant, et un mauvais choix y coûte
cher (`tesseract` sur un PDF à couche texte donnerait de l'OCR là où le texte
réel existe). Le sélecteur n'apparaît toutefois qu'une fois **un fichier ajouté
au lot** : `#zone-lot` est masqué tant que le lot est vide.

## Relecture comparative (feature 297, 17/8/2026)

Bouton « ⇄ Comparer » du bandeau de lecture : la version d'origine à gauche, la
traduction à droite, sur le **même chapitre**.

Ça ne marche que parce que les index concordent : la Bibliothèque tire ses
chapitres des marqueurs écrits par le moteur, qui portent l'index ET le titre de
la **source** (feature 327). Le chapitre n de la traduction est donc le chapitre n
de l'original. **Vérifier cet alignement avant de toucher au découpage** — sans
lui, la comparaison afficherait deux passages sans rapport.

`rendreContenu(markdown, cible)` prend une cible optionnelle : la colonne
d'origine réutilise le même moteur de rendu plutôt qu'une seconde version qui
divergerait. La source est lue via `POST /api/chapitres/contenu` sur
`chemin_source` — aucune route ajoutée.

## Fiches d'étude — deux stratégies cohabitent (18-19/8/2026)

`services/etude.py` sait produire les points clés de deux façons, et les deux
restent disponibles pour être **comparées sur un même chapitre** :

- **`condensation`** (historique) : le chapitre est condensé, puis les points
  sont tirés du condensé.
- **`sections`** (**défaut depuis le 19/8**, `STRATEGIE_PAR_DEFAUT`) : le
  chapitre est découpé, chaque section donne ses points, puis
  `consolider_points()` fusionne.

Le déclencheur était un vrai retour — « le contenu généré est trop simpliste ».
Mesuré sur Chapter 9 : `condensation` restait bloquée sur les **20 premiers pour
cent** du chapitre (7 points sur 12 décrivaient le protocole de coloration de
Cajal), là où `sections` couvre tout, en **203 s contre 400 s**. Ce n'était donc
pas un problème de modèle mais de **stratégie** — changer de modèle n'y faisait
rien.

Deux dimensionnements automatiques : `calculer_nb_points()` (3/5/8/12) et
`calculer_nb_questions()` (2/3/4/6). Côté API, **`nb_points`/`nb_questions` à 0
= automatique** ; une valeur explicite reste respectée.

⚠️ **Pièges à ne pas « corriger »** :

- `schemas.py` : `EtatJobEtude.strategie` vaut **`"condensation"`**, PAS
  `STRATEGIE_PAR_DEFAUT`. C'est le défaut de **désérialisation** des
  `.state.json` écrits avant que la stratégie n'existe — ils ont forcément été
  produits par condensation. L'aligner sur le défaut du moment relabelliserait
  d'anciennes fiches en « sections ».
- `study_runner.build_output_path()` : le repli vers le nom historique
  (`_fiche_<modele>.md`, sans stratégie) est ancré sur `STRATEGIE_CONDENSATION`,
  pour la même raison. Même piège, même conséquence.
- Le nom du fichier porte **modèle ET stratégie**
  (`<base>_fiche_<modele-slug>_<strategie>.md`) — c'est ce qui permet aux deux
  fiches de coexister. Le suffixe était `modele[:2]`, le défaut de la feature
  338 répliqué ici.
- `study_runner` compare les options (`memes_options`) et **efface
  silencieusement** les fiches déjà générées quand elles divergent. D'où la
  règle ci-dessous.

## Les options suivent le DOCUMENT, jamais les menus (F6, feature 341, 20/8/2026)

Règle générale du produit, violée quatre fois à ce jour : **toute option envoyée
au backend pour un document existant se lit sur le document** (`doc.modele`,
`doc.langue_cible`), jamais sur le menu affiché à l'écran.

Changer un menu ne doit pas changer le sort d'un travail déjà commencé. Deux
conséquences distinctes selon le domaine : en **traduction**, `build_output_path`
dérive le nom du fichier du modèle, donc un menu changé crée un fichier fantôme
(c'est F6) ; en **étude**, `study_runner` efface les fiches déjà générées.

Historique : corrigé côté web, puis sur macOS pour `reprendre()` et
`basculerPause()` le 1/8 (F6), puis **de nouveau** sur macOS pour
`genererFiche()` le 20/8 — une quatrième fonction que le correctif de F6 n'avait
pas touchée. **Corriger les appelants connus d'un défaut ne protège pas les
suivants**, et rien dans le code ne fait respecter cette règle.

**Parité macOS des fiches (feature 341)** — trois autres écarts corrigés le même
jour, tous silencieux : `strategie` n'était pas envoyée ; `nbPoints`/`nbQuestions`
étaient codés en dur à 5 et 3, donc **le dimensionnement automatique ne
s'appliquait pas** (5 points pour un chapitre de 55 000 caractères) ; et
`etudeStatut()` ne ciblait ni modèle ni stratégie, si bien que la route retombait
sur « la plus récente » et affichait **une fiche sur deux au hasard**.

⚠️ **Un paramètre optionnel omis ne se signale jamais.** Il prend le défaut du
serveur, raisonnable en général et faux ici. C'est ce qui rend cette classe de
défaut durable : rien ne casse, le résultat est seulement moins bon. Quand une
route gagne un paramètre, **vérifier les DEUX clients** — c'est toujours macOS
qui décroche, parce que le travail est fait d'abord sur le web.

## Fix — timeout client trop court sur l'analyse PDF (22/8/2026)

**Symptôme** : à l'ajout d'un document dans « Nouveau document », erreur
`⚠ signal is aborted without reason` (texte brut du navigateur) au lieu
d'un vrai message d'échec.

**Cause** : `_fetchAvecTimeout()` (`commun.js`) appliquait un timeout unique
de **15 s** à TOUTES les requêtes API, y compris `/analyser`, `/chapitres` et
`/convert` — les seules routes qui font un vrai travail (extraction complète
du texte du PDF, puis appel LLM). Or `analysis_agent.py` s'autorise lui-même
jusqu'à **60 s** pour cet appel Ollama (`_appel_llm`, `timeout=60`). Le
client abandonnait donc systématiquement avant le serveur sur un PDF un peu
long ou un modèle froid, alors que le traitement continuait derrière — zéro
rapport avec un vrai plantage du backend.

**Fix** : `API_TIMEOUT_LONG_MS` (90 s) ajouté à côté du timeout court
existant (`API_TIMEOUT_MS`, 15 s, pensé pour du polling léger — F11).
`apiPost`/`apiGet` acceptent un timeout optionnel en 2ᵉ/3ᵉ argument ; les
6 appels à `/analyser`/`/chapitres`/`/convert` (`module-import.js`,
`module-laboratoire.js`) le passent désormais explicitement. Le timeout
court reste inchangé pour tout le reste (health, feature-flags, bibliothèque…).

⚠️ **Toute nouvelle route qui fait un vrai travail d'extraction ou un appel
LLM synchrone doit utiliser `API_TIMEOUT_LONG_MS`**, jamais le défaut — le
timeout court était initialement pensé pour du polling (F11), pas pour ce
genre d'appel ponctuel plus lourd.

## Contraintes d'interface à ne pas casser

- **La barre supérieure doit rester sur UNE rangée.** Elle est `sticky` et la
  Bibliothèque calcule sa hauteur avec `calc(100vh - var(--hauteur-barre))` : une
  barre sur deux lignes décalerait toute la mise en page. Sous 800 px les onglets
  défilent *dans* la barre (`min-width: 0` est indispensable — sans lui un élément
  flex refuse de passer sous la largeur de son contenu) ; sous 590 px le texte du
  logo s'efface pour que la navigation ne soit pas écrasée.
- **`chemin_sortie` doit rester un vrai chemin sur le disque.** Le frontend en
  dérive le dossier des images (`module-bibliotheque.js`, `urlImage`). Un `.md`
  purement virtuel casserait l'affichage des images, en silence. À retenir pour la
  phase 9, où le `.md` devient un export dérivé : il doit continuer d'être écrit.
- **Le CSS et le JS sont versionnés par un paramètre d'URL** (`style.css?v=N`,
  `module-bibliotheque.js?v=N`). Sans l'incrémenter, le navigateur sert l'ancienne
  version — vérifié : une modification de CSS restait sans effet jusqu'à la bascule.
- **Le bouton du mode avancé n'a de nom accessible que par son `aria-label`.** Le
  texte « Mode avancé » voisin n'est pas un `<label>`, et il disparaît sous 800 px.

## Géré par bilbao — ne pas éditer à la main
_Bloc régénéré par le cockpit bilbao (2026-07-14). La prose hors marqueurs n'est jamais touchée._

### Roadmap (issue des feedbacks)
- [Livré] Voix TTS personnalisée (9 votes)

### Feature flags actifs
_Aucun feature flag actif._
<!-- bilbao:managed:end -->
