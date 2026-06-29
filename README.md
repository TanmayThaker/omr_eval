---
title: OMR Eval
emoji: 📄
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# OMR Answer-Sheet Evaluation

Extracts the roll number, the paper series (booklet), and 200 answers
(5 options each) from a scanned OMR sheet (GN-107 / GN-101 family) and produces
CSV/JSON plus a visual QC overlay.

### Result JSON (compact export — `GET /api/result/{id}/json`)
```json
{
  "roll_number": 101005721,
  "series": "A",
  "responses": {"1": "C", "2": "A", "...": "..."},
  "needs_review": true,
  "messages": [
    "Grid alignment looks imperfect — please rescan/realign the sheet (flat, full page, upright) and submit again.",
    "3 question(s) have multiple marks — verify: [10, 75, 150].",
    "6 question(s) appear unanswered — verify: [5, 30, 60, 111, 123, 200]."
  ],
  "review": {
    "roll_confidence": 1.0,
    "series_confidence": 1.0,
    "unanswered": [5, 30, 60, 111, 123, 200],
    "multiple_marked": [10, 75, 150],
    "low_confidence": [88]
  }
}
```
`roll_number` is an int when fully numeric; `series` is the filled booklet
letter (A–H or A–D depending on the sheet); `responses` covers all 200
questions (value is `A`–`E`, or `BLANK` / `MULTI`).

**Actionable feedback** so the submitter can realign/resubmit or verify manually:
- `needs_review` — `true` when anything needs attention (otherwise a clean pass).
- `messages` — human-readable notes: auto-corrections applied (orientation,
  inversion, skew, rescale), a **rescan/realign** prompt when grid alignment is
  poor, and which fields/questions to verify.
- `review` — exact items to check: `unanswered` (blank), `multiple_marked`,
  `low_confidence` question numbers, plus roll/series confidence.

All of this reflects **manual corrections** — once you fix a flagged item and
save, it drops out of `review` and the messages. A clean, fully-marked sheet
returns `needs_review: false` with no messages.

## Status
- [x] **Milestone 1–2: OMR core, verified on the reference sheet** — 200/200 answers
      and roll number `107030379` decoded correctly; pixel-perfect overlay.
- [x] **Milestone 3: FastAPI endpoints** — extract / scan / overlay / result / correct,
      CSV+JSON export, static frontend serving.
- [x] **Milestone 4: React + Vite QC UI** — upload, zoom/pan overlay viewer with
      click-to-locate, color-coded editable 200-row table, roll edit, CSV/JSON export.
      Verified end-to-end in a headless browser (200 rows, roll, badges).
- [x] **Milestone 5: real-world robustness + tests** — registration pipeline,
      auto-calibrated thresholds, marking-quality logic, graceful failure with
      warnings. 34/35 synthetic degradations decode at 100%; see below.

## Real-world robustness

The pipeline normalizes each sheet before decoding so it tolerates the
conditions a real scanner or phone camera produces. Auto-corrections are
reported back to the UI (blue info bar) and flagged as warnings.

**Handled (verified at 100% on synthetic degradations):**
- **Layout variants** — works across sheet variants of this family that place the
  answer grid at different heights (e.g. a shorter A–D booklet block vs. A–H, as in
  the `GN-101`/ACF sheet). The answer grid and roll grid are located adaptively as
  the regular run of evenly-spaced rows, not by fixed coordinates, so the grid can
  start higher or lower. Validated on both the `GN-107` and `GN-101` (ACF) samples.
- **Orientation** — upside-down (180°) and sideways (90°/270°), resolved by
  decoding each candidate and scoring geometry + roll confidence.
- **Skew** — scanner-feed rotation up to ±8° (projection-profile deskew).
- **Perspective / translation / scale / crop** — single homography to a
  canonical frame using the corner registration marks.
