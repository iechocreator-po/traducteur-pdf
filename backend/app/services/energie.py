"""
Assertion d'énergie — empêcher le Mac de s'endormir pendant un job (principe ⑩).

Défaut F12 de l'audit : aucun `caffeinate`, aucune `IOPMAssertion`, aucun
`ProcessInfo.beginActivity` dans tout le dépôt. Un job long s'arrêtait donc dès
que la machine s'endormait. Sur un produit dont le cas d'usage est « traduire un
livre pendant que je fais autre chose », c'est une exigence fonctionnelle.

Correct par accident, à ne pas « corriger » : le budget de retry, lui, SURVIT à
la veille — `time.monotonic()` vaut `mach_absolute_time()` sur macOS, qui ne
compte pas le temps de sommeil. Le budget mural de 30 min d'`OLLAMA_RETRY_BUDGET_
SECONDES` traverse donc une nuit sans se consommer. Ce module ne change rien à
ça ; il empêche seulement le sommeil de survenir pendant qu'on travaille.

⚠️ CE QUE CE MODULE NE FAIT PAS — la moitié manquante pour le différé.
`caffeinate` empêche l'endormissement PENDANT le travail. Il ne réveille pas une
machine DÉJÀ endormie pour atteindre une échéance planifiée. Une traduction
programmée à 23 h sur un portable endormi à 22 h ne partira donc toujours pas.
Le réveil programmé (`pmset schedule wake`) exige les droits administrateur et
ne peut pas être armé par le backend ; voir `commande_reveil_programme()`, qui
rend la commande à exécuter sans jamais la lancer.
"""

import subprocess
import sys
import threading
from datetime import datetime

_lock = threading.Lock()
_processus: subprocess.Popen | None = None


def disponible() -> bool:
    """`caffeinate` n'existe que sur macOS."""
    return sys.platform == "darwin"


def acquerir() -> bool:
    """
    Empêche la mise en veille. IDEMPOTENT : appeler plusieurs fois ne lance qu'un
    seul `caffeinate`, et un seul `relacher()` suffit à tout arrêter.

    Volontairement non réentrant. Le worker appelle `acquerir()` à chaque travail
    dépilé mais ne `relacher()` que lorsque la file se vide : avec un compteur de
    prises, trois jobs enchaînés laisseraient le compteur à 2 et `caffeinate`
    tournerait indéfiniment après la fin du dernier — le Mac ne se rendormirait
    plus jamais.

    Retourne True si une assertion est active à la sortie de l'appel.
    """
    global _processus
    if not disponible():
        return False

    with _lock:
        if _processus is not None and _processus.poll() is None:
            return True
        try:
            # -i : empêche la veille SYSTÈME (idle sleep), pas la veille de
            # l'écran — inutile de garder l'écran allumé toute la nuit.
            # -w : caffeinate meurt si notre process meurt, donc aucune assertion
            # orpheline ne peut survivre à un crash du backend.
            _processus = subprocess.Popen(
                ["caffeinate", "-i", "-w", str(_pid_courant())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("[energie] veille système inhibée pendant le travail", flush=True)
            return True
        except (OSError, ValueError) as e:
            print(f"[energie] caffeinate indisponible : {e}", flush=True)
            _processus = None
            return False


def relacher() -> None:
    """Arrête l'assertion. Sans effet si aucune n'est active."""
    global _processus
    with _lock:
        if _processus is None:
            return
        try:
            _processus.terminate()
            _processus.wait(timeout=5)
        except Exception:
            pass
        finally:
            _processus = None
            print("[energie] veille système de nouveau autorisée", flush=True)


def actif() -> bool:
    with _lock:
        return _processus is not None and _processus.poll() is None


def _pid_courant() -> int:
    import os

    return os.getpid()


def commande_reveil_programme(echeance: datetime) -> str:
    """
    Rend la commande `pmset` qui armerait un réveil pour cette échéance.

    NE L'EXÉCUTE PAS, et c'est délibéré : `pmset schedule` exige les droits
    administrateur, donc le backend ne peut pas l'armer lui-même sans demander un
    mot de passe — ce qu'une app locale n'a pas à faire dans le dos de son
    utilisateur. La commande est exposée pour que l'interface puisse la proposer
    à la copie, et pour que l'utilisateur sache que sans elle, une traduction
    planifiée ne partira pas si la machine dort.
    """
    quand = echeance.strftime("%m/%d/%y %H:%M:%S")
    return f'sudo pmset schedule wake "{quand}"'
