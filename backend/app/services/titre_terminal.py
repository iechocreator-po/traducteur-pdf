"""
Titre de la fenêtre de terminal (10/10/2026).

Plusieurs backends tournent en parallèle sur la machine (un par produit du portfolio) et leurs
fenêtres de terminal portent toutes le même titre par défaut (« python -m uvicorn … »). Le backend
de toledo écrit donc au démarrage la séquence OSC 0 (« définir le titre de la fenêtre »), comprise
par Terminal.app, iTerm2, VS Code, etc. : la fenêtre s'appelle « toledo · backend (port 8000) ».

N'écrit RIEN si la sortie n'est pas un terminal (fichier de log, tube, tests, launcher qui
redirige vers /dev/null) : une séquence d'échappement dans un fichier de log serait du bruit.
"""

import sys
from typing import TextIO


def port_depuis_arguments(argv: list[str], defaut: int = 8000) -> int:
    """Port passé à uvicorn (`--port 8000` ou `--port=8000`), sinon `defaut`."""
    for i, arg in enumerate(argv):
        if arg == "--port" and i + 1 < len(argv) and argv[i + 1].isdigit():
            return int(argv[i + 1])
        if arg.startswith("--port=") and arg[7:].isdigit():
            return int(arg[7:])
    return defaut


def definir_titre(titre: str, flux: TextIO | None = None) -> bool:
    """Écrit le titre de la fenêtre si `flux` (stdout par défaut) est un terminal. Retourne
    True si la séquence a été écrite."""
    flux = flux or sys.stdout
    try:
        if not flux.isatty():
            return False
        titre = "".join(c for c in titre if c.isprintable())   # jamais d'échappement injecté dans le titre
        flux.write(f"\033]0;{titre}\007")
        flux.flush()
        return True
    except (OSError, ValueError, AttributeError):
        return False
