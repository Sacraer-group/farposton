"""Lightweight FastAPI entrypoint for the Evening MVP (Docker/standalone)."""
from __future__ import annotations

import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from evening.routes import router as evening_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ensure seed exists in writable demo dir on startup
    from evening.seed import load_or_create_seed
    load_or_create_seed()
    yield


app = FastAPI(title="Evening MVP", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("WEB_SECRET_KEY", secrets.token_urlsafe(32)),
    max_age=int(os.getenv("SESSION_TTL_SECONDS", "43200")),
    same_site="strict",
    https_only=False,
)

# health endpoint compatible with compose healthcheck expectations if needed
@app.get("/health")
def health():
    return {"ok": True}

app.include_router(evening_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "evening_app:app",
        host=os.getenv("WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("WEB_PORT", "18447")),
        reload=False,
    )
