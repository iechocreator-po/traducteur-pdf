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

app = FastAPI(
    title="Traducteur PDF API",
    description="Backend local pour la traduction de documents PDF via Ollama.",
    version="0.1.0",
)

# Autorise le frontend (servi séparément, ex: file:// ou http://localhost:5500)
# à appeler cette API depuis le navigateur.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
