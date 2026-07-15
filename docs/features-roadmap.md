# Roadmap des fonctionnalités

Document de travail en attendant la mise en place d'un vrai tableau kanban
(Trello, GitHub Projects, Focalboard, ou autre — décision en cours).

## Légende des statuts
- 🔲 À faire
- 🔶 En cours
- ✅ Terminé

---

## Fonctionnalités proposées par Jean-Pierre

| # | ID | Fonctionnalité | Statut | Notes |
| --- | --- | --- | --- | --- |
| 1 | 63 | Pause / reprise de traduction | ✅ | Fichier `.state.json` à côté de la sortie ; boutons Pause/Continuer/Reprendre (web + app macOS). |
| 2 | 64 | Langues anglais / français / espagnol (entrée + sortie) | ✅ | Menus déroulants dans le frontend et l'app Swift. |
| 3 | 65 | Programmation différée (traduction de nuit) | ✅ | `scheduler.py` + `scheduled_jobs.json`, vues Swift ScheduleSheet/ScheduledJobsView. |
| 4 | 66 | Préservation des liens URL | ✅ | Annexe « Liens du document original » en fin de fichier traduit et de conversion (dédoublonnée, une seule fois au re-run). |
| 5 | 67 | Analyse préliminaire des 5 premières pages | ✅ | Route `/api/analyser` : langue détectée, nb de chunks, durée estimée, avertissements, recommandation. |
| 6 | 68 | Indicateur Ollama vert/rouge dans l'UI | ✅ | Bouton 🔄 Reconnecter (retry auto 30 s), re-vérification backend/Ollama juste avant chaque lancement de traduction. |
| 7 | 69 | Choix de fichier sans toggle PDF/Markdown | ✅ | Un seul champ — le type est détecté par l'extension, les options s'adaptent. |
| 8 | 70 | Planification multi-fichiers avec liste et statuts | ✅ | `POST /api/schedule/batch`, section « 5. Planification » : tableau fichier / planifié pour / statut réel / retirer. |
| 9 | 71 | Fiche d'étude par chapitres (points à retenir + questions) | ✅ | Onglet « Étude » : sélection de chapitres, N points à retenir + N questions de compréhension avec corrigé masqué (`<details>`), langue de la fiche configurable. Route `POST /api/etude`, `study_runner.py` + `etude.py`, sortie `_fiche_xx.md`, progression 2 étapes/chapitre, pause/annulation/reprise via la file d'attente. |

## Fonctionnalités proposées par Claude

| # | ID | Fonctionnalité | Statut | Notes |
| --- | --- | --- | --- | --- |
| A | 72 | Détection automatique de la langue source | ✅ | Faite pendant l'analyse préliminaire (`analysis_agent.py`). |
| B | 73 | Estimation du temps total avant de lancer | ✅ | Affichée dans l'analyse + confirmation avant lancement ; temps restant pendant le job. |
| C | 74 | Journal d'erreurs/avertissements par section | ✅ | `.errors.log` à côté de la sortie ; le job continue malgré les échecs de section. |
| D | 75 | Glossaire de termes à ne pas traduire | ✅ | Section « 4. Glossaire » (un terme par ligne), injection dans le prompt, vérification post-traduction avec avertissement. |
| E | 76 | Mode relecture comparative (côte à côte) | 🔲 | Affichage anglais/français en parallèle pour validation rapide. |
| F | 77 | Nettoyer les artefacts VAD d'OpenVoice | 🔲 | `se_extractor` (clonage vocal) écrit ses segments dans `backend/processed/` (CWD du sous-processus) et ne les nettoie pas — ça s'accumule à chaque voix clonée. Faire écrire `openvoice_extract.py` dans un dossier temporaire supprimé après extraction (paramètre `target_dir` de `get_se`). Ignoré du repo pour l'instant (`.gitignore`). |

## Livré en plus (delta v2 → v4)

| Fonctionnalité | Notes |
|---|---|
| Traduction par chapitres | Sélection via signets PDF / TOC, route `/api/chapitres`. |
| Extracteurs PDF multiples | pymupdf4llm / marker / tesseract (OCR), configurable. |
| Détection de couche texte corrompue | L'analyse détecte les PDF « muets » (police sans ToUnicode, ex. export Aperçu) et recommande l'OCR Tesseract. |
| Conversion PDF → Markdown seule | Route `/api/convert`. |
| App macOS native (Swift) | `macos-app/itraducteur-pdf.xcodeproj`, polling 2 s. |
| File d'attente séquentielle | Worker unique dans `job_manager.py`, statut `en_attente`. |
| Annulation d'un job | Route `POST /api/job/{id}/annuler`, boutons web + macOS, reprise possible. |
| Contrôle qualité anti-résumé | Ratio < 0.5 sur textes ≥ 200 car., 1 retry, avertissements dans l'état du job. |
| Cache de chunks | `cache_traduction.py` (SHA-256), résultats suspects non mis en cache. |
| Text-to-Speech local | Moteurs Piper (rapide) / Kokoro (qualité), dropdowns moteur+voix ; extrait à écouter et génération audio d'un `.md` complet via la file d'attente. `tts.py`, `tts_runner.py`, routes `/api/tts/*`. |
| Refonte web « Workflow » (3 modules) | Design toledo_v2 : Nouveau document (lot multi-fichiers, analyse auto, mode avancé) / Bibliothèque (lecture par chapitre, barre audio, panneau IA points clés + quiz) / Laboratoire (config, outils, teasers voix personnalisées et export PDF avec capture d'intérêt `POST /api/interet`). |
| Refonte macOS « Workflow » | Même design 3 modules en SwiftUI : drag & drop natif + NSOpenPanel multi-fichiers, Bibliothèque avec lecteur AVAudioPlayer (WAV lu du disque) et panneau IA, Laboratoire avec teasers (confirmationDialog + email). Moteur/voix TTS partagés via @AppStorage. |
| CI GitHub Actions | pytest + ruff à chaque push. |

---

## Décisions d'architecture déjà prises

- **Backend** : FastAPI + Pydantic AI + pytest
- **Frontend** : Web (HTML/CSS/JS), pas de framework de build, ouvrable sur Mac et PC
- **Agents IA** : Pydantic AI, avec Ollama en local (pas de dépendance cloud)
- **Tests** : pytest, lancés automatiquement via GitHub Actions à chaque push
- **Feature flags** : fichier de config simple pour commencer (`feature_flags.py`)

## Prochaine étape suggérée

Mettre en place un vrai outil de suivi (kanban) pour remplacer ce tableau texte,
une fois le choix d'outil fait (GitHub Projects, Focalboard, ou tableau sur mesure).
