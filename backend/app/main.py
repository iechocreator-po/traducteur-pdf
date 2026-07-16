"""
Point d'entrée de l'application backend.

Lancer en développement :
    uvicorn app.main:app --reload --port 8000

La documentation interactive de l'API est alors disponible sur :
    http://localhost:8000/docs

"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.validation import ORIGINES_LOCALES
from app.services.scheduler import demarrer_surveillance
from app.services.uploads import purger_uploads_anciens



app = FastAPI(
    title="Traducteur PDF API",
    description="Backend local pour la traduction de documents PDF via Ollama.",
    version="0.1.0",
)

# Autorise uniquement le frontend local à appeler cette API depuis le navigateur :
# http://localhost:5500 (python -m http.server) et "null" (fichier ouvert en file://).
# L'API acceptant des chemins absolus en entrée, un wildcard laisserait n'importe
# quel site visité déclencher des lectures/écritures de fichiers locaux.
# Le client Swift (URLSession) n'est pas soumis au CORS et n'est pas affecté.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ORIGINES_LOCALES),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

demarrer_surveillance()
# Ménage des uploads abandonnés (jamais des traductions produites) au démarrage.
purger_uploads_anciens()

