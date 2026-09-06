"""Colony health inference and alerting.

Loads the trained classifier and scores recent telemetry per hive, writing
`hive_events` rows that become the beekeeper's alerts.

## Alerting rules that exist because a false alarm is expensive

A beekeeper who walks 3 km to a hive that turns out to be fine stops trusting
the app, and an app they do not trust is worse than no app. So:

**Persistence, not instants.** A state must hold across most of a multi-hour
window before it raises an alert. Single-frame flips are noise, and the faults
worth catching (varroa, queenlessness) are multi-week trajectories anyway.

**Confidence floors, per class.** `varroa` recall is around 0.5 on our own test
set and its precision is 0.62; it therefore needs a much higher probability
before it speaks than `queenless`, which the model gets right 98% of the time.
Treating every class with one threshold would flood the beekeeper with the
class the model is worst at.

**Deduplication.** An open unacknowledged alert of the same type is not raised
again. The hive is already on the list.

**Plain language, in the beekeeper's language.** `ADVICE` is what actually
reaches a person; the class name never does.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
from psycopg.types.json import Jsonb

log = logging.getLogger("aurabee.health")

ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = ROOT / "ml" / "models"
sys.path.insert(0, str(ROOT / "ml"))

# Minimum probability before a class is allowed to raise an alert. Set from the
# model's own measured per-class precision -- the classes it is bad at have to
# be more certain before they interrupt someone's day.
MIN_CONFIDENCE = {
    "queenless": 0.70,
    "pre_swarm": 0.70,
    "robbing": 0.75,
    "starvation": 0.70,
    "varroa": 0.88,     # precision ~0.62; make it earn the interruption
    "wax_moth": 0.90,   # almost no training support; treat with suspicion
    "dead": 0.85,
}

# Fraction of the window that must agree before we call it.
MIN_PERSISTENCE = 0.6

# The schema's event_type CHECK is the authority. A model class that is not in
# this set is dropped with a warning rather than aborting the whole batch --
# adding a class to the model should never take the alerting job down.
ALLOWED_EVENT_TYPES = {
    "queenless", "swarm", "pre_swarm", "absconding", "robbing", "varroa",
    "wax_moth", "starvation", "theft", "node_offline", "sensor_fault",
    "healthy", "dead",
}

SEVERITY = {
    "dead": "critical", "absconding": "critical", "starvation": "critical",
    "robbing": "critical", "queenless": "warn", "pre_swarm": "warn",
    "varroa": "warn", "wax_moth": "warn",
}

# What the beekeeper actually reads. English and Hindi, because the person this
# is for may read neither English nor a class label.
ADVICE = {
    "queenless": (
        "No queen detected. Open the hive within 3 days and check for eggs.",
        "रानी नहीं मिली। 3 दिन के अंदर पेटी खोलकर अंडे देखें।"),
    "pre_swarm": (
        "This colony is preparing to swarm. Add a super or split it this week.",
        "यह कॉलोनी छत्ता छोड़ने की तैयारी में है। इस हफ्ते सुपर लगाएं या बांटें।"),
    "varroa": (
        "Signs of varroa mites. Do a sticky-board count before treating.",
        "वरोआ माइट के लक्षण। इलाज से पहले स्टिकी बोर्ड पर गिनती करें।"),
    "starvation": (
        "Stores are almost gone. Feed sugar syrup now.",
        "भोजन लगभग खत्म। अभी चीनी का घोल दें।"),
    "robbing": (
        "Robbing in progress. Reduce the entrance immediately.",
        "लूट हो रही है। तुरंत प्रवेश द्वार छोटा करें।"),
    "wax_moth": (
        "Possible wax moth. Check the frames and remove damaged comb.",
        "मोम कीट संभव। फ्रेम जांचें और खराब छत्ता हटाएं।"),
    "dead": (
        "This hive has stopped regulating temperature. Inspect it.",
        "यह पेटी तापमान नियंत्रित नहीं कर रही। जांच करें।"),
}

_model = None
_meta: dict | None = None


def load_model():
    """Loaded once, lazily. A missing model is not fatal: the rest of the API
    works and health inference simply reports itself unavailable."""
    global _model, _meta
    if _model is None:
        import json
        import joblib
        path = MODEL_DIR / "health_clf.joblib"
        if not path.exists():
            raise FileNotFoundError(
                f"no model at {path}. Train it: python ml/train_health.py")
        _model = joblib.load(path)
        meta_path = MODEL_DIR / "health_clf.meta.json"
        _meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        log.info("health model %s loaded (%d classes)",
                 _meta.get("version", "?"), len(_model.classes_))
    return _model


def model_info() -> dict:
    try:
        load_model()
    except FileNotFoundError as exc:
        return {"available": False, "error": str(exc)}
    return {"available": True, **(_meta or {})}


# Deliberately the last N *samples* rather than a wall-clock window. Simulated
# telemetry is stamped with simulated time -- often months from now in either
# direction -- so `ts >= now() - interval` either matches everything or nothing.
# Row counts behave identically for simulated and live data.
TELEMETRY_SQL = """
SELECT * FROM (
    SELECT t.ts, t.node_id, t.hive_id::text AS hive_id,
           t.weight_kg, t.t_in, t.rh_in, t.t_out, t.rh_out, t.sound_rms,
           t.entrance_in, t.entrance_out,
           (t.features->>'centroid_hz')::float AS centroid_hz,
           t.features->'band_energy' AS band_energy,
           t.features->'mfcc'        AS mfcc
    FROM telemetry t
    WHERE t.hive_id = %s
    ORDER BY t.ts DESC
    LIMIT %s
) recent
ORDER BY ts
"""


def _frame_from_rows(rows: list[dict]) -> pd.DataFrame:
    """Telemetry rows -> the wide frame `build_features` expects."""
    recs = []
    for r in rows:
        be = r.get("band_energy") or []
        mf = r.get("mfcc") or []
        rec = {
            "ts": int(r["ts"].timestamp()),
            "node_id": r["node_id"],
            "weight_kg": r["weight_kg"], "t_in": r["t_in"], "rh_in": r["rh_in"],
            "t_out": r["t_out"], "rh_out": r["rh_out"],
            "sound_rms": r["sound_rms"], "centroid_hz": r.get("centroid_hz"),
            "entrance_in": r["entrance_in"], "entrance_out": r["entrance_out"],
        }
        for i in range(20):
            rec[f"be_{i}"] = be[i] if i < len(be) else 0.0
        for i in range(13):
            rec[f"mfcc_{i}"] = mf[i] if i < len(mf) else 0.0
        recs.append(rec)
    return pd.DataFrame(recs)


def assess_hive(conn, hive_id: str, samples: int = 200) -> dict | None:
    """Predict the current state of one hive. None when there is too little
    telemetry to say anything -- which is a legitimate answer, not a failure."""
    from features import build_features, feature_matrix

    model = load_model()
    with conn.cursor() as cur:
        cur.execute(TELEMETRY_SQL, (hive_id, samples))
        rows = cur.fetchall()
    # rolling features need a day of history before they mean anything
    if len(rows) < 30:
        return None

    df = build_features(_frame_from_rows(rows))
    X = feature_matrix(df).tail(24)          # the most recent day
    if X.empty:
        return None

    proba = model.predict_proba(X)
    classes = list(model.classes_)
    preds = [classes[i] for i in proba.argmax(axis=1)]

    # persistence: the modal state across the window, and how much of the
    # window agreed with it
    modal = max(set(preds), key=preds.count)
    persistence = preds.count(modal) / len(preds)
    confidence = float(proba[:, classes.index(modal)].mean())

    return {
        "hive_id": hive_id,
        "state": modal,
        "confidence": round(confidence, 4),
        "persistence": round(persistence, 4),
        "samples": len(X),
        "model_version": (_meta or {}).get("version"),
        "alertable": (
            modal != "healthy"
            and persistence >= MIN_PERSISTENCE
            and confidence >= MIN_CONFIDENCE.get(modal, 0.8)
        ),
    }


def record_event(conn, assessment: dict) -> bool:
    """Write an alert, unless an identical one is already open."""
    if not assessment.get("alertable"):
        return False
    state = assessment["state"]
    if state not in ALLOWED_EVENT_TYPES:
        log.warning("model predicted %r, which the schema does not allow; "
                    "skipping alert", state)
        return False
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM hive_events
               WHERE hive_id = %s AND event_type = %s AND acknowledged_at IS NULL
                 AND ts > now() - interval '14 days' LIMIT 1""",
            (assessment["hive_id"], state))
        if cur.fetchone():
            return False

        en, hi = ADVICE.get(state, (state, state))
        cur.execute(
            """INSERT INTO hive_events
                 (hive_id, event_type, severity, confidence, source,
                  model_version, detail)
               VALUES (%s,%s,%s,%s,'model',%s,%s)""",
            (assessment["hive_id"], state, SEVERITY.get(state, "info"),
             assessment["confidence"], assessment.get("model_version"),
             Jsonb({
                 "advice_en": en, "advice_hi": hi,
                 "persistence": assessment["persistence"],
                 "samples": assessment["samples"],
             })))
    return True


def scan_all(conn, samples: int = 200) -> dict:
    """Score every sentinel hive. This is the batch job a scheduler runs."""
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM hives WHERE is_sentinel AND status='active'")
        hive_ids = [r["id"] for r in cur.fetchall()]

    assessed, alerted, skipped = 0, 0, 0
    states: dict[str, int] = {}
    for hid in hive_ids:
        a = assess_hive(conn, hid, samples)
        if a is None:
            skipped += 1
            continue
        assessed += 1
        states[a["state"]] = states.get(a["state"], 0) + 1
        if record_event(conn, a):
            alerted += 1
    return {"hives": len(hive_ids), "assessed": assessed,
            "skipped_no_data": skipped, "alerts_raised": alerted,
            "states": states}
