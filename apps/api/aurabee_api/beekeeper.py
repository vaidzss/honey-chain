"""Endpoints for the beekeeper app.

Everything here is shaped by who is on the other end: someone with a cheap
Android phone, patchy 2G, and possibly limited literacy, standing in a field.

**One request per screen.** Round trips are expensive on a bad connection, so
each screen gets one endpoint that returns everything it needs. This costs a
little denormalisation and saves several seconds of staring at a spinner.

**Advice, not diagnoses.** The payload carries the sentence to show, in Hindi
and English, not a class name for the client to translate. Adding Marathi later
is then a server change, not an app release.

**Idempotent writes.** An inspection logged offline may be replayed several
times as the phone drifts in and out of signal, so `client_uuid` is generated
on the device and the insert is a no-op the second time.
"""

from __future__ import annotations

import logging
from datetime import date

from psycopg.types.json import Jsonb

from . import health as health_svc, store

log = logging.getLogger("aurabee.beekeeper")

HIVES_SQL = """
SELECT h.id::text AS hive_id, h.label, h.species, h.source, h.is_sentinel,
       h.status, ap.id::text AS apiary_id, ap.name AS apiary,
       n.id AS node_id, n.last_seen, n.batt_v,
       lt.ts AS last_reading, lt.weight_kg, lt.t_in, lt.t_out,
       round((lt.t_in - lt.t_out)::numeric, 1) AS thermal_delta
FROM hives h
JOIN apiaries ap ON ap.id = h.apiary_id
LEFT JOIN nodes n ON n.hive_id = h.id
LEFT JOIN LATERAL (
    SELECT t.ts, t.weight_kg, t.t_in, t.t_out
    FROM telemetry t WHERE t.hive_id = h.id
    ORDER BY t.ts DESC LIMIT 1
) lt ON true
WHERE ap.actor_id = %s AND h.status = 'active'
ORDER BY h.label
"""

ALERTS_SQL = """
SELECT e.id::text, e.hive_id::text, h.label, e.event_type, e.severity,
       e.confidence, e.ts, e.detail->>'advice_en' AS advice_en,
       e.detail->>'advice_hi' AS advice_hi
FROM hive_events e
JOIN hives h    ON h.id = e.hive_id
JOIN apiaries a ON a.id = h.apiary_id
WHERE a.actor_id = %s AND e.acknowledged_at IS NULL
ORDER BY CASE e.severity WHEN 'critical' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
         e.ts DESC
LIMIT 50
"""


def dashboard(conn, actor_id: str, assess: bool = False) -> dict:
    """Everything the home screen shows, in one request."""
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, name, district, state FROM actors WHERE id = %s",
                    (actor_id,))
        me = cur.fetchone()
        if not me:
            raise ValueError(f"unknown beekeeper {actor_id}")

        cur.execute(HIVES_SQL, (actor_id,))
        hives = [dict(r) for r in cur.fetchall()]
        cur.execute(ALERTS_SQL, (actor_id,))
        alerts = [dict(r) for r in cur.fetchall()]

    # Live inference is optional: the dashboard must render on a slow phone
    # even when the model is unavailable or slow.
    if assess:
        for h in hives:
            if not h["is_sentinel"]:
                continue
            try:
                a = health_svc.assess_hive(conn, h["hive_id"])
                if a:
                    h["predicted_state"] = a["state"]
                    h["confidence"] = a["confidence"]
            except Exception as exc:  # noqa: BLE001
                log.warning("assess failed for %s: %s", h["hive_id"], exc)

    alerted = {a["hive_id"] for a in alerts}
    for h in hives:
        h["has_alert"] = h["hive_id"] in alerted
        # A hive is only "monitored" if a node is actually reporting. Showing a
        # confident green tick for a box with a dead node would be a lie.
        h["monitored"] = bool(h["node_id"] and h["last_reading"])

    return {
        "beekeeper": me,
        "summary": {
            "hives": len(hives),
            "monitored": sum(1 for h in hives if h["monitored"]),
            "needs_attention": len(alerted),
            "critical": sum(1 for a in alerts if a["severity"] == "critical"),
        },
        "hives": hives,
        "alerts": alerts,
    }


HARVEST_SQL = """
SELECT ap.id::text AS apiary_id, ap.name AS apiary, ap.hive_count,
       e.season, e.p10_kg, e.p50_kg, e.p90_kg, e.coverage_ratio,
       e.sentinel_count, e.model_version, e.window_start, e.window_end,
       coalesce((SELECT sum(b.declared_kg) FROM batches b
                 WHERE b.apiary_id = ap.id AND b.envelope_id = e.id), 0) AS already_declared
FROM apiaries ap
LEFT JOIN LATERAL (
    SELECT * FROM yield_envelopes y WHERE y.apiary_id = ap.id
    ORDER BY y.created_at DESC LIMIT 1
) e ON true
WHERE ap.actor_id = %s
ORDER BY ap.name
"""


