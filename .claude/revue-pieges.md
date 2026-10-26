# Pièges de toledo (lus par la skill `/revue-code`)

Fichier propre à ce produit. La méthode (passes, notes, rapport) est dans la skill commune
`2000_DigitalProducts/skills/revue-code/`. Chaque piège ci-dessous est une erreur **réellement arrivée** ici
ou relevée par l'audit de la zone 1 (26/10/2026) ; les détails sont dans `CLAUDE.md` (chercher avec `grep`).

## Commandes mécaniques

Dossier de travail : `traducteur-pdf/backend/`. Un échec est un constat à 100.

```bash
./venv/bin/python -m pytest tests/ -q      # 389 tests au 26/10/2026 ; le pytest global échoue (pdfplumber absent hors venv)
```

- Ne pas démarrer le backend ni Ollama, ne pas lire `backend/toledo.db`, `bibliotheque.json`, `uploads/` ni les `.state.json`
  réels : la suite isole le store (`conftest.store_isole`), pas le reste.
- Frontend : pas de suite de tests. Si le diff touche `frontend/js/` ou `frontend/css/`, vérifier que `?v=N` est incrémenté
  dans `index.html` pour chaque fichier modifié.
- Swift : `swiftc -typecheck -sdk $(xcrun --show-sdk-path)` attrape les erreurs de type mais **pas** celles du projet Xcode
  (fichier absent du projet, `import Combine` manquant) : à signaler dans « Non vérifié ».

## Avant de pousser (bloc du rapport avant-push)

- Le backend tourne **sans `--reload`** : relancer uvicorn (et supprimer tout `_converti_*.md` obsolète du document testé).
- `?v=N` incrémenté sur le CSS/JS modifié : oui/non.
- Parité macOS : une route qui gagne un paramètre doit être appelée avec ce paramètre par **les deux clients** (web et `APIService.swift`).
- Repères macOS (`.repere("C3")`) à renuméroter si une carte est insérée au milieu d'un module.
- Build Xcode réel à faire par JP si du Swift est touché.
- Référence golden (`tests/reference/Chapter9_*`) à régénérer si l'extraction ou le découpage change.

## Pièges (passe A)

1. **Un seul moteur d'exécution, un seul point d'entrée.** `translation_runner._executer_traduction` est l'unique moteur ;
   toute soumission passe par `soumission.soumettre_traduction` (preflight Ollama + idempotence). Un appel direct à
   `demarrer_traduction` depuis une route ou le planificateur contourne le preflight (défaut F9).
2. **Options lues sur le DOCUMENT, jamais sur un menu.** `doc.modele`, `doc.langue_cible` : changer un menu ne doit pas changer
   le sort d'un travail commencé (fichier fantôme en traduction, fiches effacées en étude). Violé quatre fois. Un paramètre
   optionnel omis ne se signale jamais : vérifier les **deux** clients.
3. **Ne jamais `rmtree` un dossier d'upload.** Il est indexé sur le contenu (sha256) : il contient peut-être la sortie, le cache,
   l'état et les images d'un autre travail. Ne retirer que ce que l'upload courant a écrit.
4. **Ne jamais supposer qu'un marqueur est en fin de fichier** (annexe des liens, en-tête) parce qu'il l'est la première fois :
   une traduction en plusieurs passes le fait suivre d'autres chapitres. Borner par `_RE_MARQUEUR_CHAPITRE`.
5. **Nom de sortie par modèle.** `suffixe_modele()` donne un slug (`llama3-1`) ; l'ancien nom `modele[:2]` n'est repris par
   `build_output_path` que si l'état porté appartient au même modèle. Tout code qui cherche un état ou une sortie sans passer
   le modèle prend un état arbitraire d'un autre modèle.
6. **Écriture atomique.** Tout JSON ou tout fichier qui contient tout un travail passe par `persistance.ecrire_json_atomique` /
   `ecrire_texte_atomique` (tmp + fsync + `os.replace`), jamais `open(..., "w")`. Lecture par `lire_json_tolerant`.
   Connu et non corrigé : `_mettre_a_jour_entete_chapitres` réécrit tout le `.md` en `open("w")` après chaque chapitre.
