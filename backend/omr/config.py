"""Tunable parameters for the OMR pipeline.

Values are calibrated for the GN-107 style sheet rendered at ~200 DPI, but the
grid detection is relative/cluster-based so most of these are tolerances, not
hard pixel coordinates.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class OMRConfig:
    # --- rendering ---
    render_dpi: int = 200

    # --- sheet structure (this sheet family) ---
    num_questions: int = 200
    num_blocks: int = 4
    rows_per_block: int = 50
    options_per_question: int = 5
    option_labels: str = "ABCDE"

    roll_digits: int = 9          # columns in the roll-number grid
    roll_rows: int = 10           # values 0-9

    # --- bubble candidate size filter (px @ render_dpi) ---
    bubble_w_min: int = 25
    bubble_w_max: int = 75
    bubble_h_min: int = 18
    bubble_h_max: int = 50
    bubble_aspect_min: float = 1.0
    # Oval answer bubbles top out near 1.65; the wide timing-track marks sit at
    # ~2.3. Capping below that keeps the timing track from being mistaken for an
    # answer column once the full-height working region is used.
    bubble_aspect_max: float = 1.85

    # --- clustering tolerances (px) ---
    col_cluster_gap: int = 25     # max x-gap within one option column
    row_cluster_gap: int = 22     # max y-gap within one row
    block_gap_min: int = 120      # min x-gap that separates the 4 blocks
    min_col_count: int = 50       # min candidates to accept a real answer column

    # --- working region (fraction of page height): excludes only the very top
    #     header bar/text and the footer. The answer grid's true vertical extent
    #     is found adaptively (see grid._regular_run), so this can be loose and
    #     the grid may start higher (e.g. a shorter booklet block). ---
    answer_region_top_frac: float = 0.12
    answer_region_bottom_frac: float = 0.92

    # --- fill sampling (half-size of the inner sampling box, px) ---
    fill_half_w: int = 17
    fill_half_h: int = 12

    # --- decision thresholds (fill ratio 0..1) ---
    fill_threshold: float = 0.45  # min fill to count as marked
    fill_margin: float = 0.15     # winner must beat runner-up by this much
    multi_threshold: float = 0.45 # runner-up above this (and within margin) => MULTI

    # --- deskew ---
    deskew_max_deg: float = 8.0   # only correct skews smaller than this
    deskew_min_deg: float = 0.25  # below this, skip (already straight)

    # --- canonical frame for homography registration (from the reference sheet
    #     at 200 DPI). Corner fiducials are the extreme registration marks. ---
    canon_w: int = 2400
    canon_h: int = 3394
    # TL, TR, BR, BL  (x, y) in canonical pixels
    canon_corners: tuple = ((190.8, 117.8), (2324.5, 265.5),
                            (2324.5, 2890.5), (76.5, 2888.5))
