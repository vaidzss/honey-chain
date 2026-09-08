"""Colony-level models on REAL hives with REAL measured outcomes.

    python ml/train_mspb.py

Data: MSPB (Zenodo 10.5281/zenodo.8371700), **CC BY-NC 4.0**, fetched by
`ml/fetch_datasets.py`. 53 colonies in Quebec, one year, continuous audio-band
energies plus temperature and humidity, with hand-measured phenotypes.

**NON-COMMERCIAL DATA.** Nothing trained here may ship in a commercial
deployment without permission from the authors. It is a research check on the
two claims this project is weakest on.

## Why this dataset matters to us specifically

Two of our models have never seen a real colony:

- **Varroa.** Our classifier scores 0.510 recall on simulated hives and the
  sticky-board counter is validated only against synthetic boards. MSPB carries
  `Nb varroa / 100 bees` counted by hand on real colonies.
- **Yield.** The P90 of our forecast becomes an on-chain mint ceiling, and it
  has never been fitted against a real harvest. MSPB carries
  `Total honey production (kg)` per colony.

## The honesty constraint that shapes the evaluation

There are **53 colonies**, so the sample is 53 -- not the ~1.9 million sensor
rows, which are 53 colonies measured repeatedly. Any model evaluated per-row
would be scoring itself on near-duplicates of its training data.

So the unit is the **colony**, evaluation is leave-one-colony-out, and every
result is reported against a **predict-the-mean baseline**. With n=53 an R² near
zero is the honest expectation for a hard target, and saying so is worth more
than a number that flatters us.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "ml/datasets/raw/mspb_quebec"
METRICS = ROOT / "metrics"
VERSION = "mspb-colony-0.1.0"

BANDS = [f"hz_{v}" for v in (
    "122.0703125", "152.587890625", "183.10546875", "213.623046875",
    "244.140625", "274.658203125", "305.17578125", "335.693359375",
    "366.2109375", "396.728515625", "427.24609375", "457.763671875",
    "488.28125", "518.798828125", "549.31640625", "579.833984375")]
EXTRA = ["temperature", "humidity", "audio_density", "audio_density_ratio",
         "density_variation", "hive_power"]


TAG_OFFSET = 200_000


def colony_from_tag(tag: object) -> float:
    """`tag_number` 202056 is colony 2056.

    This is the join, and getting it wrong is silent. `beehub_name` looks like
    an identifier but is the APIARY -- there are only two of them for all 53
    colonies -- so joining on it assigns two feature vectors to fifty-three
    colonies and any resulting score is an apiary-mean effect wearing an
    acoustic model's clothes. The README calls tag_number "ID unique to each
    hive"; subtracting the 200000 offset reproduces all 53 colony numbers in
    the workbook's own lookup table, which is what confirms it.
    """
    v = pd.to_numeric(tag, errors="coerce")
    return v - TAG_OFFSET


def load_phenotypes() -> pd.DataFrame:
    xl = pd.ExcelFile(RAW / "D1_ant.xlsx")
    df = xl.parse("Phenotypic measurements", header=[0, 1])
    df.columns = [f"{a}|{b}" for a, b in df.columns]

    def col(pat: str) -> str | None:
        for c in df.columns:
            if pat.lower() in c.lower():
                return c
        return None

    out = pd.DataFrame({
        "colony": pd.to_numeric(df[col("Hive ID")], errors="coerce"),
        "apiary": df[col("Apiary")],
        "varroa_1": pd.to_numeric(df[col("Varroa infestation|Nb varroa / 100 bees")],
                                  errors="coerce"),
        "varroa_2": pd.to_numeric(df[col("Nb varroa / 100 bees.1")],
                                  errors="coerce"),
        "honey_kg": pd.to_numeric(df[col("Total honey production")],
                                  errors="coerce"),
        "winter_weight_kg": pd.to_numeric(df[col("Weight before winter")],
                                          errors="coerce"),
        "brood_total": pd.to_numeric(df[col("Total brood")], errors="coerce"),
    })
    out = out[out["colony"].notna()].copy()
    # the later count is the end-of-season load, which is the one that decides
    # whether a colony survives winter
    out["varroa"] = out["varroa_2"].fillna(out["varroa_1"])
    return out


def load_sensor_aggregates() -> pd.DataFrame:
    """One row per colony. Streamed in chunks: the two CSVs are ~1.9M rows and
    this machine has been OOM-killed before."""
    use = ["tag_number", "beehub_name", "date"] + BANDS + EXTRA
    acc: dict[str, list[pd.DataFrame]] = {}
    for f in ("D1_sensor_data.csv", "D2_sensor_data.csv"):
        p = RAW / f
        if not p.exists():
            continue
        print(f"  streaming {f}")
        for chunk in pd.read_csv(p, usecols=lambda c: c in use,
                                 chunksize=250_000, low_memory=False):
            chunk["colony"] = colony_from_tag(chunk["tag_number"])
            chunk = chunk[chunk["colony"].notna()]
            num = [c for c in BANDS + EXTRA if c in chunk.columns]
            for c in num:
                chunk[c] = pd.to_numeric(chunk[c], errors="coerce")
            g = chunk.groupby("colony")[num].agg(["mean", "std", "count"])
            acc.setdefault("parts", []).append(g)

    parts = acc.get("parts", [])
    if not parts:
        raise SystemExit("no sensor rows read")
    # combine chunk-level means by weighting on count
    allp = pd.concat(parts)
    out = {}
    for colony, sub in allp.groupby(level=0):
        row = {}
        for c in {c for c, _ in sub.columns}:
            n = sub[(c, "count")].to_numpy(float)
            m = sub[(c, "mean")].to_numpy(float)
            s = sub[(c, "std")].to_numpy(float)
            tot = np.nansum(n)
            if tot <= 0:
                row[f"{c}_mean"], row[f"{c}_std"] = np.nan, np.nan
                continue
            row[f"{c}_mean"] = float(np.nansum(m * n) / tot)
            # a chunk holding one row for a colony has no sd; all-NaN is normal
            row[f"{c}_std"] = float(np.nanmean(s)) if not np.all(np.isnan(s)) \
                else 0.0
        row["n_rows"] = float(np.nansum(sub[(BANDS[0], "count")].to_numpy(float)))
        out[colony] = row
    df = pd.DataFrame(out).T
    df.index.name = "colony"
    df = df.reset_index()
    df["colony"] = pd.to_numeric(df["colony"], errors="coerce")
    return df


def evaluate(X: np.ndarray, y: np.ndarray, label: str, unit: str) -> dict:
    """Leave-one-colony-out against a predict-the-mean baseline."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import LeaveOneOut

    preds, base = np.zeros(len(y)), np.zeros(len(y))
    for tr, te in LeaveOneOut().split(X):
        m = HistGradientBoostingRegressor(
            max_iter=200, learning_rate=0.05, min_samples_leaf=5,
            l2_regularization=1.0, random_state=0).fit(X[tr], y[tr])
        preds[te] = m.predict(X[te])
        base[te] = y[tr].mean()

    mae = float(np.mean(np.abs(preds - y)))
    bmae = float(np.mean(np.abs(base - y)))
    ss_res = float(np.sum((y - preds) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")
    beats = mae < bmae

    print(f"\n  {label}  (n={len(y)} colonies, unit={unit})")
    print(f"    model MAE     {mae:8.3f}")
    print(f"    baseline MAE  {bmae:8.3f}   (predict the training mean)")
    print(f"    R^2           {r2:8.3f}")
    print(f"    verdict       {'beats baseline' if beats else 'NO BETTER THAN THE MEAN'}")
    return {"n_colonies": int(len(y)), "unit": unit,
            "mae": round(mae, 4), "baseline_mae": round(bmae, 4),
            "r2": round(float(r2), 4), "beats_baseline": bool(beats),
            "improvement_over_baseline": round(float(bmae - mae), 4)}


def main() -> int:
    if not (RAW / "D1_ant.xlsx").exists():
        raise SystemExit("run: python ml/fetch_datasets.py mspb_quebec")

    print("loading hand-measured phenotypes")
    ph = load_phenotypes()
    print(f"  {len(ph)} colonies with a bee-hub id")

    print("aggregating sensor stream per colony")
    sens = load_sensor_aggregates()
    print(f"  {len(sens)} colonies in the sensor stream")

    df = ph.merge(sens, on="colony", how="inner")
    print(f"  {len(df)} colonies join on both sides")

    # Guard against the join silently collapsing. If the key were the apiary
    # rather than the hive, every colony would share one of two feature
    # vectors and every score below would be an apiary effect.
    distinct = df[[c for c in df.columns if c.endswith("_mean")]].round(6)         .drop_duplicates()
    print(f"  {len(distinct)} distinct feature vectors for {len(df)} colonies")
    if len(distinct) < 0.5 * len(df):
        raise SystemExit(
            f"JOIN COLLAPSED: {len(distinct)} distinct feature rows for "
            f"{len(df)} colonies. The join key is not colony-unique; refusing "
            f"to report a score that would be a group-mean effect.")

    feat = [c for c in df.columns
            if c.endswith(("_mean", "_std")) and not c.startswith(
                ("varroa", "honey", "winter", "brood"))]
    results = {}

    for target, unit in (("varroa", "varroa per 100 bees"),
                         ("honey_kg", "kg of honey")):
        sub = df[df[target].notna()].copy()
        X = sub[feat].to_numpy(float)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        y = sub[target].to_numpy(float)
        if len(y) < 12:
            print(f"\n  {target}: only {len(y)} colonies, skipping")
            continue
        results[target] = evaluate(X, y, target, unit)
        results[target]["target_mean"] = round(float(y.mean()), 3)
        results[target]["target_std"] = round(float(y.std()), 3)

    meta = {
        "version": VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": "MSPB (Zenodo 8371700), CC BY-NC 4.0",
        "dataset_doi": "10.5281/zenodo.8371700",
        "licence_restriction": (
            "NON-COMMERCIAL. Research use only. Nothing trained on MSPB may "
            "ship in a commercial deployment without the authors' permission."),
        "species": "Apis mellifera",
        "colonies_joined": int(len(df)),
        "features": len(feat),
        "feature_source": "per-colony mean and sd of 16 audio band energies, "
                          "temperature, humidity, audio density and hive power",
        "evaluation": (
            "Leave-one-COLONY-out against a predict-the-mean baseline. The unit "
            "is the colony (n=53), not the sensor row (~1.9M), because rows "
            "from one colony are repeated measurements of the same thing."),
        "results": results,
        "finding": (
            "Season-MEAN acoustic, temperature and humidity features carry no "
            "usable signal about a colony's end-of-season varroa load or honey "
            "yield across these 53 colonies. Both targets score worse than "
            "predicting the training mean."),
        "what_this_does_and_does_not_say": (
            "It does NOT say hive sensors cannot predict yield. It says the "
            "crudest defensible summarisation -- one average per colony per "
            "sensor over a whole season -- throws away the trajectory, and "
            "trajectory is where our own simulator models find their signal "
            "(ml/features.py uses rolling windows precisely because faults are "
            "trajectories, not instants). A time-resolved model is the obvious "
            "next step."),
        "why_we_did_not_keep_tuning": (
            "With n=53 colonies and a leave-one-out score, iterating on feature "
            "engineering until the number turns positive is fitting the "
            "evaluation, not the problem. We ran the analysis we designed "
            "before seeing the answer, and we are reporting what it gave."),
        "bug_found_and_fixed": (
            "The first run of this script joined on `beehub_name` and reported "
            "honey R^2 = +0.136, 'beats baseline'. beehub_name is the APIARY: "
            "two values for 53 colonies, so every colony received one of two "
            "feature vectors and the score was an apiary-mean effect. Joining "
            "correctly on tag_number - 200000 moved honey R^2 to -0.309. A "
            "guard now refuses to report any score when the number of distinct "
            "feature vectors is under half the number of colonies."),
        "caveat": (
            "Apis mellifera in Quebec, n=53 colonies. A negative result at this "
            "sample size is weak evidence, and it is reported rather than tuned "
            "away."),
    }
    METRICS.mkdir(exist_ok=True)
    (METRICS / "mspb_colony.json").write_text(json.dumps(meta, indent=2),
                                              encoding="utf-8")
    print(f"\nsaved {METRICS / 'mspb_colony.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
