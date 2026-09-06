"""Evaluate the varroa counter against boards with known mite counts.

    python ml/eval_varroa.py

## What this does and does not measure

Synthetic sticky boards are generated with a *known* number of mites plus
realistic confounders -- wax flakes, fibres, bee legs, uneven lighting, camera
noise -- and the counter is scored against ground truth.

**This validates the algorithm's mechanics, not its field accuracy.** It shows
the size and shape filters reject the debris they were designed to reject and
that the count survives uneven illumination. It says nothing about whether real
mites on a real board under an Indian sun look like these ellipses. Reporting
this number as field accuracy would be dishonest.

Its real job is regression testing: if someone changes a threshold, this says
whether they broke it.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from aurabee_api.varroa import count_mites  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "metrics"

BOARD_W_MM = 430.0     # a Langstroth bottom board is about 43 cm wide
BOARD_H_MM = 350.0


def make_board(n_mites: int, n_debris: int, px_per_mm: float, seed: int,
               lighting: float = 0.25, blur: float = 0.6) -> tuple[bytes, int]:
    rng = np.random.default_rng(seed)
    w = int(BOARD_W_MM * px_per_mm)
    h = int(BOARD_H_MM * px_per_mm)
    img = Image.new("L", (w, h), 236)
    d = ImageDraw.Draw(img)

    def ellipse(cx, cy, a_mm, b_mm, angle, fill):
        a, b = a_mm * px_per_mm / 2, b_mm * px_per_mm / 2
        pts = []
        for t in np.linspace(0, 2 * np.pi, 24):
            x = a * np.cos(t); y = b * np.sin(t)
            pts.append((cx + x * np.cos(angle) - y * np.sin(angle),
                        cy + x * np.sin(angle) + y * np.cos(angle)))
        d.polygon(pts, fill=fill)

    # mites: reddish-brown, ~1.1 x 1.6 mm, wider than long
    for _ in range(n_mites):
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        ellipse(cx, cy, rng.normal(1.6, 0.12), rng.normal(1.1, 0.09),
                rng.uniform(0, np.pi), int(rng.integers(55, 95)))

    # confounders the filters are supposed to reject
    for _ in range(n_debris):
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        kind = rng.integers(0, 3)
        if kind == 0:      # wax flake: too big
            ellipse(cx, cy, rng.uniform(4, 9), rng.uniform(3, 7),
                    rng.uniform(0, np.pi), int(rng.integers(120, 170)))
        elif kind == 1:    # fibre or bee leg: too elongated
            ang = rng.uniform(0, np.pi)
            ln = rng.uniform(5, 14) * px_per_mm
            d.line([(cx, cy),
                    (cx + ln * np.cos(ang), cy + ln * np.sin(ang))],
                   fill=int(rng.integers(70, 120)),
                   width=max(1, int(0.25 * px_per_mm)))
        else:              # pollen speck / dust: too small
            ellipse(cx, cy, rng.uniform(0.2, 0.5), rng.uniform(0.2, 0.45),
                    0, int(rng.integers(90, 140)))

    arr = np.asarray(img, dtype=np.float32)
    # uneven lighting: a phone photo in a field is brighter on one side
    yy, xx = np.mgrid[0:h, 0:w]
    grad = 1.0 - lighting * (xx / w) - 0.5 * lighting * (yy / h)
    arr = arr * grad
    arr += rng.normal(0, 3.5, arr.shape)          # sensor noise
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)      # phones send JPEG
    return buf.getvalue(), n_mites


def main() -> int:
    cases = [
        ("clean board, no mites",        0,   0,  8.0),
        ("light infestation",            8,  40,  8.0),
        ("moderate",                    25,  60,  8.0),
        ("heavy",                       60,  90,  8.0),
        ("heavy + lots of debris",      60, 220,  8.0),
        ("low resolution photo",        25,  60,  4.5),
        ("high resolution photo",       25,  60, 12.0),
    ]
    print(f"{'case':<26}{'true':>6}{'found':>7}{'err':>7}{'conf':>7}"
          f"{'  rejected'}")
    print("-" * 78)

    errs, records = [], []
    for name, n_mites, n_debris, ppm in cases:
        data, truth = make_board(n_mites, n_debris, ppm, seed=hash(name) % 9999)
        r = count_mites(data, BOARD_W_MM, BOARD_H_MM)
        if not r.usable:
            records.append({"case": name, "true": truth, "refused": True,
                            "reason": r.advice_en})
            print(f"{name:<26}{truth:>6}{'--':>7}{'refused':>7}"
                  f"{r.confidence:>7.2f}   {r.advice_en[:34]}")
            continue
        err = r.count - truth
        errs.append(abs(err) / max(1, truth))
        records.append({"case": name, "true": truth, "found": r.count,
                        "error": err, "confidence": round(r.confidence, 3),
                        "px_per_mm": round(r.px_per_mm, 2),
                        "rejected": r.rejected})
        rej = ", ".join(f"{k}={v}" for k, v in sorted(r.rejected.items()))
        print(f"{name:<26}{truth:>6}{r.count:>7}{err:>+7}{r.confidence:>7.2f}"
              f"   {rej[:38]}")

    if errs:
        print(f"\nmean relative error across countable boards: "
              f"{np.mean(errs) * 100:.1f}%")

    # explicit refusal behaviour
    print("\nrefusal cases:")
    tiny, _ = make_board(20, 30, 2.0, seed=1)     # way too far away
    r = count_mites(tiny, BOARD_W_MM)
    print(f"  distant photo   -> usable={r.usable}  {r.advice_en[:52]}")
    for label, b in (("mildly blurred", 3.0), ("very blurred", 14.0)):
        img, _ = make_board(20, 30, 8.0, seed=2, blur=b)
        r = count_mites(img, BOARD_W_MM)
        print(f"  {label:<15} -> usable={str(r.usable):<5} count={r.count:<3} "
              f"{r.advice_en[:46]}")
        records.append({"case": label, "refused": not r.usable,
                        "reason": r.advice_en})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "varroa_counter.json").write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": "varroa-cv-0.1.0",
        "method": "classical CV: adaptive threshold + size/shape/solidity filters",
        "mean_relative_error": round(float(np.mean(errs)), 4) if errs else None,
        "cases": records,
        "caveat": (
            "Measured against SYNTHETIC boards with known counts. This "
            "validates the algorithm's mechanics and serves as a regression "
            "test; it is NOT field accuracy. There is no annotated Indian "
            "sticky-board dataset to measure that against yet."),
    }, indent=2), encoding="utf-8")
    print()
    print(f"saved {OUT / 'varroa_counter.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
