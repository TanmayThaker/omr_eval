"""FastAPI server for OMR extraction + QC."""
from __future__ import annotations
from pathlib import Path
import io
import csv
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from omr.config import OMRConfig
from omr.pipeline import process_document
from models import CorrectRequest
from store import store, Session

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = FastAPI(title="OMR Evaluation API", version="1.0")

# Dev: allow the Vite dev server (5173) to call the API on 8000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type {ext!r}. "
                                 f"Allowed: {sorted(ALLOWED_EXT)}")
    session_id = uuid.uuid4().hex[:12]
    sess_dir = store_dir(session_id)
    sess_dir.mkdir(parents=True, exist_ok=True)
    src = sess_dir / f"source{ext}"
    src.write_bytes(await file.read())

    cfg = OMRConfig()
    try:
        result = process_document(str(src), cfg)
    except Exception as e:  # surface processing failures cleanly
        raise HTTPException(422, f"OMR processing failed: {e}")

    sess = store.create(session_id, file.filename or src.name, cfg, result)
    return store.result_dict(sess)


@app.get("/api/result/{session_id}")
def get_result(session_id: str):
    sess = _require(session_id)
    return store.result_dict(sess)


@app.get("/api/scan/{session_id}")
def get_scan(session_id: str):
    sess = _require(session_id)
    return FileResponse(sess.dir / "scan.png", media_type="image/png")


@app.get("/api/overlay/{session_id}")
def get_overlay(session_id: str):
    sess = _require(session_id)
    return FileResponse(sess.dir / "overlay.png", media_type="image/png")


@app.post("/api/correct/{session_id}")
def correct(session_id: str, req: CorrectRequest):
    sess = _require(session_id)
    corrections = {c.question: c.answer.upper() for c in req.corrections}
    series = req.series.upper() if req.series else None
    store.apply_corrections(sess, req.roll_number, corrections, series)
    return store.result_dict(sess)


@app.get("/api/result/{session_id}/csv")
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


@app.get("/api/result/{session_id}/json")
def get_json_download(session_id: str):
    """Compact export: {"roll_number", "series", "responses": {...}}."""
    sess = _require(session_id)
    fname = f"omr_{session_id}.json"
    return JSONResponse(store.compact_dict(sess),
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/result/{session_id}/full")
def get_full_json(session_id: str):
    """Full session JSON (geometry, confidence, quality) for tooling."""
    return store.result_dict(_require(session_id))


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
