# Traducteur PDF (toledo)

Application locale de traduction de documents PDF, propulsée par [Ollama](https://ollama.com) (modèles open source exécutés
100 % sur la machine, aucune donnée envoyée vers le cloud). TTS local, OCR, fiches d'étude, clonage vocal, app macOS.

> **Historique complet** (correctifs datés, incidents, mesures, jusqu'au 10/10/2026) :
> [`docs/journal/claude-md-complet-2026-10-31.md`](docs/journal/claude-md-complet-2026-10-31.md). Ce fichier ne garde que le
> contexte stable ; le *pourquoi* et le *quand* sont là, dans `git log` ou dans bilbao. Ne pas y rajouter de récit de session.

## Démarrage de session — features en attente

**bilbao** (`../feature-factory/`) est l'unique source de vérité des features. Présenter d'emblée celles qui ne sont ni `Livre`
ni `Rejete` (lecture directe, bilbao n'a pas besoin de tourner) :

```bash
python3 -c "
import json
d = json.load(open('../feature-factory/data/toledo/features.json'))
for f in d['features']:
    if f['statut'] not in ('Livre', 'Rejete'):
        print(f['id'], f['statut'], f['votes'], f['titre'])
"
```

Marquer une feature livrée, en créer une, assembler une release note : API de bilbao (`npm start` dans `feature-factory/`,
127.0.0.1:4600). Jamais `features.json` à la main.

## Architecture

```
traducteur-pdf/
├── backend/        # FastAPI (Python 3.13) : toute la logique métier
│   ├── app/api/        # routes HTTP (+ validation.py : garde de chemin)
│   ├── app/services/   # logique déterministe (extraction, traduction, jobs, store SQLite)
│   ├── app/agents/     # logique pilotée par LLM
│   ├── app/models/     # schémas Pydantic
│   ├── app/config/     # feature flags
│   └── tests/          # pytest
├── frontend/       # HTML/CSS/JS vanilla, sans build ; appels HTTP seulement, aucune logique métier
├── macos-app/      # app Swift/SwiftUI, même design « Workflow »
└── docs/           # décisions, plans, release notes
```

**Interface « Workflow »** (décisions : `docs/refonte-workflow-decisions.md`) : barre supérieure (thème, statuts, ⚙ Réglages),
modules chargés depuis `frontend/js/` (`commun.js` + un fichier par module) :
- **Vos traductions** : tous les documents du registre (`GET /api/bibliotheque`), progression « N/M chapitres · X/Y morceaux »,
  Pause / Reprendre / ➕ Chapitres (sélecteur inline, chapitres traduits verrouillés, flux additif) / Supprimer (retire du registre,
  fichiers disque conservés ; masqué pendant un job).
- **Nouveau document** : lot multi-fichiers, import navigateur (`POST /api/upload`) ou chemin absolu (mode avancé), analyse auto,
  réglages, lancement en file séquentielle, planification (`scheduler.py`).
- **Bibliothèque** : lecture par chapitre, audio TTS, fiches « points clés + quiz » (`services/etude.py`, `study_runner.py`,
  sortie `<base>_fiche_<xx>.md`), export HTML autonome (flag `export_fiche_html`).
- **Laboratoire** (mode avancé seulement) : état système, glossaire, TTS, voix clonées, outils document, **Scan multi-colonnes**
  (OCR par colonne des PDF scannés, sans LLM, flag `extraction_scan_colonnes`), **Chronique** (historique jetons/durées de chaque
  traitement, estimateur de durée).

**Moteur de traduction : un seul** (`translation_runner._executer_traduction`). Tout document est une liste ordonnée de chapitres
(chapitre implicite « Document entier » si aucun titre). Ne jamais réintroduire un second chemin.
- Progression au grain du sous-morceau ; **un chapitre n'est écrit que si tous ses sous-morceaux réussissent** (pas de trou).
- Retry réseau dans `translator.appeler_ollama()` (transitoire vs définitif, budget mural 30 min) ; `OllamaIndisponible` fatal
  (job reprenable) vs `OllamaErreurApplicative` (local au chapitre).
- Statut honnête : ≥ 1 `chapitres_echoues` → `erreur`, jamais `termine`.
- Reprise additive (append des chapitres restants) ou rejeu ordonné à cache chaud s'il y a des trous (cache indexé par contenu).
- Au démarrage, `recuperer_jobs_interrompus()` passe tout `en_cours` en `en_pause` (quatre familles : traduction, étude, TTS, clonage).
- Écrire un chapitre et avancer `chapitres_traduits` = **une seule transaction** (`store.ecrire_chapitre_et_etat`).

**Stockage** : store SQLite `toledo.db` prioritaire en lecture ; le JSON (`.state.json`, `bibliotheque.json`) reste écrit
indéfiniment comme filet lisible. Règles de lecture : voir piège 7 de `.claude/revue-pieges.md`. Plan et état :
`docs/architecture-cible-plan.md`, `docs/architecture-cible-etat.md`.

**Import par navigateur** (`services/uploads.py`) : octets validés par contenu, nom assaini, écrits dans
`backend/uploads/<sha256[:16]>/<nom>` (idempotent). Garde de chemin centralisée `api/validation.py` (chemin absolu, extensions
`.pdf`/`.md`) ; les deux flux (upload et chemin absolu) coexistent volontairement. Garde `Origin` sur l'upload (multipart =
CORS-safelisted). La purge ne supprime que les dossiers abandonnés (les sorties sont écrites à côté de la source).

**Extraction d'images du PDF** (flag `extraction_images_pdf`, off par défaut) : `embed_images=True` puis écriture par nos soins
dans `<base>_images/` (`write_images` plante sur un nom avec espace) ; persistance du `_converti_*.md` ; les tags image sont
masqués (`⟦IMGk⟧`) avant Ollama et restaurés ; affichage via `GET /api/image` (mode avancé + flag) ; export HTML du document
(chapitres de sommet seulement).

**Fiches d'étude** : plafonds `num_predict` mesurés (JSON 2048, condensation 4096) ; une réponse coupée au plafond lève une erreur.

**Clonage vocal** (moteur `openvoice`, web seulement) : capture micro en WAV côté client (AudioWorklet), registre
`tts_modeles/openvoice/voix_utilisateur/`, traitement en **sous-processus** dans un venv Python 3.10 séparé
(`venv_openvoice/`, incompatible avec le venv 3.13). Installation : [docs/installation-clonage-vocal.md](docs/installation-clonage-vocal.md).

**Plusieurs modèles Ollama cohabitent** : le nom de sortie porte le modèle (`suffixe_modele()`) ; les options suivent le
**document** (`doc.modele`, `doc.langue_cible`), jamais les menus.

## Réglages, mode avancé, feature flags, styles

- **Mode avancé** (`appliquerModeAvance`, `commun.js`) : `[data-avance]` + classe `.avance` sur `<html>` ; interrupteur dans
  ⚙ Réglages › Personnalisation, gated par le flag `mode_avance`.
- **Feature flags** (`backend/app/config/feature_flags.py`, `GET /api/feature-flags`) : priorité `FLAGS_PAR_DEFAUT` →
  `bilbao.features.json` (artefact de bilbao, à committer) → `feature_flags.json` local → variables `FEATURE_<NOM>`.
- **Styles visuels** (⚙ Réglages → « Style de l'interface », 6 styles) : web `<html data-style>` + `css/styles-visuels.css`
  (chargé après `theme-toledo.css`), grille `js/reglages-styles.js` ; macOS `StylesVisuels.swift`. Ajouter un style = tokens ×3,
  `.apercu-<id>`, id dans `STYLES_IDS` et `STYLES_VISUELS`, `case` Swift. Polices système seulement (jamais de CDN).
- **Repères d'interface** : lettre par module, lettre+numéro par carte, dérivés du DOM, OFF par défaut ; sur macOS le numéro
  d'une carte est écrit à la main (seul identifiant en dur du portfolio).

## Installer, lancer, tester

Prérequis : Ollama lancé avec au moins un modèle (`ollama pull llama3.1`), Python 3.10+, Tesseract facultatif
(`brew install tesseract tesseract-lang`).

```bash
cd backend && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cd backend && ./venv/bin/python -m uvicorn app.main:app --port 8000     # API, doc sur http://localhost:8000/docs
cd frontend && python3 -m http.server 5500                              # interface sur http://localhost:5500
cd backend && ./venv/bin/python -m pytest tests/ -q                     # tests
```

- Configurations `traducteur-backend` / `traducteur-frontend` dans `.claude/launch.json`. Le backend y tourne **sans `--reload`** :
  **après toute modification du backend, le relancer**, sinon l'ancien code reste actif alors que `pytest` passe.
- Avec `--reload`, `uvloop 0.22.1` peut faire crasher le worker (SIGSEGV, MagicStack/uvloop#706) : ajouter `--loop asyncio`.
- Après une modification de `pdf_extractor.py`, supprimer aussi le `_converti_*.md` du document testé (il est relu tel quel).
- Tests manuels bout-en-bout (vrai PDF via Ollama) : `tests/test_pdf_translation.py <pdf> [--test T1|T2|T3|all]`, doc dans
  `tests/README_TESTS.md`.
- macOS : vérifié surtout par `swiftc -typecheck` ; un build Xcode reste la vraie vérification.

## Design system

Copie vendorée (`frontend/css/tokens.css` + `components.css`) chargée avant le CSS applicatif ; variables sémantiques
seulement, **jamais de hex en dur** (sauf palettes de `styles-visuels.css` et pastilles d'aperçu). L'accent vert d'eau de
toledo est une surcharge locale (`theme-toledo.css`), **jamais une retouche de `tokens.css`** (surveillé par bilbao). macOS :
`macos-app/Theme.swift` (énumération `DS`, couleurs dynamiques, `.tint(DS.accent)`) ; utiliser `DS.*`, pas `.green`/`.red` bruts.
Resynchroniser : recopier `tokens.css` et réaligner `Theme.swift`.

## Contraintes d'interface à ne pas casser

- **La barre supérieure reste sur UNE rangée** (la Bibliothèque calcule sa hauteur avec `--hauteur-barre`) ; sous 800 px les
  onglets défilent dans la barre (`min-width: 0` indispensable), sous 590 px le texte du logo s'efface.
- **`chemin_sortie` reste un vrai fichier sur le disque** : le frontend en dérive le dossier des images.
- **CSS et JS versionnés par paramètre d'URL** (`style.css?v=N`) : l'incrémenter, sinon le navigateur sert l'ancienne version.
- Les interrupteurs `.ui-switch` n'ont de nom accessible que par l'`aria-label` de leur `<label>`.

## Pièges et revue de code

Les pièges vécus (moteur unique, options sur le document, uploads indexés par contenu, marqueurs pas forcément en fin de
fichier, écriture atomique, double source JSON/SQLite, plafonds Ollama, `use_ocr=OCRMode.NEVER`, planificateur, récupération
au démarrage, timeouts clients, « un 0 qui ment », `.gitignore` ancré, données et secrets) sont dans
[`.claude/revue-pieges.md`](.claude/revue-pieges.md), lu par `/revue-code` (zones `moteur`, `extraction`, `etude-tts`, `api`,
`frontend`, `macos`). Mécanique : `cd backend && ./venv/bin/python -m pytest tests/ -q`.

À retenir hors de ce fichier : **l'extraction texte d'un PDF ne rend que le texte** (pictogrammes, tableaux en image perdus) ;
**un test sur un extrait ne valide pas une heuristique de document** (Scan multi-colonnes : une règle juste sur 3 pages abîmait
le livre complet).

<!-- bilbao:managed:start -->
## Géré par bilbao — ne pas éditer à la main
_Bloc régénéré par le cockpit bilbao (2026-07-14). La prose hors marqueurs n'est jamais touchée._

### Roadmap (issue des feedbacks)
- [Livré] Voix TTS personnalisée (9 votes)

### Feature flags actifs
_Aucun feature flag actif._
<!-- bilbao:managed:end -->
