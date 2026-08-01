# Architecture cible — état des travaux

Branche : `feat/architecture-cible` (partie de `feat/extraction-images-pdf`).
Dernière mise à jour : 2026-08-01.
Plan complet : [architecture-cible-plan.md](architecture-cible-plan.md).

## Où on en est

| Phase | Principes | État | Vérifié comment |
|---|---|---|---|
| 1 — Durabilité des écritures | ③ ⑦ | **fait** | `pytest`, fichiers réellement tronqués |
| 2 — Différé replié dans le job | ⑨ ⑪ ⑫ | **fait** | `pytest` |
| 3 — Idempotence | ⑧ | **fait** | `pytest` |
| 4 — Récupération généralisée | ① partiel | **fait** | `pytest` |
| 5 — Énergie | ⑩ | **fait sauf réveil** | vrai `caffeinate` lancé |
| 8 — F4 / F6 macOS | ⑤ partiel | **fait** | `swiftc -typecheck` |
| 6 — Push serveur SSE | ⑥ | **fait** | navigateur : flux établi, poll éteint |
| 7 — « Vos traductions » sur macOS | ④ | **fait** | `swiftc -typecheck` seulement |
| 8 — Clients générés (codegen) | ⑤ | à faire | — |
| 9 — Store transactionnel SQLite | ② ① complet | à faire | — |

Suite `pytest` : **267 verts** (231 au départ, +36). L'app démarre, toutes les
routes répondent 200.

**Trois bugs de perte de données ont été trouvés et corrigés en chemin** — ils ne
faisaient pas partie du plan et sont détaillés dans le `CLAUDE.md` du produit :
un upload rejeté qui détruisait les traductions déjà présentes, l'OCR de
pymupdf4llm qui remplaçait le texte des pages illustrées, et des images extraites
qui n'étaient pas les figures du PDF.

**Piège de build macOS (31/7)** : `VosTraductionsView.swift` avait bien été créé
mais **pas ajouté au projet Xcode**, qui liste ses fichiers explicitement malgré
la mention « groupes synchronisés » du `CLAUDE.md`. `swiftc -typecheck` passait
(on lui donne la liste des fichiers à la main) mais le build Xcode échouait sur
« Cannot find 'VosTraductionsView' in scope ». Tout nouveau fichier Swift doit
être ajouté aux **4 emplacements** de `project.pbxproj` : PBXBuildFile,
PBXFileReference, `children` du groupe, et phase `Sources`.

## À tester demain matin

```bash
cd backend && source venv/bin/activate && uvicorn app.main:app --port 8000
```

1. **La nouvelle route de santé** — `curl -s localhost:8000/api/scheduler/sante | python3 -m json.tool`.
   Elle répond `thread_vivant`, `dernier_tick_reussi`, `prochaine_echeance`,
   `echeances_depassees`, `en_panne` et `corruptions`.
2. **L'erreur typée** — arrêter Ollama (`killall ollama`) puis lancer une
   traduction : le 503 porte maintenant
   `{"erreur": {"code": "ollama_indisponible", "remediation": "…"}}` en plus de
   `detail`.
3. **La durabilité** — tronquer un `.state.json` à la main au milieu du fichier :
   la Bibliothèque doit continuer de répondre 200 et de lister les AUTRES
   documents. Avant, elle renvoyait 500 et tout disparaissait.
4. **L'énergie** — pendant une traduction, `pmset -g assertions | grep -i
   PreventUserIdleSystemSleep` doit montrer une assertion active ; elle doit
   disparaître quand la file se vide.
5. **macOS** — le build doit être refait dans Xcode (voir la limite ci-dessous),
   puis vérifier qu'Ollama arrêté produit un message lisible avec la consigne de
   redémarrage, au lieu d'une erreur de décodage.

## Ce qui n'a PAS été vérifié ici

- **Le build macOS.** `xcodebuild` n'est pas disponible sur cette machine
  (Command Line Tools uniquement, pas Xcode). Les 11 fichiers Swift passent
  `swiftc -typecheck -sdk $(xcrun --show-sdk-path)`, ce qui attrape les erreurs
  de type et de signature — **pas** les erreurs de projet Xcode, de ressources ou
  d'exécution. Le build reste à faire par JP.
- **Aucune vraie traduction de bout en bout.** Rien n'a été passé à travers
  Ollama sur cette branche. Les tests couvrent la mécanique (persistance,
  planification, récupération, idempotence), pas la qualité d'une traduction.
  Avant de fusionner, refaire tourner
  `tests/test_pdf_translation.py` et `tests/validate_translation.py`.
- **Le réveil programmé (principe ⑩, moitié manquante).** `caffeinate` empêche
  l'endormissement *pendant* le travail ; il ne réveille pas une machine déjà
  endormie *pour atteindre* 23 h. `pmset schedule wake` exige les droits admin,
  donc le backend ne l'arme pas. `energie.commande_reveil_programme()` rend la
  commande sans jamais l'exécuter. **Conséquence à assumer aujourd'hui : une
  traduction planifiée la nuit ne partira pas si le Mac dort**, et l'interface ne
  le dit toujours pas — c'est le premier geste de la phase 6/7.

## Changements de contrat à connaître

- **`declenche` n'existe plus** dans `scheduled_jobs.json`. Les statuts sont
  `planifie | annule | expire | abandonne`. Un fichier existant avec des
  `declenche` restera affiché tel quel mais ne sera plus produit ; rien ne casse.
- **`POST /translate` renvoie un champ de plus** : `deja_soumis` (booléen). Les
  clients existants l'ignorent sans problème.
- **Les erreurs gardent `detail`** en plus du nouveau `erreur` — le frontend web
  actuel continue de fonctionner sans modification.
- **`recuperer_jobs_interrompus()` n'est plus appelée directement** par
  `main.py` : elle passe par `recuperation.recuperer_tout()`, qui couvre les
  quatre familles. La fonction d'origine existe toujours et garde son
  comportement.

## Reprendre le travail

Le plan détaille les phases 6 à 9. L'ordre recommandé n'a pas changé : SSE (⑥)
avant le port macOS (④), parce que ⑥ supprime les boucles de poll que ④ devrait
sinon réécrire deux fois. La phase 9 (SQLite) reste le seul vrai chantier, et
elle n'est toujours pas nécessaire pour tenir la promesse de durabilité.
