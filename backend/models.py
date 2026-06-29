"""Pydantic schemas for the API."""
from __future__ import annotations
from pydantic import BaseModel, Field


class AnswerOut(BaseModel):
    question: int
    answer: str                      # 'A'..'E' | 'BLANK' | 'MULTI'
    confidence: float
    fills: list[float]
    corrected: bool = False          # set when overridden via /correct


class Counts(BaseModel):
    total: int
    marked: int
    blank: int
    multi: int


class ExtractResponse(BaseModel):
    session_id: str
    filename: str
    roll_number: str
    roll_confidence: float
    skew_applied_deg: float
    counts: Counts
    warnings: list[str]
    answers: list[AnswerOut]
    image_width: int
    image_height: int


class Correction(BaseModel):
    question: int = Field(ge=1)
    answer: str                      # 'A'..'E' | 'BLANK' | 'MULTI'


class CorrectRequest(BaseModel):
    roll_number: str | None = None
    series: str | None = None
    corrections: list[Correction] = []
