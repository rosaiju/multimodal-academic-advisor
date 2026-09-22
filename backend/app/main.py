"""FastAPI application entry point.

Run:  uvicorn app.main:app --reload
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.catalog.registry import registry
from app.config import get_settings
from app.routers import audit as audit_router
from app.routers import auth as auth_router
from app.routers import catalog as catalog_router
from app.routers import ingest as ingest_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Deliberately NOT wrapped in try/except: a malformed catalog must stop the
    # server, not start it with silently-wrong degree requirements.
    registry.load(settings.catalog_dir)

    # Registered here, from outside the ingestion package, so app/ingestion/ never
    # imports app/llm/. Absent an API key this is a no-op and text transcripts
    # keep working.
    from app.llm.registration import register_vision_extractor

    vision = register_vision_extractor(settings)
    logger.info("vision transcript extractor: %s", vision or "not configured")

    logger.info(
        "catalog ready (%d programs); llm_provider=%s configured=%s",
        len(registry.list_programs()),
        settings.llm_provider,
        settings.llm_configured,
    )
    yield


app = FastAPI(
    title="Multimodal AI Academic Advisor",
    description=(
        "COSC 490 senior project. Degree progress is computed by a deterministic "
        "rules engine, never by a language model. All student data is fictional."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Without this a browser cannot READ Retry-After on a 429, even though the
    # server sent it. In development the Vite proxy makes requests same-origin
    # and CORS never applies; in production it would.
    expose_headers=["Retry-After"],
)


@app.get("/health", tags=["system"])
def health() -> dict[str, object]:
    settings = get_settings()
    from app.ingestion.extractor import registered_extractors

    extractors = registered_extractors()
    return {
        "status": "ok",
        "catalog_loaded": registry.is_loaded,
        "programs": [p.program_id for p in registry.list_programs()],
        "llm_provider": settings.llm_provider,
        "llm_configured": settings.llm_configured,
        # A restart invalidates every token when no secret is configured, so
        # say so rather than letting the team debug mysterious sign-outs.
        "auth_secret_is_ephemeral": settings.jwt_secret_is_ephemeral,
        # Which transcript formats actually work right now. Without a configured
        # provider the vision reader is absent and scans cannot be processed,
        # which is worth surfacing rather than discovering on a 415.
        "transcript_extractors": extractors,
        "accepts_scanned_transcripts": any(e.startswith("vision:") for e in extractors),
    }


@app.get("/programs", tags=["catalog"])
def list_programs() -> list[dict[str, object]]:
    """Every degree program the engine can audit against."""
    return [
        {
            "program_id": p.program_id,
            "program": p.program,
            "institution": p.institution,
            "catalog_year": p.catalog_year,
            "total_credits_required": p.total_credits_required,
            "requirement_blocks": len(p.requirement_blocks),
            "courses": len(p.courses),
        }
        for p in registry.list_programs()
    ]


# Auth first: everything below it is reachable only with a token from here.
app.include_router(auth_router.router)
app.include_router(catalog_router.router)
app.include_router(ingest_router.router)
app.include_router(audit_router.router)

# Remaining routers land here as each owner delivers them:
#   app.include_router(chat.router)      # Person 2
#   app.include_router(students.router)  # Person 3
