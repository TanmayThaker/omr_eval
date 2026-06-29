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
from .detect import decode_answers, decode_roll, QuestionResult
from .overlay import draw_overlay


@dataclass
class ProcessResult:
    roll_number: str
    roll_confidence: float
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
    except (GridError, IndexError, ValueError):
        return None  # this orientation/method is unusable; another will win
    score = _score(geom, answers, roll, roll_conf, cfg)
    return _Attempt(gray, geom, answers, roll, roll_conf, skew, label, method, score)


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
    lowconf = sum(1 for a in best.answers if a.answer in "ABCDE" and a.confidence < 0.5)
    if lowconf:
        warnings.append(f"{lowconf} answer(s) marked with low confidence.")

    return ProcessResult(
        roll_number=best.roll,
        roll_confidence=best.roll_conf,
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
