"""OMR core: render -> preprocess -> grid detection -> bubble decode -> overlay.

The pipeline is template-light and self-calibrating: the answer grid is located
by clustering detected bubble candidates into columns/blocks/rows, so it adapts
to scale/translation and minor skew of scans for this sheet family
(200 questions, 4 blocks of 50, 5 options A-E).
"""
from .config import OMRConfig
from .pipeline import process_document, ProcessResult

__all__ = ["OMRConfig", "process_document", "ProcessResult"]
