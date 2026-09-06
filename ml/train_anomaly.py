"""Train the unsupervised anomaly detector.

    python ml/train_anomaly.py

## Why this exists alongside a classifier

The health classifier can only report states it was trained on. A hive that is
wrong in a way nobody labelled -- a load cell drifting, a node someone moved, a
colony absconding in a manner the simulator never scripted -- comes back
"healthy" with high confidence, because "unlike anything I have seen" is not one
of its classes.

So this is trained on **healthy windows only** and asks a different question:
*how far is this hive from any normal hive I have seen?* It catches novelty,
where the classifier catches known faults. Neither subsumes the other.

## Evaluation

One-class training means the usual metrics do not apply. What is reported is
**AUC per fault class**: given a healthy window and a faulty one, how often does
the detector score the faulty one as more anomalous? 0.5 is a coin flip.

Anything near 0.5 is reported as such. A detector that cannot separate a class
should say so rather than be quietly shipped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_COLUMNS, build_features  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "ml" / "models"
MODEL_VERSION = "anomaly-iforest-0.1.0"

# A deliberately small, physically-meaningful subset. IsolationForest degrades
# with many correlated dimensions -- feeding it all 33 acoustic bands makes
# every point look far from every other, which is the curse of dimensionality
# rather than an anomaly.
ANOMALY_FEATURES = [
    "thermal_delta", "setpoint_error", "delta_mean_24h", "delta_min_24h",
    "t_in_std_24h", "weight_chg_24h", "weight_chg_7d", "weight_std_24h",
    "sound_rms", "centroid_hz", "spectral_spread",
    "entrance_total", "traffic_per_gram", "rh_delta",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default=str(ROOT / "ml/datasets/train_v2.csv"))
    ap.add_argument("--holdout", default=str(ROOT / "ml/datasets/holdout_v1.csv"))
    ap.add_argument("--contamination", type=float, default=0.02)
    args = ap.parse_args()

    print("loading...")
    t0 = time.time()
    tr = build_features(pd.read_csv(args.train))
    te = build_features(pd.read_csv(args.holdout))
    print(f"  {len(tr):,} train / {len(te):,} test rows  ({time.time() - t0:.1f}s)")

    # one-class: fit on normality only
    healthy = tr[tr["gt_state"] == "healthy"].dropna(subset=ANOMALY_FEATURES)
    print(f"  fitting on {len(healthy):,} healthy windows only")

    scaler = StandardScaler().fit(healthy[ANOMALY_FEATURES])
    clf = IsolationForest(
        n_estimators=200, contamination=args.contamination,
        max_samples=4096, random_state=0, n_jobs=-1)
    clf.fit(scaler.transform(healthy[ANOMALY_FEATURES]))
    print(f"  trained in {time.time() - t0:.1f}s")

    te = te.dropna(subset=ANOMALY_FEATURES)
    # higher = more anomalous
    scores = -clf.score_samples(scaler.transform(te[ANOMALY_FEATURES]))
    te = te.assign(anomaly=scores)

    normal = te[te["gt_state"] == "healthy"]["anomaly"]
    print(f"\n{'fault class':<14}{'n':>8}{'AUC':>8}{'median score':>15}"
          f"{'  vs healthy median'}")
    print("-" * 62)
    print(f"{'healthy':<14}{len(normal):>8}{'--':>8}{normal.median():>15.3f}")

    aucs = {}
    for state in sorted(set(te["gt_state"]) - {"healthy"}):
        faulty = te[te["gt_state"] == state]["anomaly"]
        if len(faulty) < 20:
            print(f"{state:<14}{len(faulty):>8}{'too few':>8}")
            continue
        y = np.r_[np.zeros(len(normal)), np.ones(len(faulty))]
        auc = roc_auc_score(y, np.r_[normal.to_numpy(), faulty.to_numpy()])
        aucs[state] = round(float(auc), 4)
        verdict = ("strong" if auc > 0.85 else
                   "useful" if auc > 0.70 else
                   "weak" if auc > 0.60 else "no better than chance")
        print(f"{state:<14}{len(faulty):>8}{auc:>8.3f}{faulty.median():>15.3f}"
              f"   {verdict}")

    # Operating threshold from the healthy distribution, not from the faults:
    # in the field there is no label to tune against, and a threshold fitted to
    # known faults would not survive an unknown one.
    threshold = float(np.quantile(normal, 0.99))
    flagged = float((te["anomaly"] > threshold).mean())
    fp_rate = float((normal > threshold).mean())
    print(f"\nthreshold at the 99th percentile of healthy: {threshold:.3f}")
    print(f"  flags {flagged * 100:.1f}% of all windows, "
          f"{fp_rate * 100:.1f}% of healthy ones (false alarms)")

    import joblib
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"scaler": scaler, "model": clf, "threshold": threshold,
                 "features": ANOMALY_FEATURES},
                MODEL_DIR / "anomaly.joblib")
    (MODEL_DIR / "anomaly.meta.json").write_text(json.dumps({
        "version": MODEL_VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "features": ANOMALY_FEATURES,
        "fit_rows_healthy_only": int(len(healthy)),
        "threshold_p99_healthy": round(threshold, 4),
        "false_alarm_rate": round(fp_rate, 4),
        "auc_by_fault": aucs,
        "caveat": (
            "One-class detector fitted on simulated healthy hives. It answers "
            "'unlike normal', not 'this specific fault'. It complements the "
            "classifier rather than replacing it, and cannot be evaluated with "
            "ordinary accuracy because it never saw a labelled fault."
        ),
    }, indent=2))
    print(f"\nsaved {MODEL_DIR / 'anomaly.joblib'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
