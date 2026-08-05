#!/usr/bin/env python3
"""
Script de test multi-mode pour traduction PDF.

Usage:
    python3 test_pdf_translation.py <pdf_path> [--test T1|T2|T3|all] [--timeout 300]

Tests :
  T1 : Traduction directe Ollama (extraction PDF → LLM)
  T2 : Backend sans extraction images
  T3 : Backend avec extraction images
  all: Les trois (défaut)

Timeout par défaut: 300s (5 min)
"""

import sys
import json
import time
import subprocess
import signal
from pathlib import Path
from typing import Optional, Dict, Any

# Configuration
OLLAMA_URL = "http://127.0.0.1:11434"
BACKEND_URL = "http://127.0.0.1:8000"
OLLAMA_MODEL = "llama3.1"  # À vérifier/ajuster
TIMEOUT_DEFAULT = 300  # 5 minutes


class TimeoutError(Exception):
    """Raised when a test exceeds timeout."""
    pass


def timeout_handler(signum, frame):
    """Signal handler for timeout."""
    raise TimeoutError(f"Test dépassé le timeout ({TIMEOUT_DEFAULT}s)")


def run_with_timeout(func, args=(), kwargs=None, timeout=TIMEOUT_DEFAULT):
    """Run a function with a timeout."""
    kwargs = kwargs or {}

    # Set up signal handler
    original_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout)

    try:
        result = func(*args, **kwargs)
        signal.alarm(0)  # Cancel alarm
        return result
    except TimeoutError as e:
        return {"error": str(e), "status": "timeout"}
    finally:
        signal.signal(signal.SIGALRM, original_handler)
        signal.alarm(0)


def extract_pdf_text(pdf_path: str) -> str:
    """Extract text from PDF using pymupdf4llm or fallback."""
    try:
        import pymupdf4llm
        result = pymupdf4llm.to_markdown(pdf_path, embed_images=False)
        return result
    except Exception as e:
        # Fallback to pdfplumber
        try:
            import pdfplumber
            text = ""
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages[:5]:  # First 5 pages for quick test
                    text += page.extract_text() or ""
            return text
        except Exception as e2:
            return f"Error: {e2}"


def test_t1_ollama_direct(pdf_path: str) -> Dict[str, Any]:
    """T1: Direct Ollama translation (extract PDF → LLM)."""
    print("\n🧪 TEST T1: Ollama direct")
    print(f"  Extraction du texte de {Path(pdf_path).name}...")

    # Extract text
    text = extract_pdf_text(pdf_path)
    if "Error" in text:
        return {"error": str(text), "status": "extraction_failed"}

    # Prepare prompt
    prompt = f"""Traduis le texte suivant de l'anglais vers le français:

{text[:2000]}

Fournir UNIQUEMENT la traduction, sans commentaire."""

    # Call Ollama
    print(f"  Appel Ollama (modèle: {OLLAMA_MODEL})...")
    start = time.time()

    try:
        response = subprocess.run(
            ["curl", "-s", f"{OLLAMA_URL}/api/generate",
             "-d", json.dumps({
                 "model": OLLAMA_MODEL,
                 "prompt": prompt,
                 "stream": False
             })],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_DEFAULT
        )
        elapsed = time.time() - start

        if response.returncode != 0:
            return {"error": response.stderr, "status": "failed", "elapsed_s": elapsed}

        data = json.loads(response.stdout)
        if "error" in data:
            return {"error": data["error"], "status": "failed", "elapsed_s": elapsed}

        return {
            "status": "success",
            "elapsed_s": elapsed,
            "text_extracted_chars": len(text),
            "translation_preview": data.get("response", "")[:200]
        }
    except subprocess.TimeoutExpired:
        return {"error": "Timeout lors de l'appel Ollama", "status": "timeout"}




