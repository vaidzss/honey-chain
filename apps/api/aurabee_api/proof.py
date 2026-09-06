"""Ledger proofs and model metrics.

## What a "proof" means here

Not a screenshot of a dashboard. A **proof bundle** is everything an
independent party needs to re-check our claims *without trusting us*: contract
addresses, transaction hashes, the arithmetic of each invariant with both sides
shown, and the Merkle path for a specific jar.

The test of a proof is whether someone hostile could use it. Every number in
the bundle is re-readable from the chain with the addresses provided, and
`scripts/verify_ledger.py` does exactly that -- it reads only from chain state
and re-derives the invariants, so a bug in our database cannot make a broken
ledger look sound.

Three claims are made, each with its arithmetic exposed:

1. **Issuance was bounded** -- declared <= the telemetry-derived ceiling.
2. **Mass was conserved** -- outputs <= inputs at every transformation.
3. **Packing was bounded** -- jars x net weight <= batch mass, and no seals
   without a passing independent lab report.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aurabee_schema import gs1

from .chain import from_bytes32, to_bytes32

log = logging.getLogger("aurabee.proof")

ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = ROOT / "ml" / "models"


# --------------------------------------------------------------------------
# model metrics
# --------------------------------------------------------------------------
def model_metrics() -> dict:
    """Every model's own metadata, read from the artefacts it was saved with.

    Read from the `.meta.json` written at training time rather than from a
    hand-maintained list, so the numbers on the page cannot drift away from the
    model actually loaded. Each carries its own caveat, deliberately -- the
    caveat travels with the artefact rather than living only in a document
    nobody opens.
    """
    out = {}
    for key, fname in (("colony_health", "health_clf.meta.json"),
                       ("yield_forecast", "yield.meta.json"),
                       ("anomaly", "anomaly.meta.json")):
        path = MODEL_DIR / fname
        out[key] = json.loads(path.read_text()) if path.exists() else {
            "available": False,
            "note": f"not trained yet -- run the matching script in ml/",
        }

    # The varroa counter is classical CV, so it has no training artefact. Its
    # numbers come from ml/eval_varroa.py and are stated as what they are.
    out["varroa_counter"] = {
        "version": "varroa-cv-0.1.0",
        "method": "classical CV: adaptive threshold + size/shape/solidity filters",
        "mean_relative_error_synthetic": 0.091,
        "refuses_when": ["photo too distant", "out of focus", "over-exposed"],
        "caveat": (
            "No trained detector: there is no annotated Indian sticky-board "
            "dataset. Error is measured against SYNTHETIC boards with known "
            "counts, which validates the algorithm's mechanics, not its field "
            "accuracy. Replaced by a YOLO detector once beekeeper photographs "
            "with inspection-confirmed counts have accumulated."
        ),
    }
    return out


def system_metrics(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
              (SELECT count(*) FROM telemetry)                        AS telemetry_rows,
              (SELECT count(*) FROM hives)                            AS hives,
              (SELECT count(*) FROM hives WHERE is_sentinel)          AS sentinel_hives,
              (SELECT count(*) FROM nodes)                            AS nodes,
              (SELECT count(*) FROM batches)                          AS batches,
              (SELECT count(*) FROM seals)                            AS seals,
              (SELECT count(*) FROM scans)                            AS scans,
              (SELECT count(*) FROM custody_events)                   AS custody_events,
              (SELECT count(*) FROM hive_events WHERE source='model') AS model_alerts,
              (SELECT count(*) FROM fraud_cases WHERE status='open')  AS open_fraud_cases,
              (SELECT count(*) FROM yield_envelopes)                  AS envelopes,
              (SELECT count(*) FROM attestations)                     AS attestations
        """)
        row = dict(cur.fetchone())
        cur.execute("""SELECT round(avg(coverage_ratio)::numeric,4) AS avg_coverage,
                              round(sum(p90_kg)::numeric,1)         AS total_ceiling_kg
                       FROM yield_envelopes""")
        row.update(dict(cur.fetchone() or {}))
    row["sentinel_coverage"] = (
        round(row["sentinel_hives"] / row["hives"], 4) if row["hives"] else None)
    return row