- **Resolution** — low-DPI scans / small photos are upscaled; high-DPI downscaled.
- **Inversion** — white-on-black scans auto-detected and inverted.
- **Lighting** — dark, bright, low-contrast, uneven illumination.
- **Noise / artifacts** — Gaussian noise, salt-and-pepper, JPEG q20, blur, stray pen marks.
- **Ink** — black or blue ballpoint (min-channel ink extraction).
- **Marking quality** — blank (unanswered), double-marked (→ MULTI), partial
  fills, check/X marks, and erase-and-rechoose; per-sheet Otsu-calibrated
  fill threshold adapts to faint pencil or heavy marker.

**Graceful failure (flagged, never silently wrong):** extreme keystone
(>~8% perspective) and large free rotations (>~10°) on a borderless background
are reported with low quality + "review the overlay" warnings rather than
confident wrong answers. A non-OMR image is rejected or flagged.

**Quality signals** in every result: `orientation`, `inverted`,
`skew_applied_deg`, `resolution_scale`, `quality`, and per-answer `confidence`
(low-confidence answers are counted and badged in the UI).

## Tests
```
pytest tests/test_pipeline.py                      # core guarantees
python tests/robustness/generate_variants.py       # build degraded fixtures
python tests/robustness/evaluate.py                # per-condition accuracy table
python tests/robustness/marking_quality.py         # blank/multi/partial/check/erasure
python scripts/verify_reference.py                 # clean-sheet sanity + artifacts
```

## Run the app

**Quickest (Windows):** double-click `run.bat` — it builds the UI if needed and
serves everything at http://127.0.0.1:8000.

**Manual:**
```
# 1. backend deps (conda `all` env)
pip install -r requirements.txt

# 2. build the UI once
cd frontend && npm install && npm run build && cd ..

# 3. serve (UI + API on one origin)
cd backend && python -m uvicorn app:app --host 127.0.0.1 --port 8000
```
Open http://127.0.0.1:8000 → drop a PDF/image → review the overlay, fix any
bubble in the table, export CSV/JSON.

**UI dev mode (hot reload):** run the backend on :8000, then
`cd frontend && npm run dev` (Vite on :5173 proxies `/api` to :8000).

## API
| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/extract` | upload PDF/image → result JSON (roll, 200 answers, per-bubble coords) |
| GET  | `/api/scan/{id}` / `/api/overlay/{id}` | original scan / annotated QC image |
| GET  | `/api/result/{id}` | result JSON |
| POST | `/api/correct/{id}` | save roll/series/answer corrections |
| GET  | `/api/result/{id}/json` | compact export (roll, series, responses) |
| GET  | `/api/result/{id}/csv` | CSV (roll, series, per-question) |
| GET  | `/api/result/{id}/full` | full session JSON (geometry, confidence, quality) |

## How it works
Cluster-based, self-calibrating pipeline (no hard-coded pixel template), so it
tolerates scale/translation and minor skew across scans of this sheet family:

1. **Render / load** PDF or image to ink-maximizing grayscale — `omr/pdf.py`
2. **Register** fix inversion, normalize resolution, then per orientation try a
   corner-mark homography and a projection deskew; the best-scoring attempt wins
   — `omr/register.py`
3. **Grid detection** cluster bubble candidates into 4 blocks x 5 options x 50 rows,
   plus the 9x10 roll-number grid — `omr/grid.py`
4. **Decode** per-bubble dark-fill ratio with a per-sheet Otsu-calibrated
   threshold + winner/blank/multi decision and confidence; also decodes the
   roll-number grid and the paper-series column — `omr/detect.py`
5. **Overlay** annotated QC image (green=marked, teal=locator, red=blank/multi)
   — `omr/overlay.py`
6. **Pipeline** orchestration, quality/warnings, JSON/CSV/PNG output — `omr/pipeline.py`

The fill separation on the reference is large (~0.27 empty vs ~0.94 marked), so
decisions are unambiguous.

## Setup (conda `all` env)
```
pip install -r requirements.txt
```

## Verify on the reference sheet
```
python scripts/verify_reference.py
```
Writes `data/verify/overlay.png`, `result.json`, `answers.csv`.

## Layout
```
backend/omr/   core pipeline (pure, testable)
scripts/       verification harness
frontend/      React+Vite QC UI (Milestone 4)
data/          generated artifacts (gitignored)
```
