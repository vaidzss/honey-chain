"""Seed one realistic cluster.

Creates the Sitapur (UP) reference cluster: a collection centre, a handful of
beekeepers with apiaries and hives, a processor, a lab and a brand -- plus
sentinel nodes on roughly 15% of hives, which is the deployment economics the
whole scaling argument rests on (docs/07-deployment.md).

    python scripts/seed.py            # create
    python scripts/seed.py --reset    # wipe and recreate

Idempotent: re-running without --reset leaves existing rows alone.
"""

from __future__ import annotations

import argparse
import os
import random
import secrets
import sys
from datetime import date, timedelta

import psycopg
from dotenv import load_dotenv

load_dotenv()

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
if not DSN:
    sys.exit("DATABASE_URL not set (copy .env.example to .env)")

STATE, DISTRICT = "Uttar Pradesh", "Sitapur"
LAT, LON = 27.5700, 80.6800
FLORA = ["mustard", "acacia", "multiflora"]
SENTINEL_RATIO = 0.15   # instrument ~15% of hives, extrapolate the rest

BEEKEEPERS = [
    # (name, phone, hives, village offset)
    ("Ramesh Verma",   "+919876500001", 14, (0.010, -0.008)),
    ("Sunita Devi",    "+919876500002", 10, (-0.014, 0.011)),
    ("Imran Ansari",   "+919876500003", 18, (0.021, 0.017)),
    ("Kamla Prajapati","+919876500004",  8, (-0.006, -0.019)),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete existing rows first")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    with psycopg.connect(DSN, autocommit=False) as conn, conn.cursor() as cur:
        if args.reset:
            # order matters: telemetry references nodes, nodes reference hives
            cur.execute("""
                TRUNCATE telemetry, scans, seals, batch_composition, custody_events,
                         batches, yield_envelopes, inspections, hive_events,
                         attestations, fraud_cases, nodes, hives, apiaries,
                         clusters, actors RESTART IDENTITY CASCADE
            """)
            print("wiped existing rows")

        cur.execute("SELECT count(*) FROM actors")
        if cur.fetchone()[0] and not args.reset:
            print("already seeded; pass --reset to recreate")
            return 0

        # ---------------------------------------------------------- actors --
        def actor(kind, name, phone=None, **kw):
            cur.execute(
                """INSERT INTO actors (kind, name, phone, district, state, wallet_address, meta)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (kind, name, phone, DISTRICT, STATE,
                 "0x" + secrets.token_hex(20), psycopg.types.json.Jsonb(kw)),
            )
            return cur.fetchone()[0]

        centre_id = actor("collection_centre", "KVIC Collection Centre, Sitapur",
                          "+919876510001", kvic_code="KVIC-UP-SIT-01")
        coord_id  = actor("beekeeper", "Anil Yadav (Madhu Mitra)", "+919876510002",
                          role="cluster_coordinator")
        fpo_id    = actor("fpo", "Sitapur Madhu Producer Company Ltd", "+919876510003")
        proc_id   = actor("processor", "Awadh Honey Processing Unit", "+919876510004",
                          fssai="12722018000123")
        lab_id    = actor("lab", "FARE Labs Pvt Ltd", "+919876510005",
                          nabl="TC-5678", scope=["C3/C4 IRMS", "moisture", "HMF"])
        brand_id  = actor("brand", "Awadh Naturals", "+919876510006")
        actor("regulator", "KVIC Honey Mission Cell", "+919876510007")
        actor("admin", "AuraBee Platform Admin", "+919876510008")

        # --------------------------------------------------------- cluster --
        cur.execute(
            """INSERT INTO clusters (code, name, state, district, centre_actor_id, coordinator_id)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            ("CL-UP-SIT-01", "Sitapur Honey Cluster", STATE, DISTRICT, centre_id, coord_id),
        )
        cluster_id = cur.fetchone()[0]

        # ------------------------------------------- beekeepers and hives --
        node_n, total_hives, total_sentinels = 0, 0, 0
        for name, phone, n_hives, (dlat, dlon) in BEEKEEPERS:
            bk_id = actor("beekeeper", name, phone)
            cur.execute(
                """INSERT INTO apiaries (actor_id, cluster_id, name, lat, lon, district,
                                         state, flora_profile, hive_count)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (bk_id, cluster_id, f"{name.split()[0]} Apiary",
                 round(LAT + dlat, 5), round(LON + dlon, 5),
                 DISTRICT, STATE, FLORA, n_hives),
            )
            ap_id = cur.fetchone()[0]

            n_sentinel = max(1, round(n_hives * SENTINEL_RATIO))
            sentinel_idx = set(rng.sample(range(1, n_hives + 1), n_sentinel))

            for i in range(1, n_hives + 1):
                is_sentinel = i in sentinel_idx
                cur.execute(
                    """INSERT INTO hives (apiary_id, label, species, source,
                                          installed_on, is_sentinel)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (ap_id, f"Petti {i}", "apis_mellifera",
                     "kvic_distributed" if i <= n_hives * 0.7 else "own",
                     date(2025, 10, 1) + timedelta(days=rng.randint(0, 300)),
                     is_sentinel),
                )
                hive_id = cur.fetchone()[0]
                total_hives += 1

                if is_sentinel:
                    node_n += 1
                    total_sentinels += 1
                    cur.execute(
                        """INSERT INTO nodes (id, hive_id, node_type, firmware_version, hmac_key)
                           VALUES (%s,%s,'sim','sim-0.1.0',%s)""",
                        (f"HN-UP-SIT-{node_n:04d}", hive_id, secrets.token_hex(16)),
                    )

        conn.commit()

    print(f"seeded cluster CL-UP-SIT-01")
    print(f"  beekeepers      : {len(BEEKEEPERS)}")
    print(f"  hives           : {total_hives}")
    print(f"  sentinel nodes  : {total_sentinels} ({total_sentinels/total_hives:.0%} coverage)")
    print(f"  supply chain    : centre, FPO, processor, lab, brand, regulator")
    print("\nnext: python -m aurabee_api.ingest   (then run the simulator with --from-db)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
