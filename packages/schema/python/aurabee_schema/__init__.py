"""Shared wire contracts for AuraBee (Python side).

Installed editable into both apps/simulator and apps/api so the simulator and
the ingest service can never drift apart:

    pip install -e packages/schema/python
"""

from .telemetry import (
    FRAME_VERSION,
    TELEMETRY_WILDCARD,
    TelemetryFrame,
    compute_sig,
    is_fresh,
    topic_for,
)

__all__ = [
    "FRAME_VERSION",
    "TELEMETRY_WILDCARD",
    "TelemetryFrame",
    "compute_sig",
    "is_fresh",
    "topic_for",
]
