"""EPCIS 2.0 document generation.

Renders our custody history as a standards-conformant EPCIS 2.0 JSON-LD
document, so a buyer, customs system or downstream ERP can read our chain
without us shipping them an SDK.

## Why this and not our own format

Our `custody_events` model turned out to be a rediscovery of EPCIS Critical
Tracking Events -- the same model FDA FSMA Rule 204 mandates. Keeping our own
names for the same concepts is how a project ends up as an island, and "lack of
standardization" is the single most-cited reason blockchain traceability pilots
fail to reach production.

EPCIS is the *interchange* format. The chain stays the enforcement layer: EPCIS
describes what happened, the contract decides what is allowed to happen.

## The mapping

| AuraBee custody kind | EPCIS event      | bizStep       | disposition  |
|----------------------|------------------|---------------|--------------|
| mint                 | ObjectEvent ADD  | commissioning | active       |
| transfer             | ObjectEvent OBSERVE | shipping   | in_transit   |
| process              | TransformationEvent | transforming | in_progress |
| merge (blend)        | TransformationEvent | transforming | in_progress |
| pack                 | ObjectEvent ADD  | packing       | sellable_accessible |
| lab test             | ObjectEvent OBSERVE | inspecting | in_progress  |

A batch is an **LGTIN class** carried in `quantityList` with `uom: KGM`, because
honey in a drum is a mass, not an item. Only in a jar does it become a
serialised SGTIN. EPCIS distinguishes these precisely; most bespoke schemas
(ours included, until now) blur them.

## Known deviation, stated rather than hidden

`eventID` uses the `ni:///sha-256;...?ver=CBV2.0` *form*, but the digest is a
stable hash of our own canonical content, not the normative EPCIS event-hash
pre-hash string algorithm. Two EPCIS implementations would therefore compute
different ids for the same event. Fine for an interchange document that carries
its own ids; must be fixed before claiming conformance.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from aurabee_schema import gs1

log = logging.getLogger("aurabee.epcis")

EPCIS_CONTEXT = "https://ref.gs1.org/standards/epcis/2.0.0/epcis-context.jsonld"
AURABEE_NS = {"aurabee": "https://aurabee.in/epcis/"}

KIND_MAP = {
    "mint":     ("ObjectEvent", "ADD", "commissioning", "active"),
    "transfer": ("ObjectEvent", "OBSERVE", "shipping", "in_transit"),
    "process":  ("TransformationEvent", None, "transforming", "in_progress"),
    "merge":    ("TransformationEvent", None, "transforming", "in_progress"),
    "split":    ("TransformationEvent", None, "transforming", "in_progress"),
    "pack":     ("ObjectEvent", "ADD", "packing", "sellable_accessible"),
    "test":     ("ObjectEvent", "OBSERVE", "inspecting", "in_progress"),
    "sell":     ("ObjectEvent", "OBSERVE", "retail_selling", "retail_sold"),
    "void":     ("ObjectEvent", "DELETE", "decommissioning", "non_sellable_other"),
}


def _event_id(payload: dict) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return f"ni:///sha-256;{digest}?ver=CBV2.0"


def _iso(ts: datetime) -> tuple[str, str]:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    offset = ts.strftime("%z")
    return ts.isoformat(), f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"


def _qty(gtin: str, lot: str, kg: float) -> dict:
    return {
        "epcClass": gs1.lgtin_urn(gtin, lot),
        "quantity": round(float(kg), 3),
        "uom": gs1.UOM_KILOGRAM,
    }


def _location(gln: str | None) -> dict | None:
    return {"id": gs1.sgln_urn(gln)} if gln else None


# --------------------------------------------------------------------------
def build_document(
    *,
    batch_code: str,
    gtin: str,
    events: list[dict],
    seals: list[str] | None = None,
    seal_gtin: str | None = None,
    lab: list[dict] | None = None,
    telemetry_summary: dict | None = None,
    doc_id: str | None = None,
) -> dict:
    """Assemble the EPCIS document for one batch.

    `events` are rows from custody_events joined to actor GLNs.
    """
    event_list: list[dict] = []

    for ev in events:
        kind = ev["kind"]
        mapped = KIND_MAP.get(kind)
        if not mapped:
            log.debug("no EPCIS mapping for custody kind %r, skipping", kind)
            continue
        etype, action, biz_step, disposition = mapped
        event_time, tz_offset = _iso(ev["ts"])

        base: dict = {
            "type": etype,
            "eventTime": event_time,
            "eventTimeZoneOffset": tz_offset,
            "bizStep": biz_step,
            "disposition": disposition,
        }

        if etype == "TransformationEvent":
            # inputs are consumed, outputs created; no `action` on a
            # TransformationEvent, per the spec
            base["inputQuantityList"] = [
                _qty(gtin, src, kg) for src, kg in ev.get("inputs", [])
            ] or [_qty(gtin, ev["batch_code"], ev["qty_kg"])]
            base["outputQuantityList"] = [_qty(gtin, ev["batch_code"], ev["qty_kg"])]
            if ev.get("loss_kg"):
                base["aurabee:declaredLossKg"] = round(float(ev["loss_kg"]), 3)
        else:
            base["action"] = action
            base["quantityList"] = [_qty(gtin, ev["batch_code"], ev["qty_kg"])]

        if ev.get("to_gln"):
            base["bizLocation"] = _location(ev["to_gln"])
            base["readPoint"] = _location(ev["to_gln"])
        elif ev.get("from_gln"):
            base["readPoint"] = _location(ev["from_gln"])

        if ev.get("from_gln") and ev.get("to_gln"):
            base["sourceList"] = [
                {"type": "owning_party", "source": gs1.sgln_urn(ev["from_gln"])}
            ]
            base["destinationList"] = [
                {"type": "owning_party", "destination": gs1.sgln_urn(ev["to_gln"])}
            ]

        # The EPCIS 2.0 selling point: sensor evidence travels *inside* the
        # event, so the buyer receives the hive data that bounded this
        # declaration rather than being asked to trust the number.
        if kind == "mint" and telemetry_summary:
            base["sensorElementList"] = [_sensor_element(telemetry_summary)]

        if ev.get("tx_hash"):
            base["aurabee:blockchainTxHash"] = ev["tx_hash"]

        base["eventID"] = _event_id(base)
        event_list.append(base)

    # ---- lab attestations as inspecting events ---------------------------
    for report in lab or []:
        event_time, tz_offset = _iso(report["issued_at"])
        summary = report.get("summary") or {}
        ev = {
            "type": "ObjectEvent",
            "action": "OBSERVE",
            "bizStep": "inspecting",
            "disposition": "in_progress",
            "eventTime": event_time,
            "eventTimeZoneOffset": tz_offset,
            "quantityList": [_qty(gtin, batch_code, report.get("kg") or 0)],
            # EPCIS 2.0 added certificationInfo precisely for this
            "certificationInfo": report.get("doc_hash"),
            "aurabee:laboratory": report.get("issuer"),
            "aurabee:verdict": summary.get("verdict"),
            "sensorElementList": [{
                "sensorReport": [
                    r for r in (
                        _report("aurabee:C4Sugar", summary.get("c4_pct"), "P1"),
                        _report("aurabee:Moisture", summary.get("moisture_pct"), "P1"),
                        _report("aurabee:HMF", summary.get("hmf_mg_kg"), "M1"),
                    ) if r
                ]
            }],
        }
        ev["eventID"] = _event_id(ev)
        event_list.append(ev)

    # ---- jar commissioning ------------------------------------------------
    if seals and seal_gtin:
        last_ts = events[-1]["ts"] if events else datetime.now(timezone.utc)
        event_time, tz_offset = _iso(last_ts)
        ev = {
            "type": "ObjectEvent",
            "action": "ADD",
            "bizStep": "commissioning",
            "disposition": "sellable_accessible",
            "eventTime": event_time,
            "eventTimeZoneOffset": tz_offset,
            "epcList": [gs1.sgtin_urn(seal_gtin, s) for s in seals],
            "aurabee:parentBatch": gs1.lgtin_urn(gtin, batch_code),
        }
        ev["eventID"] = _event_id(ev)
        event_list.append(ev)

    event_list.sort(key=lambda e: e["eventTime"])

    return {
        "@context": [EPCIS_CONTEXT, AURABEE_NS],
        "id": doc_id or f"https://aurabee.in/epcis/{batch_code}",
        "type": "EPCISDocument",
        "schemaVersion": "2.0",
        "creationDate": datetime.now(timezone.utc).isoformat(),
        "epcisBody": {"eventList": event_list},
    }


def _report(rtype: str, value, uom: str) -> dict | None:
    return None if value is None else {"type": rtype, "value": float(value), "uom": uom}


def _sensor_element(summary: dict) -> dict:
    """Hive telemetry summarised into an EPCIS sensorElement.

    Not every reading -- a season is tens of thousands of frames. What travels
    is the aggregate that bounded the declaration, plus the evidence hash that
    lets anyone re-derive it from the raw series we hold.
    """
    meta = {"time": summary.get("window_end")}
    if summary.get("node_count"):
        meta["deviceMetadata"] = f"aurabee:sentinel-nodes/{summary['node_count']}"
    if summary.get("evidence_hash"):
        meta["rawData"] = f"https://aurabee.in/evidence/{summary['evidence_hash']}"

    reports = [
        r for r in (
            _report("Temperature", summary.get("mean_t_in"), "CEL"),
            _report("RelativeHumidity", summary.get("mean_rh_in"), "A93"),
            _report("aurabee:HiveMassGain", summary.get("gross_gain_kg"), "KGM"),
            _report("aurabee:YieldCeilingP90", summary.get("p90_kg"), "KGM"),
            _report("aurabee:SentinelCoverage", summary.get("coverage_ratio"), "P1"),
        ) if r
    ]
    return {"sensorMetadata": {k: v for k, v in meta.items() if v}, "sensorReport": reports}


# --------------------------------------------------------------------------
# assembly from the database
# --------------------------------------------------------------------------
EVENTS_SQL = """
SELECT ce.kind, ce.qty_kg, ce.loss_kg, ce.ts, ce.tx_hash,
       b.batch_code,
       f.gln AS from_gln, t.gln AS to_gln
