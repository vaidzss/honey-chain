"""AuraBee HTTP API.

    uvicorn aurabee_api.main:app --reload --port 8000

The consumer verification endpoints are the ones that matter most here: they
are hit by a phone camera in a shop, on a bad connection, by someone who has
never heard of a blockchain. They must be fast, they must work without
JavaScript frameworks doing anything clever, and they must be honest about
uncertainty.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(find_dotenv(usecwd=True))

from . import (beekeeper as bk, consoles, epcis, health as health_svc,
               proof as proof_svc, risk, store, verify as verify_svc)  # noqa: E402
from .chain import ChainError, get_chain  # noqa: E402

log = logging.getLogger("aurabee.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

_chain = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _chain
    try:
        _chain = get_chain()
        if not _chain.is_connected():
            log.warning("chain configured but unreachable at %s", _chain.rpc_url)
        elif not _chain.contracts_live():
            log.warning(
                "chain reachable but no contract code at %s -- the address book "
                "is stale. Re-run `npm run chain:deploy` and `scripts/sync_chain.py`.",
                _chain.book["addresses"]["HoneyBatch"])
        else:
            log.info("chain %s connected at %s", _chain.network, _chain.rpc_url)
    except ChainError as exc:
        # The API stays up without a chain: the dashboards and hive views still
        # work, and only the verification endpoints degrade. Failing to boot
        # entirely because an RPC node is down would be worse.
        log.warning("chain unavailable: %s", exc)
        _chain = None
    yield


app = FastAPI(
    title="AuraBee / Honey Chain",
    version="0.1.0",
    description="Blockchain + AI + IoT trust layer for Indian honey (SIH26021)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


def chain_or_503():
    if _chain is None or not _chain.is_connected():
        raise HTTPException(503, "chain unavailable")
    return _chain


# --------------------------------------------------------------------------
@app.get("/health")
def health():
    db_ok = True
    try:
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("db health check failed: %s", exc)
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "chain": bool(_chain and _chain.is_connected()),
        "contracts": bool(_chain and _chain.contracts_live()),
        "network": _chain.network if _chain else None,
    }


# --------------------------------------------------------------------------
# consumer verification
# --------------------------------------------------------------------------
class ScanBody(BaseModel):
    secret: str | None = None
    geohash: str | None = None


@app.get("/api/verify/{serial}")
def verify(
    serial: str,
    request: Request,
    secret: str | None = Query(None, description="code under the scratch-off"),
    geohash: str | None = Query(None, max_length=12),
    record: bool = Query(True, description="set false to inspect without logging a scan"),
):
    """The consumer payload. Works with or without the hidden code.

    Without it we can say the serial exists and show its journey; only with it
    can we say *verified*, because only then is there a Merkle proof against
    the on-chain root.
    """
    with store.connect() as conn:
        return verify_svc.verify_serial(
            conn, _chain, serial,
            secret=secret, geohash=geohash,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            record=record,
        )


@app.post("/api/verify/{serial}/scan")
def scan(serial: str, body: ScanBody, request: Request):
    with store.connect() as conn:
        return verify_svc.verify_serial(
            conn, _chain, serial,
            secret=body.secret, geohash=body.geohash,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            record=True,
        )


# --------------------------------------------------------------------------
# batches and envelopes
# --------------------------------------------------------------------------
@app.get("/api/batch/{batch_code}")
def batch(batch_code: str):
    chain = chain_or_503()
    on_chain = chain.batch(batch_code)
    if not on_chain:
        raise HTTPException(404, f"batch {batch_code} not on chain")
    return {
        "batch_code": batch_code,
        "chain": on_chain,
        "composition": chain.composition(batch_code),
        "seals": chain.seal_batch(batch_code),
    }


@app.get("/api/batch/{batch_code}/epcis")
def batch_epcis(batch_code: str, max_seals: int = Query(50, le=1000)):
    """The batch as a GS1 EPCIS 2.0 JSON-LD document.

    This is the interchange format a buyer, customs system or downstream ERP
    can read without any AuraBee-specific integration. The chain stays the
    enforcement layer; this describes what happened in the standard vocabulary.
    """
    try:
        with store.connect() as conn:
            return epcis.document_for_batch(conn, batch_code, max_seals=max_seals)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/batch/{batch_code}/risk")
def batch_risk(batch_code: str):
    """Fraud risk score and the laboratory test tier it indicates.

    The score allocates a test; it is not an accusation, and every component
    comes with the reason it fired.
    """
    try:
        with store.connect() as conn:
            a = risk.assess_batch(conn, batch_code)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "batch_code": a.batch_code,
        "score": a.score,
        "tier": a.tier,
        "tier_label": a.tier_label,
        "indicative_cost_inr": a.tier_cost,
        "reasons": a.reasons,
        "signals": [
            {"name": s.name, "value": round(s.value, 3),
             "weight": s.weight, "contribution": round(s.contribution, 4)}
            for s in sorted(a.signals, key=lambda x: x.contribution, reverse=True)
        ],
    }


@app.get("/api/admin/testing-plan")
def testing_plan(budget_inr: int = Query(10000, ge=0, le=10_000_000)):
    """Spend a fixed laboratory budget where it buys the most information.

    This is the practical answer to "who pays for testing": nobody can afford
    to test every batch, so the model decides which ones are worth it.
    """
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT batch_code FROM batches WHERE status <> 'void' "
                    "ORDER BY created_at DESC LIMIT 500")
        codes = [r["batch_code"] for r in cur.fetchall()]
        assessments = []
        for code in codes:
            try:
                assessments.append(risk.assess_batch(conn, code))
            except ValueError:
                continue
    return risk.allocate_testing_budget(assessments, budget_inr)


@app.get("/api/apiary/{apiary_id}/envelope")
def envelope(apiary_id: str, season: str = Query(...)):
    chain = chain_or_503()
    env = chain.envelope(apiary_id, season)
    if not env:
        raise HTTPException(404, "no envelope published for this apiary and season")
    env["remaining_kg"] = chain.remaining_kg(apiary_id, season)
    return env


# --------------------------------------------------------------------------
# administration
# --------------------------------------------------------------------------
@app.get("/api/admin/overview")
def overview():
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM actors  WHERE kind='beekeeper') AS beekeepers,
                 (SELECT count(*) FROM hives)                          AS hives,
                 (SELECT count(*) FROM hives WHERE is_sentinel)        AS sentinels,
                 (SELECT count(*) FROM telemetry)                      AS telemetry_rows,
                 (SELECT count(*) FROM batches)                        AS batches,
                 (SELECT count(*) FROM seals)                          AS seals,
                 (SELECT count(*) FROM scans)                          AS scans,
                 (SELECT count(*) FROM fraud_cases WHERE status='open') AS open_cases"""
        )
        row = cur.fetchone()
        cur.execute(
            """SELECT round(avg(coverage_ratio)::numeric, 4) AS avg_coverage,
                      round(sum(p90_kg)::numeric, 1)         AS total_ceiling_kg
               FROM yield_envelopes"""
        )
        env = cur.fetchone()
    return {**row, **(env or {})}


