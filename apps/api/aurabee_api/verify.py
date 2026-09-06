"""Consumer verification: the /verify/{serial} payload, and clone detection.

Two jobs:

 1. **Answer honestly.** A jar is either provably part of a real packing run, or
    it is not, and the page must be able to say "we cannot confirm this" without
    hedging. A traceability system that always shows a reassuring green tick is
    worse than none at all, because it launders fraud.

 2. **Notice cloning.** A photographed label reproduces the serial perfectly.
    What it cannot reproduce is the code under the scratch-off, and what it
    cannot control is that the genuine jar was already scanned somewhere else.
    So the signals are: a wrong secret, the same serial appearing in distant
    places, and scan counts that outrun plausibility.

Nothing here blocks a sale. It surfaces what is known and opens a case for a
human. Automated accusation against a rural beekeeper on a statistical signal
is not a product, it is a liability.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from . import store
from .seals import merkle_proof

log = logging.getLogger("aurabee.verify")

# Two geohash characters is roughly a 1,250 km cell; sharing fewer than two
# means two scans are in genuinely different parts of the country.
GEO_PREFIX = 2
IMPLAUSIBLE_WINDOW = timedelta(hours=24)
HIGH_SCAN_COUNT = 8


def _hash(value: str | None) -> str | None:
    return hashlib.sha256(value.encode()).hexdigest()[:32] if value else None


def _origin_rows(conn, batch_uuid: str) -> list[dict]:
    """Constituent origins with percentages, descending.

    This is the EU 2024/1438 declaration. For a single-origin batch it collapses
    to one row at 100%.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT bc.pct, bc.kg, bc.origin_country, bc.origin_state, bc.origin_district,
                   ap.name AS apiary_name, act.name AS beekeeper
            FROM batch_composition bc
            JOIN batches sb       ON sb.id = bc.source_batch_id
            LEFT JOIN apiaries ap ON ap.id = sb.apiary_id
            -- Credit the APIARY owner, not the batch owner. By the time honey is
            -- blended the batch belongs to a processor, and naming them as the
            -- beekeeper would be false on the very label meant to prove origin.
            LEFT JOIN actors act  ON act.id = ap.actor_id
            WHERE bc.blend_batch_id = %s
            ORDER BY bc.pct DESC
            """,
            (batch_uuid,),
        )
        rows = cur.fetchall()
        if rows:
            return rows

        # not a blend: the batch is its own origin
        cur.execute(
            """SELECT 100.0 AS pct, b.declared_kg AS kg, 'IN' AS origin_country,
                      ap.state AS origin_state, ap.district AS origin_district,
                      ap.name AS apiary_name, act.name AS beekeeper
               FROM batches b
               LEFT JOIN apiaries ap ON ap.id = b.apiary_id
               LEFT JOIN actors act  ON act.id = ap.actor_id
               WHERE b.id = %s""",
            (batch_uuid,),
        )
        row = cur.fetchone()
        return [row] if row and row["origin_state"] else []


def _journey(conn, batch_uuid: str) -> list[dict]:
    """Custody events for this batch AND everything it was made from.

    A consumer scanning a blended jar wants the whole story, but the upstream
    transfers belong to the source batches, not the blend. Walking the
    composition graph upward is what turns "one merge event" into the actual
    hive-to-jar journey.

    Depth is bounded: a cycle in the composition graph should be impossible,
    but a malformed row should not hang a consumer-facing request.
    """
    seen: set[str] = set()
    frontier = [batch_uuid]
    depth = 0
    while frontier and depth < 8:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT source_batch_id::text AS src
                   FROM batch_composition
                   WHERE blend_batch_id = ANY(%s)""",
                (frontier,),
            )
            parents = [r["src"] for r in cur.fetchall()]
        seen.update(frontier)
        frontier = [p for p in parents if p not in seen]
        depth += 1
    seen.update(frontier)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ce.kind, ce.qty_kg, ce.loss_kg, ce.ts, ce.tx_hash,
                   b.batch_code,
                   f.name AS from_name, f.kind AS from_role,
                   t.name AS to_name,   t.kind AS to_role
            FROM custody_events ce
            JOIN batches b     ON b.id = ce.batch_id
            LEFT JOIN actors f ON f.id = ce.from_actor_id
            LEFT JOIN actors t ON t.id = ce.to_actor_id
            WHERE ce.batch_id = ANY(%s)
            ORDER BY ce.ts, ce.created_at
            """,
            (list(seen),),
        )
        return cur.fetchall()


def _lab_results(conn, batch_uuid: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT a.kind, a.summary, a.issued_at, a.expires_at, act.name AS issuer
               FROM attestations a
               LEFT JOIN actors act ON act.id = a.issuer_actor_id
               WHERE a.subject_type = 'batch' AND a.subject_id = %s
               ORDER BY a.issued_at DESC""",
            (batch_uuid,),
        )
        return cur.fetchall()


