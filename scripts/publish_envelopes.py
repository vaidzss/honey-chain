"""Derive a yield envelope per apiary from telemetry, publish it on chain.

    python scripts/publish_envelopes.py --season 2026-mustard \
        --start 2026-10-15 --end 2027-03-15

This is the step that turns hive data into an enforceable ceiling. Everything
after it -- the mint that succeeds, the mint that reverts -- follows from what
this script writes.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from aurabee_api.chain import ChainError, get_chain  # noqa: E402
from aurabee_api.yields import estimate_envelope, persist_envelope  # noqa: E402

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2026-mustard")
    ap.add_argument("--start", default="2026-10-15")
    ap.add_argument("--end", default="2027-03-15")
    ap.add_argument("--dry-run", action="store_true", help="compute but do not publish")
    args = ap.parse_args()

    if len(args.season.encode()) > 32:
        sys.exit("season label must fit in 32 bytes")

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    chain = None if args.dry_run else get_chain()
    if chain and not chain.is_connected():
        sys.exit(f"no chain at {chain.rpc_url}")

    published = skipped = 0
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.id::text, a.name FROM apiaries a
                   ORDER BY a.created_at"""
            )
            apiaries = cur.fetchall()

        print(f"season {args.season}  window {start} .. {end}\n")
        header = f"{'apiary':<20} {'hives':>5} {'sent':>5} {'cov':>5} " \
                 f"{'P10':>8} {'P50':>8} {'P90 (cap)':>10}"
        print(header)
        print("-" * len(header))

        for apiary_id, name in apiaries:
            try:
                env = estimate_envelope(conn, apiary_id, args.season, start, end)
            except ValueError as exc:
                print(f"{name[:20]:<20} skipped: {exc}")
                skipped += 1
                continue

            print(f"{name[:20]:<20} {env.hives_counted:>5} {env.sentinel_count:>5} "
                  f"{env.coverage_ratio*100:>4.0f}% {env.p10_kg:>8.1f} "
                  f"{env.p50_kg:>8.1f} {env.p90_kg:>10.1f}")

            tx_hash = None
            if chain:
                try:
                    res = chain.publish_envelope(
                        apiary_id=apiary_id,
                        season=args.season,
                        p90_kg=env.p90_kg,
                        p50_kg=env.p50_kg,
                        window_start=int(
                            __import__("datetime").datetime.combine(
                                start, __import__("datetime").time()).timestamp()),
                        window_end=int(
                            __import__("datetime").datetime.combine(
                                end, __import__("datetime").time()).timestamp()),
                        evidence_hash=env.evidence_hash,
                        coverage_bps=env.coverage_bps,
                        model_version=env.model_version,
                    )
                    tx_hash = res["tx_hash"]
                    published += 1
                except ChainError as exc:
                    print(f"  chain publish failed: {exc.reason or exc}")

            persist_envelope(conn, env, tx_hash)

    print(f"\npublished {published} envelope(s) on chain, {skipped} skipped")
    if published:
        print("mint ceilings are now live; try scripts/demo_flow.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
