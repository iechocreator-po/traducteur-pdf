# Roadmap des fonctionnalités

Document de travail en attendant la mise en place d'un vrai tableau kanban
(Trello, GitHub Projects, Focalboard, ou autre — décision en cours).

## Légende des statuts
- 🔲 À faire
- 🔶 En cours
- ✅ Terminé

---

## Fonctionnalités proposées par Jean-Pierre

| # | Fonctionnalité | Statut | Notes |
|---|---|---|---|
| 1 | Pause / reprise de traduction | 🔲 | Mécanisme prévu : fichier d'état JSON à côté du `.txt` de sortie, notant la dernière section complétée. `job_manager.py` déjà en place dans le squelette. |
| 2 | Langues anglais / français / espagnol (entrée + sortie) | 🔲 | Simple à ajouter : menus déroulants déjà présents dans le frontend (`index.html`). |
| 3 | Programmation différée (traduction de nuit) | 🔲 | Approche simple d'abord (champ horaire dans l'app) avant d'envisager `launchd`. |
| 4 | Préservation des liens URL | 🔲 | Extraire les annotations de liens séparément via `pdfplumber`, les lister en fin de section plutôt que de les replacer dans le texte traduit. |
| 5 | Analyse préliminaire des 5 premières pages | 🔶 | Route `/api/analyser` et `analysis_agent.py` déjà en place dans le squelette — logique d'analyse à enrichir. |

## Fonctionnalités proposées par Claude

| # | Fonctionnalité | Statut | Notes |
|---|---|---|---|
| A | Détection automatique de la langue source | 🔲 | Librairie légère type `langdetect`. |
| B | Estimation du temps total avant de lancer | 🔲 | Dépend de l'analyse préliminaire (#5). |
| C | Journal d'erreurs/avertissements par section | 🔲 | Continuer le traitement même si une section échoue ; lister les échecs à la fin. |
| D | Glossaire de termes à ne pas traduire | 🔲 | Utile pour noms propres, acronymes, termes Quatre-Chemins.org. |
| E | Mode relecture comparative (côte à côte) | 🔲 | Affichage anglais/français en parallèle pour validation rapide. |

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
