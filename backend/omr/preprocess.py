"""Binarization. (Skew/registration live in register.py.)"""
from __future__ import annotations
import numpy as np
import cv2


def binarize(gray: np.ndarray) -> np.ndarray:
    """Otsu threshold, inverted so ink/marks are white (255)."""
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
