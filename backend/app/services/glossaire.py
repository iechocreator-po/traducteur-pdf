"""
Glossaire : termes à ne pas traduire (noms propres, acronymes, marques…) et termes à traduction imposée.
Persisté dans glossaire.json à la racine du backend, éditable via l'API.
Les entrées présentes dans un chunk sont injectées dans le prompt du traducteur,
puis leur présence est vérifiée dans la traduction (avertissement sinon).

Une entrée est une chaîne : « Oreanwalkers » (recopié tel quel) ou « Labyrinth tile => tuile Labyrinthe »
(traduction imposée). Le format de fichier et l'API ne changent pas : c'est la même liste de chaînes.
Mesuré sur le Cycle I (17/11/2026) : sans traduction imposée, « Labyrinth tile » devenait « tuile Labyrinthe »
puis « Pavillon du Labyrinthe » dix lignes plus bas.
"""

import os
import re
import threading

from app.services.persistance import ecrire_json_atomique, lire_json_tolerant

_FICHIER_GLOSSAIRE = os.path.join(os.path.dirname(__file__), "..", "..", "glossaire.json")
_FICHIER_GLOSSAIRE = os.path.normpath(_FICHIER_GLOSSAIRE)

_lock = threading.Lock()


def charger_termes() -> list[str]:
    """Retourne la liste des termes du glossaire (vide si aucun fichier)."""
    with _lock:
        data = lire_json_tolerant(_FICHIER_GLOSSAIRE, defaut={})
        if not isinstance(data, dict):
            return []
        termes = data.get("termes", [])
        return termes if isinstance(termes, list) else []


def sauvegarder_termes(termes: list[str]) -> list[str]:
    """
    Nettoie (espaces, vides, doublons en conservant l'ordre) puis persiste
    la liste. Retourne la liste nettoyée.
    """
    vus = set()
    nettoyes = []
    for terme in termes:
        t = terme.strip()
        if t and t.lower() not in vus:
            vus.add(t.lower())
            nettoyes.append(t)
    with _lock:
        ecrire_json_atomique(_FICHIER_GLOSSAIRE, {"termes": nettoyes})
    return nettoyes


SEPARATEUR = "=>"


def decomposer(entree: str) -> tuple[str, str | None]:
    """« source => cible » → (source, cible) ; un terme seul → (terme, None)."""
    source, sep, cible = entree.partition(SEPARATEUR)
    if sep and source.strip() and cible.strip():
        return source.strip(), cible.strip()
    return entree.strip(), None


def _motif(terme: str) -> re.Pattern:
    """Le terme en mots entiers, chaque mot pouvant porter une marque de pluriel (s, x, es) : « Labyrinth tile »
    reconnaît « Labyrinth tiles », « tuile Labyrinthe » reconnaît « tuiles Labyrinthe »."""
    mots = [re.escape(m) + r"(?:e?s|x)?" for m in terme.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(mots) + r"(?!\w)", re.IGNORECASE)


def termes_presents(texte: str, termes: list[str] | None = None) -> list[str]:
    """
    Retourne les entrées du glossaire présentes dans le texte (insensible à la casse) : un terme à conserver
    par simple inclusion, un terme à traduction imposée par sa source en mots entiers (« tile » ne doit pas
    s'imposer dans « tiled »).
    """
    if termes is None:
        termes = charger_termes()
    texte_bas = texte.lower()
    presents = []
    for t in termes:
        source, cible = decomposer(t)
        if (cible is None and t.lower() in texte_bas) or (cible is not None and _motif(source).search(texte)):
            presents.append(t)
    return presents


def termes_perdus(termes: list[str], texte_traduit: str) -> list[str]:
    """
    Parmi les entrées attendues, retourne celles absentes de la traduction (insensible à la casse — un
    changement de casse n'est pas une altération). Pour une traduction imposée, c'est la CIBLE qui est attendue.
    """
    traduit_bas = texte_traduit.lower()
    perdus = []
    for t in termes:
        _, cible = decomposer(t)
        if (cible is None and t.lower() not in traduit_bas) or (cible is not None
                                                                 and not _motif(cible).search(texte_traduit)):
            perdus.append(t)
    return perdus