def flag_extraction_images() -> str:
    """
    Valeur REELLE du flag cote serveur.

    ⚠️ `extraction_images_pdf` n'est PAS un parametre de requete : il vit dans
    feature_flags.json / bilbao.features.json et se lit via GET /api/feature-flags.
    T2 et T3 ne pouvaient donc jamais tester deux modes differents — ils envoyaient
    un champ `extraction_images_pdf` que l'API ignorait, et ne differaient en
    realite que par un message affiche. Plutot que de faire semblant, on lit et on
    AFFICHE l'etat du flag : pour comparer les deux modes, il faut le basculer
    entre deux executions et redemarrer le backend.
    """
    try:
        rep = subprocess.run(["curl", "-s", f"{BACKEND_URL}/api/feature-flags"],
                             capture_output=True, text=True, timeout=15)
        flags = json.loads(rep.stdout)
        return str(flags.get("extraction_images_pdf"))
    except Exception:
        return "inconnu"


def attendre_fin_job(chemin_sortie: str, timeout_s: int) -> Dict[str, Any]:
    """
    Attend qu'un job de traduction atteigne un statut terminal.

    ⚠️ C'est LA correction du 29/7 : sans cette attente, T2 et T3 renvoyaient
    « SUCCESS » en 0,1 s alors qu'aucun job n'avait meme demarre. Le POST partait
    avec la cle `source` (inconnue de l'API → 422) et le script ne testait que la
    presence d'une cle `error`, absente d'un 422 FastAPI qui repond `detail`.
    Un lancement n'est PAS une traduction : il faut lire le statut final.
    """
    debut = time.time()
    while time.time() - debut < timeout_s:
        time.sleep(5)
        rep = subprocess.run(["curl", "-s", f"{BACKEND_URL}/api/bibliotheque"],
                             capture_output=True, text=True, timeout=30)
        try:
            docs = json.loads(rep.stdout).get("documents", [])
        except json.JSONDecodeError:
            continue
        doc = next((d for d in docs if d.get("chemin_sortie") == chemin_sortie), None)
        if doc is None:
            continue
        if doc.get("statut") in ("termine", "erreur", "annule"):
            return {
                "statut": doc["statut"],
                "sections": f"{doc.get('sections_completees')}/{doc.get('total_sections')}",
                "echecs": doc.get("nb_sections_echouees", 0),
                "elapsed_s": round(time.time() - debut, 1),
            }
    return {"statut": "timeout", "elapsed_s": round(time.time() - debut, 1)}


def test_t2_backend_no_images(pdf_path: str) -> Dict[str, Any]:
    """T2: Backend without image extraction."""
    print("\n🧪 TEST T2: Backend (sans extraction images)")
    print(f"  Upload {Path(pdf_path).name}...")

    start = time.time()

    try:
        # Upload (paramètre correct: "fichier", pas "file")
        response = subprocess.run(
            ["curl", "-s", f"{BACKEND_URL}/api/upload",
             "-F", f"fichier=@{pdf_path}"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if response.returncode != 0:
            return {"error": response.stderr, "status": "upload_failed"}

        upload_data = json.loads(response.stdout)
        if "error" in upload_data:
            return {"error": upload_data["error"], "status": "upload_failed"}

        chemin_source = upload_data.get("chemin")
        if not chemin_source:
            return {"error": "No chemin_source in response", "status": "upload_failed"}

        print(f"  Analyse du PDF...")
        # Analyze
        analyze_response = subprocess.run(
            ["curl", "-s", f"{BACKEND_URL}/api/analyser",
             "-X", "POST",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"chemin_pdf": chemin_source})],
            capture_output=True,
            text=True,
            timeout=60
        )

        if analyze_response.returncode != 0:
            return {"error": analyze_response.stderr, "status": "analyze_failed", "elapsed_s": time.time() - start}

        analyze_data = json.loads(analyze_response.stdout)
        if "error" in analyze_data:
            return {"error": analyze_data["error"], "status": "analyze_failed", "elapsed_s": time.time() - start}

        # Start translation (just first 2 chapters for speed)
        print(f"  Lancement traduction (flag extraction_images_pdf cote serveur = {flag_extraction_images()})...")
        translate_response = subprocess.run(
            ["curl", "-s", f"{BACKEND_URL}/api/translate",
             "-X", "POST",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({
                 "chemin_pdf": chemin_source,
                 "chapitres_selectionnes": list(range(min(2, analyze_data.get("nb_chapitres", 1)))),
             })],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_DEFAULT
        )

        elapsed = time.time() - start

        if translate_response.returncode != 0:
            return {"error": translate_response.stderr, "status": "translate_failed", "elapsed_s": elapsed}

        translate_data = json.loads(translate_response.stdout)
        if "error" in translate_data:
            return {"error": translate_data["error"], "status": "translate_failed", "elapsed_s": elapsed}

        job_id = translate_data.get("job_id")
        chemin_sortie = translate_data.get("chemin_sortie")
        if not job_id or not chemin_sortie:
            # Un 422/503 renvoie `detail`, pas `error` : sans ce garde, le script
            # annoncait un succes alors qu'aucun job n'existait.
            return {"error": translate_data.get("detail", translate_data),
                    "status": "translate_failed", "elapsed_s": elapsed}

        print(f"  Traduction lancee ({job_id[:8]}…), attente de la fin...")
        fin = attendre_fin_job(chemin_sortie, TIMEOUT_DEFAULT)
        if fin["statut"] != "termine":
            return {"error": f"job {fin['statut']} ({fin.get('sections', '?')})",
                    "status": "translate_failed", "elapsed_s": fin["elapsed_s"]}
        # « Rien a traduire » n'est PAS une preuve que la traduction fonctionne.
        # Le flux additif termine instantanement quand les chapitres demandes sont
        # deja faits : sans ce garde, le test repasserait au vert en ne traduisant
        # rien du tout — la meme illusion que celle corrigee le 29/7.
        if fin.get("sections", "0/0").endswith("/0"):
            return {"error": "aucune section a traduire (document deja traduit) — "
                             "supprimez la sortie et le cache pour un vrai test",
                    "status": "rien_a_faire", "elapsed_s": fin["elapsed_s"]}

        return {
            "status": "success",
            "elapsed_s": fin["elapsed_s"],
            "job_id": job_id,
            "sections": fin["sections"],
            "echecs": fin["echecs"],
            "chapitres_traduits": analyze_data.get("nb_chapitres", 1)
        }
    except subprocess.TimeoutExpired:
        return {"error": "Timeout", "status": "timeout", "elapsed_s": time.time() - start}
    except Exception as e:
        return {"error": str(e), "status": "failed", "elapsed_s": time.time() - start}


