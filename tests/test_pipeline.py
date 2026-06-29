"""Core pipeline tests. Run: pytest tests/test_pipeline.py

Covers the reference decode plus the most important real-world transforms
applied on the fly (no external fixtures needed).
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
import cv2
import pytest

from omr import process_document, OMRConfig
from omr.pdf import load_grayscale

PDF = os.path.join(os.path.dirname(__file__), "..", "OMR_sheet_2467.pdf")
ROLL = "107030379"
TMP = os.path.join(os.path.dirname(__file__), "..", "data", "_pytest")
os.makedirs(TMP, exist_ok=True)


@pytest.fixture(scope="module")
def clean():
    return process_document(PDF, OMRConfig())


@pytest.fixture(scope="module")
def gt(clean):
    return {a.question: a.answer for a in clean.answers}


def _save_and_run(img, name):
    p = os.path.join(TMP, name)
    cv2.imwrite(p, img)
    return process_document(p, OMRConfig())


def test_reference_decode(clean):
    assert len(clean.answers) == 200
    assert clean.roll_number == ROLL
    assert clean.series == "A"                 # 8-option (A-H) series block
    assert clean.counts()["marked"] == 200
    assert clean.counts()["blank"] == 0
    assert clean.counts()["multi"] == 0


def test_compact_export_shape(clean):
    c = clean.compact_dict()
    assert c["roll_number"] == 107030379 and isinstance(c["roll_number"], int)
    assert c["series"] == "A"
    assert len(c["responses"]) == 200
    assert c["responses"]["1"] == "D" and c["responses"]["4"] == "A"
    # a clean fully-marked sheet needs no review
    assert c["needs_review"] is False
    assert c["messages"] == []
    assert c["review"]["unanswered"] == [] and c["review"]["multiple_marked"] == []


def test_review_flags_problems():
    """Blanks and double-marks must be flagged for manual verification."""
    base = load_grayscale(PDF, dpi=200)
    res = process_document(PDF, OMRConfig())
    geom = res.geometry
    img = res.gray.copy()
    # erase Q5's mark (blank) and add a second mark to Q10 (multi)
    import cv2
    def cell(q):
        bi, ri = (q - 1) // 50, (q - 1) % 50
        return geom.block_cols[bi], geom.block_rows[bi][ri]
    cols, cy = cell(5); cv2.ellipse(img, (int(cols[res.answers[4].fills.index(max(res.answers[4].fills))]), int(cy)), (16, 11), 0, 0, 360, 255, -1)
    cols, cy = cell(10); cv2.ellipse(img, (int(cols[0]), int(cy)), (16, 11), 0, 0, 360, 0, -1); cv2.ellipse(img, (int(cols[2]), int(cy)), (16, 11), 0, 0, 360, 0, -1)
    out = _save_and_run(img, "review_probe.png")
    c = out.compact_dict()
    assert c["needs_review"] is True
    assert 5 in c["review"]["unanswered"]
    assert 10 in c["review"]["multiple_marked"]
    assert any("verify" in m.lower() or "rescan" in m.lower() for m in c["messages"])


def test_geometry_exact(clean):
    g = clean.geometry
    assert len(g.block_cols) == 4
    assert all(len(c) == 5 for c in g.block_cols)
    assert all(len(r) == 50 for r in g.block_rows)


@pytest.mark.parametrize("rot,name", [(2, "rot180"), (1, "rot90"), (3, "rot270")])
def test_orientation(gt, rot, name):
    base = load_grayscale(PDF, dpi=200)
    res = _save_and_run(np.rot90(base, rot), f"{name}.png")
    assert res.roll_number == ROLL
    acc = sum(1 for a in res.answers if gt[a.question] == a.answer) / 200
    assert acc >= 0.99


def test_inverted(gt):
    base = load_grayscale(PDF, dpi=200)
    res = _save_and_run(255 - base, "inverted.png")
    assert res.inverted is True
    acc = sum(1 for a in res.answers if gt[a.question] == a.answer) / 200
    assert acc >= 0.99


def test_low_resolution(gt):
    base = load_grayscale(PDF, dpi=200)
    small = cv2.resize(base, (base.shape[1] // 2, base.shape[0] // 2))
    res = _save_and_run(small, "small.png")
    acc = sum(1 for a in res.answers if gt[a.question] == a.answer) / 200
    assert acc >= 0.99


def test_skew(gt):
    base = load_grayscale(PDF, dpi=200)
    h, w = base.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 4.0, 1.0)
    sk = cv2.warpAffine(base, M, (w, h), borderValue=255)
    res = _save_and_run(sk, "skew4.png")
    acc = sum(1 for a in res.answers if gt[a.question] == a.answer) / 200
    assert acc >= 0.99


def test_acf_variant_layout():
    """ACF (GN-101) variant: shorter A-D booklet block, same answer grid family.
    Must decode cleanly with adaptive grid/roll detection."""
    acf = os.path.join(os.path.dirname(__file__), "..", "ACF omr 101005721_0269.pdf")
    if not os.path.exists(acf):
        pytest.skip("ACF sample not present")
    res = process_document(acf, OMRConfig())
    assert len(res.answers) == 200
    assert res.roll_number == "101005721"        # matches the printed barcode
    assert res.roll_confidence >= 0.99
    assert res.series == "A"                      # 4-option (A-D) series block
    assert len(res.geometry.series_rows) == 4
    g = res.geometry
    assert len(g.block_cols) == 4 and all(len(c) == 5 for c in g.block_cols)
    assert all(len(r) == 50 for r in g.block_rows)
    assert res.counts()["marked"] == 200          # fully-marked sample
    assert not res.warnings

    # the variant must also survive registration transforms (e.g. upside-down)
    base = load_grayscale(acf, dpi=200)
    flipped = _save_and_run(np.rot90(base, 2), "acf_rot180.png")
    assert flipped.roll_number == "101005721"
    assert flipped.counts()["marked"] == 200


def test_roll_grid_dense_row_selection():
    """Regression: a sparse spurious row (printed digits) near the roll grid must
    not anchor the row layout. This sheet previously misaligned the roll grid
    (roll confidence ~0.4); it should now read confidently."""
    p = os.path.join(os.path.dirname(__file__), "..", "vishwa_acf_101002047_0718.pdf")
    if not os.path.exists(p):
        pytest.skip("vishwa sample not present")
    res = process_document(p, OMRConfig())
    assert res.roll_number == "101002047"
    assert res.roll_confidence >= 0.9
    assert res.series == "C"


def test_non_omr_image_does_not_silently_succeed():
    """A non-OMR image must either fail or come back clearly flagged — never a
    confident-looking full decode."""
    from omr.grid import GridError
    rng = np.random.RandomState(0)
    noise = (rng.rand(800, 600) * 255).astype(np.uint8)
    try:
        res = _save_and_run(noise, "noise.png")
    except GridError:
        return  # acceptable: cleanly rejected
    # otherwise it must be flagged as imperfect (warnings) — not a clean 200/200
    assert res.warnings, "non-OMR image decoded with no warnings"
