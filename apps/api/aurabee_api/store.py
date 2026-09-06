"""Postgres persistence shared by the API and the demo scripts.

The chain is authoritative for *quantities and custody*; Postgres holds the
human-readable context around them -- names, districts, photos, lab documents,
scan history. The verify page needs both, so both write through here rather
than each growing its own SQL.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, datetime, timezone

import psycopg
from psycopg.rows import dict_row

from aurabee_schema import gs1

log = logging.getLogger("aurabee.store")


def dsn() -> str:
    raw = os.getenv("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL not set")
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def connect(autocommit: bool = True) -> psycopg.Connection:
    return psycopg.connect(dsn(), autocommit=autocommit, row_factory=dict_row)


# --------------------------------------------------------------------------
# GS1 identifier allocation
# --------------------------------------------------------------------------
def allocate_gln(conn, *, table: str, row_id: str) -> str:
    """Allocate and persist a GLN for an actor or apiary. Idempotent."""
    if table not in ("actors", "apiaries"):
        raise ValueError(f"no GLN column on {table}")
    with conn.cursor() as cur:
        cur.execute(f"SELECT gln FROM {table} WHERE id = %s", (row_id,))
        row = cur.fetchone()
        if row and row["gln"]:
            return row["gln"]
        cur.execute("SELECT nextval('gs1_location_ref_seq') AS ref")
        gln = gs1.gln13(cur.fetchone()["ref"])
        cur.execute(f"UPDATE {table} SET gln = %s WHERE id = %s", (gln, row_id))
        return gln


def allocate_gtin(conn, *, batch_code: str, column: str = "gtin") -> str:
    """Allocate and persist a GTIN for a batch lot or its retail jar."""
    if column not in ("gtin", "jar_gtin"):
        raise ValueError(f"unexpected GTIN column {column}")
    with conn.cursor() as cur:
        cur.execute(f"SELECT {column} FROM batches WHERE batch_code = %s", (batch_code,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"unknown batch {batch_code}")
        if row[column]:
            return row[column]
        cur.execute("SELECT nextval('gs1_item_ref_seq') AS ref")
        gtin = gs1.gtin14(cur.fetchone()["ref"])
        cur.execute(f"UPDATE batches SET {column} = %s WHERE batch_code = %s",
                    (gtin, batch_code))
        return gtin


def allocate_sscc(conn) -> str:
    """A logistic unit -- one drum in transit."""
    with conn.cursor() as cur:
        cur.execute("SELECT nextval('gs1_sscc_ref_seq') AS ref")
        return gs1.sscc18(cur.fetchone()["ref"])


# --------------------------------------------------------------------------
# batches
# --------------------------------------------------------------------------
def upsert_batch(
    conn,
    *,
    batch_code: str,
    apiary_id: str | None,
    owner_actor_id: str,
    kind: str,
    declared_kg: float,
    current_kg: float,
    floral_source: str | None,
    harvest_date: date | None,
    status: str = "minted",
    surplus_kg: float = 0.0,
    tx_hash: str | None = None,
    envelope_id: str | None = None,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO batches (batch_code, apiary_id, owner_actor_id, envelope_id,
                                 kind, declared_kg, current_kg, floral_source,
                                 harvest_date, status, unverified_surplus_kg, tx_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (batch_code) DO UPDATE SET
                current_kg = EXCLUDED.current_kg,
                status     = EXCLUDED.status,
                tx_hash    = COALESCE(EXCLUDED.tx_hash, batches.tx_hash)
            RETURNING id::text
            """,
            (batch_code, apiary_id, owner_actor_id, envelope_id, kind, declared_kg,
             current_kg, floral_source, harvest_date, status, surplus_kg, tx_hash),
        )
        return cur.fetchone()["id"]