def test_t3_backend_with_images(pdf_path: str) -> Dict[str, Any]:
    """T3: Backend with image extraction."""
    print("\n🧪 TEST T3: Backend (avec extraction images)")
    print(f"  Upload {Path(pdf_path).name}...")

    start = time.time()

    try:
        # Upload (paramètre correct: "fichier", pas "file")
        response = subprocess.run(
            ["curl", "-s", f"{BACKEND_URL}/api/upload",
             "-F", f"fichier=@{pdf_path}"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if response.returncode != 0:
            return {"error": response.stderr, "status": "upload_failed"}

        upload_data = json.loads(response.stdout)
        if "error" in upload_data:
            return {"error": upload_data["error"], "status": "upload_failed"}

        chemin_source = upload_data.get("chemin")
        if not chemin_source:
            return {"error": "No chemin in response", "status": "upload_failed"}

        print(f"  Analyse du PDF (avec images)...")
        # Analyze
        analyze_response = subprocess.run(
            ["curl", "-s", f"{BACKEND_URL}/api/analyser",
             "-X", "POST",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"chemin_pdf": chemin_source})],
            capture_output=True,
            text=True,
            timeout=60
        )

        if analyze_response.returncode != 0:
            return {"error": analyze_response.stderr, "status": "analyze_failed", "elapsed_s": time.time() - start}

        analyze_data = json.loads(analyze_response.stdout)
        if "error" in analyze_data:
            return {"error": analyze_data["error"], "status": "analyze_failed", "elapsed_s": time.time() - start}

        # Start translation with images
        print(f"  Lancement traduction (flag extraction_images_pdf cote serveur = {flag_extraction_images()})...")
        translate_response = subprocess.run(
            ["curl", "-s", f"{BACKEND_URL}/api/translate",
             "-X", "POST",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({
                 "chemin_pdf": chemin_source,
                 "chapitres_selectionnes": list(range(min(2, analyze_data.get("nb_chapitres", 1)))),
             })],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_DEFAULT
        )

        elapsed = time.time() - start

        if translate_response.returncode != 0:
            return {"error": translate_response.stderr, "status": "translate_failed", "elapsed_s": elapsed}

        translate_data = json.loads(translate_response.stdout)
        if "error" in translate_data:
            return {"error": translate_data["error"], "status": "translate_failed", "elapsed_s": elapsed}

        job_id = translate_data.get("job_id")
        chemin_sortie = translate_data.get("chemin_sortie")
        if not job_id or not chemin_sortie:
            # Un 422/503 renvoie `detail`, pas `error` : sans ce garde, le script
            # annoncait un succes alors qu'aucun job n'existait.
            return {"error": translate_data.get("detail", translate_data),
                    "status": "translate_failed", "elapsed_s": elapsed}

        print(f"  Traduction lancee ({job_id[:8]}…), attente de la fin...")
        fin = attendre_fin_job(chemin_sortie, TIMEOUT_DEFAULT)
        if fin["statut"] != "termine":
            return {"error": f"job {fin['statut']} ({fin.get('sections', '?')})",
                    "status": "translate_failed", "elapsed_s": fin["elapsed_s"]}
        # « Rien a traduire » n'est PAS une preuve que la traduction fonctionne.
        # Le flux additif termine instantanement quand les chapitres demandes sont
        # deja faits : sans ce garde, le test repasserait au vert en ne traduisant
        # rien du tout — la meme illusion que celle corrigee le 29/7.
        if fin.get("sections", "0/0").endswith("/0"):
            return {"error": "aucune section a traduire (document deja traduit) — "
                             "supprimez la sortie et le cache pour un vrai test",
                    "status": "rien_a_faire", "elapsed_s": fin["elapsed_s"]}

        return {
            "status": "success",
            "elapsed_s": fin["elapsed_s"],
            "job_id": job_id,
            "sections": fin["sections"],
            "echecs": fin["echecs"],
            "chapitres_traduits": analyze_data.get("nb_chapitres", 1),
            "extraction_images_serveur": flag_extraction_images()
        }
    except subprocess.TimeoutExpired:
        return {"error": "Timeout", "status": "timeout", "elapsed_s": time.time() - start}
    except Exception as e:
        return {"error": str(e), "status": "failed", "elapsed_s": time.time() - start}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    pdf_path = sys.argv[1]
    test_mode = "all"
    timeout = TIMEOUT_DEFAULT

    for i, arg in enumerate(sys.argv[2:]):
        if arg == "--test" and i + 3 < len(sys.argv):
            test_mode = sys.argv[i + 3]
        elif arg == "--timeout" and i + 3 < len(sys.argv):
            timeout = int(sys.argv[i + 3])

    if not Path(pdf_path).exists():
        print(f"❌ Fichier non trouvé: {pdf_path}")
        sys.exit(1)

    print(f"📋 Tests de traduction PDF")
    print(f"📄 Fichier: {pdf_path}")
    print(f"⏱️  Timeout: {timeout}s par test")
    print(f"🔧 Mode: {test_mode}")

    results = {}

    if test_mode in ("T1", "all"):
        results["T1"] = run_with_timeout(test_t1_ollama_direct, (pdf_path,), timeout=timeout)

    if test_mode in ("T2", "all"):
        results["T2"] = run_with_timeout(test_t2_backend_no_images, (pdf_path,), timeout=timeout)

    if test_mode in ("T3", "all"):
        results["T3"] = run_with_timeout(test_t3_backend_with_images, (pdf_path,), timeout=timeout)

    # Print results
    print("\n" + "=" * 60)
    print("📊 RÉSULTATS")
    print("=" * 60)

    for test_name, result in results.items():
        status = result.get("status", "unknown")
        emoji = "✅" if status == "success" else "❌"
        print(f"\n{emoji} {test_name}: {status.upper()}")

        if "elapsed_s" in result:
            print(f"   ⏱️  {result['elapsed_s']:.1f}s")

        if "error" in result:
            print(f"   Error: {result['error']}")

        for key, value in result.items():
            if key not in ("status", "elapsed_s", "error"):
                print(f"   {key}: {value}")

    # Summary
    print("\n" + "=" * 60)
    success_count = sum(1 for r in results.values() if r.get("status") == "success")
    print(f"✨ {success_count}/{len(results)} tests réussis")


if __name__ == "__main__":
    main()
