"""Canonical telemetry frame + device signing.

This module is imported by BOTH the simulator (apps/simulator) and the ingest
service (apps/api). The ESP32 firmware reimplements `canonical_string` in C --
see firmware/src/telemetry.c. If the three ever disagree, ingest rejects frames
and you will see it immediately in the sig-fail counter.

Signing scheme (v1)
-------------------
    sig = hex(HMAC_SHA256(node_key, canonical_string))[:32]

`canonical_string` is a pipe-delimited string with a FIXED field order and
FIXED decimal precision. Deterministic string formatting is the whole point:
signing serialised JSON would require byte-identical JSON from a Python dict
and an ArduinoJson document, which is a trap.

Known limitation, deliberate for v1: `features` and the frame version prefix
aside, only the scalar fields below are signed. Acoustic features are advisory
-- they influence model inference but never a chain write on their own, and any
event derived from them is cross-checked against signed scalars (weight, t_in)
before it can affect a yield envelope. v2 moves to a CBOR frame with a
whole-payload signature once the firmware is real.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any

FRAME_VERSION = 1
SIG_LEN = 32  # hex chars, i.e. 16 bytes of the HMAC. Keeps LoRa frames small.

# Field order and precision are part of the wire contract. Do not reorder.
_SIGNED_FIELDS: tuple[tuple[str, int | None], ...] = (
    ("weight_kg", 3),
    ("t_in", 2),
    ("rh_in", 2),
    ("t_out", 2),
    ("rh_out", 2),
    ("sound_rms", 4),
    ("entrance_in", None),   # None => integer, rendered without a decimal point
    ("entrance_out", None),
    ("batt_v", 2),
    ("lat", 5),
    ("lon", 5),
)


def _fmt(value: Any, places: int | None) -> str:
    """None renders as an empty field. Missing is a legitimate reading -- a
    node with a dead load cell still reports temperature."""
    if value is None:
        return ""
    if places is None:
        return str(int(value))
    return f"{float(value):.{places}f}"


@dataclass(slots=True)
class TelemetryFrame:
    node_id: str
    ts: int
    seq: int
    weight_kg: float | None = None
    t_in: float | None = None
    rh_in: float | None = None
    t_out: float | None = None
    rh_out: float | None = None
    sound_rms: float | None = None
    entrance_in: int | None = None
    entrance_out: int | None = None
    batt_v: float | None = None
    lat: float | None = None
    lon: float | None = None
    features: dict[str, Any] | None = None
    sig: str = ""
    v: int = FRAME_VERSION

    # -- signing ---------------------------------------------------------
    def canonical_string(self) -> str:
        parts = [f"v{self.v}", self.node_id, str(self.ts), str(self.seq)]
        parts += [_fmt(getattr(self, name), places) for name, places in _SIGNED_FIELDS]
        return "|".join(parts)

    def sign(self, key: str | bytes) -> "TelemetryFrame":
        self.sig = compute_sig(self.canonical_string(), key)
        return self

    def verify(self, key: str | bytes) -> bool:
        expected = compute_sig(self.canonical_string(), key)
        # constant-time: signature comparison is attacker-facing
        return hmac.compare_digest(expected, self.sig or "")

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # keep frames small on constrained links: drop nulls
        return {k: val for k, val in d.items() if val is not None}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TelemetryFrame":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: val for k, val in d.items() if k in known})


def compute_sig(canonical: str, key: str | bytes) -> str:
    kb = key.encode() if isinstance(key, str) else key
    return hmac.new(kb, canonical.encode(), sha256).hexdigest()[:SIG_LEN]


def is_fresh(ts: int, window_s: int = 300, now: int | None = None) -> bool:
    """Reject frames from too far in the past or future.

    Future-dated frames are rejected too: a node with a wrong clock is a real
    field condition, but so is an attacker replaying a captured frame with a
    bumped timestamp.
    """
    now = now if now is not None else int(time.time())
    return abs(now - ts) <= window_s


def topic_for(node_id: str) -> str:
    return f"aurabee/telemetry/{node_id}"


TELEMETRY_WILDCARD = "aurabee/telemetry/+"
