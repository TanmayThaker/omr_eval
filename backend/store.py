"""Session storage: keeps results in memory and persists artifacts to disk.

Each session directory under data/sessions/<id>/ holds:
  scan.png      - rendered grayscale page (for the UI's original view)
  overlay.png   - annotated QC overlay
  result.json   - full structured result (updated on corrections)
"""
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field
import json
import threading
import cv2

from omr.config import OMRConfig
from omr.pipeline import ProcessResult
from omr.overlay import draw_overlay

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"


@dataclass
class Session:
    session_id: str
    filename: str
    cfg: OMRConfig
    result: ProcessResult
    # corrections: question -> answer; roll/series overrides
    answer_overrides: dict[int, str] = field(default_factory=dict)
    roll_override: str | None = None
    series_override: str | None = None

    @property
    def dir(self) -> Path:
        return DATA_DIR / self.session_id

    # --- effective (corrected) views ---
    def effective_roll(self) -> str:
        return self.roll_override if self.roll_override is not None else self.result.roll_number

    def effective_series(self) -> str:
        return self.series_override if self.series_override is not None else self.result.series

    def effective_answer(self, question: int) -> str:
        if question in self.answer_overrides:
            return self.answer_overrides[question]
        return self.result.answers[question - 1].answer


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def create(self, session_id: str, filename: str, cfg: OMRConfig,
               result: ProcessResult) -> Session:
        sess = Session(session_id, filename, cfg, result)
        with self._lock:
            self._sessions[session_id] = sess
        sess.dir.mkdir(parents=True, exist_ok=True)
        # persist scan + overlay
        cv2.imwrite(str(sess.dir / "scan.png"), result.gray)
        cv2.imwrite(str(sess.dir / "overlay.png"),
                    draw_overlay(result.gray, result.geometry, result.answers, cfg))
        self._write_json(sess)
        return sess

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def apply_corrections(self, sess: Session, roll: str | None,
                          corrections: dict[int, str], series: str | None = None) -> None:
        with self._lock:
            if roll is not None:
                sess.roll_override = roll
            if series is not None:
                sess.series_override = series
            sess.answer_overrides.update(corrections)
        self._write_json(sess)

    def compact_dict(self, sess: Session) -> dict:
        """Requested export shape using corrected values, plus actionable feedback:
        {"roll_number", "series", "responses", "needs_review", "messages", "review"}.
        Reflects manual corrections (corrected items are treated as verified)."""
        roll = sess.effective_roll()
        out = {
            "roll_number": int(roll) if roll.isdigit() else roll,
            "series": sess.effective_series() or None,
            "responses": {str(a.question): sess.effective_answer(a.question)
                          for a in sess.result.answers},
        }
        out.update(self._review(sess))
        return out

    # --- serialization ---
    def result_dict(self, sess: Session) -> dict:
        res = sess.result
        geom = res.geometry
        cfg = sess.cfg
        answers = []
        for a in res.answers:
            eff = sess.effective_answer(a.question)
            bi = (a.question - 1) // cfg.rows_per_block
            ri = (a.question - 1) % cfg.rows_per_block
            centers, cy = [], None
            if bi < len(geom.block_cols) and ri < len(geom.block_rows[bi]):
                centers = [round(float(x), 1) for x in geom.block_cols[bi]]
                cy = round(float(geom.block_rows[bi][ri]), 1)
            answers.append({
                "question": a.question,
                "answer": eff,
                "confidence": a.confidence,
                "fills": a.fills,
                "corrected": a.question in sess.answer_overrides,
                "centers": centers,   # x of each option bubble (image px)
                "cy": cy,             # y of the row (image px)
            })
        marked = sum(1 for a in answers if a["answer"] in "ABCDE")
        blank = sum(1 for a in answers if a["answer"] == "BLANK")
        multi = sum(1 for a in answers if a["answer"] == "MULTI")
        lowconf = sum(1 for a in answers
                      if a["answer"] in "ABCDE" and a["confidence"] < 0.5)
        h, w = res.gray.shape
        return {
            "session_id": sess.session_id,
            "filename": sess.filename,
            "roll_number": sess.effective_roll(),
            "roll_confidence": res.roll_confidence,
            "series": res.series or None,
            "series_confidence": res.series_confidence,
            "skew_applied_deg": round(res.skew_applied, 3),
            "orientation": res.orientation,
            "inverted": res.inverted,
            "resolution_scale": round(res.resolution_scale, 3),
            "quality": round(res.quality, 3),
            "counts": {"total": len(answers), "marked": marked, "blank": blank,
                       "multi": multi, "low_confidence": lowconf},
            "warnings": res.warnings,
            "answers": answers,
            "image_width": int(w),
            "image_height": int(h),
            "bubble_half_w": cfg.fill_half_w,
            "bubble_half_h": cfg.fill_half_h,
            "options": list(cfg.option_labels),
            **self._review(sess),
        }

    def _review(self, sess: Session) -> dict:
        from omr.pipeline import build_review
        res = sess.result
        view = [(a.question, sess.effective_answer(a.question), a.confidence,
                 a.question in sess.answer_overrides) for a in res.answers]
        return build_review(view, sess.effective_roll(), res.roll_confidence,
                            sess.effective_series(), res.series_confidence, res)

    def _write_json(self, sess: Session) -> None:
        with open(sess.dir / "result.json", "w") as f:
            json.dump(self.result_dict(sess), f, indent=2)


store = SessionStore()
