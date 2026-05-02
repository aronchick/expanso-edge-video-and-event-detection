"""Shared event schema for the edge-ISR demo.

Kept separate from the box-counting events.py so the two demos don't
collide on field names. The wire format is JSON-over-HTTP between
sensor and orchestrator.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Detection(BaseModel):
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]  # (x1, y1, x2, y2)


class Event(BaseModel):
    node: str
    ts: float
    yolo_hits: list[Detection]
    gemini_description: Optional[str] = None
    model_versions: dict[str, Optional[str]] = Field(default_factory=dict)
    signature: Optional[str] = None
    queued_offline: bool = False


class FusedAlert(BaseModel):
    """Cross-sector correlation event emitted by the orchestrator."""

    type: str = "multi_sector_correlation"
    ts: float
    sectors: list[str]
    contacts: list[dict]