FROM custody_events ce
JOIN batches b     ON b.id = ce.batch_id
LEFT JOIN actors f ON f.id = ce.from_actor_id
LEFT JOIN actors t ON t.id = ce.to_actor_id
WHERE b.batch_code = %s
ORDER BY ce.ts, ce.created_at
"""

INPUTS_SQL = """
SELECT sb.batch_code AS source_code, bc.kg
FROM batch_composition bc
JOIN batches blend ON blend.id = bc.blend_batch_id
JOIN batches sb    ON sb.id = bc.source_batch_id
WHERE blend.batch_code = %s
"""

LAB_SQL = """
SELECT a.kind, a.summary, a.issued_at, a.doc_hash, act.name AS issuer
FROM attestations a
JOIN batches b       ON b.id = a.subject_id
LEFT JOIN actors act ON act.id = a.issuer_actor_id
WHERE a.subject_type = 'batch' AND b.batch_code = %s
ORDER BY a.issued_at
"""

ENVELOPE_SQL = """
SELECT e.p90_kg, e.coverage_ratio, e.sentinel_count, e.evidence_hash, e.window_end
FROM yield_envelopes e
JOIN batches b ON b.envelope_id = e.id
WHERE b.batch_code = %s
"""


def document_for_batch(conn, batch_code: str, max_seals: int = 50) -> dict:
    """Build the EPCIS document for a batch straight from the database."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT batch_code, gtin, jar_gtin FROM batches WHERE batch_code = %s",
            (batch_code,))
        batch = cur.fetchone()
        if not batch:
            raise ValueError(f"unknown batch {batch_code}")
        gtin = batch["gtin"]
        if not gtin:
            raise ValueError(f"batch {batch_code} has no GTIN allocated")

        cur.execute(EVENTS_SQL, (batch_code,))
        events = [dict(r) for r in cur.fetchall()]

        cur.execute(INPUTS_SQL, (batch_code,))
        inputs = [(r["source_code"], float(r["kg"])) for r in cur.fetchall()]
        for ev in events:
            if ev["kind"] in ("process", "merge", "split"):
                ev["inputs"] = inputs

        cur.execute(LAB_SQL, (batch_code,))
        lab = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT s.serial FROM seals s JOIN batches b ON b.id = s.batch_id "
            "WHERE b.batch_code = %s ORDER BY s.serial LIMIT %s",
            (batch_code, max_seals))
        seals = [r["serial"] for r in cur.fetchall()]

        cur.execute(ENVELOPE_SQL, (batch_code,))
        env = cur.fetchone()

        # telemetry summary that bounded this declaration
        summary = None
        if env:
            cur.execute(
                """SELECT round(avg(t.t_in)::numeric, 2)  AS mean_t_in,
                          round(avg(t.rh_in)::numeric, 2) AS mean_rh_in,
                          count(DISTINCT t.node_id)       AS node_count
                   FROM telemetry t
                   JOIN hives h    ON h.id = t.hive_id
                   JOIN apiaries a ON a.id = h.apiary_id
                   JOIN batches b  ON b.apiary_id = a.id
                   WHERE b.batch_code = %s""", (batch_code,))
            tele = cur.fetchone() or {}
            summary = {
                "p90_kg": float(env["p90_kg"]) if env["p90_kg"] else None,
                "coverage_ratio": float(env["coverage_ratio"]) if env["coverage_ratio"] else None,
                "evidence_hash": env["evidence_hash"],
                "window_end": env["window_end"].isoformat() if env["window_end"] else None,
                "mean_t_in": float(tele["mean_t_in"]) if tele.get("mean_t_in") else None,
                "mean_rh_in": float(tele["mean_rh_in"]) if tele.get("mean_rh_in") else None,
                "node_count": tele.get("node_count"),
            }

    jar_gtin = batch["jar_gtin"] or gtin
    return build_document(
        batch_code=batch_code, gtin=gtin, events=events, seals=seals,
        seal_gtin=jar_gtin if seals else None, lab=lab,
        telemetry_summary=summary,
    )
