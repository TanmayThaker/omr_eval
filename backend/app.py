"""FastAPI server for OMR extraction + QC."""
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlsplit
import io
import csv
import os
import tempfile
import uuid

from fastapi import APIRouter, FastAPI, UploadFile, File, HTTPException, Security, Depends, Request
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from omr.config import OMRConfig
from omr.pipeline import process_document
from models import CorrectRequest
from store import store, Session

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

_API_KEY = os.getenv("OMR_API_KEY")  # unset → auth disabled (dev mode)
_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _is_trusted_browser(request: Request) -> bool:
    """True for requests originating from the app's own UI: same-origin, or an
    Origin in the configured CORS allow-list (covers the Vite dev server). The UI
    is served from this same origin on a (private) Space, so it needs no key — and
    plain <img>/<a> GETs can't send a header anyway. Programmatic callers (curl,
    scripts) send no Origin/Referer and fall through to API-key auth below."""
    origin = request.headers.get("origin")
    if origin and origin in _origins:
        return True
    host = request.headers.get("host", "")
    for src in (origin, request.headers.get("referer")):
        if src and host and urlsplit(src).netloc == host:
            return True
    return False


async def _require_key(request: Request, key: str = Security(_key_header)):
    if not _API_KEY:
        return                              # auth disabled (dev mode)
    if key == _API_KEY:
        return                              # programmatic caller with a valid key
    if _is_trusted_browser(request):
        return                              # same-origin UI on a private Space
    raise HTTPException(401, "Invalid or missing X-API-Key header")


app = FastAPI(title="OMR Evaluation API", version="1.0")

# Origins can be overridden via ALLOWED_ORIGINS env var (comma-separated).
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All /api/* routes share the key guard via the router dependency.
api = APIRouter(prefix="/api", dependencies=[Depends(_require_key)])


@api.get("/health")
def health():
    return {"status": "ok"}


@api.post("/extract")
async def extract(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type {ext!r}. "
                                 f"Allowed: {sorted(ALLOWED_EXT)}")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large — maximum is {MAX_UPLOAD_BYTES // (1024*1024)} MB.")

    session_id = uuid.uuid4().hex[:12]
    sess_dir = store_dir(session_id)
    sess_dir.mkdir(parents=True, exist_ok=True)
    src = sess_dir / f"source{ext}"
    src.write_bytes(content)

    cfg = OMRConfig()
    try:
        result = process_document(str(src), cfg)
    except Exception as e:
        raise HTTPException(422, f"OMR processing failed: {e}")

    sess = store.create(session_id, file.filename or src.name, cfg, result)
    return store.result_dict(sess)


@api.get("/result/{session_id}")
def get_result(session_id: str):
    sess = _require(session_id)
    return store.result_dict(sess)


@api.get("/scan/{session_id}")
def get_scan(session_id: str):
    sess = _require(session_id)
    return FileResponse(sess.dir / "scan.png", media_type="image/png")


@api.get("/overlay/{session_id}")
def get_overlay(session_id: str):
    sess = _require(session_id)
    return FileResponse(sess.dir / "overlay.png", media_type="image/png")


@api.post("/correct/{session_id}")
def correct(session_id: str, req: CorrectRequest):
    sess = _require(session_id)
    corrections = {c.question: c.answer.upper() for c in req.corrections}
    series = req.series.upper() if req.series else None
    store.apply_corrections(sess, req.roll_number, corrections, series)
    return store.result_dict(sess)


@api.get("/result/{session_id}/csv")
def get_csv(session_id: str):
    sess = _require(session_id)
    data = store.result_dict(sess)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["roll_number", data["roll_number"]])
    w.writerow(["series", data.get("series") or ""])
    w.writerow([])
    w.writerow(["question", "answer", "confidence", "corrected"])
    for a in data["answers"]:
        w.writerow([a["question"], a["answer"], a["confidence"],
                    "yes" if a["corrected"] else ""])
    fname = f"omr_{session_id}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@api.get("/result/{session_id}/json")
def get_json_download(session_id: str):
    """Compact export: {"roll_number", "series", "responses": {...}}."""
    sess = _require(session_id)
    fname = f"omr_{session_id}.json"
    return JSONResponse(store.compact_dict(sess),
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@api.get("/result/{session_id}/full")
def get_full_json(session_id: str):
    """Full session JSON (geometry, confidence, quality) for tooling."""
    return store.result_dict(_require(session_id))


@api.post("/process")
async def process(file: UploadFile = File(...)):
    """Stateless OMR: PDF in → structured JSON out. No session, no disk storage."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type {ext!r}. "
                                 f"Allowed: {sorted(ALLOWED_EXT)}")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large — maximum is {MAX_UPLOAD_BYTES // (1024*1024)} MB.")

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    cfg = OMRConfig()
    try:
        result = process_document(tmp_path, cfg)
    except Exception as e:
        raise HTTPException(422, f"OMR processing failed: {e}")
    finally:
        os.unlink(tmp_path)
    return result.compact_dict()


app.include_router(api)


# --- helpers ---
def store_dir(session_id: str):
    from store import DATA_DIR
    return DATA_DIR / session_id


def _require(session_id: str) -> Session:
    sess = store.get(session_id)
    if sess is None:
        raise HTTPException(404, "Session not found (server may have restarted)")
    return sess


# --- serve built frontend if present (production) ---
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
