"""Nectar-flow calendar.

Mirrors infra/sql/03_reference.sql. The simulator keeps its own copy so it can
run with no database (useful for unit tests and for a laptop demo with the
stack down), but the DB rows are the source of truth once a cluster is live.

A "flow" is not a step function: nectar ramps up, peaks, and tails off. We
model each flow as a raised-cosine bell across its month window, which is what
hive-weight curves actually look like in the literature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Flow:
    flora: str
    start_month: int
    end_month: int
    peak_kg_per_day: float


# Keep in step with 03_reference.sql.
FLOWS: dict[str, Flow] = {
    "mustard":    Flow("mustard", 11, 2, 2.6),
    "litchi":     Flow("litchi", 3, 4, 3.1),
    "eucalyptus": Flow("eucalyptus", 2, 4, 1.8),
    "sunflower":  Flow("sunflower", 2, 4, 2.0),
    "coriander":  Flow("coriander", 2, 3, 1.5),
    "berseem":    Flow("berseem", 2, 4, 1.4),
    "acacia":     Flow("acacia", 2, 4, 1.6),
    "multiflora": Flow("multiflora", 9, 11, 1.0),
}

# June-August is the monsoon dearth across most of the plains: no meaningful
# nectar, colonies eat their stores. Any yield model that has not seen a dearth
# will happily forecast a summer harvest, so the simulator must reproduce it.
DEARTH_MONTHS = {6, 7, 8}


def _months_in_window(start: int, end: int) -> list[int]:
    """Flow windows wrap the year end (mustard is Nov -> Feb)."""
    months, m = [], start
    while True:
        months.append(m)
        if m == end:
            break
        m = 1 if m == 12 else m + 1
        if len(months) > 12:  # malformed window; fail loud rather than hang
            raise ValueError(f"bad flow window {start}->{end}")
    return months


def flow_intensity(flow: Flow, dt) -> float:
    """0..1 bell across the flow window. 0 outside it."""
    months = _months_in_window(flow.start_month, flow.end_month)
    if dt.month not in months:
        return 0.0
    idx = months.index(dt.month)
    # position through the whole window, 0..1
    frac_in_month = (dt.day - 1) / 31.0
    pos = (idx + frac_in_month) / len(months)
    # raised cosine: zero at the edges, 1 at the middle
    return 0.5 * (1 - math.cos(2 * math.pi * pos)) if 0 < pos < 1 else 0.0


def nectar_kg_per_day(flora_profile: list[str], dt) -> tuple[float, str | None]:
    """Best available flow for this apiary today.

    Returns (kg/day at full colony strength, dominant flora name). Colonies
    work the strongest source available rather than summing every flowering
    plant in the district.
    """
    best, best_name = 0.0, None
    for name in flora_profile:
        flow = FLOWS.get(name)
        if not flow:
            continue
        rate = flow.peak_kg_per_day * flow_intensity(flow, dt)
        if rate > best:
            best, best_name = rate, name
    if dt.month in DEARTH_MONTHS:
        best *= 0.12   # a trickle of wild forage, not a flow
    return best, best_name
