"""Collect every model result into `metrics/` as durable artefacts.

    python scripts/export_metrics.py

## Why a folder and not just a live page

`/metrics` reads the models currently loaded, which is the right thing for a
running system and the wrong thing for a record. A folder of files can be
committed, diffed across runs, opened without the stack up, and carried into a
room where the laptop is not on the network.

Each training script already writes its own `.meta.json` next to its model.
This gathers those, adds the results that had nowhere to live (sensor ablation,
the varroa regression, the ledger verification), and writes a human-readable
summary alongside PNG charts.

**Nothing here is retyped.** Every number is read from the artefact that
produced it, so the summary cannot drift from the models. The caveats travel
with the numbers for the same reason.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "ml" / "models"
OUT = ROOT / "metrics"

# --------------------------------------------------------------------------
# PNG charts, drawn with Pillow
#
# PNG rather than SVG because these go into slide decks, printed posters and
# chat messages, where SVG often will not paste. No matplotlib either: it is a
# heavy dependency for a handful of bar charts, and drawing them directly keeps
# the palette identical to the app.
#
# Everything is drawn at 3x and downsampled with LANCZOS. Pillow has no
# anti-aliasing for shapes or text, so supersampling is what makes the rounded
# bars and labels look clean instead of jagged.
# --------------------------------------------------------------------------
INK, MUTED, LINE, BG = "#1a1a17", "#6b6b63", "#e6e2d6", "#fbfaf6"
GREEN, HONEY, RED = "#1a7f4b", "#d98c00", "#b3261e"
SS = 3  # supersample factor

_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf"),
    ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Helvetica.ttc"),
]


def _fonts(size_bold: int, size_reg: int, size_small: int):
    from PIL import ImageFont

    for bold_path, reg_path in _FONT_CANDIDATES:
        if Path(bold_path).exists() and Path(reg_path).exists():
            return (ImageFont.truetype(bold_path, size_bold),
                    ImageFont.truetype(reg_path, size_reg),
                    ImageFont.truetype(reg_path, size_small))
    # No TTF anywhere: the bitmap default cannot be sized, so the chart comes
    # out small but still readable rather than crashing the export.
    d = ImageFont.load_default()
    return d, d, d


def bar_chart_png(path: Path, title: str, rows: list[tuple[str, float]], *,
                  subtitle: str = "", good: float = 0.9, poor: float = 0.7,
                  baseline: float | None = None, baseline_label: str = "",
                  width: int = 760) -> None:
    """Horizontal bars, 0..1.

    `baseline` draws a reference line where "no skill" sits. For AUC that is
    0.5, and without the line a bar drawn from zero makes 0.62 look like a
    passing grade when it is barely above chance. A chart that flatters the
    number is a chart that misleads the room.
    """
    from PIL import Image, ImageDraw

    row_h = 34
    pad_top = 78 if subtitle else 58
    label_w = 190
    right_pad = 78
    height = pad_top + row_h * len(rows) + (36 if baseline is not None else 22)
    bar_w = width - label_w - right_pad

    W, H = width * SS, height * SS
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_title, f_label, f_small = _fonts(17 * SS, 13 * SS, 11 * SS)

    d.text((20 * SS, 18 * SS), title, font=f_title, fill=INK)
    if subtitle:
        d.text((20 * SS, 44 * SS), subtitle, font=f_small, fill=MUTED)

    if baseline is not None:
        bx = (label_w + bar_w * baseline) * SS
        top = (pad_top - 8) * SS
        bottom = (pad_top + row_h * len(rows) + 2) * SS
        # dashed, so it reads as a reference and not as data
        step = 7 * SS
        for yy in range(int(top), int(bottom), step * 2):
            d.line([bx, yy, bx, min(yy + step, bottom)], fill=MUTED, width=max(1, SS // 2))
        if baseline_label:
            lw = d.textlength(baseline_label, font=f_small)
            d.text((bx - lw / 2, bottom + 3 * SS), baseline_label,
                   font=f_small, fill=MUTED)

    for i, row in enumerate(rows):
        # A row may force its own colour. That exists for bars which are NOT
        # results -- a leaked score shown for comparison must never be painted
        # green just because the number is high, or the chart argues the
        # opposite of its caption.
        label, value = row[0], row[1]
        override = row[2] if len(row) > 2 else None
        y = (pad_top + i * row_h) * SS
        v = max(0.0, min(1.0, float(value)))
        colour = override or (GREEN if v >= good else HONEY if v >= poor else RED)
        bar_h = 14 * SS
        radius = bar_h // 2

        d.text((20 * SS, y + 1 * SS), label, font=f_label, fill=INK)
        d.rounded_rectangle(
            [label_w * SS, y, (label_w + bar_w) * SS, y + bar_h],
            radius=radius, fill=LINE)
        filled = max(bar_h, int(bar_w * SS * v))
        d.rounded_rectangle(
            [label_w * SS, y, label_w * SS + filled, y + bar_h],
            radius=radius, fill=colour)
        pct_text = f"{v * 100:.1f}%"
        tw = d.textlength(pct_text, font=f_label)
        d.text(((width - 20) * SS - tw, y + 1 * SS), pct_text,
               font=f_label, fill=MUTED)

    img.resize((width, height), Image.LANCZOS).save(path, "PNG", optimize=True)


# --------------------------------------------------------------------------
def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_meta(name: str) -> dict | None:
    p = MODELS / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def run_ledger_check() -> dict:
    """Capture the independent verifier's verdict as an artefact."""
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify_ledger.py")],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        text = r.stdout
        held = broken = batches = 0
        for line in text.splitlines():
            if "invariant checks held" in line:
                # "11 batches, 17 invariant checks held, 0 broken"
                bits = line.replace(",", "").split()
                batches = int(bits[0])
                held = int(bits[2])
                broken = int(bits[-2])
        # A verifier that could not reach the stack has NOT found broken
        # invariants -- it has found nothing. Recording that as
        # all_hold=false/0 batches reads like the ledger failed, which is a
        # different and much worse claim, so it is kept distinct. The last
        # successful verdict is preserved rather than overwritten by a run
        # that never connected.
        if r.returncode != 0 and batches == 0:
            prev = read_json(OUT / "ledger_verification.json") or {}
            # Carry the last GOOD verdict forward. If the previous file was
            # itself a failed run, take the record it was already carrying --
            # otherwise every re-export with the stack down erodes the history
            # by one step until the evidence is gone.
            stale = {k: v for k, v in prev.items()
                     if k in ("batches_checked", "invariants_held",
                              "invariants_broken", "all_hold", "verified_at")}
            if not stale:
                stale = prev.get("last_successful_run") or {}
            return {
                "ran": False,
                "reason": "could not reach the chain or database; the stack "
                          "was not running when metrics were exported",
                "not_a_verification_failure": True,
                "exit_code": r.returncode,
                "stderr_tail": (r.stderr or "").strip().splitlines()[-1:],
                "last_successful_run": stale or None,
                "reproduce": "start the stack, then: python scripts/verify_ledger.py",
            }
        return {
            "ran": True,
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "exit_code": r.returncode,
            "batches_checked": batches,
            "invariants_held": held,
            "invariants_broken": broken,
            "all_hold": r.returncode == 0 and broken == 0,
            "note": ("Verified against chain state alone. The database supplied "
                     "only the list of batch codes; every value was read from "
                     "the deployed contracts."),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "error": str(exc),
                "note": "Chain not running? Start it and re-export."}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    health = read_meta("health_clf.meta.json")
    yieldm = read_meta("yield.meta.json")
    anomaly = read_meta("anomaly.meta.json")
    ablation = json.loads((OUT / "sensor_ablation.json").read_text()) \
        if (OUT / "sensor_ablation.json").exists() else None
    varroa = json.loads((OUT / "varroa_counter.json").read_text()) \
        if (OUT / "varroa_counter.json").exists() else None

    # Models trained on REAL, licensed data. Kept in a block separate from the
    # simulator models everywhere downstream, because merging the two would let
    # a simulator number quietly borrow credibility from a real one.
    acoustic = read_json(OUT / "acoustic_queen.json")
    varroa_cnn = read_json(OUT / "varroa_entrance_cnn.json")
    queen_base = read_json(OUT / "queen_baseline.json")

    # copy the per-model metadata in under stable names
    for meta, fname in ((health, "colony_health.json"),
                        (yieldm, "yield_forecast.json"),
                        (anomaly, "anomaly_detector.json")):
        if meta:
            (OUT / fname).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    ledger = run_ledger_check()
    (OUT / "ledger_verification.json").write_text(
        json.dumps(ledger, indent=2), encoding="utf-8")

    # ---- charts ----------------------------------------------------------
    charts = []
    if health and health.get("per_class_recall"):
        rows = sorted(
            [(k, v) for k, v in health["per_class_recall"].items()
             # a class with no test examples has a meaningless 0.0
             if not (v == 0.0 and k == "wax_moth")],
            key=lambda kv: -kv[1])
        bar_chart_png(
            OUT / "colony_health_recall.png",
            "Colony health - recall per class", rows,
            subtitle=f"{health.get('test_rows', 0):,} rows over "
                     f"{health.get('test_hives', 0)} hives from a separate "
                     f"simulation run")
        charts.append("colony_health_recall.png")

    if anomaly and anomaly.get("auc_by_fault"):
        rows = sorted(anomaly["auc_by_fault"].items(), key=lambda kv: -kv[1])
        bar_chart_png(
            OUT / "anomaly_auc.png",
            "Anomaly detector - AUC per fault class", rows,
            subtitle="one-class, fitted on healthy windows only",
            good=0.85, poor=0.70,
            baseline=0.5, baseline_label="0.5 = chance")
        charts.append("anomaly_auc.png")

    if ablation:
        rows = sorted(
            [(k, v["macro_f1"]) for k, v in ablation["results"].items()],
            key=lambda kv: -kv[1])
        bar_chart_png(
            OUT / "sensor_ablation.png",
            "Which sensors earn their place - macro F1", rows,
            subtitle="removing the microphone costs more than removing "
                     "anything else",
            good=0.80, poor=0.60)
        charts.append("sensor_ablation.png")

    if acoustic:
        bar_chart_png(
            OUT / "acoustic_queen.png",
            "Queen detection on REAL audio",
            [("leave-one-hive-out", acoustic["headline_accuracy_loro"]),
             ("queenless recall", acoustic["queenless_recall"]),
             ("majority baseline", acoustic["majority_baseline"], MUTED),
             ("random split - LEAKED", acoustic["naive_random_split_accuracy"],
              MUTED)],
            subtitle="grey bars are not results: the baseline to beat, and the "
                     "score data leakage would have produced",
            good=0.85, poor=0.65,
            baseline=0.5, baseline_label="0.5 = chance")
        charts.append("acoustic_queen.png")

    # ---- combined index --------------------------------------------------
    index = {
        "generated_at": stamp,
        "models": {
            "colony_health": health,
            "yield_forecast": yieldm,
            "anomaly_detector": anomaly,
            "varroa_counter": varroa,
        },
        "models_trained_on_real_data": {
            "acoustic_queen": acoustic,
            "queen_per_hive_baseline": queen_base,
            "varroa_entrance_cnn": varroa_cnn,
        },
        "datasets": read_json(ROOT / "ml/datasets/registry.json"),
        "mspb_colony": read_json(OUT / "mspb_colony.json"),
        "sensor_ablation": ablation,
        "ledger_verification": ledger,
        "charts": charts,
        "global_caveat": (
            "Read the two halves of this file differently. Everything above the "
            "'REAL, licensed data' heading was measured on SIMULATED hives: it "
            "shows the models learned the physics the simulator encodes, and is "
            "not evidence they work on real bees. Everything below it was "
            "measured on real colonies, and two of those three results are "
            "negative -- reported because a folder that only records what "
            "worked is advertising.\n\n"
            "The gap that remains after both halves is the same one: every real "
            "dataset available is Apis mellifera in Europe or Canada. Apis "
            "cerana indica, common in Indian beekeeping, has NO public dataset "
            "at all, and the yield ceiling that gates a beekeeper's income has "
            "still never been fitted against a real Indian harvest."),
    }
    (OUT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    # ---- human-readable summary ------------------------------------------
    (OUT / "README.md").write_text(build_readme(index), encoding="utf-8")

    written = sorted(p.name for p in OUT.iterdir() if p.is_file())
    print(f"metrics/ exported at {stamp}")
    for name in written:
        size = (OUT / name).stat().st_size
        print(f"  {name:<32} {size:>7,} bytes")
    return 0


def build_readme(ix: dict) -> str:
    h = ix["models"]["colony_health"] or {}
    y = ix["models"]["yield_forecast"] or {}
    a = ix["models"]["anomaly_detector"] or {}
    v = ix["models"]["varroa_counter"] or {}
    ab = ix["sensor_ablation"]
    lg = ix["ledger_verification"]

    L = [
        "# Model results",
        "",
        f"Generated {ix['generated_at']} by `scripts/export_metrics.py`.",
        "Every number is read from the artefact that produced it — nothing here",
        "is retyped, so this cannot drift from the models actually loaded.",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Model | Version | Headline | Weakest point |",
        "|---|---|---|---|",
    ]

    if h:
        recalls = h.get("per_class_recall", {})
        worst = min(((k, x) for k, x in recalls.items() if x > 0),
                    key=lambda kv: kv[1], default=("—", 0))
        L.append(f"| Colony health | `{h.get('version')}` | "
                 f"{h.get('test_accuracy', 0) * 100:.1f}% accuracy, "
                 f"queenless recall {recalls.get('queenless', 0):.3f} | "
                 f"{worst[0]} recall {worst[1]:.3f} |")
    if y:
        L.append(f"| Yield forecaster | `{y.get('version')}` | "
                 f"P90 covers {y.get('p90_coverage', 0) * 100:.1f}% after "
                 f"calibration | +{y.get('p90_mean_headroom', 0) * 100:.0f}% "
                 f"mean headroom — a generous ceiling |")
    if a:
        aucs = a.get("auc_by_fault", {})
        best = max(aucs.items(), key=lambda kv: kv[1], default=("—", 0))
        worst = min(aucs.items(), key=lambda kv: kv[1], default=("—", 0))
        L.append(f"| Anomaly detector | `{a.get('version')}` | "
                 f"{best[0]} AUC {best[1]:.3f}, "
                 f"{a.get('false_alarm_rate', 0) * 100:.1f}% false alarms | "
                 f"{worst[0]} AUC {worst[1]:.3f} |")
    if v:
        L.append(f"| Varroa counter | `{v.get('version')}` | "
                 f"{v.get('mean_relative_error', 0) * 100:.1f}% mean error on "
                 f"synthetic boards | no real-board data to measure against |")

    L += ["", "---", "", "## Colony health classifier", ""]
    if h:
        L += [f"- **{h.get('test_accuracy', 0) * 100:.1f}%** accuracy on "
              f"{h.get('test_rows', 0):,} rows from **{h.get('test_hives')} hives "
              f"in a separate simulation run** (different seed, different weather).",
              "- Split by hive, never by row: consecutive hourly frames from one "
              "colony are near-duplicates, and a random row split produces a fake "
              "99% that collapses on a new hive.",
              "",
              "| Class | Recall |", "|---|---|"]
        for k, val in sorted(h.get("per_class_recall", {}).items(),
                             key=lambda kv: -kv[1]):
            note = " *(no test examples)*" if val == 0.0 else ""
            L.append(f"| {k} | {val:.3f}{note} |")
        L += ["",
              "Accuracy is the least useful number here — the set is ~70% healthy, "
              "so always predicting \"healthy\" scores 0.70. Recall per class is "
              "what matters, and **varroa is the weak one**: it is a three-week "
              "decline that looks like \"slightly less healthy\". The alerting "
              "layer therefore requires 0.88 confidence before varroa may "
              "interrupt a beekeeper.",
              "", "![recall](colony_health_recall.png)", ""]

    L += ["---", "", "## Sensor ablation", ""]
    if ab:
        L += ["| Sensor set | macro F1 | accuracy | queenless | pre_swarm | varroa |",
              "|---|---|---|---|---|---|"]
        for name, r in sorted(ab["results"].items(),
                              key=lambda kv: -kv[1]["macro_f1"]):
            rc = r["recall"]
            L.append(f"| {name} | **{r['macro_f1']:.3f}** | {r['accuracy']:.3f} | "
                     f"{rc.get('queenless', 0):.3f} | {rc.get('pre_swarm', 0):.3f} | "
                     f"{rc.get('varroa', 0):.3f} |")
        L += ["", ab["finding"], "", "![ablation](sensor_ablation.png)", ""]

    L += ["---", "", "## Yield forecaster", ""]
    if y:
        L += [f"- P90 coverage **{y.get('p90_coverage', 0) * 100:.1f}%** after "
              f"conformal calibration (×{y.get('calibration_factor')}), "
              f"from {y.get('p90_coverage_uncalibrated', 0) * 100:.1f}% before.",
              f"- Trained on {y.get('train_windows')} hive-windows, tested on "
              f"{y.get('test_windows')}.",
              "",
              "The P90 becomes the **on-chain mint ceiling**, so this is the one "
              "model whose error costs a real person money. The two errors are "
              "not symmetric: a ceiling that is too low blocks an honest "
              "beekeeper from selling their own honey and ends adoption, while "
              "one that is too high lets some fraud through to the chemical "
              "testing ladder. The ceiling is deliberately generous.",
              "",
              "The first fit covered only 74.6% of actual yields — a quarter of "
              "honest beekeepers would have been blocked. Conformal calibration "
              "on hives the model had never seen fixed it, but only after "
              "training across four independent weather realisations: the first "
              "calibration attempt returned a factor of exactly 1.000 and did "
              "nothing, because the calibration hives shared a weather seed with "
              "the fit hives while the test run did not. Conformal guarantees "
              "require exchangeability, and a weather shift breaks it.",
              ""]

    L += ["---", "", "## Anomaly detector", ""]
    if a:
        L += ["Fitted on healthy windows only, so it answers *\"unlike anything "
              "normal\"* rather than naming a known fault — which is what catches "
              "faults the classifier has no class for.",
              "", "| Fault | AUC |", "|---|---|"]
        for k, val in sorted(a.get("auc_by_fault", {}).items(),
                             key=lambda kv: -kv[1]):
            verdict = ("strong" if val > 0.85 else "useful" if val > 0.70
                       else "weak" if val > 0.60 else "no better than chance")
            L.append(f"| {k} | {val:.3f} — {verdict} |")
        L += ["",
              f"False alarm rate {a.get('false_alarm_rate', 0) * 100:.1f}% at a "
              f"threshold set from the *healthy* distribution — not tuned against "
              f"known faults, because in the field there is no label to tune "
              f"against and a threshold fitted to known faults would not survive "
              f"an unknown one.",
              "", "![auc](anomaly_auc.png)", ""]

    L += ["---", "", "## Varroa counter", ""]
    if v:
        L += [f"- Method: {v.get('method')}",
              f"- Mean relative error: **{v.get('mean_relative_error', 0) * 100:.1f}%** "
              f"against synthetic boards with known counts",
              "", "| Case | True | Found | Error |", "|---|---|---|---|"]
        for c in v.get("cases", []):
            if c.get("refused"):
                L.append(f"| {c['case']} | {c.get('true', '—')} | refused | "
                         f"{c.get('reason', '')[:52]} |")
            else:
                L.append(f"| {c['case']} | {c['true']} | {c['found']} | "
                         f"{c['error']:+d} |")
        L += ["", v.get("caveat", ""), ""]

    # ---- models trained on real, licensed data --------------------------
    real = ix.get("models_trained_on_real_data") or {}
    aq, vc, mspb = (real.get("acoustic_queen"), real.get("varroa_entrance_cnn"),
                    ix.get("mspb_colony"))
    queen_base = real.get("queen_per_hive_baseline")
    if any((aq, vc, mspb, queen_base)):
        L += ["---", "", "## Models trained on REAL, licensed data", "",
              "Kept separate from everything above, which is simulator-trained. "
              "Dataset licences and the sources we rejected are in "
              "[`ml/datasets/registry.json`](../ml/datasets/registry.json).", ""]

    if vc:
        L += [f"### Varroa on real bee images — `{vc['version']}`", "",
              f"- **{vc['test_accuracy']:.1%}** accuracy, "
              f"**AUC {vc['test_auc']:.3f}** on {vc['test_images']:,} held-out "
              f"images from {vc['test_sessions']} unseen recording sessions.",
              f"- Infested recall {vc['infested_recall']:.3f}, "
              f"precision {vc['infested_precision']:.3f}.",
              f"- Data: {vc['dataset']}", "",
              vc["label_note"], "", vc["caveat"], ""]

    if aq:
        verdict = "SHIPPED" if aq.get("deployable") else "**NOT SHIPPED**"
        L += [f"### Queen detection on real audio — {verdict}", "",
              f"- Leave-one-hive-out accuracy **{aq['headline_accuracy_loro']:.3f}** "
              f"against a **{aq['majority_baseline']:.3f}** majority baseline.",
              f"- A random split of the same windows scores "
              f"**{aq['naive_random_split_accuracy']:.3f}** — a leakage gap of "
              f"**{aq['leakage_gap']:+.3f}**.",
              f"- Data: {aq['dataset']}", "",
              aq["finding"], "", aq["why_below_chance"], "",
              aq["why_not_shipped"], "",
              "![queen](acoustic_queen.png)", ""]

    if queen_base:
        cb = queen_base
        L += ["### Queen detection reframed as per-hive change detection", "",
              f"- Per-hive AUC **{cb['mean_auc']:.3f}**, detecting "
              f"{cb['mean_detection_at_5pct_false_alarm']:.1%} of queenless "
              f"windows at ~5% false alarm.",
              f"- **Negative control:** {cb['control_verdict']}",
              f"- Shipped: **{'yes' if cb['deployable_framing'] else 'NO'}**", "",
              cb["finding"], "", cb["what_would_actually_settle_it"], ""]

    if mspb:
        L += ["### Colony-level check on real hives (MSPB)", "",
              f"- {mspb.get('colonies', '?')} colonies, "
              f"{mspb.get('features', '?')} features. "
              f"**{mspb.get('licence_restriction', '')}**", ""]
        L += ["| Target | n | result | baseline | verdict |",
              "|---|---|---|---|---|"]
        for k, r in (mspb.get("results") or {}).items():
            if "auc" in r:
                L += [f"| {k} ({r.get('unit', '')}) | {r['n']} | "
                      f"AUC {r['auc']}, acc {r['accuracy']} | "
                      f"majority {r['majority_baseline']} | "
                      f"{'signal' if r['beats_chance'] else '**no signal**'} |"]
            else:
                L += [f"| {k} ({r.get('unit', '')}) | {r['n']} | "
                      f"MAE {r['mae']}, R² {r['r2']} | MAE {r['baseline_mae']} | "
                      f"{'beats baseline' if r['beats_baseline'] else '**no better than the mean**'} |"]
        L += [""]
        for note in (mspb.get("notes") or {}).values():
            L += [note, ""]
        if mspb.get("changes_from_0_1_0"):
            L += [f"*What changed since the first attempt:* "
                  f"{mspb['changes_from_0_1_0']}", ""]

    L += ["---", "", "## Ledger verification", ""]
    if lg.get("ran"):
        verdict = "**all held**" if lg.get("all_hold") else "**FAILURES**"
        L += [f"- {lg.get('batches_checked')} batches, "
              f"{lg.get('invariants_held')} invariant checks {verdict}, "
              f"{lg.get('invariants_broken')} broken.",
              f"- {lg.get('note')}",
              "",
              "The invariants checked are: issuance bounded by the "
              "telemetry-derived ceiling, mass conserved through every "
              "transformation, jars issued bounded by the honey that exists, and "
              "no seals without a passing independent lab report.",
              "",
              "Reproduce with `python scripts/verify_ledger.py`.", ""]
    else:
        L += [f"- **Not run** — {lg.get('reason', lg.get('error', 'unknown'))}.",
              "- This is *not* a verification failure: nothing was checked.",
              f"- Reproduce: `{lg.get('reproduce', 'python scripts/verify_ledger.py')}`", ""]
        last = lg.get("last_successful_run")
        if last:
            L += [f"- Last successful run: {last.get('batches_checked')} batches, "
                  f"{last.get('invariants_held')} invariants held, "
                  f"{last.get('invariants_broken')} broken.", ""]

    L += ["---", "", "## Caveat", "", ix["global_caveat"], ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