def _detect_anomalies(conn, seal: dict, geohash: str | None,
                      secret_ok: bool | None) -> list[dict]:
    """Returns warnings. Each carries a code the UI can style and a sentence a
    person can act on."""
    warnings: list[dict] = []

    if secret_ok is False:
        warnings.append({
            "code": "secret_mismatch",
            "severity": "critical",
            "message": "The code under the scratch-off does not match this serial. "
                       "A copied label reproduces the printed code but not the "
                       "hidden one.",
        })

    first_geo = seal.get("first_scan_geohash")
    if first_geo and geohash and first_geo[:GEO_PREFIX] != geohash[:GEO_PREFIX]:
        warnings.append({
            "code": "geo_mismatch",
            "severity": "warn",
            "message": f"This seal was first scanned in a different region "
                       f"({first_geo[:GEO_PREFIX]}). One jar cannot be in two places.",
        })

    if (seal.get("scan_count") or 0) >= HIGH_SCAN_COUNT:
        warnings.append({
            "code": "scan_velocity",
            "severity": "warn",
            "message": f"Scanned {seal['scan_count']} times. Unusual for a single jar.",
        })

    # more distinct seals in circulation than the run ever produced
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS scanned,
                      (SELECT count(*) FROM seals WHERE batch_id = %(b)s) AS issued
               FROM seals WHERE batch_id = %(b)s AND scan_count > 0""",
            {"b": seal["batch_id"]},
        )
        counts = cur.fetchone()
    if counts and counts["issued"] and counts["scanned"] > counts["issued"]:
        warnings.append({
            "code": "overissue",
            "severity": "critical",
            "message": "More seals from this batch are in circulation than were issued.",
        })

    return warnings


def verify_serial(
    conn,
    chain,
    serial: str,
    *,
    secret: str | None = None,
    geohash: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    record: bool = True,
) -> dict:
    seal = store.seal_by_serial(conn, serial)
    if not seal:
        # Deliberately not "invalid". We know this serial is not ours; we do not
        # know that the honey is fake, and saying so would be an accusation we
        # cannot support.
        return {
            "serial": serial,
            "known": False,
            "verified": False,
            "message": "This code is not in the AuraBee registry.",
            "warnings": [{
                "code": "unknown_serial",
                "severity": "critical",
                "message": "No jar with this serial was ever issued on this platform.",
            }],
        }

    batch_code = seal["batch_code"]

    # --- cryptographic check, when the consumer supplied the hidden code ---
    secret_ok: bool | None = None
    merkle_ok: bool | None = None
    if secret:
        secret_ok = store.secret_hash(secret) == seal["secret_hash"]
        if secret_ok:
            serials = store.sibling_serials(conn, seal["batch_id"])
            try:
                idx = serials.index(serial)
                # Leaves are rebuilt from the stored serials; only the secret for
                # THIS jar is known to us at request time, which is the point --
                # we never hold every secret in a form that can regenerate the
                # whole tree on demand.
                leaves = _rebuild_leaves(conn, batch_code, serials)
                if leaves:
                    proof = merkle_proof(leaves, idx)
                    merkle_ok = chain.verify_seal(
                        batch_code, serial, secret, ["0x" + p.hex() for p in proof]
                    )
            except ValueError:
                merkle_ok = False

    if record:
        scan = store.record_scan(
            conn, seal_id=seal["id"], geohash=geohash, ip_hash=_hash(ip),
            ua_hash=_hash(user_agent), secret_ok=secret_ok,
        )
        seal = {**seal, **scan}

    warnings = _detect_anomalies(conn, seal, geohash, secret_ok)
    for w in warnings:
        if w["severity"] == "critical":
            store.open_fraud_case(
                conn, kind="seal_clone" if w["code"] != "overissue" else "scan_velocity",
                subject_type="seal", subject_id=seal["id"], score=0.8,
                detail={"serial": serial, "code": w["code"]},
            )

    on_chain = chain.batch(batch_code) if chain else None
    origins = _origin_rows(conn, seal["batch_uuid"])

    return {
        "serial": serial,
        "known": True,
        # "verified" means proven against the chain, not merely present in our
        # database. Without the hidden code we can only say the serial exists.
        "verified": bool(merkle_ok),
        "secret_checked": secret is not None,
        "secret_ok": secret_ok,
        "batch_code": batch_code,
        "net_weight_g": seal.get("net_weight_g"),
        "chain": on_chain,
        "origins": [
            {
                "pct": float(o["pct"]),
                "kg": float(o["kg"] or 0),
                "country": o["origin_country"],
                "state": o["origin_state"],
                "district": o["origin_district"],
                "apiary": o["apiary_name"],
                "beekeeper": o["beekeeper"],
            }
            for o in origins
        ],
        "journey": [
            {
                "step": j["kind"],
                "batch": j["batch_code"],
                "from": j["from_name"],
                "from_role": j["from_role"],
                "to": j["to_name"],
                "to_role": j["to_role"],
                "kg": float(j["qty_kg"]),
                "loss_kg": float(j["loss_kg"] or 0),
                "at": j["ts"].isoformat() if j["ts"] else None,
                "tx": j["tx_hash"],
            }
            for j in _journey(conn, seal["batch_uuid"])
        ],
        "lab": [
            {
                "kind": r["kind"],
                "issuer": r["issuer"],
                "summary": r["summary"],
                "issued_at": r["issued_at"].isoformat() if r["issued_at"] else None,
            }
            for r in _lab_results(conn, seal["batch_uuid"])
        ],
        "scan": {
            "count": seal.get("scan_count"),
            "first_at": seal["first_scan_at"].isoformat() if seal.get("first_scan_at") else None,
            "first_region": (seal.get("first_scan_geohash") or "")[:GEO_PREFIX] or None,
        },
        "warnings": warnings,
    }


_LEAF_CACHE: dict[str, list[bytes]] = {}


def _rebuild_leaves(conn, batch_code: str, serials: list[str]) -> list[bytes] | None:
    """Rebuild the tree's leaves for proof generation.

    We hold hashed secrets, not secrets, so leaves cannot be recomputed from the
    database alone. The issuing step therefore stores each leaf alongside the
    seal; this reads them back in serial order.
    """
    if batch_code in _LEAF_CACHE:
        return _LEAF_CACHE[batch_code]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.serial, s.leaf_hex FROM seals s
               JOIN batches b ON b.id = s.batch_id
               WHERE b.batch_code = %s AND s.leaf_hex IS NOT NULL
               ORDER BY s.serial""",
            (batch_code,),
        )
        rows = cur.fetchall()
    if len(rows) != len(serials):
        return None
    leaves = [bytes.fromhex(r["leaf_hex"][2:]) for r in rows]
    _LEAF_CACHE[batch_code] = leaves
    return leaves
