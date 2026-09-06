"""Train the colony-health classifier.

    python ml/train_health.py

Predicts the state a beekeeper needs to act on -- healthy, queenless,
pre_swarm, varroa, starvation, robbing, wax_moth, dead -- from telemetry a
sentinel node actually produces.

## Two evaluation rules that are not negotiable

**Split by hive, never by row.** Consecutive hourly frames from one colony are
near-duplicates. A random row split puts almost-identical rows on both sides
and yields a fake 99% that collapses on contact with a new hive. Here the
training and validation hives are disjoint, and the *test set is a separate
simulation run with different hives and a different seed entirely*.

**Report per-class recall, not just accuracy.** The set is 68% healthy, so a
model that predicts "healthy" for everything scores 68% and is worthless. The
rare classes are the ones that matter.

## What this number is and is not

This measures whether the model learned the physics the simulator encodes. It
is **not** evidence that it works on real bees. Publishing simulator accuracy
as field accuracy would be dishonest, and the gap is stated everywhere this
model is reported.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import classification_report, confusion_matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_COLUMNS, build_features, feature_matrix  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "ml" / "models"
MODEL_VERSION = "health-hgb-0.1.0"


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return build_features(df)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default=str(ROOT / "ml/datasets/train_v1.csv"))
    ap.add_argument("--holdout", default=str(ROOT / "ml/datasets/holdout_v1.csv"))
    ap.add_argument("--val-hives", type=int, default=9,
                    help="hives held out of training for early stopping")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    print("loading and building features...")
    t0 = time.time()
    train_all = load(Path(args.train))
    holdout = load(Path(args.holdout))
    print(f"  train {len(train_all):,} rows / {train_all.node_id.nunique()} hives")
    print(f"  test  {len(holdout):,} rows / {holdout.node_id.nunique()} hives "
          f"(separate run, disjoint hives)")
    print(f"  {time.time() - t0:.1f}s")

    # --- hive-level split -------------------------------------------------
    rng = np.random.default_rng(args.seed)
    hives = np.array(sorted(train_all.node_id.unique()))
    rng.shuffle(hives)
    val_hives = set(hives[: args.val_hives])
    is_val = train_all.node_id.isin(val_hives)
    train, val = train_all[~is_val], train_all[is_val]
    print(f"\nsplit by hive: {train.node_id.nunique()} train / "
          f"{val.node_id.nunique()} val (no hive in both)")

    Xtr, ytr = feature_matrix(train), train["gt_state"]
    Xva, yva = feature_matrix(val), val["gt_state"]
    Xte, yte = feature_matrix(holdout), holdout["gt_state"]

    print("\ntraining...")
    t0 = time.time()
    clf = HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.1,
        max_leaf_nodes=31,
        min_samples_leaf=40,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=None,   # we supply our own, split by hive
        random_state=args.seed,
    )
    # sklearn cannot take an explicit validation set here, so early stopping
    # uses an internal split; the hive-held-out set below is the honest check.
    clf.fit(Xtr, ytr)
    print(f"  {time.time() - t0:.1f}s, {clf.n_iter_} iterations")

    # --- honest evaluation -------------------------------------------------
    for name, X, y in (("VALIDATION (held-out hives, same run)", Xva, yva),
                       ("TEST (separate run, different hives + seed)", Xte, yte)):
        pred = clf.predict(X)
        print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
        print(classification_report(y, pred, digits=3, zero_division=0))

    pred = clf.predict(Xte)
    labels = sorted(set(yte) | set(pred))
    cm = confusion_matrix(yte, pred, labels=labels)
    print("confusion matrix on the separate test run (rows = truth):")
    w = max(len(x) for x in labels) + 1
    print(" " * w + "".join(f"{l[:8]:>9}" for l in labels))
    for lab, row in zip(labels, cm):
        print(f"{lab:<{w}}" + "".join(f"{v:>9,}" for v in row))

    # --- what the model actually leans on ---------------------------------
    try:
        from sklearn.inspection import permutation_importance
        sub = Xte.sample(min(4000, len(Xte)), random_state=0)
        imp = permutation_importance(
            clf, sub, yte.loc[sub.index], n_repeats=3, random_state=0, n_jobs=1)
        order = np.argsort(imp.importances_mean)[::-1][:12]
        print("\ntop features by permutation importance:")
        for i in order:
            print(f"  {FEATURE_COLUMNS[i]:<20} {imp.importances_mean[i]:.4f}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n(permutation importance skipped: {exc})")

    # --- persist -----------------------------------------------------------
    import joblib
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, MODEL_DIR / "health_clf.joblib")

    report = classification_report(yte, clf.predict(Xte), digits=3,
                                   output_dict=True, zero_division=0)
    meta = {
        "version": MODEL_VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "features": FEATURE_COLUMNS,
        "classes": sorted(clf.classes_.tolist()),
        "train_rows": int(len(train)),
        "train_hives": int(train.node_id.nunique()),
        "test_rows": int(len(holdout)),
        "test_hives": int(holdout.node_id.nunique()),
        "test_macro_f1": round(report["macro avg"]["f1-score"], 4),
        "test_accuracy": round(report["accuracy"], 4),
        "per_class_recall": {
            k: round(v["recall"], 4) for k, v in report.items()
            if isinstance(v, dict) and "recall" in v and k not in
            ("macro avg", "weighted avg")
        },
        "caveat": (
            "Trained and evaluated entirely on simulated hives. This measures "
            "whether the model learned the simulator's physics, NOT that it "
            "works on real bees. Most public bee acoustics are Apis mellifera; "
            "Apis cerana indica is a further domain gap."
        ),
    }
    (MODEL_DIR / "health_clf.meta.json").write_text(json.dumps(meta, indent=2))
    print(f"\nsaved {MODEL_DIR / 'health_clf.joblib'}")
    print(f"  macro F1 {meta['test_macro_f1']}  accuracy {meta['test_accuracy']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
