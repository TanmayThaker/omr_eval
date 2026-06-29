"""Render an annotated QC overlay: locator boxes on every bubble, marks colored."""
from __future__ import annotations
import numpy as np
import cv2

from .config import OMRConfig
from .grid import GridGeometry
from .detect import QuestionResult

# BGR colors
GREEN = (0, 170, 0)      # confidently marked
TEAL = (190, 150, 0)     # empty locator box
RED = (0, 0, 220)        # blank / multi / ambiguous
ORANGE = (0, 140, 255)   # low confidence


def draw_overlay(gray: np.ndarray, geom: GridGeometry, results: list[QuestionResult],
                 cfg: OMRConfig) -> np.ndarray:
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    hw, hh = cfg.fill_half_w, cfg.fill_half_h
    by_q = {r.question: r for r in results}

    for bi, (cols, rows) in enumerate(zip(geom.block_cols, geom.block_rows)):
        for ri, cy in enumerate(rows):
            q = bi * cfg.rows_per_block + ri + 1
            r = by_q.get(q)
            if r is None:
                continue
            marked_idx = (cfg.option_labels.index(r.answer)
                          if r.answer in cfg.option_labels else -1)
            for oi, cx in enumerate(cols):
                p1 = (int(cx - hw), int(cy - hh))
                p2 = (int(cx + hw), int(cy + hh))
                if oi == marked_idx:
                    color = GREEN if r.confidence >= 0.5 else ORANGE
                    cv2.rectangle(img, p1, p2, color, 2)
                else:
                    cv2.rectangle(img, p1, p2, TEAL, 1)
            if r.answer in ("BLANK", "MULTI"):
                cv2.rectangle(img, (int(cols[0] - hw - 3), int(cy - hh - 3)),
                              (int(cols[-1] + hw + 3), int(cy + hh + 3)), RED, 2)

    # roll-number boxes
    for cx in geom.roll_cols:
        for cy in geom.roll_rows:
            cv2.rectangle(img, (int(cx - hw), int(cy - hh)),
                          (int(cx + hw), int(cy + hh)), TEAL, 1)
    # series column boxes
    if geom.series_col is not None:
        for cy in geom.series_rows:
            cv2.rectangle(img, (int(geom.series_col - hw), int(cy - hh)),
                          (int(geom.series_col + hw), int(cy + hh)), TEAL, 1)
    return img
