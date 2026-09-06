"""Mirror seeded actors and apiaries onto the chain.

The database is seeded first (scripts/seed.py), then this pushes the identities
on chain and records the resulting tx hashes back. Idempotent: an actor already
registered is skipped rather than reverting the whole run.

Note the actor addresses are the random ones seed.py generated. That is not a
shortcut -- it is the custodial model working as designed. Beekeepers never hold
keys; the relayer signs on their behalf and passes the actor address explicitly,
so an actor address only has to be a stable identifier, not a controlled account.

    python scripts/sync_chain.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from aurabee_api.chain import ChainError, get_chain, to_bytes32  # noqa: E402

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# DB actor kind -> Registry.Role enum ordinal
ROLE = {
    "beekeeper": 1,
    "collection_centre": 2,
    "fpo": 3,
    "processor": 4,
    "lab": 5,
    "brand": 6,
    "regulator": 7,
    "admin": 8,
}


def main() -> int:
    if not DSN:
        sys.exit("DATABASE_URL not set")
    chain = get_chain()
    if not chain.is_connected():
        sys.exit(f"no chain at {chain.rpc_url}. Start it: npm run chain:node")

    print(f"chain    : {chain.network} (chainId {chain.chain_id})")
    print(f"relayer  : {chain.account.address}\n")

    registered = skipped = 0

    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        # ---------------------------------------------------------- actors --
        cur.execute(
            "SELECT id::text, kind, name, wallet_address FROM actors ORDER BY created_at"
        )
        actors = cur.fetchall()
        for actor_id, kind, name, wallet in actors:
            addr = Web3.to_checksum_address(wallet)
            on_chain_role = chain.registry.functions.roleOf(addr).call()
            if on_chain_role != 0:
                skipped += 1
                continue
            try:
                res = chain.register_actor(addr, ROLE[kind], name[:60])
                cur.execute("UPDATE actors SET tx_hash = %s WHERE id = %s",
                            (res["tx_hash"], actor_id))
                registered += 1
                print(f"  actor   {kind:<18} {name[:28]:<28} {addr[:10]}...")
            except ChainError as exc:
                print(f"  FAILED  {name}: {exc.reason or exc}")

        # -------------------------------------------------------- apiaries --
        cur.execute(
            """SELECT a.id::text, a.name, a.geohash, a.hive_count,
                      act.wallet_address,
                      (SELECT count(*) FROM hives h
                        WHERE h.apiary_id = a.id AND h.is_sentinel) AS sentinels
               FROM apiaries a JOIN actors act ON act.id = a.actor_id
               ORDER BY a.created_at"""
        )
        apiary_rows = cur.fetchall()
        ap_registered = ap_skipped = 0
        for ap_id, name, geohash, hive_count, owner_wallet, sentinels in apiary_rows:
            existing = chain.registry.functions.apiaryOwner(to_bytes32(ap_id)).call()
            if int(existing, 16) != 0:
                ap_skipped += 1
                continue
            try:
                res = chain.register_apiary(
                    ap_id, Web3.to_checksum_address(owner_wallet),
                    geohash or "", hive_count, sentinels)
                cur.execute("UPDATE apiaries SET tx_hash = %s WHERE id = %s",
                            (res["tx_hash"], ap_id))
                ap_registered += 1
                print(f"  apiary  {name[:28]:<28} {hive_count:>3} hives, "
                      f"{sentinels} sentinels")
            except ChainError as exc:
                print(f"  FAILED  apiary {name}: {exc.reason or exc}")

    print(f"\nactors  : {registered} registered, {skipped} already on chain")
    print(f"apiaries: {ap_registered} registered, {ap_skipped} already on chain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
