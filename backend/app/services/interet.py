"""
Capture d'intérêt pour les fonctionnalités en développement (teasers de la
refonte Workflow : clonage de voix, export PDF…). Chaque manifestation
d'intérêt est tracée dans un log local — aucune donnée n'est envoyée ailleurs.
"""

import datetime
import os
import re
import threading

_FICHIER_LOG = os.path.join(os.path.dirname(__file__), "..", "..", "interet_fonctionnalites.log")
_FICHIER_LOG = os.path.normpath(_FICHIER_LOG)

_lock = threading.Lock()

_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_valide(email: str) -> bool:
    return bool(_RE_EMAIL.match(email.strip()))


def enregistrer_interet(fonctionnalite: str, email: str) -> None:
    """Trace l'intérêt d'un utilisateur pour une fonctionnalité (append horodaté)."""
    horodatage = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ligne = f"[{horodatage}] fonctionnalite={fonctionnalite.strip()} email={email.strip()}\n"
    with _lock:
        with open(_FICHIER_LOG, "a", encoding="utf-8") as f:
            f.write(ligne)
