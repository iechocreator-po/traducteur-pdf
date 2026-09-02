# Plan d'implémentation — architecture cible (12 principes)

Branche : `feat/architecture-cible`, partie de `feat/extraction-images-pdf`.
Source : l'audit du 27/7/2026 + addendum différé du 28/7
([frontend/docs/architecture-messagerie.html](../frontend/docs/architecture-messagerie.html)).

## Règle de conduite

**La branche doit rester fonctionnelle et la suite `pytest` verte après chaque
phase.** Les 12 principes ne sont pas de même taille : ③⑦⑧⑨⑩⑪⑫ sont des
chantiers bornés et vérifiables en test automatisé, ① et ② touchent le cœur du
moteur, ④ et ⑤ passent par du Swift qu'on ne peut pas compiler sur cette machine
(`xcodebuild` absent — validation par `swiftc -typecheck` seulement, build final
par JP dans Xcode). L'ordre ci-dessous va donc du plus sûr au plus risqué, et non
de ① à ⑫.

## Ordre d'exécution

| Phase | Principes | Risque | Vérifiable ici |
|---|---|---|---|
| 1 — Durabilité des écritures | ③ ⑦ | faible | oui, `pytest` |
| 2 — Le différé replié dans le job | ⑨ ⑪ ⑫ | faible | oui, `pytest` |
| 3 — Idempotence de soumission | ⑧ | faible | oui, `pytest` |
| 4 — Récupération généralisée | ① (partiel) | moyen | oui, `pytest` |
| 5 — Énergie | ⑩ | moyen | partiellement |
| 6 — Push serveur | ⑥ | moyen | oui (SSE) + front |
| 7 — Client sans état de job | ④ | moyen | web oui, macOS non |
| 8 — Clients générés | ⑤ | élevé | non (pas de build Swift) |
| 9 — Store transactionnel | ② ① (complet) | **élevé** | oui mais gros |

---

## Phase 1 — Durabilité des écritures (③ ⑦)

**Problème** : six emplacements JSON écrits en `open("w")` — troncature puis
réécriture. Une coupure dans cette fenêtre laisse un fichier illisible. Selon le
fichier touché, la conséquence va de la perte silencieuse (cache) à la
disparition de toute la Bibliothèque (500 sur `GET /api/bibliotheque`, F1) ou à
la mort définitive du planificateur (F13).

**Livrables**
1. `app/services/persistance.py` — `ecrire_json_atomique()` (fichier temporaire
   dans le même répertoire + `os.replace()`, atomique sur APFS) et
   `lire_json_tolerant()` (retourne `None` + met le fichier corrompu en
   quarantaine `.corrompu-<horodatage>` au lieu de lever).
2. Bascule des 8 points d'écriture : `job_manager`, `cache_traduction`,
   `bibliotheque`, `scheduler`, `glossaire`, `voix_clonees`, `study_runner`,
   `tts_runner`.
3. Lectures tolérantes là où la corruption est aujourd'hui fatale :
   `charger_etat`, `lister_documents` (garde **par document**), `scheduler._charger`.
4. Erreurs typées `{code, message, remediation}` (⑦) — modèle `ErreurAPI`,
   gestionnaire d'exception FastAPI, et le 503 du preflight Ollama porte enfin sa
   consigne dans un champ structuré plutôt que dans une chaîne que seul le web
   sait lire.

**Preuve attendue** : tests qui tronquent réellement chaque fichier et vérifient
que l'API répond encore.

## Phase 2 — Le différé replié dans le modèle de job (⑨ ⑪ ⑫)

**Problème** : `_lancer_job()` appelle `demarrer_traduction()` en direct, sans le
preflight (F9) ; `declenche` est un cul-de-sac dont rien ne sort ni ne récupère ;
une boucle morte est invisible (F13).

**Livrables**
1. `app/services/soumission.py` — **le** point d'entrée unique
   (`soumettre_traduction()`) : preflight, validation, journal. La route
   `/translate`, le planificateur et la reprise l'empruntent tous.
