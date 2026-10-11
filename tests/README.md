# Scripts de validation manuelle — traduction PDF

> Ces scripts vivent hors de `backend/tests/` (la vraie suite `pytest`). Ce sont
> des outils de validation **manuelle end-to-end** contre un vrai Ollama + le vrai
> backend — utiles pour prouver qu'une traduction fonctionne réellement, pas juste
> que le job démarre.

## `validate_translation.py` — LA validation avec preuve (à utiliser)

Prouve qu'une traduction est **correcte** en comparant la sortie à une référence
« golden » (`reference/Chapter9_traduit_reference.md`), validée à la main.

**Pourquoi pas une comparaison exacte ?** Ollama est non-déterministe
(`temperature 0.3`) : deux traductions du même texte diffèrent au mot près. On
valide donc les propriétés qui comptent, pas l'égalité stricte :

| Invariant (échec dur) | Attrape |
|---|---|
| taille dans [50 %, 200 %] de la référence | résumé/troncature (le bug du 23/7 faisait ~9 %) |
| tous les tags images `![](...)` de la source présents à l'identique | Ollama qui altère un chemin d'image |
| sortie en français (densité de mots-outils) | résumé anglais |
| 0 section en échec (lu dans `.state.json`) | chapitres perdus |

Indicatifs (jamais bloquants) : similarité difflib, nombre de titres.

### Usage

```bash
# 1) Preflight seul : Ollama est-il EN ÉTAT de traduire ? (voir plus bas)
python3 tests/validate_translation.py --preflight

# 2) Validation complète : preflight → traduit Chapter 9 → compare à la référence
python3 tests/validate_translation.py \
  --pdf "test_integre/Models of the Mind_ Chapter 9.pdf" --chapitres 0

# 3) (Re)générer la référence à partir d'une sortie validée à la main
python3 tests/validate_translation.py \
  --save-reference "backend/uploads/<hash>/..._traduit_ll.md" \
  --source "backend/uploads/<hash>/..._converti_py.md"
```

Sortie : `✅ VALIDATION RÉUSSIE` / `❌ VALIDATION ÉCHOUÉE` + le détail invariant
par invariant. Code de sortie 0 (succès) / 1 (échec) → utilisable en CI.

### Le preflight (réponse à « comment s'assurer qu'Ollama va fonctionner ? »)

Avant tout long job, `--preflight` (ou automatiquement en flux complet) vérifie
qu'Ollama peut **réellement traduire**, pas juste qu'il répond :

1. `/api/tags` répond (Ollama lancé) ;
2. RAM libre suffisante (< 25 % = avertissement de risque de stall) ;
3. **une vraie mini-traduction** avec les params exacts du backend (`num_ctx`
   compris), plafonnée à 60 s. **C'est ce test qui attrape le blocage** : un
   `llama-server` figé (chargé mais bloqué à ~3 % CPU) ne répondra pas ici, alors
   que `/api/tags` répond encore. En cas d'échec → **redémarrer Ollama**
   (`killall ollama; open -a Ollama`) et relancer.

## `test_pdf_translation.py` — smoke test multi-mode (historique)

Teste 3 chemins (T1 Ollama direct, T2 backend sans images, T3 backend avec
images). ⚠️ T2/T3 ne font qu'**enfiler** le job (retour immédiat) — ils ne
prouvent PAS que la traduction va au bout. Pour une vraie preuve, utiliser
`validate_translation.py`. Conservé pour le diagnostic rapide de connectivité.

## `reference/`

- `Chapter9_traduit_reference.md` — traduction golden validée à la main (23/7/2026).
- `Chapter9_source_reference.md` — le Markdown source correspondant (pour vérifier
  les tags images). Régénérer les deux via `--save-reference` après toute
  amélioration volontaire de la qualité de traduction.
```