@app.get("/api/admin/fraud")
def fraud_cases(status: str = Query("open"), limit: int = Query(50, le=200)):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id::text, kind, subject_type, subject_id::text, score,
                      status, detail, opened_at
               FROM fraud_cases WHERE status = %s
               ORDER BY opened_at DESC LIMIT %s""",
            (status, limit),
        )
        return {"cases": cur.fetchall()}


class InspectionBody(BaseModel):
    client_uuid: str
    hive_id: str
    actor_id: str
    ts: str
    queen_seen: bool | None = None
    brood_frames: float | None = None
    honey_frames: float | None = None
    pests: list[str] = []
    varroa_count: int | None = None
    fed: bool = False
    treated_with: str | None = None
    notes: str | None = None


@app.get("/api/beekeeper/{actor_id}/dashboard")
def bk_dashboard(actor_id: str, assess: bool = Query(False)):
    """One request, one screen. Round trips are expensive on 2G."""
    try:
        with store.connect() as conn:
            return bk.dashboard(conn, actor_id, assess=assess)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/beekeeper/{actor_id}/harvest-context")
def bk_harvest_context(actor_id: str):
    """What may be declared, and why -- shown before the beekeeper types."""
    with store.connect() as conn:
        return bk.harvest_context(conn, actor_id)


@app.post("/api/beekeeper/inspections")
def bk_inspection(body: InspectionBody):
    """Idempotent by client_uuid, because offline clients replay."""
    with store.connect() as conn:
        return bk.log_inspection(
            conn, client_uuid=body.client_uuid, hive_id=body.hive_id,
            actor_id=body.actor_id, ts=body.ts, queen_seen=body.queen_seen,
            brood_frames=body.brood_frames, honey_frames=body.honey_frames,
            pests=body.pests, varroa_count=body.varroa_count, fed=body.fed,
            treated_with=body.treated_with, notes=body.notes)


class HarvestBody(BaseModel):
    client_uuid: str
    actor_id: str
    apiary_id: str
    kg: float
    recorded_at: str | None = None


@app.post("/api/beekeeper/harvest")
def bk_harvest(body: HarvestBody):
    """Where the offline queue drains. Idempotent on client_uuid."""
    from datetime import datetime, timezone
    ts = (datetime.fromisoformat(body.recorded_at.replace("Z", "+00:00"))
          if body.recorded_at else datetime.now(timezone.utc))
    with store.connect() as conn:
        return bk.record_harvest(
            conn, _chain, client_uuid=body.client_uuid, actor_id=body.actor_id,
            apiary_id=body.apiary_id, kg=body.kg, recorded_at=ts)


@app.post("/api/beekeeper/{actor_id}/alerts/{event_id}/ack")
def bk_ack(actor_id: str, event_id: str):
    with store.connect() as conn:
        if not bk.acknowledge(conn, event_id, actor_id):
            raise HTTPException(404, "no such open alert for this beekeeper")
    return {"acknowledged": True}


@app.get("/api/fpo/{actor_id}/console")
def fpo_console(actor_id: str):
    """Supply aggregation: members, their headroom, and lots held."""
    try:
        with store.connect() as conn:
            return consoles.fpo_console(conn, actor_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/processor/{actor_id}/console")
def processor_console(actor_id: str):
    """The processor worklist: what is in custody and what is blocking each batch."""
    try:
        with store.connect() as conn:
            return consoles.processor_console(conn, _chain, actor_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/admin/console")
def admin_console():
    """KVIC view: clusters, scheme attribution, fraud and alert concentration."""
    with store.connect() as conn:
        return consoles.admin_console(conn)


@app.get("/api/metrics")
def metrics():
    """Every model metric and system count, for the metrics page.

    Model numbers are read from the .meta.json each training run writes, so the
    page cannot drift from the model actually loaded.
    """
    with store.connect() as conn:
        system = proof_svc.system_metrics(conn)
    return {
        "models": proof_svc.model_metrics(),
        "system": system,
        "chain": {
            "connected": bool(_chain and _chain.is_connected()),
            "contracts_live": bool(_chain and _chain.contracts_live()),
            "network": _chain.network if _chain else None,
            "chain_id": _chain.chain_id if _chain else None,
            "addresses": _chain.book["addresses"] if _chain else {},
        },
    }


@app.get("/api/batch/{batch_code}/proof")
def batch_proof(batch_code: str, serial: str | None = Query(None)):
    """A proof bundle: every claim with its arithmetic, and how to re-check it."""
    chain = chain_or_503()
    try:
        with store.connect() as conn:
            return proof_svc.batch_proof(conn, chain, batch_code, serial)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/model/health")
def health_model_info():
    """What the colony-health model is, and what it is not."""
    return health_svc.model_info()


@app.get("/api/hive/{hive_id}/assess")
def assess_hive(hive_id: str, samples: int = Query(200, ge=48, le=2000)):
    """Current predicted colony state for one hive."""
    try:
        with store.connect() as conn:
            a = health_svc.assess_hive(conn, hive_id, samples)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    if a is None:
        raise HTTPException(404, "not enough telemetry for this hive")
    return a


@app.post("/api/admin/scan-hives")
def scan_hives(samples: int = Query(200, ge=48, le=2000)):
    """Score every sentinel hive and raise alerts. The batch job a scheduler runs."""
    try:
        with store.connect() as conn:
            return health_svc.scan_all(conn, samples)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/admin/hives")
def hives(limit: int = Query(100, le=500)):
    """Latest reading per sentinel hive, with the thermal-decoupling signal the
    health model keys on made explicit."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (t.node_id)
                   t.node_id, h.label, ap.name AS apiary, act.name AS beekeeper,
                   t.ts, t.weight_kg, t.t_in, t.t_out,
                   round((t.t_in - t.t_out)::numeric, 2) AS thermal_delta,
                   (t.features->>'centroid_hz')::float   AS centroid_hz,
                   t.batt_v
            FROM telemetry t
            JOIN hives h     ON h.id = t.hive_id
            JOIN apiaries ap ON ap.id = h.apiary_id
            JOIN actors act  ON act.id = ap.actor_id
            ORDER BY t.node_id, t.ts DESC
            LIMIT %s
            """,
            (limit,),
        )
        return {"hives": cur.fetchall()}