2. Statuts de planification alignés : `declenche` disparaît au profit d'un retour
   en `planifie` avec compteur de tentatives, plus deux états terminaux
   **explicites** — `expire` (échéance dépassée au-delà de la borne de rattrapage)
   et `abandonne` (limite de tentatives atteinte).
3. Rattrapage **borné** : au-delà de `SCHEDULER_RATTRAPAGE_MAX_HEURES`, on expire
   au lieu d'exécuter en silence une planification vieille de trois semaines.
4. Santé du planificateur (⑫) : dernier tick réussi, prochaine échéance, nombre en
   attente, dernière erreur — exposés par `GET /api/scheduler/sante`, et le tick
   qui lève devient un état signalé au lieu d'une ligne sur stdout.

## Phase 3 — Idempotence de soumission (⑧)

Clé dérivée de `(source, options, portée)`. Rejouer la même requête retourne le
job existant au lieu d'en créer un second. C'est aussi ce qui rendra un retry
client sûr, donc ce qui rend la phase 6 implémentable sans risque.

## Phase 4 — Récupération généralisée (① partiel)

`recuperer_jobs_interrompus()` ne connaît que la traduction : un job d'étude coupé
reste `en_cours` pour toujours et bloque définitivement le panneau Résumé & Quiz du
document (F7). Généralisation à l'étude, au TTS et au clonage — sans encore fusionner
les modèles (c'est la phase 9), en factorisant la logique de balayage.

## Phase 5 — Énergie (⑩)

- **Assertion d'énergie** : `caffeinate -i` pris à l'entrée du worker, relâché
  quand la file se vide. Aucun privilège requis.
- **Réveil programmé** : ⚠️ `pmset schedule wake` **exige les droits admin**. Il
  ne sera donc pas armé automatiquement par le backend. Le plan retenu : exposer
  la commande exacte à exécuter, et surtout **prévenir dans l'interface au moment
  où l'on planifie** qu'une traduction programmée ne partira pas si la machine
  dort — aujourd'hui l'interface laisse croire le contraire.

## Phase 6 — Push serveur (⑥)

`GET /api/jobs/events` en SSE remplace six boucles `setInterval` aux cadences et
conditions d'arrêt divergentes. Le poll reste en repli, avec `AbortController` et
recul exponentiel (F11).

## Phase 7 — Client sans état de job (④)

Porter « Vos traductions » sur macOS : le lot en mémoire disparaît, les deux
clients projettent la même liste. Le backend est déjà prêt (`GET /jobs/reprenables`
existe et n'a aucun client). **Non compilable ici** — livré en `swiftc -typecheck`,
build final par JP.

## Phase 8 — Clients générés (⑤)

Génération depuis `/openapi.json`. F4 (macOS jette les codes de statut HTTP) et F6
(reprise avec le mauvais modèle) sont des bugs de client écrit à la main. À défaut
de la génération complète, corriger d'abord F4 et F6 à la main — bénéfice immédiat,
risque nul.

## Phase 9 — Store transactionnel (② + ① complet)

SQLite en mode WAL, une ligne par morceau traduit ; le `.md` devient un export
régénéré à la demande. Écrire le contenu et avancer la progression deviennent une
seule transaction : F3 (duplication) et la corruption partielle disparaissent par
construction. **C'est le seul vrai chantier**, et il n'est pas nécessaire pour
tenir la promesse de durabilité — les phases 1 à 5 le sont.

---

## Ce que chaque phase règle

| Défaut | Réglé en phase |
|---|---|
| F1 — état tronqué → 500 sur toute la Bibliothèque | 1 |
| F2 — cache perdu en silence | 1 |
| F13 — planificateur mort en silence | 1 + 2 |
| F9 — preflight contourné, `declenche` sans retry | 2 |
| F10 — double soumission | 3 |
| F7 — étude/TTS jamais récupérés | 4 |
| F12 — veille de la machine | 5 (partiel : voir la limite `pmset`) |
| F11 — ni timeout ni recul côté client | 6 |
| F5 — macOS sans chemin de retour | 7 |
| F4, F6 — bugs de client écrit à la main | 8 |
| F3 — duplication chapitre/état | 9 |
| F8 — code mort | transverse (supprimé au passage) |
