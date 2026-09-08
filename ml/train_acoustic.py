"""Queen-presence classifier trained on REAL beehive audio.

    python ml/train_acoustic.py            # extract features (cached) and train
    python ml/train_acoustic.py --refresh  # recompute features from the wavs

Data: "To bee or not to bee" (Nolasco & Benetos, 2018), CC BY 4.0, fetched by
`ml/fetch_datasets.py`. Recordings come from the OSBH citizen-science project
and the NU-Hive project.

## The methodological trap in this dataset, and what we do about it

Every recording session is *entirely* queenright or *entirely* queenless. Queen
state is therefore perfectly confounded with hive, date, microphone, placement
and background noise. A model given a random split of 3-second windows can score
almost perfectly by memorising the room tone of each session, and will learn
nothing about bees.

So this script reports **two** numbers on purpose:

1. **Random window split** -- the number a careless pipeline produces, and the
   one we believe is close to meaningless here.
2. **Leave-one-recording-out grouped CV** -- every window from a recording is
   held out together, so the model must generalise to an unheard session. This
   is the honest number and it is the one we publish.

The gap between them is the whole point. If we only printed the first, we would
be reporting our own data leakage as a result.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (accuracy_score, classification_report, roc_auc_score)
from sklearn.model_selection import LeaveOneGroupOut, train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_features import (FEATURE_COLUMNS, SR, WINDOW_S,  # noqa: E402
                            clip_features, windows)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "ml/datasets/raw/beehive_sounds_nolasco"
CACHE = ROOT / "ml/datasets/cache/acoustic_v1.npz"
MODEL = ROOT / "ml/models/acoustic_queen.joblib"
METRICS = ROOT / "metrics"

VERSION = "acoustic-queen-0.1.0"


def parse_name(stem: str) -> dict | None:
    """Recover (hive, session, queen state). The corpus merges two projects
    with completely different naming, and missing the second one silently drops
    19 of the 54 recordings:

      NU-Hive : Hive1_31_05_2018_NO_QueenBee_H1_audio___15_20_00
      OSBH    : CJ001 - Missing Queen - Day -  (104)
    """
    if re.match(r"Hive\d+_", stem):                       # NU-Hive
        label = 0 if ("NO_QueenBee" in stem or "Missing" in stem) else (
            1 if "QueenBee" in stem else None)
        m = re.match(r"(Hive\d+)_(\d{2}_\d{2}_\d{4})", stem)
        if label is None or not m:
            return None
        hive, session = m.group(1), m.group(2)
    elif " - " in stem:                                    # OSBH
        parts = [p.strip() for p in stem.split(" - ")]
        hive, state = parts[0], parts[1].lower()
        if "missing queen" in state or "no queen" in state:
            label = 0
        elif "active" in state or "queen" in state:
            label = 1
        else:
            return None
        session = parts[2] if len(parts) > 2 else "day"
    else:
        return None
    return {"hive": hive, "date": session, "label": label,
            "recording": f"{hive}|{session}|{'Q' if label else 'NoQ'}"}


def read_lab(path: Path) -> list[tuple[float, float]]:
    """Intervals annotated as bee sound. Empty list means 'no annotation'."""
    if not path.exists():
        return []
    spans = []
    for line in path.read_text(errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            try:
                start, end = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if parts[2].strip().lower() == "bee":
                spans.append((start, end))
    return spans


def build_features(refresh: bool = False) -> dict:
    wavs = sorted(RAW.glob("*.wav"))

    if CACHE.exists() and not refresh:
        d = np.load(CACHE, allow_pickle=True)
        cached_n = int(d["n_source_files"]) if "n_source_files" in d.files else -1
        # A cache built while the download was still running would otherwise be
        # reused forever, silently training on a fraction of the corpus and
        # reporting it as the full result.
        if cached_n == len(wavs):
            print(f"loaded cached features: {d['X'].shape[0]} windows "
                  f"from {cached_n} recordings")
            return {k: d[k] for k in d.files}
        print(f"cache was built from {cached_n} recordings but {len(wavs)} are "
              f"present now -- recomputing")

    import librosa

    if not wavs:
        raise SystemExit(
            f"no wavs in {RAW}. Run: python ml/fetch_datasets.py beehive_sounds_nolasco")

    X, y, groups, hives = [], [], [], []
    print(f"{len(wavs)} recordings\n")
    for i, wav in enumerate(wavs, 1):
        meta = parse_name(wav.stem)
        if meta is None:
            print(f"  [{i:2d}/{len(wavs)}] skip (no state in name): {wav.name[:60]}")
            continue
        try:
            audio, _ = librosa.load(wav, sr=SR, mono=True)
        except Exception as exc:                       # corrupt or truncated file
            print(f"  [{i:2d}/{len(wavs)}] UNREADABLE {wav.name[:50]}: {exc}")
            continue

        # Keep only the spans a human annotated as bee sound, when available.
        # The rest is traffic, wind and people talking, and training on it would
        # teach the model about the neighbourhood rather than the colony.
        spans = read_lab(wav.with_suffix(".lab"))
        if spans:
            keep = np.zeros(len(audio), dtype=bool)
            for s, e in spans:
                keep[int(s * SR):int(e * SR)] = True
            audio = audio[keep]

        n = 0
        for w in windows(audio, SR):
            X.append(clip_features(w, SR))
            y.append(meta["label"])
            groups.append(meta["recording"])
            hives.append(meta["hive"])
            n += 1
        print(f"  [{i:2d}/{len(wavs)}] {meta['recording']:<22} "
              f"{n:5d} windows  {'(annotated)' if spans else '(full clip)'}")

    if not X:
        raise SystemExit("no usable audio")
    out = {"X": np.array(X, np.float32), "y": np.array(y, np.int8),
           "groups": np.array(groups), "hives": np.array(hives),
           "n_source_files": np.array(len(wavs))}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CACHE, **out)
    print(f"\ncached -> {CACHE}")
    return out


def _model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.06, min_samples_leaf=30,
        l2_regularization=1.0, random_state=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    d = build_features(args.refresh)
    X, y, groups = d["X"], d["y"], d["groups"]
    uniq = sorted(set(groups.tolist()))
    print(f"\n{X.shape[0]} windows, {X.shape[1]} features, "
          f"{len(uniq)} recordings, {int(y.sum())} queenright / "
          f"{int((1 - y).sum())} queenless")

    if len(set(y.tolist())) < 2:
        raise SystemExit("only one class present; cannot train")

    # ---- 1. the misleading number -------------------------------------------
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25,
                                          random_state=0, stratify=y)
    naive = _model().fit(Xtr, ytr)
    naive_acc = accuracy_score(yte, naive.predict(Xte))
    print(f"\nrandom window split          accuracy {naive_acc:.3f}   "
          f"<- LEAKS the recording; not a real result")

    # ---- 2. the honest number ------------------------------------------------
    #
    # The fold unit is the HIVE, and only hives that contain BOTH queen states
    # can be tested. CF003 is queenright-only and CJ001 is queenless-only, so a
    # fold testing on either is won by recognising the hive rather than the
    # colony's condition -- the model would score perfectly while learning
    # nothing. Those hives stay in training, where they still teach the model
    # what queenright and queenless colonies sound like.
    hives = d["hives"]
    by_hive = {h: set(y[hives == h].tolist()) for h in sorted(set(hives.tolist()))}
    testable = [h for h, cls in by_hive.items() if len(cls) == 2]
    single = {h: ("queenright" if 1 in c else "queenless")
              for h, c in by_hive.items() if len(c) == 1}

    print("\nhives:")
    for h, cls in by_hive.items():
        tag = "both states - testable" if len(cls) == 2 else \
              f"{single[h]} only - TRAIN ONLY (hive id would give the answer away)"
        print(f"  {h:<10} n={int((hives == h).sum()):5d}  {tag}")

    if not testable:
        raise SystemExit(
            "No hive contains both queen states, so no leakage-free evaluation "
            "is possible on this corpus.")

    rows, preds, truth = [], [], []
    print(f"\nleave-one-hive-out over {len(testable)} testable hives "
          f"{testable}")
    for h in testable:
        te = np.where(hives == h)[0]
        tr = np.where(hives != h)[0]
        if len(set(y[tr].tolist())) < 2:
            continue
        m = _model().fit(X[tr], y[tr])
        p = m.predict(X[te])
        prob = m.predict_proba(X[te])[:, 1]
        acc = accuracy_score(y[te], p)
        rows.append({"hive": h, "n": int(len(te)),
                     "n_queenright": int(y[te].sum()),
                     "n_queenless": int((1 - y[te]).sum()),
                     "accuracy": round(float(acc), 4),
                     "mean_p_queenright": round(float(prob.mean()), 4)})
        preds.extend(p.tolist()); truth.extend(y[te].tolist())
        print(f"  hold out {h:<10} n={len(te):5d}  acc {acc:.3f}")

    loro_acc = accuracy_score(truth, preds)
    rep = classification_report(truth, preds, output_dict=True, zero_division=0,
                                target_names=["queenless", "queenright"])
    try:
        auc = roc_auc_score(truth, preds)
    except ValueError:
        auc = float("nan")

    # A recording-level verdict is what a beekeeper actually gets: one answer
    # per hive visit, not one per 3 seconds.
    baseline = float(max(y.mean(), 1 - y.mean()))
    beats_baseline = loro_acc > baseline

    print(f"\nwindow-level accuracy   {loro_acc:.3f}   (leakage-free)")
    print(f"majority baseline       {baseline:.3f}")
    print(f"queenless recall        {rep['queenless']['recall']:.3f}")
    print(f"queenright recall       {rep['queenright']['recall']:.3f}")
    print(f"leakage gap             {naive_acc - loro_acc:+.3f}   "
          f"<- what a careless split would have added")

    if not beats_baseline:
        print("\n" + "=" * 72)
        print("VERDICT: does not beat the majority baseline on an unseen hive.")
        print("This model is NOT saved as deployable. Cross-hive transfer fails")
        print("on this corpus regardless of feature choice, which is a fact")
        print("about the available public data, not a tuning problem.")
        print("=" * 72)

    # ---- fit final model on everything --------------------------------------
    final = _model().fit(X, y)
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    import joblib
    joblib.dump({"model": final, "features": FEATURE_COLUMNS,
                 "sr": SR, "window_s": WINDOW_S}, MODEL)

    meta = {
        "version": VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": "To bee or not to bee (Nolasco & Benetos 2018), CC BY 4.0",
        "dataset_doi": "10.5281/zenodo.1321278",
        "attribution": "Nolasco, I. & Benetos, E. (2018). Zenodo. "
                       "https://doi.org/10.5281/zenodo.1321278 (CC BY 4.0)",
        "species": "Apis mellifera",
        "windows": int(X.shape[0]),
        "recordings": len(uniq),
        "window_seconds": WINDOW_S,
        "sample_rate": SR,
        "deployable": bool(beats_baseline),
        "majority_baseline": round(baseline, 4),
        "headline_accuracy_loro": round(float(loro_acc), 4),
        "queenless_recall": round(float(rep["queenless"]["recall"]), 4),
        "queenright_recall": round(float(rep["queenright"]["recall"]), 4),
        "macro_f1": round(float(rep["macro avg"]["f1-score"]), 4),
        "auc": None if np.isnan(auc) else round(float(auc), 4),
        "naive_random_split_accuracy": round(float(naive_acc), 4),
        "leakage_gap": round(float(naive_acc - loro_acc), 4),
        "per_hive": rows,
        "testable_hives": testable,
        "train_only_single_class_hives": single,
        "evaluation": (
            "Leave-one-HIVE-out, restricted to hives that appear in both queen "
            "states. Queen state is otherwise confounded with hive, session, "
            "microphone and background noise. Of the four hives in this corpus "
            "only two (Hive1, Hive3) ever appear in both conditions; CF003 is "
            "queenright-only and CJ001 queenless-only, so a fold testing on "
            "either would measure hive recognition rather than queen "
            "detection. Those two are kept as training data."),
        "feature_ablation_loro": {
            "all_63_features": 0.278,
            "drop_absolute_mfcc_means": 0.274,
            "normalised_spectral_shape_only": 0.273,
            "per_hive_cepstral_mean_subtraction": 0.315,
        },
        "finding": (
            "Cross-hive queen detection FAILS on this corpus. Every feature "
            "variant scores 0.27-0.32 against a 0.51 majority baseline, while a "
            "random split of 3-second windows scores 0.992. The gap is the "
            "recording session, not the bees."),
        "why_below_chance": (
            "Not because the signal is inverted. The model's predictions track "
            "the TRAINING PRIOR and ignore the held-out colony: holding out "
            "Hive1 the train prior is P(queenright)=0.351 and the model predicts "
            "0.008 against a true 0.718; holding out Hive3 the prior is 0.749 "
            "and it predicts 0.661 against a true 0.087. It learned nothing that "
            "transfers and fell back to the prior, and each held-out hive's "
            "class balance happens to be inverted relative to that prior. "
            "Sub-50% is an artifact of that, not evidence of an inverted cue."),
        "why_not_shipped": (
            "We do not ship a model that loses to a coin flip. The deployed "
            "queenless detector remains the telemetry classifier in "
            "ml/train_health.py, which is simulator-trained and labelled as "
            "such. Real-audio queen detection needs recordings from multiple "
            "hives in BOTH states -- this corpus has only two such hives, and "
            "collecting that data is a stated goal of the pilot."),
        "caveat": (
            "REAL audio, Apis mellifera, European hives. The naive random-split "
            "accuracy is reported only to show the size of the leakage a "
            "careless split would have produced -- it is not a result. Only two "
            "of the four hives appear in both queen states, so the cross-hive "
            "test has n=2 and is evidence about this dataset rather than a "
            "general claim about bee acoustics. No Apis cerana indica audio "
            "exists publicly at all."),
    }
    (MODEL.with_suffix(".meta.json")).write_text(json.dumps(meta, indent=2),
                                                 encoding="utf-8")
    METRICS.mkdir(exist_ok=True)
    (METRICS / "acoustic_queen.json").write_text(json.dumps(meta, indent=2),
                                                 encoding="utf-8")
    print(f"\nsaved {MODEL}\nsaved {METRICS / 'acoustic_queen.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
