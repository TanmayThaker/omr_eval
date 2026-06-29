"""Self-calibrating grid detection.

Detects bubble candidates and clusters them into the answer grid
(4 blocks x 5 option-columns x 50 rows) and the top ID fields
(roll-number grids + booklet column). Cluster-based so it tolerates
scale/translation and minor skew without hard-coded pixel coordinates.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import cv2

from .config import OMRConfig


class GridError(Exception):
    """Raised when the answer grid cannot be located (e.g. wrong orientation)."""


@dataclass
class BubbleCell:
    cx: float
    cy: float


@dataclass
class GridGeometry:
    # answer grid: blocks[b] = list of 5 option x-centers; rows[b] = list of 50 y-centers
    block_cols: list[list[float]]
    block_rows: list[list[float]]
    bubble_w: int
    bubble_h: int
    # ID fields (optional; empty list if not found)
    roll_cols: list[float] = field(default_factory=list)
    roll_rows: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _candidates(bw: np.ndarray, cfg: OMRConfig) -> list[tuple[float, float, int, int]]:
    cnts, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w == 0 or h == 0:
            continue
        ar = w / float(h)
        if (cfg.bubble_w_min <= w <= cfg.bubble_w_max
                and cfg.bubble_h_min <= h <= cfg.bubble_h_max
                and cfg.bubble_aspect_min <= ar <= cfg.bubble_aspect_max):
            out.append((x + w / 2.0, y + h / 2.0, w, h))
    return out


def _cluster_1d(values: list[float], gap: float) -> list[list[float]]:
    if not values:
        return []
    vs = sorted(values)
    clusters = [[vs[0]]]
    for v in vs[1:]:
        if v - clusters[-1][-1] < gap:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


def _regular_run(rows: list[float], n_expected: int, cfg: OMRConfig) -> list[float]:
    """Pick the evenly-spaced run of rows that is the answer grid.

    A sheet's column also contains header rows (roll/booklet) separated from the
    answer grid by a large vertical gap. Splitting at gaps far larger than the
    typical row spacing and choosing the run closest to ``n_expected`` isolates
    the answer grid wherever it sits — so the layout can shift up or down
    (e.g. a shorter booklet block) without breaking detection.
    """
    rows = sorted(rows)
    if len(rows) <= 2:
        return rows
    gaps = np.diff(rows)
    spacing = float(np.median(gaps))
    thr = max(cfg.row_cluster_gap * 2.0, 1.7 * spacing)
    runs, start = [], 0
    for i, g in enumerate(gaps):
        if g > thr:
            runs.append((start, i))
            start = i + 1
    runs.append((start, len(rows) - 1))
    # prefer the run whose length is closest to expected, breaking ties by length
    best = min(runs, key=lambda r: (abs((r[1] - r[0] + 1) - n_expected), -(r[1] - r[0])))
    return rows[best[0]:best[1] + 1]


def detect_grid(bw: np.ndarray, cfg: OMRConfig) -> GridGeometry:
    H, W = bw.shape
    cands = _candidates(bw, cfg)
    if not cands:
        raise GridError("No bubble candidates detected on the sheet")

    bw_med = int(np.median([c[2] for c in cands]))
    bh_med = int(np.median([c[3] for c in cands]))
    warnings: list[str] = []

    # --- working region: exclude only the very top header bar and the footer;
    #     the answer grid's vertical extent is found adaptively per block. ---
    y_top = cfg.answer_region_top_frac * H
    y_bot = cfg.answer_region_bottom_frac * H
    ans = [c for c in cands if y_top < c[1] < y_bot]

    # --- columns: cluster x, keep dense clusters (real option columns) ---
    xclusters = _cluster_1d([c[0] for c in ans], cfg.col_cluster_gap)
    col_centers = sorted(np.mean(cl) for cl in xclusters if len(cl) >= cfg.min_col_count)

    expected_cols = cfg.num_blocks * cfg.options_per_question
    if len(col_centers) != expected_cols:
        warnings.append(
            f"Detected {len(col_centers)} answer columns, expected {expected_cols}")

    if not col_centers:
        raise GridError("No answer columns detected")

    # group columns into blocks by large x-gaps
    blocks: list[list[float]] = []
    cur = [col_centers[0]]
    for x in col_centers[1:]:
        if x - cur[-1] > cfg.block_gap_min:
            blocks.append(cur)
            cur = [x]
        else:
            cur.append(x)
    blocks.append(cur)

    if len(blocks) != cfg.num_blocks:
        warnings.append(f"Detected {len(blocks)} blocks, expected {cfg.num_blocks}")

    # --- rows per block: cluster y near the block's columns, then isolate the
    #     answer grid as the regular run (drops header/footer rows adaptively) ---
    block_rows: list[list[float]] = []
    for cols in blocks:
        sel = [c for c in ans if any(abs(c[0] - cx) < bw_med for cx in cols)]
        rclusters = _cluster_1d([c[1] for c in sel], cfg.row_cluster_gap)
        rows = _regular_run([float(np.mean(cl)) for cl in rclusters],
                            cfg.rows_per_block, cfg)
        if len(rows) < 2:
            raise GridError("Degenerate block: fewer than 2 rows detected")
        # if the run isn't exactly the expected count, evenly space between its
        # (now correct) extremes
        if len(rows) != cfg.rows_per_block:
            y0, y1 = rows[0], rows[-1]
            rows = [y0 + (y1 - y0) * i / (cfg.rows_per_block - 1)
                    for i in range(cfg.rows_per_block)]
            warnings.append(
                f"Block row count adjusted to {cfg.rows_per_block} via even-spacing fit")
        block_rows.append(rows)

    answer_top = min(r[0] for r in block_rows) if block_rows else 0.30 * H
    roll_cols, roll_rows = _detect_roll(cands, cfg, H, W, bw_med, answer_top, warnings)

    return GridGeometry(
        block_cols=blocks,
        block_rows=block_rows,
        bubble_w=bw_med,
        bubble_h=bh_med,
        roll_cols=roll_cols,
        roll_rows=roll_rows,
        warnings=warnings,
    )


def _detect_roll(cands, cfg, H, W, bw_med, answer_top, warnings):
    """Detect the left roll-number grid (9 cols x 10 rows) in the top-left header.

    Bounded below by the detected answer-grid top so the band adapts to layouts
    where the grid starts higher/lower, and never pulls in answer rows.
    """
    y_lo, y_hi = 0.04 * H, answer_top - bw_med  # just above the answer grid
    top = [c for c in cands
           if y_lo < c[1] < y_hi and 0.06 * W < c[0] < 0.45 * W]
    if not top:
        return [], []
    xclusters = _cluster_1d([c[0] for c in top], cfg.col_cluster_gap)
    cols = sorted(np.mean(cl) for cl in xclusters if len(cl) >= cfg.roll_rows - 2)
    cols = cols[:cfg.roll_digits]  # left grid only
    if len(cols) < cfg.roll_digits:
        warnings.append(f"Roll-number grid: found {len(cols)}/{cfg.roll_digits} columns")
        return cols, []
    sel = [c for c in top if any(abs(c[0] - cx) < bw_med for cx in cols)]
    rclusters = _cluster_1d([c[1] for c in sel], cfg.row_cluster_gap)
    rows = _regular_run([float(np.mean(cl)) for cl in rclusters], cfg.roll_rows, cfg)
    if len(rows) != cfg.roll_rows and len(rows) >= 2:
        y0, y1 = rows[0], rows[-1]
        rows = [y0 + (y1 - y0) * i / (cfg.roll_rows - 1) for i in range(cfg.roll_rows)]
    return cols, rows
