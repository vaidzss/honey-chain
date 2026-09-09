"""Time-resolved colony features for MSPB.

The first attempt at `ml/train_mspb.py` used ONE season-average per colony per
sensor and found nothing. Two things were wrong with that:

1. **It threw away the trajectory.** A colony's acoustic signature is a curve
   across a season, and averaging it to a point discards the shape. Our own
   telemetry models use rolling windows for exactly this reason -- faults are
   trajectories, not instants.

2. **It leaked.** A season average spans the whole year, including weeks AFTER
   the varroa count was taken. Predicting a September measurement partly from
   October data is not a forecast.

This module builds features that respect both: monthly aggregates, summarised
into shape statistics, and for a dated target, restricted to the window BEFORE
the measurement.

Feature count is kept deliberately small. There are 53 colonies; a model with
150 features and 53 samples will fit noise and a leave-one-out score will not
save it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# The 16 published bands collapse to three physically meaningful groups. A
# worker hum sits near 100-300 Hz; queenless and agitated colonies shift energy
# upward. Keeping three ratios instead of sixteen absolute levels also makes the
# feature robust to overall gain, which varies by installation.
BAND_GROUPS = {
    "low": ["hz_122.0703125", "hz_152.587890625", "hz_183.10546875",
            "hz_213.623046875", "hz_244.140625"],
    "mid": ["hz_274.658203125", "hz_305.17578125", "hz_335.693359375",
            "hz_366.2109375", "hz_396.728515625"],
    "high": ["hz_427.24609375", "hz_457.763671875", "hz_488.28125",
             "hz_518.798828125", "hz_549.31640625", "hz_579.833984375"],
}
SIGNALS = ["low", "mid", "high", "high_low_ratio", "temperature", "humidity",
           "audio_density"]


def derive_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse raw bands into the handful of signals we model."""
    out = pd.DataFrame(index=df.index)
    for name, cols in BAND_GROUPS.items():
        present = [c for c in cols if c in df.columns]
        out[name] = df[present].mean(axis=1) if present else np.nan
    # bands are stored in dB, so a ratio is a difference
    out["high_low_ratio"] = out["high"] - out["low"]
    for c in ("temperature", "humidity", "audio_density"):
        out[c] = df[c] if c in df.columns else np.nan
    return out


def shape_features(monthly: pd.DataFrame) -> dict:
    """Summarise a colony's monthly curve into a few numbers.

    `monthly` is indexed by month with one column per signal.
    """
    feats: dict[str, float] = {}
    if monthly.empty:
        return feats
    months = monthly.index.to_numpy(dtype=float)
    for sig in monthly.columns:
        v = monthly[sig].to_numpy(dtype=float)
        ok = ~np.isnan(v)
        if ok.sum() == 0:
            continue
        vv, mm = v[ok], months[ok]
        feats[f"{sig}_mean"] = float(np.mean(vv))
        feats[f"{sig}_sd"] = float(np.std(vv))
        feats[f"{sig}_last"] = float(vv[-1])
        feats[f"{sig}_range"] = float(np.max(vv) - np.min(vv))
        # trend across the season: the single most trajectory-like number
        feats[f"{sig}_slope"] = (float(np.polyfit(mm, vv, 1)[0])
                                 if ok.sum() >= 3 else 0.0)
    return feats


EXCEL_EPOCH = pd.Timestamp("1899-12-30")


def excel_serial_to_date(v: object) -> pd.Timestamp | None:
    """MSPB stores evaluation dates as Excel serial numbers (e.g. 43991)."""
    n = pd.to_numeric(v, errors="coerce")
    if pd.isna(n):
        return None
    return EXCEL_EPOCH + pd.Timedelta(days=float(n))
