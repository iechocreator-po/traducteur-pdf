"""
Erreurs typées de l'API (principe cible ⑦).

Le problème résolu : jusqu'ici toute erreur partait en `HTTPException(detail=str)`.
Le 503 du preflight Ollama portait sa consigne de redémarrage — la chose la plus
utile du message — noyée dans une phrase que seul le frontend web savait afficher.
Sur macOS, `APIService.swift` jette le code de statut ET le corps (F4), donc cette
consigne n'arrivait jamais jusqu'à l'utilisateur.

Le contrat devient : toute erreur de l'API a la même forme.

    {
      "code": "ollama_indisponible",       # stable, testable, jamais traduit
      "message": "…",                       # phrase affichable telle quelle
      "remediation": "killall ollama; …"   # ce que l'utilisateur peut FAIRE, ou null
    }

`code` est la clé de voûte : c'est lui qu'un client peut brancher sur un bouton
(« Redémarrer Ollama ») sans faire de correspondance de chaînes fragile.

Compatibilité : FastAPI sérialise `HTTPException(detail=…)` sous la clé `detail`.
On conserve donc `detail` — le frontend web actuel le lit — et on AJOUTE `erreur`
à côté. Aucun client existant ne casse ; les nouveaux lisent le champ structuré.
"""

from fastapi import HTTPException
from pydantic import BaseModel


class ErreurAPI(BaseModel):
    """Corps normalisé d'une erreur de l'API."""

    code: str
    message: str
    remediation: str | None = None


class ErreurMetier(HTTPException):
    """
    HTTPException porteuse d'un `ErreurAPI`.

    S'utilise comme une HTTPException ordinaire ; le corps de la réponse contient
    à la fois `detail` (compat) et `erreur` (structuré).
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        remediation: str | None = None,
    ):
        self.erreur = ErreurAPI(code=code, message=message, remediation=remediation)
        super().__init__(
            status_code=status_code,
            detail=message if not remediation else f"{message} {remediation}",
        )


# ── Erreurs courantes, nommées une fois pour toutes ──────────────────────────

def ollama_indisponible(message: str) -> ErreurMetier:
    """
    Preflight en échec : Ollama ne répond pas, ou répond mais ne génère plus
    (llama-server figé — le cas qui a coûté une séance de debug le 23/7 : /api/tags
    répondait encore alors qu'aucune génération n'aboutissait).
    """
    return ErreurMetier(
        status_code=503,
        code="ollama_indisponible",
        message=message,
        remediation="Redémarrez Ollama : killall ollama ; open -a Ollama",
    )


def source_invalide(message: str) -> ErreurMetier:
    return ErreurMetier(
        status_code=422,
        code="source_invalide",
        message=message,
        remediation="Vérifiez le chemin du fichier (absolu, .pdf ou .md).",
    )


def introuvable(message: str, code: str = "introuvable") -> ErreurMetier:
    return ErreurMetier(status_code=404, code=code, message=message)
