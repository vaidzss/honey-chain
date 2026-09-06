"""Fault injection.

This is the demo surface. Every scenario here exists because it produces a
*distinct* signature that a specific model or rule is supposed to catch -- if
you add one, say in the docstring which detector it is meant to exercise.

CLI form:  --scenario kind@node_id@YYYY-MM-DD[@param]
e.g.       --scenario queenless@HN-UP-SIT-0007@2026-11-18
           --scenario swarm@HN-UP-SIT-0003@2026-12-02
           --scenario varroa@HN-UP-SIT-0011@2026-11-05@8.0
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

KINDS = (
    "queenless",     # -> thermal decouple + bimodal acoustics
    "swarm",         # -> step weight drop, preceded by rising spectral centroid
    "abscond",       # -> weight to comb-only, traffic to zero
    "robbing",       # -> sharp stores loss WITH high traffic (the inverse of a harvest)
    "varroa",        # -> slow health/population decline over weeks
    "wax_moth",      # -> health decline with brood collapse
    "starvation",    # -> stores to zero in dearth
    "theft",         # -> GPS jump then node silence
    "sensor_drift",  # -> load cell bias ramp; must NOT be read as a real yield
    "offline",       # -> node stops publishing
    "harvest",       # -> legitimate step weight drop; the control case for 'swarm'
)


@dataclass
class Scenario:
    kind: str
    node_id: str
    when: datetime
    param: float | None = None
    fired: bool = False

    @classmethod
    def parse(cls, spec: str) -> "Scenario":
        parts = spec.split("@")
        if len(parts) < 3:
            raise ValueError(
                f"bad scenario {spec!r}; expected kind@node_id@YYYY-MM-DD[@param]"
            )
        kind, node_id, when = parts[0], parts[1], parts[2]
        if kind not in KINDS:
            raise ValueError(f"unknown scenario kind {kind!r}; one of {KINDS}")
        param = float(parts[3]) if len(parts) > 3 else None
        return cls(kind=kind, node_id=node_id, when=datetime.fromisoformat(when), param=param)


def apply(scenario: Scenario, colony, state: dict) -> str:
    """Mutate the colony. `state` is the runner's per-node bag for effects that
    persist beyond the instant of firing (offline, drift). Returns a log line."""
    k = scenario.kind

    if k == "queenless":
        colony.queen_present = False
        colony.queen_age_days = 0
        colony.queenless_days = 0.0
        # a colony that can still requeen is not a fault case; it heals itself
        # in about three weeks. Block it so the detector has something to find.
        colony.requeen_blocked = True
        return f"queen removed from {colony.hive_id}"

    if k == "swarm":
        before = colony.gross_weight_kg
        colony.do_swarm()
        return f"swarm: {before:.1f} -> {colony.gross_weight_kg:.1f} kg"

    if k == "abscond":
        colony.do_abscond()
        return f"colony absconded from {colony.hive_id}"

    if k == "robbing":
        colony.robbing = True
        state["robbing_until"] = scenario.when.timestamp() + 3600 * 36
        return "robbing started"

    if k == "varroa":
        colony.varroa_load = scenario.param or 8.0
        return f"varroa load set to {colony.varroa_load}/100 bees"

    if k == "wax_moth":
        colony.wax_moth = scenario.param or 0.4
        return f"wax moth infestation {colony.wax_moth}"

    if k == "starvation":
        colony.stores_kg = 0.3
        return "stores emptied"

    if k == "theft":
        # node physically moved, then goes dark
        colony.gps_offset = (0.031, -0.024)      # ~3.5 km
        state["offline_from"] = scenario.when.timestamp() + 1800
        return "node displaced (theft)"

    if k == "sensor_drift":
        state["drift_per_day"] = scenario.param or 0.35
        return f"load cell drift {state['drift_per_day']} kg/day"

    if k == "offline":
        state["offline_from"] = scenario.when.timestamp()
        return "node offline"

    if k == "harvest":
        kg = colony.harvest(scenario.param or 8.0)
        return f"harvested {kg:.1f} kg"

    return f"unhandled scenario {k}"