def record_custody(
    conn, *, batch_code: str, kind: str, from_actor_id: str | None,
    to_actor_id: str | None, qty_kg: float, loss_kg: float = 0.0,
    tx_hash: str | None = None, meta: dict | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM batches WHERE batch_code = %s", (batch_code,))
        row = cur.fetchone()
        if not row:
            log.warning("custody event for unknown batch %s", batch_code)
            return
        cur.execute(
            """INSERT INTO custody_events
                 (batch_id, kind, from_actor_id, to_actor_id, qty_kg, loss_kg, tx_hash, meta)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (row["id"], kind, from_actor_id, to_actor_id, qty_kg, loss_kg,
             tx_hash, psycopg.types.json.Jsonb(meta or {})),
        )


def record_composition(conn, *, blend_code: str, parts: list[dict]) -> None:
    """parts: [{source_batch_code, kg, origin_state, origin_district}]"""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM batches WHERE batch_code = %s", (blend_code,))
        blend = cur.fetchone()
        if not blend:
            return
        total = sum(p["kg"] for p in parts) or 1.0
        for p in parts:
            cur.execute("SELECT id FROM batches WHERE batch_code = %s",
                        (p["source_batch_code"],))
            src = cur.fetchone()
            if not src:
                continue
            cur.execute(
                """INSERT INTO batch_composition
                     (blend_batch_id, source_batch_id, kg, pct,
                      origin_country, origin_state, origin_district)
                   VALUES (%s,%s,%s,%s,'IN',%s,%s)
                   ON CONFLICT (blend_batch_id, source_batch_id) DO UPDATE
                     SET kg = EXCLUDED.kg, pct = EXCLUDED.pct""",
                (blend["id"], src["id"], p["kg"], round(p["kg"] / total * 100, 2),
                 p.get("origin_state"), p.get("origin_district")),
            )


def record_attestation(conn, *, batch_code: str, kind: str, issuer_actor_id: str,
                       doc_hash: str, summary: dict, tx_hash: str | None = None,
                       doc_key: str | None = None) -> None:
    """Store a lab report or certificate against a batch."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM batches WHERE batch_code = %s", (batch_code,))
        row = cur.fetchone()
        if not row:
            log.warning("attestation for unknown batch %s", batch_code)
            return
        cur.execute(
            """INSERT INTO attestations (subject_type, subject_id, kind,
                                         issuer_actor_id, doc_hash, doc_key,
                                         summary, tx_hash)
               VALUES ('batch', %s, %s, %s, %s, %s, %s, %s)""",
            (row["id"], kind, issuer_actor_id, doc_hash, doc_key,
             psycopg.types.json.Jsonb(summary), tx_hash),
        )


# --------------------------------------------------------------------------
# seals
# --------------------------------------------------------------------------
def secret_hash(secret: str) -> str:
    """Stored instead of the secret itself.

    A database dump must not hand an attacker the codes needed to forge
    verification for every jar in circulation. The seal secret is short by
    necessity -- a person reads it off a scratch panel -- so it is peppered with
    a server-side key before hashing rather than hashed bare.
    """
    return hashlib.blake2b(
        secret.encode(), key=_seal_pepper()[:64], digest_size=32).hexdigest()


def _seal_pepper() -> bytes:
    """Fail closed: an unset pepper must never silently fall back to a default.

    This key is the only thing standing between a stolen database dump and the
    ability to forge verification for every jar in circulation. A hardcoded
    fallback would be published in this repository, so an operator who forgets
    to set it would get a pepper an attacker can simply read -- which is the
    exact attack the peppering exists to prevent, restored in full.

    So the default is to refuse. Development opts in explicitly.
    """
    key = os.getenv("SEAL_HMAC_KEY")
    if key:
        return key.encode()
    if os.getenv("AURABEE_ENV", "").lower() == "development":
        return b"dev-seal-pepper-not-for-any-real-deployment"
    raise RuntimeError(
        "SEAL_HMAC_KEY is not set. Generate one with "
        "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"` and put "
        "it in the environment. Set AURABEE_ENV=development to use the "
        "throwaway development pepper instead.")


def persist_seals(conn, *, batch_code: str, seals, net_weight_g: int,
                  tx_hash: str | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM batches WHERE batch_code = %s", (batch_code,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"unknown batch {batch_code}")
        batch_id = row["id"]
        cur.executemany(
            """INSERT INTO seals (serial, batch_id, secret_hash, leaf_hex,
                                  net_weight_g, tx_hash)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (serial) DO NOTHING""",
            [(s.serial, batch_id, secret_hash(s.secret), s.leaf_hex,
              net_weight_g, tx_hash) for s in seals],
        )
        return len(seals)


def seal_by_serial(conn, serial: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.*, b.batch_code, b.id::text AS batch_uuid
               FROM seals s JOIN batches b ON b.id = s.batch_id
               WHERE s.serial = %s""",
            (serial,),
        )
        return cur.fetchone()


def sibling_serials(conn, batch_id: str) -> list[str]:
    """All serials in a packing run, ordered, so the Merkle proof can be rebuilt.

    We store the tree's leaves implicitly rather than the tree itself: the leaf
    order is serial order, which is deterministic, so the proof is derivable
    whenever it is needed instead of being stored 240 times over.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT serial FROM seals WHERE batch_id = %s ORDER BY serial", (batch_id,)
        )
        return [r["serial"] for r in cur.fetchall()]


def record_scan(conn, *, seal_id: str, geohash: str | None, ip_hash: str | None,
                ua_hash: str | None, secret_ok: bool | None,
                anomalous: bool = False, reason: str | None = None) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO scans (seal_id, geohash, ip_hash, ua_hash, secret_ok,
                                  is_anomalous, anomaly_reason)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               -- aliased: callers merge this into the seal dict, and a bare
               -- "id" would clobber the seal's UUID with the scan's integer id
               RETURNING id AS scan_id, ts AS scan_ts""",
            (seal_id, geohash, ip_hash, ua_hash, secret_ok, anomalous, reason),
        )
        scan = cur.fetchone()
        cur.execute(
            """UPDATE seals
                  SET scan_count    = scan_count + 1,
                      first_scan_at = COALESCE(first_scan_at, now()),
                      first_scan_geohash = COALESCE(first_scan_geohash, %s),
                      status = CASE WHEN status = 'issued' THEN 'scanned' ELSE status END
                WHERE id = %s
            RETURNING scan_count, first_scan_at, first_scan_geohash""",
            (geohash, seal_id),
        )
        return {**scan, **cur.fetchone()}


def open_fraud_case(conn, *, kind: str, subject_type: str, subject_id: str,
                    score: float, detail: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO fraud_cases (kind, subject_type, subject_id, score, detail)
               VALUES (%s,%s,%s,%s,%s)""",
            (kind, subject_type, subject_id, score, psycopg.types.json.Jsonb(detail)),
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
