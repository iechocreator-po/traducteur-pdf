# Tests de traduction PDF — Script réutilisable

## Overview

Ce script teste **3 modes de traduction** d'un PDF :

| Test | Description | Dépendances |
|------|-------------|-------------|
| **T1** | Traduction directe Ollama | Ollama local (127.0.0.1:11434) |
| **T2** | Backend sans extraction images | Backend Toledo (127.0.0.1:8000) |
| **T3** | Backend avec extraction images | Backend Toledo (127.0.0.1:8000) |

Chaque test a un **timeout de 5 min** (300s) — si ça dépasse, le test échoue proprement.

## Installation

**Prérequis** :
- Python 3.10+
- Ollama lancé : `ollama serve` (ou déjà en cours d'exécution)
- Backend Toledo lancé : `cd traducteur-pdf/backend && uvicorn app.main:app --reload --port 8000`
- `curl` disponible dans le PATH

```bash
# Faire le script exécutable
chmod +x test_pdf_translation.py
```

## Usage

```bash
# Tester les 3 modes (défaut)
python3 test_pdf_translation.py <chemin-pdf>

# Tester un mode spécifique
python3 test_pdf_translation.py <chemin-pdf> --test T1
python3 test_pdf_translation.py <chemin-pdf> --test T2
python3 test_pdf_translation.py <chemin-pdf> --test T3

# Timeout personnalisé (en secondes)
python3 test_pdf_translation.py <chemin-pdf> --timeout 600  # 10 min
```

## Exemples

```bash
# Test complet sur Chapter 9
python3 test_pdf_translation.py ~/Documents/2000_DigitalProducts/traducteur-pdf/test_integre/Models\ of\ the\ Mind_\ Chapter\ 9.pdf

# Juste T2 (backend sans images)
python3 test_pdf_translation.py ~/Documents/2000_DigitalProducts/traducteur-pdf/test_integre/Models\ of\ the\ Mind_\ Chapter\ 9.pdf --test T2

# Timeout 10 min
python3 test_pdf_translation.py ~/Documents/2000_DigitalProducts/traducteur-pdf/test_integre/Models\ of\ the\ Mind_\ Chapter\ 9.pdf --timeout 600
```

## Sortie

Le script affiche :
- ✅ ou ❌ pour chaque test
- Durée élapsée
- Détails d'erreur si applicable
- Nombre de tests réussis

```
📊 RÉSULTATS
======================================================

✅ T1: SUCCESS
   ⏱️  45.2s
   text_extracted_chars: 5234
   translation_preview: Voici la traduction...

❌ T2: FAILED
   Error: Connection refused (Backend not running)

✅ T3: SUCCESS
   ⏱️  120.5s
   job_id: job_xyz123
   chapitres_traduits: 2
   extraction_images: True

======================================================
✨ 2/3 tests réussis
```

## Où trouver le script

- **Scratchpad (session actuelle)** : `/private/tmp/claude-501/-Users-jpierre-parra-Documents-2000-DigitalProducts-traducteur-pdf/c63271b9-9171-454a-becb-9a48b5042646/scratchpad/test_pdf_translation.py`
- **À copier dans** : `~/Documents/2000_DigitalProducts/traducteur-pdf/tests/` (à créer si nécessaire)

```bash
# Copier pour réutilisation
cp /private/tmp/claude-501/-Users-jpierre-parra-Documents-2000-DigitalProducts-traducteur-pdf/c63271b9-9171-454a-becb-9a48b5042646/scratchpad/test_pdf_translation.py ~/Documents/2000_DigitalProducts/traducteur-pdf/tests/test_pdf_translation.py
cp /private/tmp/claude-501/-Users-jpierre-parra-Documents-2000-DigitalProducts-traducteur-pdf/c63271b9-9171-454a-becb-9a48b5042646/scratchpad/README_TESTS.md ~/Documents/2000_DigitalProducts/traducteur-pdf/tests/README_TESTS.md
```

## Debugging

### T1 échoue : "Connection refused"
- Ollama n'est pas lancé
- Vérifier : `curl -s http://127.0.0.1:11434/api/tags` (doit retourner JSON)
- Lancer : `ollama serve` dans un terminal séparé

### T2 ou T3 échouent : "Connection refused"
- Backend Toledo non lancé
- Vérifier : `curl -s http://127.0.0.1:8000/docs` (doit retourner HTML)
- Lancer : `cd traducteur-pdf/backend && uvicorn app.main:app --reload --port 8000`

### Timeout sur un test
- Le PDF est très gros ou le système est lent
- Augmenter le timeout : `--timeout 600`
- Ou tester juste T1 pour isoler le problème

### Erreur "No module named 'pdfplumber'"
- Activer le venv backend : `cd traducteur-pdf/backend && source venv/bin/activate`
- Puis lancer le script depuis ce shell

## Notes

- **T1** extrait les 5 premières pages pour la rapidité (prototypage)
- **T2/T3** traduisent les 2 premiers chapitres pour la rapidité
- Pour un test complet, éditer le script ou augmenter les limites
- Le script utilise `subprocess` + `curl` pour éviter les dépendances HTTP Python manquantes
