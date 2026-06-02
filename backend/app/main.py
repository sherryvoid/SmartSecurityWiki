import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, health, models, projects
from app.core.config import get_settings
from app.db.database import init_db
from app.services.vector_index import get_embedding_mode


settings = get_settings()
logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).info(
    "Configured embedding provider=%s model=%s. Semantic provider falls back to hash if unavailable.",
    settings.embedding_provider,
    settings.embedding_model,
)
logging.getLogger(__name__).info("[VectorIndex] Embedding mode: %s", get_embedding_mode())
init_db()

app = FastAPI(title="Security CodeWiki", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(models.router, prefix="/api")
