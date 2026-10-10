"""
Mesures d'un traitement Ollama (traduction, fiche d'étude) — 10/10/2026.

Ollama renvoie, avec chaque réponse non diffusée, les compteurs qui permettent de savoir ce
qu'un traitement a VRAIMENT coûté : `prompt_eval_count` (jetons lus), `eval_count` (jetons
générés), `eval_duration` / `load_duration` / `total_duration` (nanosecondes). Jusqu'ici ils
étaient jetés. On les accumule ici, par traitement, pour bâtir un historique (services/stats.py)
qui sert ensuite à estimer les durées futures.

Pourquoi un `ContextVar` et pas un paramètre passé partout : `appeler_ollama()` est appelé au
fond de trois couches (runner → contrôle qualité → traducteur) dont les signatures sont testées
et utilisées ailleurs. Le worker de la file exécute un traitement par thread ; un ContextVar posé
au début du traitement est visible de tout ce qu'il appelle, et d'AUCUN autre fil.

Sans collecte active (preflight, analyse, tests), `enregistrer_*` ne fait rien.
"""

import contextvars
from dataclasses import dataclass


@dataclass
class Compteurs:
    appels: int = 0                    # réponses Ollama reçues
    jetons_entree: int = 0             # prompt_eval_count
    jetons_sortie: int = 0             # eval_count
    duree_generation_ns: int = 0       # eval_duration : le calcul utile
    duree_chargement_ns: int = 0       # load_duration : modèle monté en mémoire (démarrage à froid)
    duree_totale_ns: int = 0           # total_duration
    reponses_tronquees: int = 0        # done_reason == "length" : plafond num_predict atteint
    morceaux_traduits: int = 0         # morceaux réellement envoyés au modèle
    morceaux_cache: int = 0            # morceaux resservis par le cache (aucun jeton)
    caracteres_traites: int = 0        # texte source envoyé au modèle (hors cache)
    caracteres_cache: int = 0          # texte source resservi par le cache
    caracteres_sortie: int = 0         # texte produit par le modèle
    nouvelles_tentatives: int = 0      # morceau refait par le contrôle qualité

    def vers_dict(self) -> dict:
        return dict(self.__dict__)


_courant: contextvars.ContextVar[Compteurs | None] = contextvars.ContextVar("compteurs_traitement", default=None)


def demarrer() -> Compteurs:
    """Ouvre une collecte pour le traitement en cours (ce fil seulement) et la retourne."""
    c = Compteurs()
    _courant.set(c)
    return c


def terminer() -> None:
    _courant.set(None)


def courant() -> Compteurs | None:
    return _courant.get()


def enregistrer_reponse(data: dict) -> None:
    """Accumule les compteurs d'une réponse Ollama (dict JSON décodé). Tolère les champs absents."""
    c = _courant.get()
    if c is None or not isinstance(data, dict):
        return
    c.appels += 1
    c.jetons_entree += int(data.get("prompt_eval_count") or 0)
    c.jetons_sortie += int(data.get("eval_count") or 0)
    c.duree_generation_ns += int(data.get("eval_duration") or 0)
    c.duree_chargement_ns += int(data.get("load_duration") or 0)
    c.duree_totale_ns += int(data.get("total_duration") or 0)
    if data.get("done_reason") == "length":
        c.reponses_tronquees += 1


def enregistrer_morceau(caracteres_source: int, caracteres_sortie: int) -> None:
    """Un morceau a été envoyé au modèle et a produit `caracteres_sortie` caractères."""
    c = _courant.get()
    if c is None:
        return
    c.morceaux_traduits += 1
    c.caracteres_traites += caracteres_source
    c.caracteres_sortie += caracteres_sortie


def enregistrer_cache(caracteres_source: int) -> None:
    c = _courant.get()
    if c is None:
        return
    c.morceaux_cache += 1
    c.caracteres_cache += caracteres_source


def enregistrer_nouvelle_tentative() -> None:
    c = _courant.get()
    if c is not None:
        c.nouvelles_tentatives += 1
