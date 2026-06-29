"""Image registration / normalization to make detection robust to real-world
scan and photo conditions:

- inversion (white-on-black scans)
- resolution / scale (phone photos, low-DPI scans)
- skew (scanner feed rotation) via projection-profile deskew
- orientation (90/180/270) resolved downstream by decode-scoring

Orientation candidates are produced here; the pipeline decodes each and keeps
the one with the best geometry+confidence score.
"""
from __future__ import annotations
import numpy as np
import cv2

from .config import OMRConfig


def fix_inversion(gray: np.ndarray) -> tuple[np.ndarray, bool]:
    """OMR paper is mostly light. If the page reads dark, invert it."""
    if float(np.median(gray)) < 110:
        return cv2.bitwise_not(gray), True
    return gray, False


def normalize_resolution(gray: np.ndarray, target_long: int = 3394,
                         lo: int = 3000, hi: int = 4400) -> tuple[np.ndarray, float]:
    """Scale so the long side lands in a working band; keeps bubble sizes within
    the detector's px filters regardless of input DPI."""
    h, w = gray.shape
    long = max(h, w)
    if lo <= long <= hi:
        return gray, 1.0
    s = target_long / long
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
    out = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))), interpolation=interp)
    return out, s


def coarse_deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Align an arbitrarily-rotated sheet to axis using the min-area rectangle of
    its ink. Handles large rotations (hand-held photos) that exceed the fine
    deskew range. The residual 90/180 ambiguity is resolved by orientation
    scoring downstream. The output canvas is expanded so nothing is clipped.
    """
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    small = cv2.resize(bw, (bw.shape[1] // 4, bw.shape[0] // 4),
                       interpolation=cv2.INTER_NEAREST)
    ys, xs = np.where(small > 0)
    if len(xs) < 200:
        return gray, 0.0
    pts = np.column_stack([xs, ys]).astype(np.float32)
    (_, _), (_, _), angle = cv2.minAreaRect(pts)
    angle = angle % 90.0
    if angle > 45.0:
        angle -= 90.0
    if abs(angle) < 0.5:
        return gray, 0.0
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    out = cv2.warpAffine(gray, M, (nw, nh), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return out, float(angle)


def _deskew_angle(bw: np.ndarray, max_deg: float, step: float) -> float:
    """Projection-profile deskew: the angle whose horizontal projection has the
    sharpest (highest-variance) row structure is the de-skewed one."""
    h, w = bw.shape
    # work on a central band at reduced scale for speed and to avoid header/footer
    y0, y1 = int(0.20 * h), int(0.92 * h)
    band = bw[y0:y1]
    scale = 700.0 / band.shape[1] if band.shape[1] > 700 else 1.0
    if scale < 1.0:
        band = cv2.resize(band, (int(band.shape[1] * scale), int(band.shape[0] * scale)))
    cx, cy = band.shape[1] / 2, band.shape[0] / 2
    best_a, best_s = 0.0, -1.0
    a = -max_deg
    while a <= max_deg + 1e-9:
        M = cv2.getRotationMatrix2D((cx, cy), a, 1.0)
        r = cv2.warpAffine(band, M, (band.shape[1], band.shape[0]), flags=cv2.INTER_NEAREST)
        proj = r.sum(axis=1, dtype=np.float64)
        s = float(np.var(np.diff(proj)))  # sharp row edges => high
        if s > best_s:
            best_s, best_a = s, a
        a += step
    return best_a


def deskew(gray: np.ndarray, cfg: OMRConfig) -> tuple[np.ndarray, float]:
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coarse = _deskew_angle(bw, cfg.deskew_max_deg, 0.5)
    fine = _deskew_angle(bw, 0.6, 0.1) if abs(coarse) < cfg.deskew_max_deg else 0.0
    # refine around coarse
    angle = coarse if abs(coarse) >= 0.5 else fine
    if abs(angle) < cfg.deskew_min_deg:
        return gray, 0.0
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return out, float(angle)


def _corner_points(bw: np.ndarray) -> np.ndarray | None:
    """Find the 4 extreme registration-mark centroids near the page corners.

    Returns float32 [TL, TR, BR, BL] or None if too few marks are present.
    """
    H, W = bw.shape
    n, _, stats, cent = cv2.connectedComponentsWithStats(bw, 8)
    pts = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        cx, cy = cent[i]
        near = (x < 0.08 * W or x + w > 0.92 * W or y < 0.08 * H or y + h > 0.92 * H)
        if near and 80 < a < 4000 and 5 < w < 80 and 5 < h < 80:
            pts.append((cx, cy))
    if len(pts) < 12:
        return None
    pts = np.array(pts, dtype=np.float32)
    s = pts[:, 0] + pts[:, 1]
    d = pts[:, 0] - pts[:, 1]
    quad = np.float32([pts[np.argmin(s)], pts[np.argmax(d)],
                       pts[np.argmax(s)], pts[np.argmin(d)]])  # TL, TR, BR, BL
    # reject degenerate quads (must cover most of the page)
    area = cv2.contourArea(quad)
    if area < 0.45 * W * H:
        return None
    return quad


def register_to_canonical(gray: np.ndarray, cfg: OMRConfig) -> np.ndarray | None:
    """Warp the sheet onto the canonical frame using corner fiducials.

    A single homography removes rotation, skew, perspective, scale and
    translation at once. Returns the warped grayscale, or None if fiducials
    are not confidently found (caller falls back to deskew).
    """
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    src = _corner_points(bw)
    if src is None:
        return None
    dst = np.float32(cfg.canon_corners)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(gray, M, (cfg.canon_w, cfg.canon_h),
                               flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def orientation_candidates(gray: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Candidate orientations to try, pruned by aspect ratio.

    Portrait sheet -> {0, 180}.  Landscape input -> {90cw, 90ccw} (both portrait).
    The pipeline decodes each and keeps the best-scoring one.
    """
    h, w = gray.shape
    if w > h * 1.05:  # landscape: was rotated 90 or 270
        return [("rot90cw", np.rot90(gray, 3)), ("rot90ccw", np.rot90(gray, 1))]
    return [("0", gray), ("180", np.rot90(gray, 2))]
