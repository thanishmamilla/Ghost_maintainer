"""FastAPI entrypoint for Ghost Maintainer."""

from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import router as api_router
from .config import get_settings
from .models import HealthResponse
from .sandbox import prune_workspaces

logger = logging.getLogger("ghost_maintainer")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    # httpx logs every request at INFO; keep the scan log readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
    prune_workspaces(settings.workspaces_dir)
    logger.info(
        "Ghost Maintainer %s starting (dry_run=%s, llm=%s, sandbox=%s)",
        __version__,
        settings.dry_run,
        settings.llm_configured,
        settings.sandbox_mode,
    )
    yield
    logger.info("Ghost Maintainer shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Ghost Maintainer",
        description="Finds rotten dependencies, proves reachability, fixes them with tests, prepares a PR.",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin, "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def timed_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        if request.url.path.startswith("/api/"):
            logger.info("%s %s -> %s in %.0fms", request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "error": exc.__class__.__name__},
        )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            dry_run=settings.dry_run,
            github_configured=settings.github_configured,
            llm_configured=settings.llm_configured,
            sandbox_mode=settings.sandbox_mode,
        )

    # API routers are mounted as each phase lands (scan, fix, review).
    app.include_router(api_router)
    return app


app = create_app()