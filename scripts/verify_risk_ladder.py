"""Check that the risk ladder actually escalates, and for the right reasons.

A scorer that returns plausible-looking numbers but never moves is worse than
no scorer, because it produces false confidence. This applies each risk factor
in turn to a real batch, asserts the score rises and the test tier escalates,
then puts everything back.

    python scripts/verify_risk_ladder.py

Requires the stack and a seeded, demo-run database.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
load_dotenv(ROOT / ".env")

from aurabee_api import risk, store  # noqa: E402

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def show(label: str, a: risk.RiskAssessment, baseline: float | None = None) -> None:
    delta = f"  ({a.score - baseline:+.3f})" if baseline is not None else ""
    print(f"  {label:<34} score {a.score:.3f}{delta:<11} tier {a.tier:<9} "
          f"Rs {a.tier_cost}")
    if a.reasons:
        print(f"     {DIM}{a.reasons[0][:88]}{RESET}")


def _cleanup(conn, batch_id: str, owner: str, surplus: float = 0.0) -> None:
    """Remove every mutation this script makes. Run before and after."""
    with conn.cursor() as cur:
        cur.execute("UPDATE custody_events SET seal_intact = NULL WHERE batch_id = %s",
                    (batch_id,))
        cur.execute("DELETE FROM fraud_cases WHERE subject_id = %s "
                    "AND detail->>'test' = 'true'", (owner,))
        cur.execute("DELETE FROM custody_events WHERE batch_id = %s AND kind = 'process' "
                    "AND loss_kg > 0 AND tx_hash IS NULL", (batch_id,))
        cur.execute("UPDATE batches SET unverified_surplus_kg = %s WHERE id = %s",
                    (surplus, batch_id))


def main() -> int:
    conn = store.connect()
    failures = 0

    with conn.cursor() as cur:
        # must have a transfer event, or the transport-seal signal cannot fire
        cur.execute("""SELECT b.batch_code, b.id::text, b.owner_actor_id::text AS owner
                       FROM batches b
                       WHERE b.kind = 'raw' AND b.envelope_id IS NOT NULL
                         AND EXISTS (SELECT 1 FROM custody_events ce
                                     WHERE ce.batch_id = b.id AND ce.kind = 'transfer')
                       ORDER BY b.created_at DESC LIMIT 1""")
        row = cur.fetchone()
    if not row:
        sys.exit("no envelope-backed raw batch with a transfer event found; "
                 "run scripts/demo_flow.py first")

    code, batch_id, owner = row["batch_code"], row["id"], row["owner"]
    print(f"{BOLD}risk ladder on {code}{RESET}\n")

    base = risk.assess_batch(conn, code)
    show("baseline (honest batch)", base)
    if base.tier != "screen":
        print(f"  {RED}FAIL: an honest batch should need only the free field screen{RESET}")
        failures += 1

    # --- 1. a broken drum seal in transit ---------------------------------
    with conn.cursor() as cur:
        cur.execute("UPDATE custody_events SET seal_intact = false "
                    "WHERE batch_id = %s AND kind = 'transfer'", (batch_id,))
        touched_transfer = cur.rowcount
    a1 = risk.assess_batch(conn, code)
    show("+ broken transport seal", a1, base.score)
    if touched_transfer and a1.score <= base.score:
        print(f"  {RED}FAIL: a broken drum seal must raise risk{RESET}")
        failures += 1

    # --- 2. prior cases against the owner ---------------------------------
    # The signal saturates at three cases by design -- a fourth prior case does
    # not make a producer meaningfully more suspect than a third -- so this
    # must start from zero to show any movement at all.
    with conn.cursor() as cur:
        for _ in range(3):
            cur.execute(
                "INSERT INTO fraud_cases (kind, subject_type, subject_id, score, detail) "
                "VALUES ('yield_outlier', 'actor', %s, 0.7, '{\"test\":true}')", (owner,))
    a2 = risk.assess_batch(conn, code)
    show("+ 3 prior cases against owner", a2, a1.score)
    if a2.score <= a1.score:
        print(f"  {RED}FAIL: owner history must raise risk{RESET}")
        failures += 1

    # --- 3. implausible processing loss ------------------------------------
    with conn.cursor() as cur:
        cur.execute("SELECT qty_kg FROM custody_events WHERE batch_id = %s "
                    "ORDER BY qty_kg DESC LIMIT 1", (batch_id,))
        q = cur.fetchone()
        if not q:
            sys.exit(f"{code} has no custody events to derive a quantity from")
        cur.execute(
            "INSERT INTO custody_events (batch_id, kind, qty_kg, loss_kg) "
            "VALUES (%s, 'process', %s, %s)",
            (batch_id, float(q["qty_kg"]) * 0.7, float(q["qty_kg"]) * 0.3))
    a3 = risk.assess_batch(conn, code)
    show("+ 30% declared processing loss", a3, a2.score)
    if a3.score <= a2.score:
        print(f"  {RED}FAIL: an implausible loss must raise risk{RESET}")
        failures += 1

    # --- 4. surplus minted above the telemetry ceiling --------------------
    with conn.cursor() as cur:
        cur.execute("SELECT unverified_surplus_kg FROM batches WHERE id = %s", (batch_id,))
        prev_surplus = float(cur.fetchone()["unverified_surplus_kg"])
        cur.execute("UPDATE batches SET unverified_surplus_kg = 40 WHERE id = %s",
                    (batch_id,))
    a4 = risk.assess_batch(conn, code)
    show("+ 40 kg unverified surplus", a4, a3.score)
    if a4.score <= a3.score:
        print(f"  {RED}FAIL: unverified surplus must raise risk{RESET}")
        failures += 1

    escalated = a4.tier != base.tier
    print(f"\n  tier moved {base.tier} -> {a4.tier} "
          f"(Rs {base.tier_cost} -> Rs {a4.tier_cost})")
    if not escalated:
        print(f"  {RED}FAIL: accumulated risk never escalated the test tier{RESET}")
        failures += 1

    print(f"\n  {DIM}reasons at the top of the ladder:{RESET}")
    for r in a4.reasons:
        print(f"    - {r}")

    # --- restore -----------------------------------------------------------
    _cleanup(conn, batch_id, owner, surplus=prev_surplus)

    restored = risk.assess_batch(conn, code)
    print(f"\n  restored: score {restored.score:.3f}, tier {restored.tier}")
    if abs(restored.score - base.score) > 0.001:
        print(f"  {RED}FAIL: cleanup did not restore the baseline "
              f"({restored.score:.3f} vs {base.score:.3f}){RESET}")
        failures += 1

    conn.close()
    if failures:
        print(f"\n{RED}{failures} check(s) failed{RESET}")
        return 1
    print(f"\n{GREEN}PASS{RESET}: the ladder escalates monotonically and cleans up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
