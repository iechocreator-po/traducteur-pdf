"""
Écriture et lecture durables des fichiers d'état JSON.

Pourquoi ce module existe (audit du 27/7/2026, principe cible ③) : tous les
fichiers d'état du produit étaient écrits en `open(chemin, "w")` — la troncature
survient AVANT la réécriture, donc toute coupure dans cette fenêtre laisse un
JSON illisible. Selon le fichier touché, la conséquence allait de la perte
silencieuse (cache) à la disparition de toute la Bibliothèque des deux frontends
(F1 : `JSONDecodeError` remontant jusqu'à `GET /api/bibliotheque`, qui renvoie
500) ou à la mort définitive du planificateur (F13).

Deux garanties, à utiliser ensemble :

- `ecrire_json_atomique()` — le contenu part dans un fichier temporaire du MÊME
  répertoire, puis `os.replace()` le met en place. `os.replace` est atomique sur
  APFS comme sur ext4 : un lecteur concurrent voit soit l'ancien fichier complet,
  soit le nouveau complet, jamais un fichier à moitié écrit. Le même répertoire
  est indispensable — `os.replace` n'est atomique qu'à l'intérieur d'un système
  de fichiers.
- `lire_json_tolerant()` — un fichier illisible n'est jamais fatal : il est mis
  de côté (`.corrompu-<horodatage>`) et la fonction retourne la valeur par
  défaut. Mettre de côté plutôt que supprimer, parce qu'un état corrompu reste
  la seule trace de ce qui a été fait, et parce que laisser le fichier en place
  ferait retomber dans l'erreur au prochain appel.

⚠️ Ces fonctions ne remplacent PAS un store transactionnel (principe cible ②).
Elles garantissent qu'un fichier est entier, pas que deux fichiers sont
cohérents entre eux — la fenêtre de duplication F3 (chapitre écrit dans le `.md`
avant que l'état ne soit persisté) reste ouverte tant que le contenu et la
progression ne sont pas écrits dans la même transaction.
"""

import datetime
import json
import os
import tempfile
from typing import Any

# Journal des corruptions rencontrées depuis le démarrage. Sert à la santé du
# planificateur et au diagnostic : sans ça une corruption avalée redevient
# invisible, ce qui était précisément le défaut F2.
_corruptions: list[dict[str, str]] = []


def corruptions_rencontrees() -> list[dict[str, str]]:
    """Copie du journal des fichiers mis en quarantaine depuis le démarrage."""
    return list(_corruptions)


def reinitialiser_corruptions() -> None:
    """Vide le journal — pour les tests, et pour un éventuel bouton d'acquittement."""
    _corruptions.clear()


def ecrire_texte_atomique(chemin: str, contenu: str) -> None:
    """
    Écrit `contenu` dans `chemin` sans jamais laisser le fichier à moitié écrit.

    Le fichier temporaire est créé dans le répertoire de destination (et non dans
    /tmp) : `os.replace` n'est atomique qu'au sein d'un même système de fichiers.
    """
    repertoire = os.path.dirname(os.path.abspath(chemin)) or "."
    os.makedirs(repertoire, exist_ok=True)

    fd, chemin_tmp = tempfile.mkstemp(
        dir=repertoire, prefix=f".{os.path.basename(chemin)}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contenu)
            f.flush()
            # Le contenu doit être sur le disque AVANT le replace : sans ce fsync,
            # une panne de courant peut laisser un fichier correctement renommé
            # mais vide (métadonnées et données ne sont pas ordonnées entre elles).
            os.fsync(f.fileno())
        os.replace(chemin_tmp, chemin)
    except BaseException:
        # Ne jamais laisser traîner un .tmp derrière soi, même sur interruption.
        try:
            os.unlink(chemin_tmp)
        except OSError:
            pass
        raise


def ecrire_json_atomique(chemin: str, donnees: Any, indent: int | None = 2) -> None:
    """Sérialise `donnees` en JSON et l'écrit atomiquement."""
    ecrire_texte_atomique(
        chemin, json.dumps(donnees, indent=indent, ensure_ascii=False)
    )


def _mettre_en_quarantaine(chemin: str, raison: str) -> None:
    horodatage = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = f"{chemin}.corrompu-{horodatage}"
    try:
        os.replace(chemin, destination)
    except OSError:
        destination = ""
    _corruptions.append({
        "chemin": chemin,
        "raison": raison,
        "quarantaine": destination,
        "horodatage": datetime.datetime.now().isoformat(timespec="seconds"),
    })
    print(
        f"[persistance] fichier illisible mis de côté : {chemin} ({raison})"
        + (f" → {destination}" if destination else ""),
        flush=True,
    )


def lire_json_tolerant(chemin: str, defaut: Any = None) -> Any:
    """
    Lit un JSON sans jamais lever sur un fichier absent ou corrompu.

    Absent → `defaut`, sans bruit (cas normal au premier lancement).
    Corrompu → mis en quarantaine, journalisé, puis `defaut`.
    """
    if not os.path.exists(chemin):
        return defaut
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _mettre_en_quarantaine(chemin, f"JSON invalide : {e}")
        return defaut
    except OSError as e:
        # Illisible sans être corrompu (permissions, disque) : on ne déplace rien,
        # déplacer un fichier qu'on n'a pas su lire ferait plus de mal que de bien.
        _corruptions.append({
            "chemin": chemin,
            "raison": f"illisible : {e}",
            "quarantaine": "",
            "horodatage": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        return defaut
