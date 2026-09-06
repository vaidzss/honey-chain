"""Feature engineering, shared by training and inference.

**This file is the contract between the two.** If training computes a feature
one way and the API computes it another, the model silently degrades in
production and nothing fails loudly. So both import from here, and
`FEATURE_COLUMNS` is the single ordered list they agree on.

The features are chosen from what a real sentinel node can actually measure,
never from ground truth. Anything named `gt_*` in the dataset is a label.

## What the model is actually keying on

`thermal_delta` and `setpoint_error` do most of the work, and that is by
design: a queenright colony with brood holds 34-35 C regardless of ambient, so
how far in-hive temperature has drifted *from that setpoint* is the single most
informative number a hive produces.

Band energies are normalised to a shape rather than a level. A loud hive and a
quiet hive with the same spectral *shape* are in the same state; absolute
loudness mostly tracks colony size and microphone gain, neither of which is a
health signal.

Rolling windows matter because most faults are trajectories, not instants.
Varroa is a three-week decline; a swarm is a step change. A model given only
the current row can see the step but never the decline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BROOD_SETPOINT_C = 34.8
N_BANDS = 20
N_MFCC = 13

# Ordered, and the order is part of the contract with the serving code.
FEATURE_COLUMNS = [
    # thermal -- the highest-value signals
    "thermal_delta", "setpoint_error", "t_in", "t_out", "rh_delta",
    # acoustic
    "sound_rms", "centroid_hz", "spectral_spread", "low_band_ratio",
    "queen_band_ratio", "harmonic_ratio",
    *[f"be_n{i}" for i in range(N_BANDS)],
    *[f"mfcc_{i}" for i in range(N_MFCC)],
    # activity
    "entrance_total", "entrance_net", "traffic_per_gram",
    # mass dynamics
    "weight_kg", "weight_chg_24h", "weight_chg_7d", "weight_std_24h",
    # thermoregulation stability
    "t_in_std_24h", "delta_mean_24h", "delta_min_24h",
    # context
    "hour", "month", "is_night",
]

BAND_HZ = 100.0
BAND_CENTRES = np.array([(i + 0.5) * BAND_HZ for i in range(N_BANDS)])


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Telemetry rows -> model features.

    `df` must carry node_id and ts so the rolling windows can be computed
    per hive in time order. Rolling across hive boundaries would leak one
    colony's history into another's prediction.
    """
    df = df.sort_values(["node_id", "ts"]).copy()

    # ---- thermal ----------------------------------------------------------
    df["thermal_delta"] = df["t_in"] - df["t_out"]
    df["setpoint_error"] = (df["t_in"] - BROOD_SETPOINT_C).abs()
    df["rh_delta"] = df["rh_in"] - df["rh_out"]

    # ---- acoustic shape ---------------------------------------------------
    be_cols = [f"be_{i}" for i in range(N_BANDS)]
    be = df[be_cols].to_numpy(dtype=float)
    total = be.sum(axis=1, keepdims=True)
    total[total <= 0] = 1e-9
    be_norm = be / total
    for i in range(N_BANDS):
        df[f"be_n{i}"] = be_norm[:, i]

    centroid = (be_norm * BAND_CENTRES).sum(axis=1)
    spread = np.sqrt((be_norm * (BAND_CENTRES - centroid[:, None]) ** 2).sum(axis=1))
    df["spectral_spread"] = spread

    # 200-300 Hz is the settled worker hum; 350-450 Hz is where a virgin queen
    # toots. The *ratio* between them is the queenless tell, and it survives
    # differences in microphone gain that the raw bands do not.
    df["low_band_ratio"] = be_norm[:, 2:4].sum(axis=1)
    df["queen_band_ratio"] = be_norm[:, 3:5].sum(axis=1)
    df["harmonic_ratio"] = be_norm[:, 4:8].sum(axis=1) / (be_norm[:, 1:4].sum(axis=1) + 1e-9)

    # ---- activity ---------------------------------------------------------
    df["entrance_total"] = df["entrance_in"].fillna(0) + df["entrance_out"].fillna(0)
    df["entrance_net"] = df["entrance_in"].fillna(0) - df["entrance_out"].fillna(0)
    # traffic relative to colony mass: heavy traffic on a light hive is robbing,
    # the same traffic on a heavy hive is a good nectar flow
    df["traffic_per_gram"] = df["entrance_total"] / (df["weight_kg"].clip(lower=1) * 1000)

    # ---- rolling, per hive ------------------------------------------------
    g = df.groupby("node_id", sort=False)
    # rows are hourly in the training dumps; 24 and 168 rows are 1 day and 1 week
    df["weight_chg_24h"] = g["weight_kg"].diff(24)
    df["weight_chg_7d"] = g["weight_kg"].diff(168)
    df["weight_std_24h"] = g["weight_kg"].transform(
        lambda s: s.rolling(24, min_periods=4).std())
    df["t_in_std_24h"] = g["t_in"].transform(
        lambda s: s.rolling(24, min_periods=4).std())
    df["delta_mean_24h"] = g["thermal_delta"].transform(
        lambda s: s.rolling(24, min_periods=4).mean())
    # the coldest hour matters more than the average: a colony that cannot hold
    # its setpoint overnight has lost the plot even if midday looks fine
    df["delta_min_24h"] = g["thermal_delta"].transform(
        lambda s: s.rolling(24, min_periods=4).min())

    # ---- context ----------------------------------------------------------
    ts = pd.to_datetime(df["ts"], unit="s", utc=True)
    df["hour"] = ts.dt.hour
    df["month"] = ts.dt.month
    df["is_night"] = ((df["hour"] < 6) | (df["hour"] >= 19)).astype(int)

    return df


def feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """The ordered feature block, with the contract enforced."""
    missing = [c for c in FEATURE_COLUMNS if c not in df.columns]
    if missing:
        raise KeyError(f"features missing after build_features: {missing}")
    return df[FEATURE_COLUMNS]
