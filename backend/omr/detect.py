"""Fill measurement and mark-decision logic."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import numpy as np
import cv2

from .config import OMRConfig
from .grid import GridGeometry


@dataclass
class QuestionResult:
    question: int
    answer: str                  # 'A'..'E' | 'BLANK' | 'MULTI'
    fills: list[float]           # fill ratio per option
    confidence: float            # 0..1


def _fill_ratio(bw: np.ndarray, cx: float, cy: float, cfg: OMRConfig) -> float:
    x0 = max(int(cx - cfg.fill_half_w), 0)
    x1 = min(int(cx + cfg.fill_half_w), bw.shape[1])
    y0 = max(int(cy - cfg.fill_half_h), 0)
    y1 = min(int(cy + cfg.fill_half_h), bw.shape[0])
    roi = bw[y0:y1, x0:x1]
    if roi.size == 0:
        return 0.0
    return float(roi.mean() / 255.0)


def auto_threshold(all_fills: list[float], cfg: OMRConfig) -> float:
    """Per-sheet fill threshold via Otsu on the fill histogram.

    Adapts to faint (pencil) or heavy (marker) sheets and to scan contrast.
    Only used when the marked/empty populations are clearly bimodal; otherwise
    (e.g. an all-blank sheet) falls back to the configured fixed threshold.
    """
    vals = np.asarray(all_fills, dtype=np.float32)
    if vals.size < 10:
        return cfg.fill_threshold
    v8 = np.clip(vals * 255.0, 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(v8, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    t /= 255.0
    lo = vals[vals <= t]
    hi = vals[vals > t]
    if lo.size and hi.size and (hi.mean() - lo.mean()) > 0.25 and 0.2 < t < 0.85:
        return float(t)
    return cfg.fill_threshold


def _decide(fills: list[float], thr: float, cfg: OMRConfig) -> tuple[str, float]:
    if len(fills) < 2:
        return "BLANK", 0.0
    order = np.argsort(fills)[::-1]
    top, sec = order[0], order[1]
    top_v, sec_v = fills[top], fills[sec]
    if top_v < thr:
        conf = min(1.0, (thr - top_v) / max(thr, 1e-6))  # how clearly blank
        return "BLANK", round(conf, 3)
    if sec_v >= thr and sec_v > top_v - cfg.fill_margin:
        return "MULTI", round(1.0 - (top_v - sec_v), 3)
    conf = min(1.0, (top_v - sec_v) / max(cfg.fill_margin, 1e-6))
    return cfg.option_labels[top], round(conf, 3)


def decode_answers(bw: np.ndarray, geom: GridGeometry, cfg: OMRConfig) -> list[QuestionResult]:
    # first pass: gather all fills, then calibrate the threshold for this sheet
    grid_fills = []
    cell_fills = []
    for bi, (cols, rows) in enumerate(zip(geom.block_cols, geom.block_rows)):
        for ri, cy in enumerate(rows):
            q = bi * cfg.rows_per_block + ri + 1
            fills = [round(_fill_ratio(bw, cx, cy, cfg), 3) for cx in cols]
            cell_fills.append((q, fills))
            grid_fills.extend(fills)
    thr = auto_threshold(grid_fills, cfg)

    results = []
    for q, fills in cell_fills:
        ans, conf = _decide(fills, thr, cfg)
        results.append(QuestionResult(q, ans, fills, conf))
    results.sort(key=lambda r: r.question)
    return results


def decode_series(bw: np.ndarray, geom: GridGeometry, cfg: OMRConfig) -> tuple[str, float]:
    """Decode the paper series: the filled option row maps to A, B, C, ...
    Returns (letter, confidence); ("", 0.0) if the column wasn't found and
    ("?", low) if no option is clearly filled."""
    if geom.series_col is None or len(geom.series_rows) < 2:
        return "", 0.0
    fills = [_fill_ratio(bw, geom.series_col, cy, cfg) for cy in geom.series_rows]
    order = np.argsort(fills)[::-1]
    top, sec = order[0], order[1]
    if fills[top] < cfg.fill_threshold:
        return "?", 0.0
    letter = chr(ord("A") + int(top)) if top < 26 else "?"
    conf = min(1.0, (fills[top] - fills[sec]) / max(cfg.fill_margin, 1e-6))
    return letter, round(conf, 3)


def decode_roll(bw: np.ndarray, geom: GridGeometry, cfg: OMRConfig) -> tuple[str, float]:
    """Decode the roll-number: per column pick the filled 0-9 row."""
    if not geom.roll_cols or len(geom.roll_rows) < 2:
        return "", 0.0
    digits = []
    confs = []
    for cx in geom.roll_cols:
        fills = [_fill_ratio(bw, cx, cy, cfg) for cy in geom.roll_rows]
        order = np.argsort(fills)[::-1]
        top, sec = order[0], order[1]
        if fills[top] < cfg.fill_threshold:
            digits.append("?")
            confs.append(0.0)
        else:
            digits.append(str(top % 10))
            confs.append(min(1.0, (fills[top] - fills[sec]) / max(cfg.fill_margin, 1e-6)))
    conf = float(np.mean(confs)) if confs else 0.0
    return "".join(digits), round(conf, 3)
