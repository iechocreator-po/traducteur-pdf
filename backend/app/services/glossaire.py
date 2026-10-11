"""
Glossaire de termes à ne pas traduire (noms propres, acronymes, marques…).
Persisté dans glossaire.json à la racine du backend, éditable via l'API.
Les termes présents dans un chunk sont injectés dans le prompt du traducteur,
puis leur présence est vérifiée dans la traduction (avertissement sinon).
"""

import os
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


def termes_presents(texte: str, termes: list[str] | None = None) -> list[str]:
    """Retourne les termes du glossaire présents dans le texte (insensible à la casse)."""
    if termes is None:
        termes = charger_termes()
    texte_bas = texte.lower()
    return [t for t in termes if t.lower() in texte_bas]


def termes_perdus(termes: list[str], texte_traduit: str) -> list[str]:
    """
    Parmi les termes attendus, retourne ceux absents de la traduction
    (insensible à la casse — un changement de casse n'est pas une altération).
    """
    traduit_bas = texte_traduit.lower()
    return [t for t in termes if t.lower() not in traduit_bas]
