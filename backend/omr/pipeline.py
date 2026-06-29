"""End-to-end OMR pipeline orchestration with real-world registration.

Flow: load -> ink grayscale -> fix inversion -> normalize resolution ->
for each candidate orientation { deskew -> binarize -> detect grid -> decode }
-> keep the highest-scoring attempt. Scoring favors exact geometry, a complete
high-confidence roll number, and many confident single marks, which together
pin the correct reading orientation (incl. 90/180/270 and upside-down).
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import cv2

from .config import OMRConfig
from .pdf import load_grayscale
from .preprocess import binarize
from .register import (fix_inversion, normalize_resolution, deskew,
                        orientation_candidates, register_to_canonical)
from .grid import detect_grid, GridGeometry, GridError
from .detect import decode_answers, decode_roll, decode_series, QuestionResult
from .overlay import draw_overlay


def _auto_correction_messages(res: "ProcessResult") -> list[str]:
    """Informational notes about adjustments the system made to the image."""
    m = []
    if res.inverted:
        m.append("Image colours were inverted (white-on-black) and corrected.")
    if res.orientation and res.orientation != "0":
        m.append(f"Sheet was re-oriented automatically (was '{res.orientation}').")
    if abs(res.skew_applied) >= 0.25:
        m.append(f"Skew of {res.skew_applied:.1f}° was corrected.")
    if res.resolution_scale and res.resolution_scale != 1.0:
        m.append(f"Image was rescaled ×{res.resolution_scale:.2f} for processing.")
    return m


def build_review(view, roll, roll_conf, series, series_conf, res) -> dict:
    """Build the actionable-feedback block for an export.

    `view` is an iterable of (question, answer, confidence, corrected). Returns
    {needs_review, messages, review} where `messages` are human-readable and
    `review` lists exactly which questions/fields to check. Corrected items are
    treated as verified (excluded from low-confidence).
    """
    unanswered, multiple, low = [], [], []
    for q, ans, conf, corrected in view:
        if ans == "BLANK":
            unanswered.append(q)
        elif ans == "MULTI":
            multiple.append(q)
        elif ans in "ABCDE" and conf < 0.5 and not corrected:
            low.append(q)
    unanswered.sort(); multiple.sort(); low.sort()

    roll_ok = bool(roll) and "?" not in str(roll) and roll_conf >= 0.5
    series_ok = bool(series) and series != "?" and series_conf >= 0.5
    geometry_imperfect = any("geometry imperfect" in w.lower() or "could not"
                             in w.lower() for w in res.warnings)

    messages = _auto_correction_messages(res)
    if geometry_imperfect:
        messages.append("Grid alignment looks imperfect — please rescan/realign "
                        "the sheet (flat, full page, upright) and submit again.")
    if not roll_ok:
        messages.append("Roll number could not be read confidently — verify it manually.")
    if not series_ok:
        messages.append("Paper series could not be read confidently — verify it manually.")
    if multiple:
        messages.append(f"{len(multiple)} question(s) have multiple marks — verify: "
                        f"{multiple}.")
    if unanswered:
        messages.append(f"{len(unanswered)} question(s) appear unanswered — verify: "
                        f"{unanswered}.")
    if low:
        messages.append(f"{len(low)} answer(s) are low-confidence — verify: {low}.")

    needs_review = bool(geometry_imperfect or not roll_ok or not series_ok
                        or unanswered or multiple or low)
    return {
        "needs_review": needs_review,
        "messages": messages,
        "review": {
            "roll_confidence": round(roll_conf, 3),
            "series_confidence": round(series_conf, 3),
            "unanswered": unanswered,
            "multiple_marked": multiple,
            "low_confidence": low,
        },
    }


@dataclass
class ProcessResult:
    roll_number: str
    roll_confidence: float
    series: str
    series_confidence: float
    answers: list[QuestionResult]
    geometry: GridGeometry
    gray: np.ndarray = field(repr=False)
    skew_applied: float = 0.0
    orientation: str = "0"
    inverted: bool = False
    resolution_scale: float = 1.0
    quality: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        marked = sum(1 for a in self.answers if a.answer in "ABCDE")
        blank = sum(1 for a in self.answers if a.answer == "BLANK")
        multi = sum(1 for a in self.answers if a.answer == "MULTI")
        lowconf = sum(1 for a in self.answers if a.answer in "ABCDE" and a.confidence < 0.5)
        return {"total": len(self.answers), "marked": marked, "blank": blank,
                "multi": multi, "low_confidence": lowconf}

    def to_dict(self) -> dict:
        return {
            "roll_number": self.roll_number,
            "roll_confidence": self.roll_confidence,
            "series": self.series,
            "series_confidence": self.series_confidence,
            "skew_applied_deg": round(self.skew_applied, 3),
            "orientation": self.orientation,
            "inverted": self.inverted,
            "resolution_scale": round(self.resolution_scale, 3),
            "quality": round(self.quality, 3),
            "counts": self.counts(),
            "warnings": self.warnings,
            "answers": [
                {"question": a.question, "answer": a.answer,
                 "confidence": a.confidence, "fills": a.fills}
                for a in self.answers
            ],
        }

    def compact_dict(self) -> dict:
        """The requested export shape:
        {"roll_number": <int>, "series": "A", "responses": {"1": "A", ...}}
        roll_number is an int when fully numeric, else the raw string.
        """
        roll = int(self.roll_number) if self.roll_number.isdigit() else self.roll_number
        view = [(a.question, a.answer, a.confidence, False) for a in self.answers]
        out = {
            "roll_number": roll,
            "series": self.series or None,
            "responses": {str(a.question): a.answer for a in self.answers},
        }
        out.update(build_review(view, self.roll_number, self.roll_confidence,
                                self.series, self.series_confidence, self))
        return out

    def overlay_png(self, cfg: OMRConfig) -> bytes:
        img = draw_overlay(self.gray, self.geometry, self.answers, cfg)
        ok, buf = cv2.imencode(".png", img)
        return buf.tobytes() if ok else b""


@dataclass
class _Attempt:
    gray: np.ndarray
    geom: GridGeometry
    answers: list[QuestionResult]
    roll: str
    roll_conf: float
    series: str
    series_conf: float
    skew: float
    orientation: str
    method: str
    score: float


def _geometry_exact(geom: GridGeometry, cfg: OMRConfig) -> bool:
    return (len(geom.block_cols) == cfg.num_blocks
            and all(len(c) == cfg.options_per_question for c in geom.block_cols)
            and all(len(r) == cfg.rows_per_block for r in geom.block_rows))


def _score(geom, answers, roll, roll_conf, cfg) -> float:
    exact = _geometry_exact(geom, cfg)
    confident = sum(1 for a in answers if a.answer in "ABCDE" and a.confidence >= 0.5)
    roll_complete = bool(roll) and "?" not in roll and len(roll) == cfg.roll_digits
    return (5000.0 * exact
            + 2000.0 * roll_complete
            + 1000.0 * roll_conf
            + float(confident))


def _attempt(gray_in: np.ndarray, label: str, method: str,
             cfg: OMRConfig) -> _Attempt | None:
    if method == "register":
        gray = register_to_canonical(gray_in, cfg)
        if gray is None:
            return None
        skew = 0.0
    else:
        gray, skew = deskew(gray_in, cfg)
    bw = binarize(gray)
    try:
        geom = detect_grid(bw, cfg)
        answers = decode_answers(bw, geom, cfg)
        roll, roll_conf = decode_roll(bw, geom, cfg)
        series, series_conf = decode_series(bw, geom, cfg)
    except (GridError, IndexError, ValueError):
        return None  # this orientation/method is unusable; another will win
    score = _score(geom, answers, roll, roll_conf, cfg)
    return _Attempt(gray, geom, answers, roll, roll_conf, series, series_conf,
                    skew, label, method, score)


def process_document(path: str, cfg: OMRConfig | None = None,
                     page_index: int = 0) -> ProcessResult:
    cfg = cfg or OMRConfig()
    gray = load_grayscale(path, dpi=cfg.render_dpi, page_index=page_index)
    gray, inverted = fix_inversion(gray)
    gray, res_scale = normalize_resolution(gray)

    # For each plausible orientation, try homography registration (handles
    # perspective/translate/scale) and projection deskew; score picks the best.
    attempts = [a for a in (
        _attempt(g, label, method, cfg)
        for label, g in orientation_candidates(gray)
        for method in ("register", "deskew")) if a]
    if not attempts:
        raise GridError(
            "Could not locate the answer grid in any orientation. "
            "The image may be too low-resolution, cropped, or not this sheet type.")

    best = max(attempts, key=lambda a: a.score)

    warnings = list(best.geom.warnings)
    if inverted:
        warnings.append("Image was inverted (white-on-black) and corrected.")
    if best.orientation != "0":
        warnings.append(f"Sheet re-oriented from '{best.orientation}'.")
    if abs(best.skew) >= cfg.deskew_min_deg:
        warnings.append(f"Corrected skew of {best.skew:.1f}°.")
    if res_scale != 1.0:
        warnings.append(f"Image rescaled x{res_scale:.2f} for processing.")
    if best.method == "register":
        warnings.append("Registered to canonical frame via corner marks.")
    if not _geometry_exact(best.geom, cfg):
        warnings.append("Grid geometry imperfect — review the overlay carefully.")
    if best.roll_conf < 0.5 or "?" in best.roll:
        warnings.append("Roll number low confidence — verify manually.")
    if not best.series or best.series == "?":
        warnings.append("Paper series not detected — verify manually.")
    elif best.series_conf < 0.5:
        warnings.append("Paper series low confidence — verify manually.")
    lowconf = sum(1 for a in best.answers if a.answer in "ABCDE" and a.confidence < 0.5)
    if lowconf:
        warnings.append(f"{lowconf} answer(s) marked with low confidence.")

    return ProcessResult(
        roll_number=best.roll,
        roll_confidence=best.roll_conf,
        series=best.series,
        series_confidence=best.series_conf,
        answers=best.answers,
        geometry=best.geom,
        gray=best.gray,
        skew_applied=best.skew,
        orientation=best.orientation,
        inverted=inverted,
        resolution_scale=res_scale,
        quality=best.score,
        warnings=warnings,
    )
