"""Varroa-infestation classifier trained on REAL bee images.

    python ml/train_varroa_cnn.py

Data: VarroaDataset (Schurischuster & Kampel, 2020), CC BY 4.0, fetched by
`ml/fetch_datasets.py`. ~13,500 manually annotated images of individual bees
recorded at a hive entrance under controlled lighting.

## This does NOT replace the sticky-board counter

Different sensor, different question. `apps/api/aurabee_api/varroa.py` counts
mites that have *fallen onto a board* -- the standard beekeeper survey. This
model looks at a *bee* and asks whether a mite is riding on it, which is what an
entrance camera would see. They are complementary; neither is a substitute for
the other, and reporting this model's score as an improvement to the board
counter would be dishonest.

## Two things about the labels that the dataset README does not say

1. **There are two positive label ids, not one.** gt.csv uses `1` (3,083 rows)
   and `3` (864 rows); the README documents only 0 and 1. Both carry mite
   bounding boxes and both have zero boxes for label 0. Merging them reproduces
   the published count of 3,947 infected against 9,562 healthy exactly, which is
   what confirms the reading. Filtering on `label == 1` would silently drop 22%
   of the positives.

2. **`bee_id` is reused across recording sessions**, so it looks like 2,115
   bees appear in both train and test. They do not -- the *sessions* are
   disjoint, which is the split that matters. We verified that before training.

The provided train/val/test split is session-disjoint, so we keep it rather than
reshuffling. Note the test set is only **2 recording sessions**: a good score
means the model survived two unseen sessions, not two hundred.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "ml/datasets/raw/varroa_schurischuster"
IMG_ROOT = RAW / "extracted"
MODEL = ROOT / "ml/models/varroa_entrance.pt"
METRICS = ROOT / "metrics"

VERSION = "varroa-entrance-cnn-0.1.0"
H, W = 112, 192          # from 160x280, aspect preserved
POSITIVE_LABELS = {1, 3}


def load_index() -> list[dict]:
    gt = RAW / "gt.csv"
    if not gt.exists():
        raise SystemExit(f"missing {gt}. Run: python ml/fetch_datasets.py "
                         "varroa_schurischuster")
    rows = []
    for line in gt.read_text(encoding="utf-8", errors="ignore").splitlines():
        p = line.split()
        if len(p) < 2:
            continue
        path, lab = p[0], int(p[1])
        m = re.match(r"(train|val|test)/videos/([^/]+)/", path)
        if not m:
            continue
        rows.append({"path": path, "split": m.group(1), "session": m.group(2),
                     "y": 1 if lab in POSITIVE_LABELS else 0, "raw_label": lab})
    return rows


def ensure_extracted() -> None:
    IMG_ROOT.mkdir(parents=True, exist_ok=True)
    for z in ("train.zip", "val.zip", "test.zip"):
        src, marker = RAW / z, IMG_ROOT / f".{z}.done"
        if marker.exists():
            continue
        if not src.exists():
            raise SystemExit(f"missing {src}; run ml/fetch_datasets.py")
        print(f"  extracting {z} ...")
        with zipfile.ZipFile(src) as zf:
            zf.extractall(IMG_ROOT)
        marker.write_text("ok")


def resolve(path: str) -> Path:
    """The zips may or may not carry the split directory as a top level."""
    direct = IMG_ROOT / path
    if direct.exists():
        return direct
    return IMG_ROOT / Path(path).relative_to(Path(path).parts[0])


def build_arrays(rows: list[dict], split: str) -> tuple[np.ndarray, np.ndarray]:
    from PIL import Image
    sub = [r for r in rows if r["split"] == split]
    X = np.zeros((len(sub), H, W, 3), dtype=np.uint8)
    y = np.zeros(len(sub), dtype=np.int64)
    kept = 0
    for r in sub:
        fp = resolve(r["path"])
        if not fp.exists():
            continue
        try:
            im = Image.open(fp).convert("RGB").resize((W, H), Image.BILINEAR)
        except Exception:
            continue
        X[kept] = np.asarray(im)
        y[kept] = r["y"]
        kept += 1
    print(f"  {split:5s}: {kept}/{len(sub)} images loaded, "
          f"{int(y[:kept].sum())} infested")
    return X[:kept], y[:kept]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=14)
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()

    import torch
    import torch.nn as nn
    from sklearn.metrics import classification_report, roc_auc_score

    torch.manual_seed(0)
    np.random.seed(0)

    rows = load_index()
    print(f"{len(rows)} annotations   raw label mix: "
          f"{dict(Counter(r['raw_label'] for r in rows))}")
    print(f"merged positives ({sorted(POSITIVE_LABELS)}): "
          f"{sum(r['y'] for r in rows)} infested / "
          f"{sum(1 - r['y'] for r in rows)} healthy")
    for s in ("train", "val", "test"):
        ss = {r["session"] for r in rows if r["split"] == s}
        print(f"  {s:5s} sessions={len(ss)}")

    ensure_extracted()
    print("\nloading images")
    Xtr, ytr = build_arrays(rows, "train")
    Xva, yva = build_arrays(rows, "val")
    Xte, yte = build_arrays(rows, "test")
    if min(len(Xtr), len(Xva), len(Xte)) == 0:
        raise SystemExit("no images loaded; check extraction paths")

    # Channel statistics from a subsample: computing them over every pixel of
    # every image materialises a float64 copy of the whole set.
    sample = Xtr[np.random.default_rng(0).choice(
        len(Xtr), size=min(1500, len(Xtr)), replace=False)]
    mean = sample.reshape(-1, 3).mean(0) / 255.0
    std = (sample.reshape(-1, 3).std(0) / 255.0) + 1e-6
    del sample

    # Images stay uint8 in memory and become float32 one batch at a time.
    # Converting all 13,509 up front is ~3.5 GB resident and gets the process
    # OOM-killed on this machine.
    mean_t = torch.tensor(mean, dtype=torch.float32)
    std_t = torch.tensor(std, dtype=torch.float32)

    Xtr_u8 = torch.from_numpy(Xtr)
    Xva_u8 = torch.from_numpy(Xva)
    Xte_u8 = torch.from_numpy(Xte)
    ytr_t = torch.from_numpy(ytr)

    def batch_to_float(u8: "torch.Tensor") -> "torch.Tensor":
        t = u8.float().div_(255.0).sub_(mean_t).div_(std_t)
        return t.permute(0, 3, 1, 2).contiguous()

    def block(i, o):
        return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o),
                             nn.ReLU(inplace=True), nn.MaxPool2d(2))

    net = nn.Sequential(
        block(3, 32), block(32, 64), block(64, 128), block(128, 128),
        nn.AdaptiveAvgPool2d(1), nn.Flatten(),
        nn.Dropout(0.3), nn.Linear(128, 2))

    # 2.4:1 imbalance -- weight the loss rather than resample, so every real
    # image is seen exactly once per epoch
    w = torch.tensor([1.0, float((ytr == 0).sum() / max(1, (ytr == 1).sum()))])
    lossf = nn.CrossEntropyLoss(weight=w)
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)

    def evaluate(Xu8, yt):
        net.eval()
        outs = []
        with torch.no_grad():
            for i in range(0, len(Xu8), 128):
                xb = batch_to_float(Xu8[i:i + 128])
                outs.append(torch.softmax(net(xb), 1)[:, 1])
        p = torch.cat(outs).numpy()
        return p, ((p > 0.5).astype(int) == yt).mean()

    best_va, best_state = -1.0, None
    n = len(Xtr_u8)
    print(f"\ntraining {args.epochs} epochs on {n} images (CPU)")
    for ep in range(1, args.epochs + 1):
        net.train()
        perm = torch.randperm(n)
        tot = 0.0
        t0 = time.time()
        for i in range(0, n, args.batch):
            idx = perm[i:i + args.batch]
            xb, yb = batch_to_float(Xtr_u8[idx]), ytr_t[idx]
            if np.random.rand() < 0.5:              # horizontal flip
                xb = torch.flip(xb, dims=[3])
            opt.zero_grad()
            loss = lossf(net(xb), yb)
            loss.backward()
            opt.step()
            tot += float(loss.detach()) * len(idx)
        sched.step()
        _, va = evaluate(Xva_u8, yva)
        print(f"  epoch {ep:2d}  loss {tot / n:.4f}  val acc {va:.4f}  "
              f"({time.time() - t0:.0f}s)")
        if va > best_va:
            best_va = va
            best_state = {k: v.clone() for k, v in net.state_dict().items()}

    net.load_state_dict(best_state)
    prob, acc = evaluate(Xte_u8, yte)
    pred = (prob > 0.5).astype(int)
    rep = classification_report(yte, pred, output_dict=True, zero_division=0,
                                target_names=["healthy", "infested"])
    auc = float(roc_auc_score(yte, prob))

    print(f"\nTEST  accuracy {acc:.4f}   AUC {auc:.4f}")
    print(f"      infested recall    {rep['infested']['recall']:.4f}")
    print(f"      infested precision {rep['infested']['precision']:.4f}")

    MODEL.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": net.state_dict(), "mean": mean.tolist(),
                "std": std.tolist(), "hw": [H, W], "version": VERSION}, MODEL)

    meta = {
        "version": VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": "VarroaDataset (Schurischuster & Kampel 2020), CC BY 4.0",
        "dataset_doi": "10.5281/zenodo.4085043",
        "attribution": "Schurischuster, S. & Kampel, M. (2020). VarroaDataset. "
                       "Zenodo. https://doi.org/10.5281/zenodo.4085043 (CC BY 4.0)",
        "task": "binary infested/healthy for one bee imaged at a hive entrance",
        "architecture": "4-block CNN, 112x192 RGB, ~0.3M params, CPU-trained",
        "train_images": int(len(Xtr)), "val_images": int(len(Xva)),
        "test_images": int(len(Xte)),
        "test_sessions": len({r["session"] for r in rows if r["split"] == "test"}),
        "test_accuracy": round(float(acc), 4),
        "test_auc": round(auc, 4),
        "infested_recall": round(float(rep["infested"]["recall"]), 4),
        "infested_precision": round(float(rep["infested"]["precision"]), 4),
        "healthy_recall": round(float(rep["healthy"]["recall"]), 4),
        "macro_f1": round(float(rep["macro avg"]["f1-score"]), 4),
        "label_note": (
            "gt.csv uses TWO positive ids, 1 (3,083) and 3 (864); the dataset "
            "README documents only 0 and 1. Merging them reproduces the "
            "published 3,947 infected / 9,562 healthy exactly. Training on "
            "`label == 1` alone would discard 22% of the positives."),
        "split_note": (
            "The dataset's own train/val/test split is used because it is "
            "session-disjoint, which we verified. bee_id appears to overlap "
            "train and test but is only unique within a session, so it is not "
            "leakage. The test set is 2 recording sessions."),
        "caveat": (
            "REAL images, Apis mellifera, controlled lighting at a hive "
            "entrance in Europe. This is an ENTRANCE-CAMERA model and does NOT "
            "replace the sticky-board counter in apps/api/aurabee_api/varroa.py "
            "-- different sensor, different question. It has never seen an "
            "Indian apiary or a phone photo."),
    }
    METRICS.mkdir(exist_ok=True)
    (METRICS / "varroa_entrance_cnn.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    MODEL.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2),
                                               encoding="utf-8")
    print(f"saved {MODEL}\nsaved {METRICS / 'varroa_entrance_cnn.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