# --------------------------------------------------------------------------
# ledger proof
# --------------------------------------------------------------------------
def batch_proof(conn, chain, batch_code: str, serial: str | None = None) -> dict:
    """Everything needed to re-verify one batch against the chain."""
    on_chain = chain.batch(batch_code)
    if not on_chain:
        raise ValueError(f"batch {batch_code} is not on chain")

    with conn.cursor() as cur:
        cur.execute("""SELECT b.batch_code, b.kind, b.declared_kg, b.gtin, b.jar_gtin,
                              b.tx_hash, b.apiary_id::text, ap.name AS apiary,
                              ap.district, ap.state, e.season, e.p90_kg, e.p50_kg,
                              e.evidence_hash, e.model_version, e.coverage_ratio,
                              e.sentinel_count, e.hives_counted, e.tx_hash AS envelope_tx
                       FROM batches b
                       LEFT JOIN apiaries ap ON ap.id = b.apiary_id
                       LEFT JOIN yield_envelopes e ON e.id = b.envelope_id
                       WHERE b.batch_code = %s""", (batch_code,))
        b = cur.fetchone()
        cur.execute("""SELECT ce.kind, ce.qty_kg, ce.loss_kg, ce.ts, ce.tx_hash,
                              f.name AS from_name, t.name AS to_name
                       FROM custody_events ce
                       JOIN batches x ON x.id = ce.batch_id
                       LEFT JOIN actors f ON f.id = ce.from_actor_id
                       LEFT JOIN actors t ON t.id = ce.to_actor_id
                       WHERE x.batch_code = %s ORDER BY ce.ts""", (batch_code,))
        custody = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT a.kind, a.doc_hash, a.summary, a.tx_hash,
                              act.name AS issuer
                       FROM attestations a
                       JOIN batches x ON x.id = a.subject_id
                       LEFT JOIN actors act ON act.id = a.issuer_actor_id
                       WHERE x.batch_code = %s""", (batch_code,))
        attestations = [dict(r) for r in cur.fetchall()]

    claims = []

    # --- claim 1: issuance was bounded by measured telemetry --------------
    if b and b["p90_kg"] is not None:
        cap = float(b["p90_kg"])
        declared = float(b["declared_kg"])
        claims.append({
            "claim": "Issuance was bounded by hive telemetry",
            "holds": declared <= cap + 1e-6,
            "arithmetic": f"{declared:.1f} kg declared <= {cap:.1f} kg ceiling",
            "how_derived": (
                f"P90 of a quantile forecast over {b['sentinel_count']} "
                f"instrumented hives of {b['hives_counted']}, "
                f"model {b['model_version']}"),
            "evidence_hash": b["evidence_hash"],
            "enforced_by": "HoneyBatch.mintHarvest -> ExceedsYieldEnvelope",
            "tx": b["tx_hash"],
        })

    # --- claim 2: mass was conserved --------------------------------------
    comp = chain.composition(batch_code)
    if comp:
        total_in = sum(c["kg"] for c in comp)
        out_kg = float(on_chain["minted_kg"])
        claims.append({
            "claim": "Mass was conserved through transformation",
            "holds": out_kg <= total_in + 1e-6,
            "arithmetic": (
                f"{out_kg:.1f} kg out <= {total_in:.1f} kg in "
                f"(loss {total_in - out_kg:.1f} kg, "
                f"{(total_in - out_kg) / total_in * 100:.1f}%)"),
            "inputs": comp,
            "enforced_by": "HoneyBatch.blend/processBatch -> MassNotConserved",
        })

    # --- claim 3: packing was bounded, and lab-gated ----------------------
    seals = chain.seal_batch(batch_code)
    if seals:
        claimed_kg = seals["jar_count"] * seals["net_weight_g"] / 1000
        batch_kg = float(on_chain["minted_kg"])
        claims.append({
            "claim": "Jars issued were bounded by honey that exists",
            "holds": claimed_kg <= batch_kg + 1e-6,
            "arithmetic": (
                f"{seals['jar_count']} jars x {seals['net_weight_g']} g = "
                f"{claimed_kg:.1f} kg <= {batch_kg:.1f} kg in the batch"),
            "seal_root": seals["seal_root"],
            "enforced_by": "SealRegistry.issueSeals -> OverPacking",
        })
        lab = [a for a in attestations if a["kind"] == "lab_report"]
        claims.append({
            "claim": "No seals were issued without an independent lab pass",
            "holds": bool(lab),
            "arithmetic": (
                f"{len(lab)} passing report(s) from "
                f"{', '.join(a['issuer'] or '?' for a in lab)}"
                if lab else "no lab report found"),
            "enforced_by": "SealRegistry.issueSeals -> NoPassingLabReport",
            "doc_hashes": [a["doc_hash"] for a in lab],
        })

    # --- optional: Merkle path for one jar --------------------------------
    jar = None
    if serial:
        from . import seals as sealkit, store
        row = store.seal_by_serial(conn, serial)
        if row:
            serials = store.sibling_serials(conn, row["batch_id"])
            with conn.cursor() as cur:
                cur.execute("""SELECT leaf_hex FROM seals WHERE batch_id = %s
                               AND leaf_hex IS NOT NULL ORDER BY serial""",
                            (row["batch_id"],))
                leaves = [bytes.fromhex(r["leaf_hex"][2:]) for r in cur.fetchall()]
            if len(leaves) == len(serials) and serial in serials:
                idx = serials.index(serial)
                path = sealkit.merkle_proof(leaves, idx)
                jar = {
                    "serial": serial,
                    "leaf": "0x" + leaves[idx].hex(),
                    "merkle_path": ["0x" + p.hex() for p in path],
                    "root": seals["seal_root"] if seals else None,
                    "verify_with": "SealRegistry.verifySeal(batchId, serial, secret, path)",
                    "note": ("The secret is under the scratch-off and is not "
                             "included here -- that is the point of it."),
                }

    return {
        "batch_code": batch_code,
        "chain": {
            "network": chain.network,
            "chain_id": chain.chain_id,
            "contracts": chain.book["addresses"],
            "rpc_hint": "any node on this chain can re-read every value below",
        },
        "gs1": {
            "lot_gtin": b["gtin"] if b else None,
            "jar_gtin": b["jar_gtin"] if b else None,
            "lgtin_urn": gs1.lgtin_urn(b["gtin"], batch_code) if b and b["gtin"] else None,
        },
        "origin": {
            "apiary": b["apiary"] if b else None,
            "district": b["district"] if b else None,
            "state": b["state"] if b else None,
            "season": b["season"] if b else None,
        } if b else None,
        "on_chain_state": on_chain,
        "claims": claims,
        "all_claims_hold": all(c["holds"] for c in claims) if claims else False,
        "custody": [
            {"step": c["kind"], "kg": float(c["qty_kg"]),
             "loss_kg": float(c["loss_kg"] or 0),
             "from": c["from_name"], "to": c["to_name"],
             "at": c["ts"].isoformat() if c["ts"] else None, "tx": c["tx_hash"]}
            for c in custody
        ],
        "attestations": [
            {"kind": a["kind"], "issuer": a["issuer"], "doc_hash": a["doc_hash"],
             "summary": a["summary"], "tx": a["tx_hash"]}
            for a in attestations
        ],
        "jar": jar,
        "how_to_verify_independently": (
            "Run scripts/verify_ledger.py. It reads only from chain state at the "
            "addresses above and re-derives every claim, so an error in our "
            "database cannot make a broken ledger appear sound."
        ),
    }
