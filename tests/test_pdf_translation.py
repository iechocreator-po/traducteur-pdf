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
             "-d", json.dumps({"source": chemin_source})],
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
        print(f"  Lancement traduction (flag extraction_images=false)...")
        translate_response = subprocess.run(
            ["curl", "-s", f"{BACKEND_URL}/api/translate",
             "-X", "POST",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({
                 "source": chemin_source,
                 "chapitres_selectionnes": list(range(min(2, analyze_data.get("nb_chapitres", 1)))),
                 "extraction_images_pdf": False
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

        return {
            "status": "success",
            "elapsed_s": elapsed,
            "job_id": translate_data.get("job_id"),
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
             "-d", json.dumps({"source": chemin_source})],
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
        print(f"  Lancement traduction (flag extraction_images=true)...")
        translate_response = subprocess.run(
            ["curl", "-s", f"{BACKEND_URL}/api/translate",
             "-X", "POST",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({
                 "source": chemin_source,
                 "chapitres_selectionnes": list(range(min(2, analyze_data.get("nb_chapitres", 1)))),
                 "extraction_images_pdf": True
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

        return {
            "status": "success",
            "elapsed_s": elapsed,
            "job_id": translate_data.get("job_id"),
            "chapitres_traduits": analyze_data.get("nb_chapitres", 1),
            "extraction_images": True
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
