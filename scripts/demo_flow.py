"""End-to-end walk through the trust chain, against the real stack.

A legitimate harvest is minted, an inflated one is rejected by the contract,
honey moves through custody, is processed and blended under mass conservation,
jars are sealed, and a cloned QR fails verification. Everything is mirrored into
Postgres so the consumer verify page has something to render.

    python scripts/demo_flow.py

Prerequisites: infra up, seeded, telemetry ingested, contracts deployed, actors
synced. See docs/08-running-locally.md.

Nothing here is staged. Every PASS and every REJECTED is a real transaction
against the deployed contracts, and the script exits non-zero if a guarantee it
claims to demonstrate does not actually hold -- including checking that each
rejection came from the error we intended and not an unrelated one.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
load_dotenv(ROOT / ".env")

from aurabee_api import seals as sealkit, store  # noqa: E402
from aurabee_api.chain import ChainError, get_chain, to_bytes32  # noqa: E402
from aurabee_api.yields import estimate_envelope, persist_envelope  # noqa: E402

WINDOW = (date(2026, 10, 15), date(2027, 3, 15))
JARS, NET_G = 240, 500

def ethers_zero() -> bytes:
    return b"\x00" * 32


GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def hdr(n, text): print(f"\n{BOLD}{n}. {text}{RESET}")
def ok(msg): print(f"   {GREEN}PASS{RESET}  {msg}")
def rejected(msg): print(f"   {RED}REJECTED{RESET}  {msg}")
def info(msg): print(f"   {DIM}{msg}{RESET}")
def fail(msg): print(f"   {RED}FAIL: {msg}{RESET}")


def main() -> int:
    chain = get_chain()
    if not chain.is_connected():
        sys.exit(f"no chain at {chain.rpc_url}")

    conn = store.connect()
    cur = conn.cursor()

    cur.execute("SELECT id::text, wallet_address, name FROM actors "
                "WHERE kind='processor' LIMIT 1")
    processor = cur.fetchone()
    cur.execute("SELECT id::text, wallet_address, name FROM actors "
                "WHERE kind='collection_centre' LIMIT 1")
    centre = cur.fetchone()
    cur.execute("SELECT id::text, wallet_address, name FROM actors "
                "WHERE kind='lab' LIMIT 1")
    lab = cur.fetchone()
    cur.execute(
        """SELECT a.id::text AS apiary_id, a.name AS apiary, a.state, a.district,
                  act.id::text AS actor_id, act.wallet_address, act.name AS beekeeper
           FROM apiaries a JOIN actors act ON act.id = a.actor_id
           ORDER BY a.created_at LIMIT 2"""
    )
    ap1, ap2 = cur.fetchall()

    # A fresh season label per run: the envelope comes from the same real
    # telemetry every time, but consuming it must not poison the next run.
    stamp = int(time.time()) % 100000
    SEASON = f"demo-{stamp}"
    B_MAIN, B_SECOND = f"AB-SIT-{stamp}-1", f"AB-SIT-{stamp}-2"
    B_PROC, B_BLEND = f"AB-SIT-{stamp}-P", f"AB-SIT-{stamp}-B"
    B_SRC2 = f"AB-SIT-{stamp}-S"

    print(f"{BOLD}AuraBee end-to-end{RESET}   chain {chain.network} "
          f"({chain.chain_id})   relayer {chain.account.address[:10]}...")

    # ------------------------------------------------------------------ 1 --
    hdr(1, "The yield envelope derived from hive telemetry")
    envelope_ids: dict[str, str] = {}
    for ap in (ap1, ap2):
        e = estimate_envelope(conn, ap["apiary_id"], SEASON, *WINDOW)
        res = chain.publish_envelope(
            apiary_id=ap["apiary_id"], season=SEASON, p90_kg=e.p90_kg,
            p50_kg=e.p50_kg,
            window_start=int(time.mktime(WINDOW[0].timetuple())),
            window_end=int(time.mktime(WINDOW[1].timetuple())),
            evidence_hash=e.evidence_hash, coverage_bps=e.coverage_bps,
            model_version=e.model_version)
        envelope_ids[ap["apiary_id"]] = persist_envelope(conn, e, res["tx_hash"])

    env = chain.envelope(ap1["apiary_id"], SEASON)
    cap = env["max_kg"]
    info(f"{ap1['apiary']} ({ap1['beekeeper']})")
    info(f"P50 {env['p50_kg']:.1f} kg   P90 ceiling {cap:.1f} kg   "
         f"coverage {env['coverage_bps']/100:.0f}%   model {env['model_version']}")
    info(f"evidence {env['evidence_hash'][:26]}...")
    ok(f"mint ceiling for this apiary and season is {cap:.1f} kg")

    # ------------------------------------------------------------------ 2 --
    hdr(2, "A believable harvest is accepted")
    honest = round(cap * 0.55, 1)
    res = chain.mint_harvest(B_MAIN, ap1["apiary_id"], SEASON, honest, "mustard",
                             int(time.time()))
    store.upsert_batch(conn, batch_code=B_MAIN, apiary_id=ap1["apiary_id"],
                       owner_actor_id=ap1["actor_id"], kind="raw",
                       declared_kg=honest, current_kg=honest,
                       floral_source="mustard", harvest_date=date(2027, 2, 10),
                       envelope_id=envelope_ids[ap1["apiary_id"]],
                       tx_hash=res["tx_hash"])
    store.record_custody(conn, batch_code=B_MAIN, kind="mint", from_actor_id=None,
                         to_actor_id=ap1["actor_id"], qty_kg=honest,
                         tx_hash=res["tx_hash"])
    ok(f"minted {honest} kg as {B_MAIN}   tx {res['tx_hash'][:18]}...")
    info(f"remaining this season: "
         f"{chain.remaining_kg(ap1['apiary_id'], SEASON):.1f} kg")

    # ------------------------------------------------------------------ 3 --
    hdr(3, "An inflated declaration is refused by the contract")
    absurd = round(cap * 2.6, 1)
    try:
        chain.mint_harvest(f"{B_MAIN}-X", ap1["apiary_id"], SEASON, absurd,
                           "mustard", int(time.time()))
        fail(f"the chain accepted {absurd} kg; the core guarantee is broken")
        return 1
    except ChainError as exc:
        if exc.reason != "ExceedsYieldEnvelope":
            fail(f"expected ExceedsYieldEnvelope, got {exc.reason}")
            return 1
        rejected(f"declaring {absurd} kg against a {cap:.1f} kg envelope")
        if exc.data and len(exc.data) >= 2:
            info(f"contract said: asked {exc.data[0]/1000:.1f} kg, "
                 f"{exc.data[1]/1000:.1f} kg of envelope remained")

    # ------------------------------------------------------------------ 4 --
    hdr(4, "Salami slicing is refused too -- the cap is cumulative")
    slice_kg = round(chain.remaining_kg(ap1["apiary_id"], SEASON) * 0.8, 1)
    res = chain.mint_harvest(B_SECOND, ap1["apiary_id"], SEASON, slice_kg,
                             "mustard", int(time.time()))
    store.upsert_batch(conn, batch_code=B_SECOND, apiary_id=ap1["apiary_id"],
                       owner_actor_id=ap1["actor_id"], kind="raw",
                       declared_kg=slice_kg, current_kg=slice_kg,
                       floral_source="mustard", harvest_date=date(2027, 2, 18),
                       envelope_id=envelope_ids[ap1["apiary_id"]],
                       tx_hash=res["tx_hash"])
    ok(f"minted a further {slice_kg} kg as {B_SECOND}")
    info(f"remaining: {chain.remaining_kg(ap1['apiary_id'], SEASON):.1f} kg")
    try:
        chain.mint_harvest(f"{B_SECOND}-X", ap1["apiary_id"], SEASON, slice_kg,
                           "mustard", int(time.time()))
        fail("cumulative cap not enforced")
        return 1
    except ChainError as exc:
        if exc.reason != "ExceedsYieldEnvelope":
            fail(f"expected ExceedsYieldEnvelope, got {exc.reason}")
            return 1
        rejected(f"a second {slice_kg} kg slice; only "
                 f"{chain.remaining_kg(ap1['apiary_id'], SEASON):.1f} kg is left")

    # ------------------------------------------------------------------ 5 --
    hdr(5, "Custody moves with provenance")
    r = chain.transfer_custody(B_MAIN, ap1["wallet_address"],
                               centre["wallet_address"], honest)
    store.record_custody(conn, batch_code=B_MAIN, kind="transfer",
                         from_actor_id=ap1["actor_id"], to_actor_id=centre["id"],
                         qty_kg=honest, tx_hash=r["tx_hash"])
    ok(f"{ap1['beekeeper']} -> {centre['name']}  ({honest} kg)")

    r = chain.transfer_custody(B_MAIN, centre["wallet_address"],
                               processor["wallet_address"], honest)
    store.record_custody(conn, batch_code=B_MAIN, kind="transfer",
                         from_actor_id=centre["id"], to_actor_id=processor["id"],
                         qty_kg=honest, tx_hash=r["tx_hash"])
    ok(f"{centre['name']} -> {processor['name']}  ({honest} kg)")

    # ------------------------------------------------------------------ 6 --
    hdr(6, "Processing conserves mass")
    out_kg = round(honest * 0.94, 1)
    r = chain.process_batch(B_MAIN, B_PROC, honest, out_kg,
                            processor["wallet_address"])
    store.upsert_batch(conn, batch_code=B_PROC, apiary_id=ap1["apiary_id"],
                       owner_actor_id=processor["id"], kind="processed",
                       declared_kg=out_kg, current_kg=out_kg,
                       floral_source="mustard", harvest_date=date(2027, 2, 10),
                       envelope_id=envelope_ids[ap1["apiary_id"]],
                       tx_hash=r["tx_hash"])
    store.record_custody(conn, batch_code=B_PROC, kind="process",
                         from_actor_id=processor["id"], to_actor_id=processor["id"],
                         qty_kg=out_kg, loss_kg=round(honest - out_kg, 2),
                         tx_hash=r["tx_hash"])
    ok(f"{honest} kg in -> {out_kg} kg out  (declared loss "
       f"{round(honest-out_kg,1)} kg, {(honest-out_kg)/honest*100:.1f}%)")

    # Move the second batch to the processor first, or the attempt below fails
    # on InsufficientBalance and we would be claiming to demonstrate mass
    # conservation while actually demonstrating a balance check.
    r = chain.transfer_custody(B_SECOND, ap1["wallet_address"],
                               processor["wallet_address"], slice_kg)
    store.record_custody(conn, batch_code=B_SECOND, kind="transfer",
                         from_actor_id=ap1["actor_id"], to_actor_id=processor["id"],
                         qty_kg=slice_kg, tx_hash=r["tx_hash"])
    inflated = round(slice_kg * 1.3, 1)
    try:
        chain.process_batch(B_SECOND, f"{B_PROC}-X", slice_kg, inflated,
                            processor["wallet_address"])
        fail("honey was created from nothing")
        return 1
    except ChainError as exc:
        if exc.reason != "MassNotConserved":
            fail(f"expected MassNotConserved, got {exc.reason}")
            return 1
        rejected(f"turning {slice_kg} kg into {inflated} kg")

    # ------------------------------------------------------------------ 7 --
    hdr(7, "Blending records every constituent (this is the EU declaration)")
    env2 = chain.envelope(ap2["apiary_id"], SEASON)
    second_kg = round(min(env2["max_kg"] * 0.4, 60.0), 1)
    r = chain.mint_harvest(B_SRC2, ap2["apiary_id"], SEASON, second_kg, "mustard",
                           int(time.time()))
    store.upsert_batch(conn, batch_code=B_SRC2, apiary_id=ap2["apiary_id"],
                       owner_actor_id=ap2["actor_id"], kind="raw",
                       declared_kg=second_kg, current_kg=second_kg,
                       floral_source="mustard", harvest_date=date(2027, 2, 14),
                       envelope_id=envelope_ids[ap2["apiary_id"]],
                       tx_hash=r["tx_hash"])
    r = chain.transfer_custody(B_SRC2, ap2["wallet_address"],
                               processor["wallet_address"], second_kg)
    store.record_custody(conn, batch_code=B_SRC2, kind="mint", from_actor_id=None,
                         to_actor_id=ap2["actor_id"], qty_kg=second_kg)
    store.record_custody(conn, batch_code=B_SRC2, kind="transfer",
                         from_actor_id=ap2["actor_id"], to_actor_id=processor["id"],
                         qty_kg=second_kg, tx_hash=r["tx_hash"])

    blend_out = round((out_kg + second_kg) * 0.99, 1)
    r = chain.blend(B_BLEND, [B_PROC, B_SRC2], [out_kg, second_kg], blend_out,
                    processor["wallet_address"])
    store.upsert_batch(conn, batch_code=B_BLEND, apiary_id=None,
                       owner_actor_id=processor["id"], kind="blend",
                       declared_kg=blend_out, current_kg=blend_out,
                       floral_source="multiflora blend", harvest_date=None,
                       status="packed", tx_hash=r["tx_hash"])
    store.record_custody(conn, batch_code=B_BLEND, kind="merge",
                         from_actor_id=processor["id"], to_actor_id=processor["id"],
                         qty_kg=blend_out, tx_hash=r["tx_hash"])
    store.record_composition(conn, blend_code=B_BLEND, parts=[
        {"source_batch_code": B_PROC, "kg": out_kg,
         "origin_state": ap1["state"], "origin_district": ap1["district"]},
        {"source_batch_code": B_SRC2, "kg": second_kg,
         "origin_state": ap2["state"], "origin_district": ap2["district"]},
    ])

    comp = chain.composition(B_BLEND)
    total = sum(c["kg"] for c in comp)
    ok(f"blended {blend_out} kg from {len(comp)} sources")
    for c in comp:
        origin = ap1["apiary"] if c["source_batch"] == B_PROC else ap2["apiary"]
        info(f"{c['kg']:>7.1f} kg  {c['kg']/total*100:>5.1f}%  {origin}")
    info("origin percentages in descending order is exactly what "
         "EU 2024/1438 requires on the label")

    # ------------------------------------------------------------------ 8 --
    hdr(8, "No seals without an independent lab pass")
    try:
        sealkit_root = sealkit.merkle_root(
            [s.leaf for s in sealkit.generate_seals(B_BLEND, 4)])
        chain.issue_seals(B_BLEND, sealkit_root, 4, NET_G,
                          processor["wallet_address"])
        fail("seals were issued with no lab report")
        return 1
    except ChainError as exc:
        if exc.reason != "NoPassingLabReport":
            fail(f"expected NoPassingLabReport, got {exc.reason}")
            return 1
        rejected("printing QR codes before the batch was tested")

    doc_hash = Web3.keccak(text=f"nabl-report-{B_BLEND}.pdf")
    r = chain.attest_lab_report(
        B_BLEND, lab["wallet_address"], doc_hash, passed=True,
        metrics={"c4_pct": 0.4, "moisture_pct": 18.1, "hmf_mg_kg": 12.0})
    store.record_attestation(
        conn, batch_code=B_BLEND, kind="lab_report", issuer_actor_id=lab["id"],
        doc_hash="0x" + doc_hash.hex(),
        summary={"c4_pct": 0.4, "moisture_pct": 18.1, "hmf_mg_kg": 12.0,
                 "verdict": "pass", "method": "EA/LC-IRMS"},
        tx_hash=r["tx_hash"])
    ok(f"{lab['name']} signed a passing report (C4 0.4%, moisture 18.1%)")

    # ------------------------------------------------------------------ 9 --
    hdr(9, "Seals are capped by the honey that actually exists")
    over = int((blend_out * 1000) // NET_G) + 200   # 200 jars more than the batch holds
    try:
        chain.issue_seals(B_BLEND, ethers_zero(), over, NET_G,
                          processor["wallet_address"])
        fail("seals were issued for more honey than the batch holds")
        return 1
    except ChainError as exc:
        if exc.reason != "OverPacking":
            fail(f"expected OverPacking, got {exc.reason}")
            return 1
        rejected(f"printing {over} jars x {NET_G} g "
                 f"({over*NET_G/1000:.1f} kg) against a {blend_out} kg batch")
        info("syrup added on the packing line ends up in jars with no seal to sell under")

    seal_list = sealkit.generate_seals(B_BLEND, JARS)
    leaves = [s.leaf for s in seal_list]
    root = sealkit.merkle_root(leaves)

    on_chain_leaf = chain.seals.functions.leafOf(
        to_bytes32(B_BLEND), to_bytes32(seal_list[0].serial),
        to_bytes32(seal_list[0].secret)).call()
    if bytes(on_chain_leaf) != seal_list[0].leaf:
        fail("python and solidity disagree on the leaf encoding")
        return 1
    ok("python and solidity agree on the leaf encoding")

    r = chain.issue_seals(B_BLEND, root, JARS, NET_G, processor["wallet_address"])
    store.persist_seals(conn, batch_code=B_BLEND, seals=seal_list,
                        net_weight_g=NET_G, tx_hash=r["tx_hash"])
    # a 500 g retail jar is a different trade item from the bulk lot, so it
    # gets its own GTIN; the Digital Link QR is built from this one
    lot_gtin = store.allocate_gtin(conn, batch_code=B_BLEND)
    jar_gtin = store.allocate_gtin(conn, batch_code=B_BLEND, column="jar_gtin")
    ok(f"anchored {JARS} jars x {NET_G} g under root {('0x'+root.hex())[:20]}...")
    claimed = chain.seals.functions.claimedGrams(to_bytes32(B_BLEND)).call() / 1000
    info(f"jars claim {claimed:.1f} kg against a {blend_out} kg batch "
         f"({claimed/blend_out*100:.0f}% packed)")

    # ----------------------------------------------------------------- 10 --
    hdr(10, "A real jar verifies; a photographed label does not")
    idx = 41
    proof = ["0x" + p.hex() for p in sealkit.merkle_proof(leaves, idx)]
    target = seal_list[idx]

    if not chain.verify_seal(B_BLEND, target.serial, target.secret, proof):
        fail("a genuine jar did not verify")
        return 1
    ok(f"jar {target.serial} verified with its scratch-off code")

    if chain.verify_seal(B_BLEND, target.serial, "GUESSED1", proof):
        fail("a cloned QR verified")
        return 1
    rejected(f"same serial {target.serial}, guessed secret -- a photocopied QR "
             "carries the serial but never the code under the cap")

    print(f"\n{BOLD}Every step above was a real transaction. "
          f"The six REJECTED lines are the product working.{RESET}")
    print(f"\n{DIM}Consumer page for a real jar from this run:{RESET}")
    print(f"  http://localhost:3001/verify/{target.serial}")
    print(f"  {DIM}scratch-off code: {target.secret}{RESET}")
    print()
    print(f"{DIM}The same jar as a GS1 Digital Link QR "
          f"(one code for the retail scanner, the phone and compliance):{RESET}")
    print(f"  http://localhost:3001/01/{jar_gtin}/21/{target.serial}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
