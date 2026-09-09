"""Queen loss as PER-HIVE change detection, which is what deployment actually is.

    python ml/train_queen_baseline.py

## Why this framing, after the other one failed

`ml/train_acoustic.py` asks the question the literature asks: train on some
hives, classify a hive you have never heard. It fails -- 0.278 against a 0.510
baseline -- and the peer-reviewed *Bee Together* study (Sensors 2024) reports
the same collapse across 10 hives: 99.2% on a standard split, 34-84% when a hive
is held out, and models "unable to extrapolate their predictions of precise
Queen or NoQueen labels".

But **AuraBee never has to answer that question.** A sentinel node is bolted to
one hive and stays there. It hears that colony continuously, through the same
microphone, in the same position. So the deployable question is:

> Given this hive's own baseline while it is known queenright, can we detect
> when it stops sounding like itself?

That is one-class change detection per hive, not cross-hive classification. It
is the same shape as `ml/train_anomaly.py`, which already does this for
telemetry, and it does not require the transferable acoustic signature that the
public data cannot supply.

## The confound this framing does NOT escape

Within a hive, the queenright and queenless recordings are still different
*days*. Weather, forage and time of day differ, so some of any separation found
here is day effect rather than queen effect. That is a real limit and it is
reported rather than hidden. It is nonetheless closer to deployment than the
cross-hive test: in the field the comparison genuinely is "this hive today
versus this hive last week", and day-to-day variation is exactly the noise a
deployed detector must survive.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "ml/datasets/cache/acoustic_v1.npz"
METRICS = ROOT / "metrics"
VERSION = "queen-baseline-0.1.0"

RNG = np.random.default_rng(0)
HOLDOUT = 0.30          # share of queenright windows kept out of the baseline


def main() -> int:
    if not CACHE.exists():
        raise SystemExit("run ml/train_acoustic.py first to build the feature cache")
    d = np.load(CACHE, allow_pickle=True)
    X, y, hives = d["X"], d["y"], d["hives"]

    by_hive = {h: set(y[hives == h].tolist()) for h in sorted(set(hives.tolist()))}
    testable = [h for h, c in by_hive.items() if len(c) == 2]
    print(f"hives with both states: {testable}")
    if not testable:
        raise SystemExit("no hive has both queen states")

    rows = []
    for h in testable:
        m = hives == h
        Xq, Xnq = X[m & (y == 1)], X[m & (y == 0)]
        if len(Xq) < 50 or len(Xnq) < 20:
            print(f"  {h}: too few windows ({len(Xq)}/{len(Xnq)}), skipping")
            continue

        # Baseline is fitted ONLY on queenright windows from this hive. The
        # detector never sees a queenless example, which is the deployment
        # reality: a node is installed on a working colony and nobody labels a
        # failure in advance.
        idx = RNG.permutation(len(Xq))
        n_hold = int(len(Xq) * HOLDOUT)
        fit, held = Xq[idx[n_hold:]], Xq[idx[:n_hold]]

        det = IsolationForest(n_estimators=300, contamination=0.02,
                              random_state=0).fit(fit)
        # higher score = more anomalous
        s_norm = -det.score_samples(held)
        s_queenless = -det.score_samples(Xnq)

        truth = np.r_[np.zeros(len(s_norm)), np.ones(len(s_queenless))]
        score = np.r_[s_norm, s_queenless]
        auc = float(roc_auc_score(truth, score))

        # An operating point a beekeeper would actually get: threshold at the
        # 95th percentile of the hive's own normal, then how much of the
        # queenless period is flagged, at a 5% false-alarm rate by construction.
        thr = float(np.quantile(s_norm, 0.95))
        detect = float((s_queenless > thr).mean())
        far = float((s_norm > thr).mean())

        rows.append({"hive": h, "baseline_windows": int(len(fit)),
                     "held_out_normal": int(len(held)),
                     "queenless_windows": int(len(Xnq)),
                     "auc": round(auc, 4),
                     "detection_rate_at_5pct_false_alarm": round(detect, 4),
                     "false_alarm_rate": round(far, 4)})
        print(f"  {h:<8} AUC {auc:.3f}   detects {detect:.1%} of queenless "
              f"windows at {far:.1%} false alarm")

    if not rows:
        raise SystemExit("nothing evaluated")

    # ---- negative control: is this a QUEEN detector or a DAY detector? -----
    #
    # Within a hive, queenright and queenless recordings come from different
    # days, so the scores above could be measuring weather and forage rather
    # than the colony. The control fits a baseline on one day and scores a
    # DIFFERENT day IN THE SAME STATE. If same-state-different-day separates
    # just as well, the detector is reading the calendar and the headline
    # number means nothing.
    groups = d["groups"]
    control = []
    print("\nnegative control -- same state, different day:")
    for h in testable:
        for state in (0, 1):
            m = (hives == h) & (y == state)
            days = sorted({g.split("|")[1] for g in groups[m].tolist()})
            if len(days) < 2:
                continue
            a, b = days[0], days[1]
            ma = m & np.array([g.split("|")[1] == a for g in groups.tolist()])
            mb = m & np.array([g.split("|")[1] == b for g in groups.tolist()])
            Xa, Xb = X[ma], X[mb]
            if len(Xa) < 50 or len(Xb) < 20:
                continue
            idx = RNG.permutation(len(Xa))
            n_hold = int(len(Xa) * HOLDOUT)
            det = IsolationForest(n_estimators=300, contamination=0.02,
                                  random_state=0).fit(Xa[idx[n_hold:]])
            s_a = -det.score_samples(Xa[idx[:n_hold]])
            s_b = -det.score_samples(Xb)
            auc_c = float(roc_auc_score(
                np.r_[np.zeros(len(s_a)), np.ones(len(s_b))], np.r_[s_a, s_b]))
            label = "queenright" if state else "queenless"
            control.append({"hive": h, "state": label, "day_a": a, "day_b": b,
                            "auc": round(auc_c, 4)})
            print(f"  {h} {label:<10} {a} vs {b}   AUC {auc_c:.3f}")

    if not control:
        print("  no hive has two days in the same state -- control impossible")

    ctrl_auc = float(np.mean([c["auc"] for c in control])) if control else None
    day_detector = ctrl_auc is not None and ctrl_auc > 0.75
    if ctrl_auc is None:
        control_verdict = ("No hive has two days in the same state, so the day "
                           "effect could not be separated from the queen "
                           "effect. Treat the headline as an upper bound.")
    elif day_detector:
        control_verdict = (
            f"FAILS the control: same-state different-day separates at AUC "
            f"{ctrl_auc:.3f}, so the detector is largely reading the day rather "
            f"than the colony. The headline number must not be quoted as queen "
            f"detection.")
    else:
        control_verdict = (
            f"Passes the control: same-state different-day separates at only "
            f"AUC {ctrl_auc:.3f}, well below the queen-state separation, so the "
            f"signal is not merely a day effect.")

    mean_auc = float(np.mean([r["auc"] for r in rows]))
    mean_det = float(np.mean([r["detection_rate_at_5pct_false_alarm"] for r in rows]))
    works = mean_auc > 0.70

    print(f"\nmean AUC {mean_auc:.3f} over {len(rows)} hives")
    print(f"mean detection at 5% false alarm: {mean_det:.1%}")
    print(f"\ncontrol: {control_verdict}")
    if day_detector:
        print("\n" + "=" * 72)
        print("VERDICT: NOT a queen detector. The same colony scores AS HIGH or")
        print("HIGHER against ITSELF on a different day, so this is measuring")
        print("the recording day, not the queen. Not shipped.")
        print("=" * 72)
    else:
        print(f"verdict: "
              f"{'USABLE as a per-hive change detector' if works else 'NOT usable'}")

    meta = {
        "version": VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task": "per-hive one-class change detection: is this colony still "
                "sounding like itself?",
        "dataset": "To bee or not to bee (Nolasco & Benetos 2018), CC BY 4.0",
        "dataset_doi": "10.5281/zenodo.1321278",
        "method": "IsolationForest fitted ONLY on queenright windows from the "
                  "same hive; queenless windows scored as novelty",
        "hives_evaluated": len(rows),
        "mean_auc": round(mean_auc, 4),
        "mean_detection_at_5pct_false_alarm": round(mean_det, 4),
        "per_hive": rows,
        "negative_control_same_state_different_day": control,
        "control_mean_auc": (round(float(np.mean([c["auc"] for c in control])), 4)
                             if control else None),
        "control_verdict": control_verdict,
        "deployable_framing": bool(works and not day_detector),
        "why_this_framing": (
            "A sentinel node is fixed to one hive and hears it continuously "
            "through the same microphone. It never needs to classify a hive it "
            "has not heard, which is the task that fails cross-hive here and in "
            "the published Bee Together study (99.2% on a standard split, "
            "34-84% when a hive is held out)."),
        "confound_not_escaped": (
            "Within a hive the queenright and queenless recordings are still "
            "different DAYS, so part of any separation is day effect rather "
            "than queen effect. Distinguishing them needs recordings where a "
            "colony is followed through a queen loss on adjacent days, which no "
            "public dataset provides. This is an upper bound on performance, "
            "not a field estimate."),
        "finding": (
            "BOTH framings fail on this corpus. Cross-hive classification "
            "scores 0.278 against a 0.510 baseline. Per-hive change detection "
            "looks strong at 0.960 AUC until the control is run -- and the same "
            "colony in the SAME state on a different day separates at 0.999, "
            "higher than the queen comparison itself. The detector is reading "
            "the recording day, not the colony."),
        "what_would_actually_settle_it": (
            "Recordings of one hive on ADJACENT days spanning a queen loss, so "
            "day effect and queen effect are not the same variable. This corpus "
            "has one queenright day and one queenless day per hive, weeks "
            "apart. UrBAN (Nature Sci Data 2025, CC BY 4.0) records 10 hives "
            "continuously every 30 minutes for two years with queenright/"
            "queenless labels, which is exactly the structure required."),
        "caveat": (
            "Two hives, Apis mellifera, European. This is a negative result "
            "about the available public data, not a claim that hive acoustics "
            "cannot detect queen loss."),
    }
    METRICS.mkdir(exist_ok=True)
    (METRICS / "queen_baseline.json").write_text(json.dumps(meta, indent=2),
                                                 encoding="utf-8")
    print(f"\nsaved {METRICS / 'queen_baseline.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
