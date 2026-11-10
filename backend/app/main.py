"""
Point d'entrée de l'application backend.

Lancer en développement :
    uvicorn app.main:app --reload --port 8000

La documentation interactive de l'API est alors disponible sur :
    http://localhost:8000/docs

"""

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.api.erreurs import ErreurMetier
from app.api.routes import router
from app.api.validation import HOTES_LOCAUX, METHODES_SURES, ORIGINES_LOCALES
from app.services.recuperation import recuperer_tout
from app.services.scheduler import demarrer_surveillance
from app.services.titre_terminal import definir_titre, port_depuis_arguments
from app.services.uploads import purger_uploads_anciens



# Fenêtre de terminal reconnaissable parmi les autres backends du portfolio (services/titre_terminal.py).
definir_titre(f"toledo · backend (port {port_depuis_arguments(sys.argv)})")

@asynccontextmanager
async def _cycle_de_vie(_app: FastAPI):
    """
    Démarrage du serveur, pas de l'import : ces trois appels agissent sur les VRAIES données (registre, état
    des jobs, uploads, planificateur). À l'import, pytest les déclenchait dès la collecte, avant l'isolation
    des fixtures, et mettait en pause une vraie traduction en cours (audit api, 10/11/2026). Un TestClient
    ne les lance que s'il est ouvert avec `with`.
    """
    demarrer_surveillance()
    # Ménage des uploads abandonnés (jamais des traductions produites).
    purger_uploads_anciens()
    # Récupère les jobs coupés net par un arrêt du serveur — les QUATRE familles
    # (F7). La traduction repasse `en_pause` donc reprenable ; l'étude, le TTS et le
    # clonage passent à un statut terminal explicite, ce qui débloque leur interface :
    # un job d'étude resté `en_cours` gardait le panneau Résumé & Quiz du document
    # désactivé pour toujours, rechargement de page compris.
    recuperer_tout()
    yield


app = FastAPI(
    lifespan=_cycle_de_vie,
    title="Traducteur PDF API",
    description="Backend local pour la traduction de documents PDF via Ollama.",
    version="0.1.0",
)

# Autorise uniquement le frontend local à appeler cette API depuis le navigateur :
# http://localhost:5500 (python -m http.server ; jamais "null", voir validation.py).
# L'API acceptant des chemins absolus en entrée, un wildcard laisserait n'importe
# quel site visité déclencher des lectures/écritures de fichiers locaux.
# Le client Swift (URLSession) n'est pas soumis au CORS et n'est pas affecté.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ORIGINES_LOCALES),
    allow_methods=["*"],
    allow_headers=["*"],
)
# Le CORS ne fait qu'ajouter des en-têtes de réponse : un POST « simple » (paramètres d'URL, formulaire,
# multipart) part SANS préflight et s'exécute quand même. Toute requête qui écrit et porte une Origin hors
# allowlist est donc refusée ici, avant la route (un `reinitialiser` cross-site effaçait le scan d'un PDF).
# Sans Origin (curl, client Swift) : accepté.
@app.middleware("http")
async def _garde_origine(request: Request, call_next):
    origine = request.headers.get("origin")
    if request.method not in METHODES_SURES and origine is not None and origine not in ORIGINES_LOCALES:
        return JSONResponse(status_code=403, content={"detail": "Origine non autorisée."})
    return await call_next(request)


# Ajouté en dernier, donc exécuté en premier : un Host étranger (DNS rebinding) est refusé avant tout.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(HOTES_LOCAUX))

@app.exception_handler(ErreurMetier)
def _rendre_erreur_metier(request: Request, exc: ErreurMetier) -> JSONResponse:
    """
    Rend une erreur typée (principe cible ⑦) en gardant `detail` pour les clients
    existants et en ajoutant `erreur` pour ceux qui savent le lire.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "erreur": exc.erreur.model_dump()},
    )


app.include_router(router, prefix="/api")
