"""FastAPI server for stateless OMR extraction — fast, bounded, fail-closed auth.

PDF/image in → structured JSON out. Nothing is persisted: each request renders to
a temp file deleted before the response returns. The CPU-bound pipeline runs in a
bounded thread pool, so the event loop stays responsive and a burst of requests
can't exhaust memory. Access requires a valid X-API-Key; with no keys configured
the API is locked (fail-closed) rather than open.
"""
from __future__ import annotations
from pathlib import Path
import hmac
import logging
import os
import tempfile

import anyio
from fastapi import (APIRouter, FastAPI, UploadFile, File, HTTPException,
                     Security, Depends, Request)
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware

from omr.config import OMRConfig
from omr.pipeline import process_document

log = logging.getLogger("omr.api")

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


# --- authentication: fail-closed, multi-key, constant-time -----------------
def _load_keys() -> frozenset[str]:
    """Authorized keys, one per user, from OMR_API_KEYS (comma-separated).
    Legacy single-key OMR_API_KEY is still honored."""
    raw = os.getenv("OMR_API_KEYS") or os.getenv("OMR_API_KEY") or ""
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


_API_KEYS = _load_keys()
_AUTH_DISABLED = os.getenv("OMR_AUTH_DISABLED", "").strip().lower() in {"1", "true", "yes"}
_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _key_valid(presented: str | None) -> bool:
    if not presented:
        return False
    # Compare against every configured key (no early exit) in constant time, so
    # neither validity nor key count leaks via response timing.
    valid = False
    for k in _API_KEYS:
        if hmac.compare_digest(presented, k):
            valid = True
    return valid


async def _require_key(key: str = Security(_key_header)):
    if _AUTH_DISABLED:
        return                                  # explicit local-dev opt-out only
    if not _API_KEYS:
        raise HTTPException(503, "API authentication is not configured.")  # fail closed
    if not _key_valid(key):
        raise HTTPException(401, "Invalid or missing X-API-Key header.")


# --- concurrency: bound CPU jobs so bursts can't exhaust memory/CPU --------
_MAX_CONCURRENCY = int(os.getenv("OMR_MAX_CONCURRENCY", str(max(1, os.cpu_count() or 2))))
_limiter = anyio.CapacityLimiter(_MAX_CONCURRENCY)

# Interactive docs / OpenAPI schema off by default to minimize surface area.
_ENABLE_DOCS = os.getenv("OMR_ENABLE_DOCS", "").strip().lower() in {"1", "true", "yes"}
app = FastAPI(
    title="OMR Evaluation API",
    version="2.0",
    docs_url="/docs" if _ENABLE_DOCS else None,
    redoc_url="/redoc" if _ENABLE_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_DOCS else None,
)

# CORS off unless ALLOWED_ORIGINS is set (this is a server-to-server API).
_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins,
                       allow_methods=["POST"], allow_headers=["X-API-Key"])


@app.on_event("startup")
async def _log_auth_posture():
    if _AUTH_DISABLED:
        log.warning("OMR API auth DISABLED (OMR_AUTH_DISABLED) — never use in production.")
    elif not _API_KEYS:
        log.warning("OMR API locked: no OMR_API_KEYS configured — all /api requests return 503.")
    else:
        log.info("OMR API ready: %d authorized key(s), max concurrency %d.",
                 len(_API_KEYS), _MAX_CONCURRENCY)


@app.middleware("http")
async def _limit_request_size(request: Request, call_next):
    """Reject oversized uploads via Content-Length before any body parsing."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_UPLOAD_BYTES + 1024:
        return JSONResponse(
            {"detail": f"Request too large — maximum is {MAX_UPLOAD_MB} MB."},
            status_code=413)
    return await call_next(request)


@app.get("/")
def root():
    return {"status": "ok"}


# Public liveness probe (no key) so platform/uptime checks work; exposes nothing.
@app.get("/api/health")
def health():
    return {"status": "ok"}


api = APIRouter(prefix="/api", dependencies=[Depends(_require_key)])


@api.post("/process")
async def process(file: UploadFile = File(...)):
    """Stateless OMR: PDF/image in → structured JSON out. No session, no storage."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type {ext!r}. "
                                 f"Allowed: {sorted(ALLOWED_EXT)}")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large — maximum is {MAX_UPLOAD_MB} MB.")

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    cfg = OMRConfig()
    try:
        # Run the CPU-bound pipeline off the event loop, bounded by the limiter:
        # one request can't block others, and bursts queue instead of exhausting RAM.
        result = await anyio.to_thread.run_sync(
            process_document, tmp_path, cfg, limiter=_limiter)
    except Exception as e:
        raise HTTPException(422, f"OMR processing failed: {e}")
    finally:
        os.unlink(tmp_path)
    return result.compact_dict()


app.include_router(api)