def harvest_context(conn, actor_id: str) -> dict:
    """What the beekeeper may declare, and why -- shown BEFORE they type.

    This is the screen that decides whether the whole yield-bounding idea reads
    as help or as an accusation. Showing the allowed amount up front turns a
    rejection into a conversation: the number, where it came from, how many
    hives were actually measured, and how much is left this season. A beekeeper
    who types 800 and gets a red error has been told they are a suspect. One who
    sees "up to 304 kg, from 2 measured hives of 14" has been told what the
    system knows.
    """
    with conn.cursor() as cur:
        cur.execute(HARVEST_SQL, (actor_id,))
        rows = [dict(r) for r in cur.fetchall()]

    out = []
    for r in rows:
        if r["p90_kg"] is None:
            out.append({
                "apiary_id": r["apiary_id"], "apiary": r["apiary"],
                "hive_count": r["hive_count"], "has_envelope": False,
                "message_en": "No sensor history yet for this apiary, so no "
                              "harvest can be recorded here.",
                "message_hi": "इस मधुमक्खी स्थल का सेंसर रिकॉर्ड नहीं है, "
                              "इसलिए फसल दर्ज नहीं हो सकती।",
            })
            continue
        ceiling = float(r["p90_kg"])
        used = float(r["already_declared"] or 0)
        remaining = max(0.0, ceiling - used)
        cov = float(r["coverage_ratio"] or 0)
        out.append({
            "apiary_id": r["apiary_id"], "apiary": r["apiary"],
            "hive_count": r["hive_count"], "has_envelope": True,
            "season": r["season"],
            "expected_kg": round(float(r["p50_kg"]), 1),
            "max_kg": round(ceiling, 1),
            "already_declared_kg": round(used, 1),
            "remaining_kg": round(remaining, 1),
            "sentinel_count": r["sentinel_count"],
            "coverage_pct": round(cov * 100),
            "model_version": r["model_version"],
            "window": [str(r["window_start"]), str(r["window_end"])],
            "message_en": (
                f"Sensors on {r['sentinel_count']} of {r['hive_count']} hives "
                f"suggest about {float(r['p50_kg']):.0f} kg this season. "
                f"You can record up to {remaining:.0f} kg."),
            "message_hi": (
                f"{r['hive_count']} में से {r['sentinel_count']} पेटियों के सेंसर "
                f"बताते हैं कि इस मौसम में लगभग {float(r['p50_kg']):.0f} किलो होगा। "
                f"आप {remaining:.0f} किलो तक दर्ज कर सकते हैं।"),
        })
    return {"apiaries": out}


def log_inspection(conn, *, client_uuid: str, hive_id: str, actor_id: str,
                   ts, queen_seen: bool | None = None,
                   brood_frames: float | None = None,
                   honey_frames: float | None = None,
                   pests: list[str] | None = None,
                   varroa_count: int | None = None,
                   fed: bool = False, treated_with: str | None = None,
                   notes: str | None = None) -> dict:
    """Record a hive inspection. Safe to replay -- offline clients will."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO inspections
                 (client_uuid, hive_id, actor_id, ts, queen_seen, brood_frames,
                  honey_frames, pests, varroa_count, fed, treated_with, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (client_uuid) DO NOTHING
               RETURNING id::text""",
            (client_uuid, hive_id, actor_id, ts, queen_seen, brood_frames,
             honey_frames, pests or [], varroa_count, fed, treated_with, notes))
        row = cur.fetchone()

        # A beekeeper who has just looked inside the box knows more than the
        # model does. Seeing the queen closes an open queenless alert -- and is
        # recorded as ground truth we can retrain on later.
        if queen_seen and row:
            cur.execute(
                """UPDATE hive_events SET acknowledged_at = now()
                   WHERE hive_id = %s AND event_type = 'queenless'
                     AND acknowledged_at IS NULL""", (hive_id,))
    return {"recorded": bool(row), "duplicate": row is None,
            "inspection_id": row["id"] if row else None}


def record_harvest(conn, chain, *, client_uuid: str, actor_id: str,
                   apiary_id: str, kg: float, recorded_at) -> dict:
    """Record a harvest declaration. Idempotent, because offline clients replay.

    The chain is the authority on whether this is allowed; this function does
    not re-implement the ceiling, it asks. If the mint reverts the declaration
    is stored as `rejected` with the contract error rather than being dropped,
    so the beekeeper can be told what happened and a supervisor can see it.
    """
    from datetime import date as _date

    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM batches WHERE batch_code = %s",
                    (f"AB-{client_uuid[:12]}",))
        if cur.fetchone():
            return {"recorded": False, "duplicate": True}

        cur.execute("""SELECT e.id::text, e.season, e.p90_kg
                       FROM yield_envelopes e WHERE e.apiary_id = %s
                       ORDER BY e.created_at DESC LIMIT 1""", (apiary_id,))
        env = cur.fetchone()
    if not env:
        return {"recorded": False, "error": "no_envelope",
                "message": "This apiary has no sensor history yet."}

    batch_code = f"AB-{client_uuid[:12]}"
    tx = None
    status = "minted"
    error = None
    if chain:
        try:
            res = chain.mint_harvest(batch_code, apiary_id, env["season"], kg,
                                     "mustard", int(recorded_at.timestamp())
                                     if hasattr(recorded_at, "timestamp")
                                     else 0)
            tx = res["tx_hash"]
        except Exception as exc:  # noqa: BLE001
            status = "flagged"
            error = getattr(exc, "reason", None) or "chain_error"
            log.warning("harvest mint failed for %s: %s", batch_code, error)

    store.upsert_batch(
        conn, batch_code=batch_code, apiary_id=apiary_id,
        owner_actor_id=actor_id, kind="raw", declared_kg=kg, current_kg=kg,
        floral_source=None, harvest_date=_date.today(), status=status,
        envelope_id=env["id"], tx_hash=tx)
    store.record_custody(conn, batch_code=batch_code, kind="mint",
                         from_actor_id=None, to_actor_id=actor_id,
                         qty_kg=kg, tx_hash=tx)
    return {"recorded": True, "batch_code": batch_code, "status": status,
            "tx_hash": tx, "error": error}


def acknowledge(conn, event_id: str, actor_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE hive_events e SET acknowledged_at = now()
               FROM hives h JOIN apiaries a ON a.id = h.apiary_id
               WHERE e.id = %s AND h.id = e.hive_id AND a.actor_id = %s
                 AND e.acknowledged_at IS NULL""", (event_id, actor_id))
        return cur.rowcount > 0
