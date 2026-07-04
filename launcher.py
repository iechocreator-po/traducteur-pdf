"""
Petit serveur de lancement — port 5501.
Démarre / arrête le backend FastAPI (uvicorn) à la demande du frontend.
Lancer une seule fois : python3 launcher.py
"""

import subprocess
import sys
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json

BACKEND_DIR = Path(__file__).parent / "backend"
UVICORN_CMD = [
    sys.executable, "-m", "uvicorn",
    "app.main:app", "--reload", "--port", "8000",
]

_process: subprocess.Popen | None = None
_lock = threading.Lock()


def _backend_en_cours() -> bool:
    return _process is not None and _process.poll() is None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # silence les logs HTTP

    def _repondre(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            self._repondre(200, {"en_cours": _backend_en_cours()})
        else:
            self._repondre(404, {"erreur": "route inconnue"})

    def do_POST(self):
        global _process
        if self.path == "/start":
            with _lock:
                if _backend_en_cours():
                    self._repondre(200, {"statut": "deja_en_cours"})
                    return
                env = {**os.environ, "PYTHONUNBUFFERED": "1"}
                venv_python = BACKEND_DIR / "venv" / "bin" / "python"
                cmd = (
                    [str(venv_python), "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"]
                    if venv_python.exists()
                    else UVICORN_CMD
                )
                _process = subprocess.Popen(
                    cmd, cwd=str(BACKEND_DIR), env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            self._repondre(200, {"statut": "demarre"})

        elif self.path == "/stop":
            with _lock:
                if _process and _process.poll() is None:
                    _process.terminate()
                    try:
                        _process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        _process.kill()
                self._repondre(200, {"statut": "arrete"})

        else:
            self._repondre(404, {"erreur": "route inconnue"})


if __name__ == "__main__":
    serveur = HTTPServer(("localhost", 5501), Handler)
    print("Launcher prêt sur http://localhost:5501")
    print("Démarre le frontend : cd frontend && python3 -m http.server 5500")
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        if _backend_en_cours():
            _process.terminate()
        print("\nLauncher arrêté.")
