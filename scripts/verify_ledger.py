"""Independently re-verify the ledger, reading ONLY from the chain.

    python scripts/verify_ledger.py [--batch AB-SIT-1234-B]

## Why this exists

Everything else in the system reads our database. If the database were wrong --
or dishonest -- every dashboard would agree with it. This script deliberately
does not: it takes contract addresses from the deployment book and re-derives
each invariant from on-chain state alone.

That is what makes the claims checkable by someone who does not trust us. A
judge, an auditor or a competitor can run it against the same chain and get the
same answer, or a different one, and either way learns something.

The database is used for exactly one thing: to enumerate which batch codes to
look up. Every *value* comes from the chain.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
load_dotenv(ROOT / ".env")

from aurabee_api.chain import get_chain, to_bytes32  # noqa: E402
from aurabee_api import store  # noqa: E402

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
TOL = 1e-6


def ok(msg):   print(f"    {GREEN}HOLDS{RESET}    {msg}")
def bad(msg):  print(f"    {RED}BROKEN{RESET}   {msg}")
def info(msg): print(f"    {DIM}{msg}{RESET}")


def verify_batch(chain, code: str) -> tuple[int, int]:
    """Returns (checks_passed, checks_failed) for one batch."""
    b = chain.batch(code)
    if not b:
        return 0, 0

    print(f"\n{BOLD}{code}{RESET}  {DIM}({b['kind']}, {b['minted_kg']} kg, "
          f"{b['status']}){RESET}")
    passed = failed = 0

    # ---- 1. issuance bounded by the oracle envelope ---------------------
    if b["apiary_id"] and b["season"]:
        cap = chain.envelope(b["apiary_id"], b["season"])
        if cap:
            minted = chain.batches.functions.mintedInSeason(
                to_bytes32(b["apiary_id"]), to_bytes32(b["season"])).call() / 1000
            if minted <= cap["max_kg"] + TOL:
                ok(f"issuance bounded: {minted:.1f} kg minted this season "
                   f"<= {cap['max_kg']:.1f} kg ceiling")
                info(f"ceiling from {cap['model_version']}, "
                     f"coverage {cap['coverage_bps'] / 100:.0f}%, "
                     f"evidence {cap['evidence_hash'][:22]}...")
                passed += 1
            else:
                bad(f"issuance EXCEEDS ceiling: {minted:.1f} > {cap['max_kg']:.1f} kg")
                failed += 1
        else:
            info("no envelope on chain for this apiary/season")

    # ---- 2. mass conserved through transformation ------------------------
    comp = chain.composition(code)
    if comp:
        total_in = sum(c["kg"] for c in comp)
        out_kg = float(b["minted_kg"])
        if out_kg <= total_in + TOL:
            loss_pct = (total_in - out_kg) / total_in * 100 if total_in else 0
            ok(f"mass conserved: {out_kg:.1f} kg out <= {total_in:.1f} kg in "
               f"(loss {loss_pct:.1f}%)")
            for c in comp:
                info(f"  input {c['kg']:>7.1f} kg  {c['source_batch']}")
            passed += 1
        else:
            bad(f"mass CREATED: {out_kg:.1f} kg out > {total_in:.1f} kg in")
            failed += 1

    # ---- 3. packing bounded, and lab-gated -------------------------------
    seals = chain.seal_batch(code)
    if seals:
        claimed = seals["jar_count"] * seals["net_weight_g"] / 1000
        if claimed <= float(b["minted_kg"]) + TOL:
            ok(f"packing bounded: {seals['jar_count']} jars x "
               f"{seals['net_weight_g']} g = {claimed:.1f} kg "
               f"<= {b['minted_kg']:.1f} kg")
            passed += 1
        else:
            bad(f"OVER-PACKED: {claimed:.1f} kg of jars from a "
                f"{b['minted_kg']:.1f} kg batch")
            failed += 1

        has_lab = chain.has_valid_lab_report(code)
        if has_lab:
            ok("lab gate: a valid passing report exists for this batch")
            passed += 1
        else:
            bad("seals exist but NO valid lab report is on chain")
            failed += 1

    return passed, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="verify one batch instead of all")
    args = ap.parse_args()

    chain = get_chain()
    if not chain.is_connected():
        sys.exit(f"no chain at {chain.rpc_url}")
    if not chain.contracts_live():
        sys.exit("the address book points at addresses with no code; redeploy")

    print(f"{BOLD}Independent ledger verification{RESET}")
    print(f"  network   {chain.network} (chain id {chain.chain_id})")
    for name, addr in chain.book["addresses"].items():
        print(f"  {name:<13} {addr}")
    print(f"\n{DIM}Every value below is read from chain state at those "
          f"addresses.{RESET}")
    print(f"{DIM}The database supplies only the list of batch codes to look "
          f"up.{RESET}")

    if args.batch:
        codes = [args.batch]
    else:
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT batch_code FROM batches ORDER BY created_at")
            codes = [r["batch_code"] for r in cur.fetchall()]

    total_p = total_f = checked = 0
    for code in codes:
        p, f = verify_batch(chain, code)
        if p or f:
            checked += 1
        total_p += p
        total_f += f

    print(f"\n{'=' * 64}")
    print(f"{checked} batches, {total_p} invariant checks held, "
          f"{total_f} broken")
    if total_f:
        print(f"{RED}LEDGER INTEGRITY FAILURE{RESET}")
        return 1
    print(f"{GREEN}Every invariant held, verified against chain state alone.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
