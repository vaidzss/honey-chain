"""Colony-level models on REAL hives with REAL measured outcomes.

    python ml/train_mspb.py

Data: MSPB (Zenodo 10.5281/zenodo.8371700), **CC BY-NC 4.0**, fetched by
`ml/fetch_datasets.py`. 53 colonies in Quebec, 2020-2021, continuous audio band
energies plus temperature and humidity, with hand-measured phenotypes.

**NON-COMMERCIAL DATA.** Nothing trained here may ship in a commercial
deployment without permission from the authors.

## What changed after the first attempt found nothing

The first version averaged every sensor over the whole season, one number per
colony, and both targets scored worse than predicting the mean. Three things
were wrong, and all three are fixed here:

1. **It threw away the trajectory.** Now: monthly aggregates summarised into
   mean, spread, range, last value and seasonal slope (`ml/mspb_features.py`).

2. **It leaked.** A season average spans months AFTER the varroa count was
   taken on 13 Aug 2020. Now: only data strictly before the measurement date
   feeds a prediction of it.

3. **Varroa was modelled as regression on a zero-inflated target.** More than
   half the colonies measured exactly 0 mites per 100 bees (median 0.0, max
   2.74), so "predict the mean" is a strong baseline and R² is close to
   meaningless. Now: a binary "any mites detected" classifier scored by AUC,
   with the class balance stated.

## The honesty constraint that still applies

There are **53 colonies**, so the sample is 53 -- not the ~1.9M sensor rows.
Evaluation is leave-one-colony-out against an explicit baseline, and the feature
count is kept small on purpose: 150 features against 53 samples fits noise, and
a leave-one-out score does not rescue that.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mspb_features import (BAND_GROUPS, SIGNALS, derive_signals,  # noqa: E402
                           excel_serial_to_date, shape_features)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "ml/datasets/raw/mspb_quebec"
METRICS = ROOT / "metrics"
VERSION = "mspb-colony-0.2.0"

TAG_OFFSET = 200_000
RAW_COLS = [c for cols in BAND_GROUPS.values() for c in cols] + [
    "temperature", "humidity", "audio_density"]


def colony_from_tag(tag: object) -> float:
    """`tag_number` 202056 is colony 2056.

    Getting this wrong is silent. `beehub_name` looks like an identifier but is
    the APIARY -- two values for 53 colonies -- so joining on it gives every
    colony one of two feature vectors and any score is an apiary-mean effect.
    That is exactly what version 0.1.0 did, and it reported honey R^2 = +0.136
    for it.
    """
    return pd.to_numeric(tag, errors="coerce") - TAG_OFFSET


def load_phenotypes() -> pd.DataFrame:
    xl = pd.ExcelFile(RAW / "D1_ant.xlsx")
    df = xl.parse("Phenotypic measurements", header=[0, 1])
    df.columns = [f"{a}|{b}" for a, b in df.columns]

    def col(pat: str) -> str:
        for c in df.columns:
            if pat.lower() in c.lower():
                return c
        raise KeyError(pat)

    out = pd.DataFrame({
        "colony": pd.to_numeric(df[col("Hive ID")], errors="coerce"),
        "apiary": df[col("Apiary")],
        "varroa_late": pd.to_numeric(df[col("Nb varroa / 100 bees.1")],
                                     errors="coerce"),
        "varroa_early": pd.to_numeric(
            df[col("Varroa infestation|Nb varroa / 100 bees")], errors="coerce"),
        "varroa_date": df[col("Varroa infestation|Date.1")],
        "honey_kg": pd.to_numeric(df[col("Total honey production")],
                                  errors="coerce"),
    })
    out = out[out["colony"].notna()].copy()
    out["varroa"] = out["varroa_late"].fillna(out["varroa_early"])
    return out


def monthly_aggregates() -> pd.DataFrame:
    """Per colony, per calendar month: the mean of each derived signal.

    Streamed in chunks -- the CSVs are ~1.9M rows and this machine has been
    OOM-killed before.
    """
    sums: dict[tuple, np.ndarray] = defaultdict(
        lambda: np.zeros(len(SIGNALS), dtype=float))
    counts: dict[tuple, np.ndarray] = defaultdict(
        lambda: np.zeros(len(SIGNALS), dtype=float))

    use = set(RAW_COLS) | {"tag_number", "date"}
    for f in ("D1_sensor_data.csv", "D2_sensor_data.csv"):
        p = RAW / f
        if not p.exists():
            continue
        print(f"  streaming {f}")
        for chunk in pd.read_csv(p, usecols=lambda c: c in use,
                                 chunksize=250_000, low_memory=False):
            chunk["colony"] = colony_from_tag(chunk["tag_number"])
            ts = pd.to_datetime(chunk["date"], errors="coerce")
            chunk = chunk.assign(_ym=ts.dt.year * 100 + ts.dt.month)
            chunk = chunk[chunk["colony"].notna() & chunk["_ym"].notna()]
            if chunk.empty:
                continue
            for c in RAW_COLS:
                if c in chunk.columns:
                    chunk[c] = pd.to_numeric(chunk[c], errors="coerce")
            sig = derive_signals(chunk)
            sig["colony"] = chunk["colony"].to_numpy()
            sig["_ym"] = chunk["_ym"].to_numpy()
            g = sig.groupby(["colony", "_ym"])[SIGNALS]
            for key, s in g.sum().iterrows():
                sums[key] += np.nan_to_num(s.to_numpy(dtype=float))
            for key, c in g.count().iterrows():
                counts[key] += c.to_numpy(dtype=float)

    rows = []
    for key in sums:
        n = np.where(counts[key] > 0, counts[key], np.nan)
        rows.append({"colony": key[0], "ym": int(key[1]),
                     **dict(zip(SIGNALS, sums[key] / n))})
    return pd.DataFrame(rows)


def build_features(monthly: pd.DataFrame, cutoff_ym: int | None) -> pd.DataFrame:
    """One feature row per colony, using only months before `cutoff_ym`."""
    sub = monthly if cutoff_ym is None else monthly[monthly["ym"] < cutoff_ym]
    out = []
    for colony, g in sub.groupby("colony"):
        g = g.sort_values("ym").set_index("ym")[SIGNALS]
        feats = shape_features(g)
        if feats:
            out.append({"colony": colony, "n_months": len(g), **feats})
    return pd.DataFrame(out)


def loo_regression(X: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import LeaveOneOut

    pred, base = np.zeros(len(y)), np.zeros(len(y))
    for tr, te in LeaveOneOut().split(X):
        m = HistGradientBoostingRegressor(
            max_iter=150, learning_rate=0.05, min_samples_leaf=5,
            l2_regularization=1.0, random_state=0).fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
        base[te] = y[tr].mean()
    mae, bmae = float(np.mean(np.abs(pred - y))), float(np.mean(np.abs(base - y)))
    ss_res, ss_tot = float(np.sum((y - pred) ** 2)), float(np.sum((y - y.mean()) ** 2))
    return {"n": int(len(y)), "mae": round(mae, 4), "baseline_mae": round(bmae, 4),
            "r2": round(1 - ss_res / ss_tot if ss_tot else float("nan"), 4),
            "beats_baseline": bool(mae < bmae)}


def loo_classification(X: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import LeaveOneOut

    prob = np.zeros(len(y))
    for tr, te in LeaveOneOut().split(X):
        m = HistGradientBoostingClassifier(
            max_iter=150, learning_rate=0.05, min_samples_leaf=5,
            l2_regularization=1.0, random_state=0).fit(X[tr], y[tr])
        prob[te] = m.predict_proba(X[te])[:, 1]
    auc = float(roc_auc_score(y, prob)) if len(set(y.tolist())) > 1 else float("nan")
    majority = float(max(y.mean(), 1 - y.mean()))
    acc = float(((prob > 0.5).astype(int) == y).mean())
    return {"n": int(len(y)), "positive_rate": round(float(y.mean()), 4),
            "auc": round(auc, 4), "accuracy": round(acc, 4),
            "majority_baseline": round(majority, 4),
            "beats_chance": bool(auc > 0.60)}


def main() -> int:
    if not (RAW / "D1_ant.xlsx").exists():
        raise SystemExit("run: python ml/fetch_datasets.py mspb_quebec")

    print("loading hand-measured phenotypes")
    ph = load_phenotypes()
    vdate = excel_serial_to_date(ph["varroa_date"].dropna().iloc[0])
    cutoff = vdate.year * 100 + vdate.month if vdate is not None else None
    print(f"  {len(ph)} colonies; varroa measured {vdate.date() if vdate else '?'}"
          f" -> using months before {cutoff}")

    print("aggregating sensor stream by colony and month")
    monthly = monthly_aggregates()
    print(f"  {monthly['colony'].nunique()} colonies, "
          f"{monthly['ym'].nunique()} months, {len(monthly)} colony-months")

    results, notes = {}, {}

    # ---- varroa: binary, using only pre-measurement months ------------------
    fx = build_features(monthly, cutoff)
    df = ph.merge(fx, on="colony", how="inner")
    feat = [c for c in fx.columns if c not in ("colony",)]
    distinct = df[feat].round(6).drop_duplicates()
    print(f"  varroa set: {len(df)} colonies, {len(feat)} features, "
          f"{len(distinct)} distinct feature rows")
    if len(distinct) < 0.5 * len(df):
        raise SystemExit("JOIN COLLAPSED: refusing to report a group-mean effect")

    sub = df[df["varroa"].notna()]
    X = np.nan_to_num(sub[feat].to_numpy(float), nan=0.0, posinf=0.0, neginf=0.0)
    y_bin = (sub["varroa"].to_numpy(float) > 0).astype(int)
    results["varroa_detected_binary"] = loo_classification(X, y_bin)
    results["varroa_detected_binary"]["unit"] = "any mites found (>0 per 100 bees)"
    notes["varroa"] = (
        f"Modelled as CLASSIFICATION because the count is zero-inflated: "
        f"{int((sub['varroa'] == 0).sum())} of {len(sub)} colonies measured "
        f"exactly zero, median 0.0, max {sub['varroa'].max():.2f}. Regression "
        f"on that target mostly rewards predicting the mean. Features use only "
        f"months before the measurement date.")

    # ---- honey: regression over the summer ---------------------------------
    fx_all = build_features(monthly, None)
    df2 = ph.merge(fx_all, on="colony", how="inner")
    feat2 = [c for c in fx_all.columns if c != "colony"]
    sub2 = df2[df2["honey_kg"].notna()]
    X2 = np.nan_to_num(sub2[feat2].to_numpy(float), nan=0.0, posinf=0.0, neginf=0.0)
    y2 = sub2["honey_kg"].to_numpy(float)
    results["honey_kg"] = loo_regression(X2, y2)
    results["honey_kg"]["unit"] = "kg of honey"
    results["honey_kg"]["target_mean"] = round(float(y2.mean()), 2)
    results["honey_kg"]["target_sd"] = round(float(y2.std()), 2)

    print()
    for k, r in results.items():
        if "auc" in r:
            print(f"  {k}: n={r['n']} positives={r['positive_rate']:.0%}  "
                  f"AUC {r['auc']:.3f}  acc {r['accuracy']:.3f} "
                  f"(majority {r['majority_baseline']:.3f})  "
                  f"-> {'signal' if r['beats_chance'] else 'NO SIGNAL'}")
        else:
            print(f"  {k}: n={r['n']}  MAE {r['mae']:.2f} vs baseline "
                  f"{r['baseline_mae']:.2f}  R2 {r['r2']:.3f} "
                  f"-> {'beats baseline' if r['beats_baseline'] else 'NO BETTER THAN THE MEAN'}")

    meta = {
        "version": VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": "MSPB (Zenodo 8371700), CC BY-NC 4.0",
        "dataset_doi": "10.5281/zenodo.8371700",
        "licence_restriction": (
            "NON-COMMERCIAL. Research use only. Nothing trained on MSPB may "
            "ship in a commercial deployment without the authors' permission."),
        "species": "Apis mellifera",
        "colonies": int(len(df)),
        "features": len(feat),
        "feature_design": (
            "Monthly means of 7 derived signals (low/mid/high band energy, "
            "high-low ratio, temperature, humidity, audio density), each "
            "summarised as mean, sd, last value, range and seasonal slope."),
        "varroa_cutoff_month": cutoff,
        "results": results,
        "notes": notes,
        "evaluation": (
            "Leave-one-COLONY-out (n=53). The unit is the colony, not the "
            "sensor row, because rows from one colony are repeated "
            "measurements of the same thing."),
        "changes_from_0_1_0": (
            "v0.1.0 used one season average per colony and found nothing. It "
            "also leaked (the average spanned months after the varroa count) "
            "and modelled a zero-inflated count as regression. v0.2.0 uses "
            "time-resolved monthly features, restricts varroa features to "
            "months before the measurement, and models varroa as binary "
            "detection scored by AUC."),
        "caveat": (
            "Apis mellifera in Quebec, n=53 colonies. At this sample size both "
            "a positive and a negative result are weak evidence, and no amount "
            "of feature engineering changes that. Reported as measured."),
    }
    METRICS.mkdir(exist_ok=True)
    (METRICS / "mspb_colony.json").write_text(json.dumps(meta, indent=2),
                                              encoding="utf-8")
    print(f"\nsaved {METRICS / 'mspb_colony.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
