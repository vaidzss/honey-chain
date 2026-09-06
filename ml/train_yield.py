"""Train the quantile yield forecaster.

    python ml/train_yield.py

Replaces the statistical baseline in `apps/api/aurabee_api/yields.py` behind the
same interface. Its P90 output becomes the **on-chain mint ceiling**, so this is
the one model in the system whose error has a direct commercial consequence for
a beekeeper.

## Why quantile regression and not a point estimate

A point forecast gives no principled ceiling. Three separate models trained with
pinball loss at alpha 0.1 / 0.5 / 0.9 give a distribution, and the P90 is a
statement you can defend to the person it constrains: *under this model, given
this telemetry, a yield above X is a 1-in-10 event.*

## The asymmetry that decides the loss function

The two errors are not equally bad, and pinball loss is what encodes that:

- **P90 too low** -> an honest beekeeper is blocked from selling their own
  honey. They lose income and they stop using the system. This is the failure
  that kills adoption.
- **P90 too high** -> some fraud gets through, and the chemical testing ladder
  is the next line of defence.

So the ceiling is deliberately generous. Catching every cheat at the cost of
punishing honest producers would be the wrong trade even if it scored better.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import build_features  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "ml" / "models"
MODEL_VERSION = "yield-gbq-0.1.0"

# Aggregated per hive per window. Everything here is derivable from telemetry
# a sentinel node produces -- no ground truth.
AGG_FEATURES = [
    "days_observed", "gross_gain_kg", "net_gain_kg", "peak_weight_kg",
    "mean_thermal_delta", "min_thermal_delta", "frac_regulated",
    "mean_sound_rms", "mean_centroid", "mean_traffic",
    "mean_t_out", "frac_foraging_weather", "start_month", "n_months",
]


def aggregate(df: pd.DataFrame, window_days: int = 90) -> pd.DataFrame:
    """Per hive per window: telemetry summary -> true surplus produced.

    Pre-dawn weights only (03:00-05:00), when the whole colony is home. Weighing
    at midday measures the weather and the forager population as much as the
    honey, which is why precision apiculture weighs before dawn.
    """
    df = df.copy()
    ts = pd.to_datetime(df["ts"], unit="s", utc=True)
    df["day"] = ts.dt.floor("D")
    df["hour"] = ts.dt.hour
    df["window"] = (ts - ts.min()).dt.days // window_days

    predawn = df[(df["hour"] >= 3) & (df["hour"] <= 5)]
    daily = (predawn.groupby(["node_id", "window", "day"], as_index=False)
             .agg(weight_kg=("weight_kg", "mean"),
                  stores=("gt_stores_kg", "mean"),
                  harvested=("gt_harvested_kg", "max")))
    daily = daily.sort_values(["node_id", "window", "day"])

    g = daily.groupby(["node_id", "window"], sort=False)
    daily["dw"] = g["weight_kg"].diff()
    daily["dstores"] = g["stores"].diff()

    rows = []
    for (node, win), d in daily.groupby(["node_id", "window"], sort=False):
        if len(d) < 14:          # too short a window to say anything
            continue
        src = df[(df.node_id == node) & (df.window == win)]
        if src.empty:
            continue
        # TRUE surplus: what the colony actually accumulated, plus anything the
        # beekeeper removed during the window. Netting the harvest off would
        # under-count a colony that produced 30 kg and had 12 kg taken.
        harvested_in_window = max(0.0, d["harvested"].iloc[-1] - d["harvested"].iloc[0])
        true_surplus = float(d["dstores"].clip(lower=0).sum() + harvested_in_window)

        delta = src["t_in"] - src["t_out"]
        rows.append({
            "node_id": node, "window": int(win),
            "days_observed": int(len(d)),
            "gross_gain_kg": float(d["dw"].clip(lower=0).sum()),
            "net_gain_kg": float(d["weight_kg"].iloc[-1] - d["weight_kg"].iloc[0]),
            "peak_weight_kg": float(d["weight_kg"].max()),
            "mean_thermal_delta": float(delta.mean()),
            "min_thermal_delta": float(delta.min()),
            # how much of the window the colony actually held its setpoint --
            # a proxy for "was this a working colony or a struggling one"
            "frac_regulated": float((src["t_in"].between(33.5, 36.0)).mean()),
            "mean_sound_rms": float(src["sound_rms"].mean()),
            "mean_centroid": float(src["centroid_hz"].mean()),
            "mean_traffic": float((src["entrance_in"].fillna(0)
                                   + src["entrance_out"].fillna(0)).mean()),
            "mean_t_out": float(src["t_out"].mean()),
            "frac_foraging_weather": float((src["t_out"].between(14, 38)).mean()),
            "start_month": int(pd.to_datetime(d["day"].iloc[0]).month),
            "n_months": int(d["day"].dt.month.nunique()),
            "target_surplus_kg": true_surplus,
        })
    return pd.DataFrame(rows)


def pinball(y, pred, alpha):
    d = y - pred
    return float(np.mean(np.maximum(alpha * d, (alpha - 1) * d)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default=str(ROOT / "ml/datasets/train_v1.csv"))
    ap.add_argument("--holdout", default=str(ROOT / "ml/datasets/holdout_v1.csv"))
    ap.add_argument("--window-days", type=int, default=90)
    args = ap.parse_args()

    print("aggregating per hive per window...")
    t0 = time.time()
    tr = aggregate(pd.read_csv(args.train), args.window_days)
    te = aggregate(pd.read_csv(args.holdout), args.window_days)
    print(f"  train {len(tr)} hive-windows / {tr.node_id.nunique()} hives")
    print(f"  test  {len(te)} hive-windows / {te.node_id.nunique()} hives "
          f"(separate run)")
    print(f"  {time.time() - t0:.1f}s")

    if len(tr) < 30:
        sys.exit("not enough hive-windows to train; generate a longer dataset")

    Xtr, ytr = tr[AGG_FEATURES], tr["target_surplus_kg"]
    Xte, yte = te[AGG_FEATURES], te["target_surplus_kg"]
    print(f"\ntarget surplus kg -- train mean {ytr.mean():.1f}, "
          f"test mean {yte.mean():.1f}, test max {yte.max():.1f}")

    # Hold out a calibration split, by hive, for the conformal step below.
    cal_hives = set(sorted(tr.node_id.unique())[::4])       # every 4th hive
    fit = tr[~tr.node_id.isin(cal_hives)]
    cal = tr[tr.node_id.isin(cal_hives)]
    print(f"  fit {len(fit)} windows / calibration {len(cal)} windows "
          f"({len(cal_hives)} hives held out for calibration)")

    models, metrics = {}, {}
    for alpha, name in ((0.1, "p10"), (0.5, "p50"), (0.9, "p90")):
        m = GradientBoostingRegressor(
            loss="quantile", alpha=alpha, n_estimators=300,
            max_depth=3, learning_rate=0.05, min_samples_leaf=8, random_state=0)
        m.fit(fit[AGG_FEATURES], fit["target_surplus_kg"])
        pred = m.predict(Xte)
        models[name] = m
        metrics[name] = {
            "pinball": round(pinball(yte, pred, alpha), 4),
            "mean_pred": round(float(pred.mean()), 2),
        }
        print(f"  {name}: pinball {metrics[name]['pinball']:.3f}  "
              f"mean prediction {pred.mean():.1f} kg")

    # --- conformal calibration of the ceiling ------------------------------
    #
    # A nominal 0.9 quantile is not a 90% ceiling. On this much data the model
    # underfits the upper tail and raw P90 covered only ~75% of actual yields,
    # which would block a quarter of honest beekeepers -- the exact failure that
    # ends adoption.
    #
    # So we measure instead of assuming: on hives the model never saw, take the
    # ratio of actual to predicted, and scale the ceiling by the 90th percentile
    # of that ratio. It is a distribution-free guarantee that costs one split
    # and turns a nominal quantile into an empirical one.
    cal_pred = np.maximum(models["p90"].predict(cal[AGG_FEATURES]), 1e-6)
    ratios = cal["target_surplus_kg"].to_numpy() / cal_pred
    calibration_factor = float(max(1.0, np.quantile(ratios, 0.90)))
    print(f"\nconformal calibration on {len(cal)} unseen hive-windows: "
          f"ceiling x{calibration_factor:.3f}")

    p90_raw = models["p90"].predict(Xte)
    p90 = p90_raw * calibration_factor
    p50 = models["p50"].predict(Xte)

    raw_cov = float((yte <= p90_raw).mean())
    print(f"  coverage before calibration: {raw_cov * 100:.1f}%")
    coverage = float((yte <= p90).mean())
    headroom = float(np.mean((p90 - yte) / np.maximum(yte, 1e-6)))
    print(f"\n{'=' * 66}")
    print(f"P90 covers {coverage * 100:.1f}% of actual yields "
          f"(target ~90%: below means honest beekeepers get blocked)")
    print(f"mean headroom above actual: {headroom * 100:+.0f}%")
    print(f"P50 median absolute error: "
          f"{float(np.median(np.abs(p50 - yte))):.2f} kg")
    print("=" * 66)

    if coverage < 0.85:
        print("\nWARNING: the ceiling would block more than 15% of honest "
              "declarations. That is the failure mode that kills adoption -- "
              "widen the quantile or collect more data before deploying.")

    import joblib
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, m in models.items():
        joblib.dump(m, MODEL_DIR / f"yield_{name}.joblib")
    (MODEL_DIR / "yield.meta.json").write_text(json.dumps({
        "version": MODEL_VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "features": AGG_FEATURES,
        "window_days": args.window_days,
        "train_windows": int(len(tr)),
        "test_windows": int(len(te)),
        "quantile_metrics": metrics,
        "p90_coverage": round(coverage, 4),
        "p90_coverage_uncalibrated": round(raw_cov, 4),
        "calibration_factor": round(calibration_factor, 4),
        "p90_mean_headroom": round(headroom, 4),
        "caveat": (
            "Trained on simulated hives. The P90 becomes an on-chain mint "
            "ceiling, so before any real deployment this must be refitted on "
            "measured harvests from the target district -- a ceiling calibrated "
            "on a simulator would block real beekeepers."
        ),
    }, indent=2))
    print(f"\nsaved {MODEL_DIR}/yield_p{{10,50,90}}.joblib")
    return 0


if __name__ == "__main__":
    sys.exit(main())