7. **Double source de vérité JSON / SQLite.** État : le store ne fait foi que s'il est au moins aussi récent que le JSON.
   Registre : le JSON gagne, le store n'est qu'un filet. Toute suppression doit être propagée aux DEUX (cas
   `retirer_document`, 26/10) ; les tables `morceaux` et `chapitres` n'ont toujours aucun DELETE.
8. **Chapitre = unité atomique.** Un chapitre n'est ajouté à `chapitres_traduits` qu'une fois écrit ; un échec le met dans
   `chapitres_echoues`. Le statut `termine` est impossible avec un chapitre en échec. Les tags images `![](…)` ne partent jamais
   chez Ollama (sentinelles `⟦IMGk⟧`).
9. **Ollama : plafonds et contrôle qualité.** Tout appel Ollama fixe `num_predict` (traduction 2048, fiche JSON 2048,
   condensation 4096 ; ne pas baisser sans remesurer) et `num_ctx` ; une réponse coupée (`done_reason == "length"`) lève une
   erreur au lieu d'être rendue comme complète. `OLLAMA_NUM_CTX` suit `CHAPITRE_SOUS_CHUNK_TAILLE_MAX`.
10. **Extraction PDF.** `use_ocr=OCRMode.NEVER` obligatoire dans pymupdf4llm (il remplaçait le texte réel par de l'OCR).
    Images lues par `page.get_images`, pas par `embed_images`. Le texte extrait ne rend que le texte : ne jamais conclure
    « ce n'est pas là » sans regarder la page, ou écrire « non vérifié ».
11. **Planificateur.** Statuts `planifie | annule | expire | abandonne` ; rattrapage borné à 24 h ; toute valeur venant de
    l'API (langues, modèle, date) est validée **à la création**, pas à l'échéance de nuit. Une erreur non transitoire ne
    consomme pas les tentatives. `energie.py` rend la commande `pmset`, ne l'exécute jamais.
12. **Récupération au démarrage.** Les quatre familles (traduction, étude, TTS, clonage) doivent être récupérées ; une famille
    qui dépend du registre rate les documents retirés.
13. **Timeouts clients.** Toute route qui fait un vrai travail d'extraction ou un appel LLM synchrone utilise
    `API_TIMEOUT_LONG_MS`, jamais le timeout court de polling (`/analyser`, `/chapitres`, `/convert`, `/translate`).
14. **Un 0 qui ment.** Mesure absente = `NULL` / « — », jamais 0 (Chronique, estimations, progression).
15. **Design system.** Pas de modification de `tokens.css` (surcharge dans `theme-toledo.css`) ; pas de hex en dur ; nouveau
    bouton, carte ou champ créé en JS avec les classes `.ds-*` ; polices en piles système (jamais de CDN : app 100 % locale).
16. **`.gitignore`.** Un motif de dossier sans `/` de tête attrape aussi les dossiers enfants ; vérifier par
    `git check-ignore -v`.
17. **Données et secrets.** Rien de `uploads/`, `toledo.db`, `.state.json`, voix clonées, corpus `test_integre/` dans le code ou
    les tests ; les tests n'écrivent jamais dans la vraie base.

## Zones d'audit

Chemins relatifs à `traducteur-pdf/`. Plafond d'environ 3 000 lignes par sous-agent.

| Zone | Chemins | Lignes |
|---|---|---|
| `moteur` | `backend/app/services/{translation_runner,translator,store,persistance,job_manager,cache_traduction,recuperation,scheduler,soumission,energie,bibliotheque}.py` | ~3 000 (auditée le 26/10/2026) |
| `extraction` | `backend/app/services/{pdf_extractor,scan_colonnes,segmentation,uploads}.py` | ~1 900 |
| `etude-tts` | `backend/app/services/{etude,study_runner,tts,tts_runner,voix_clonees,voix_clonage_runner,stats,metriques,glossaire}.py`, `backend/app/agents/` | ~2 300 |
| `api` | `backend/app/api/`, `backend/app/config/`, `backend/app/models/`, `backend/app/main.py`, `backend/scripts/` | ~2 000 |
| `frontend` | `frontend/js/`, `frontend/index.html`, `frontend/css/` | ~4 500 (2 sous-agents) |
| `macos` | `macos-app/` | ~3 800 |

Dans la zone `frontend`, vérifier aussi la parité avec `macos-app/` pour chaque route appelée.
