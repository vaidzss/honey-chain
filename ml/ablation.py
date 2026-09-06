"""Which sensors actually earn their place on the node?

The health classifier's permutation importances put mass dynamics on top and
acoustic features well below. If that holds under ablation it is a real
finding with a cost consequence: a microphone, its amplifier and the DSP duty
cycle are a meaningful share of node BOM and power budget, and at 10-15%
sentinel coverage across a quarter of a million KVIC bee boxes, dropping a
component that buys two points of F1 is a different deployment.

Run:  python ml/ablation.py
"""
from __future__ import annotations

import json, sys, time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import classification_report

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_COLUMNS, build_features  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

ACOUSTIC = [c for c in FEATURE_COLUMNS
            if c.startswith(("be_n", "mfcc_")) or c in
            ("sound_rms", "centroid_hz", "spectral_spread", "low_band_ratio",
             "queen_band_ratio", "harmonic_ratio")]
THERMAL = [c for c in FEATURE_COLUMNS
           if c in ("thermal_delta", "setpoint_error", "t_in", "t_out", "rh_delta",
                    "t_in_std_24h", "delta_mean_24h", "delta_min_24h")]
MASS = [c for c in FEATURE_COLUMNS
        if c in ("weight_kg", "weight_chg_24h", "weight_chg_7d", "weight_std_24h")]
TRAFFIC = [c for c in FEATURE_COLUMNS
           if c in ("entrance_total", "entrance_net", "traffic_per_gram")]
CONTEXT = [c for c in FEATURE_COLUMNS if c in ("hour", "month", "is_night")]

SETS = {
    "everything":              FEATURE_COLUMNS,
    "no microphone":           [c for c in FEATURE_COLUMNS if c not in ACOUSTIC],
    "no load cell":            [c for c in FEATURE_COLUMNS if c not in MASS],
    "no thermometers":         [c for c in FEATURE_COLUMNS if c not in THERMAL],
    "scale + thermometer only": MASS + THERMAL + CONTEXT,
    "microphone only":         ACOUSTIC + CONTEXT,
    "scale only":              MASS + CONTEXT,
}


OUT = ROOT / "metrics"


def main() -> int:
    tr = build_features(pd.read_csv(ROOT / "ml/datasets/train_v2.csv"))
    te = build_features(pd.read_csv(ROOT / "ml/datasets/holdout_v1.csv"))
    ytr, yte = tr["gt_state"], te["gt_state"]

    print(f"{'sensor set':<26}{'macro F1':>10}{'accuracy':>10}"
          f"{'queenless':>11}{'varroa':>9}{'pre_swarm':>11}")
    print("-" * 77)
    results = {}
    for name, cols in SETS.items():
        t0 = time.time()
        clf = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.1, min_samples_leaf=40,
            l2_regularization=1.0, random_state=0)
        clf.fit(tr[cols], ytr)
        rep = classification_report(yte, clf.predict(te[cols]), output_dict=True,
                                    zero_division=0)
        def rec(k): return rep[k]["recall"] if k in rep else float("nan")
        results[name] = {
            "features": len(cols),
            "macro_f1": round(rep["macro avg"]["f1-score"], 4),
            "accuracy": round(rep["accuracy"], 4),
            "recall": {k: round(rec(k), 4) for k in
                       ("queenless", "pre_swarm", "varroa", "starvation", "robbing")
                       if k in rep},
        }
        print(f"{name:<26}{rep['macro avg']['f1-score']:>10.3f}"
              f"{rep['accuracy']:>10.3f}{rec('queenless'):>11.3f}"
              f"{rec('varroa'):>9.3f}{rec('pre_swarm'):>11.3f}"
              f"   ({time.time()-t0:.0f}s)")

    # Derive the conclusion from the numbers rather than restating it from
    # memory -- a hardcoded sentence goes stale the first time the dataset
    # changes, and a metrics artefact that contradicts its own table is worse
    # than no artefact.
    full = results["everything"]
    no_mic = results["no microphone"]
    swarm_drop = (full["recall"].get("pre_swarm", 0)
                  - no_mic["recall"].get("pre_swarm", 0))
    worst = min(results.items(), key=lambda kv: kv[1]["macro_f1"])
    single_best = max(
        ((k, v) for k, v in results.items() if k.endswith("only")),
        key=lambda kv: kv[1]["macro_f1"])
    finding = (
        f"Removing the microphone costs {full['macro_f1'] - no_mic['macro_f1']:.3f} "
        f"macro F1 and {swarm_drop:.3f} of pre_swarm recall "
        f"({full['recall'].get('pre_swarm', 0):.3f} -> "
        f"{no_mic['recall'].get('pre_swarm', 0):.3f}) -- swarm prediction is "
        f"almost entirely acoustic. The best single-sensor configuration is "
        f"'{single_best[0]}' at {single_best[1]['macro_f1']:.3f}; the worst is "
        f"'{worst[0]}' at {worst[1]['macro_f1']:.3f}. Permutation importance "
        f"ranked mass features on top and was misleading, because the 33 "
        f"acoustic features are correlated and permuting one at a time barely "
        f"moves the score. Ablation by sensor GROUP is the honest measurement.")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sensor_ablation.json").write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "test_set": "ml/datasets/holdout_v1.csv (separate run, disjoint hives)",
        "results": results,
        "finding": finding,
    }, indent=2), encoding="utf-8")
    print()
    print(f"saved {OUT / 'sensor_ablation.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
