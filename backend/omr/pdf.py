"""Load a PDF or image file into a grayscale OpenCV image."""
from __future__ import annotations
from pathlib import Path
import numpy as np
import cv2


def _render_pdf_page(path: str, dpi: int, page_index: int = 0) -> np.ndarray:
    import pymupdf  # PyMuPDF; the legacy `fitz` package name is unreliable
    doc = pymupdf.open(path)
    if doc.page_count == 0:
        raise ValueError("PDF has no pages")
    page = doc[page_index]
    zoom = dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    doc.close()
    if img.shape[2] == 1:
        return img[:, :, 0].copy()
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def load_grayscale(path: str, dpi: int = 200, page_index: int = 0) -> np.ndarray:
    """Return a single-channel uint8 image for a PDF page or an image file."""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _render_pdf_page(path, dpi, page_index)
    data = np.fromfile(path, dtype=np.uint8)  # unicode-safe on Windows
    color = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if color is None:
        raise ValueError(f"Could not read image: {path}")
    return ink_grayscale(color)


def ink_grayscale(bgr: np.ndarray) -> np.ndarray:
    """Convert a color scan to grayscale that maximizes ink contrast.

    Blue/black ballpoint marks on white differ from a plain luminance: taking
    the per-pixel min across channels keeps blue ink dark while leaving paper
    light, so faint colored marks survive thresholding.
    """
    if bgr.ndim == 2:
        return bgr
    if bgr.shape[2] == 1:
        return bgr[:, :, 0].copy()
    lum = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mn = bgr.min(axis=2)            # darkest channel -> colored ink stays dark
    return cv2.min(lum, mn)
