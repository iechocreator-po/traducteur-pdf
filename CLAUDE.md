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

L'interface web est organisée en **onglets** (`.onglet` / `.contenu-onglet` dans
`frontend/index.html`, bascule gérée dans `app.js`) au-dessus d'un en-tête partagé
(fichier source + modèle Ollama) :
- **Traduction** : traduction, chapitres, glossaire, planification, TTS, conversion.
- **Étude** : génère une fiche de révision par chapitre (N points à retenir + N questions
  de compréhension avec corrigé masqué). Backend : `services/etude.py` (appels LLM en JSON
  validé Pydantic) + `services/study_runner.py` (orchestration, état `EtatJobEtude`,
  progression à 2 étapes/chapitre), routes `POST /api/etude` et `GET /api/etude/statut`.
  Sortie `<base>_fiche_<xx>.md`. Passe par la **même file d'attente séquentielle** que la
  traduction et le TTS (un seul job LLM à la fois).

> ⚠️ L'app macOS (Swift) n'a **pas** encore la parité de l'onglet Étude : une refonte
> UX/UI de l'app native est prévue, la parité sera traitée à ce moment-là.

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
