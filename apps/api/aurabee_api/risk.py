"""Fraud risk scoring and the testing ladder.

This is where the AI actually earns its keep. Not by classifying bee sounds --
by deciding which 5% of batches are worth a Rs 3,000 laboratory test.

## Why a ladder rather than a test

No single assay is sufficient. The mandated C4/IRMS test is *structurally*
blind to C3 syrups (rice, beet, wheat) because their isotopic signature
overlaps genuine honey; NMR covers C3 better but is weaker on C4; LC-HRMS
catches syrups tailored to beat both, and costs the most. So the industry
practice -- and what FSSAI could actually afford -- is a risk-based ladder:

    every batch      field screen (gas sensor / NIR)   ~Rs 0 after hardware
    elevated risk    C4 sugar, EA/LC-IRMS              ~Rs 1,200
    high risk        NMR profiling                     ~Rs 3,000
    highest risk     LC-HRMS + pollen origin           ~Rs 8,000

This mirrors **VACCP** (Vulnerability Assessment and Critical Control Points),
the food-industry standard, whose whole premise is that you do not apply
maximum testing to every SKU -- you concentrate it where fraud is both
plausible and consequential.

## Design constraints that are not negotiable

**Every score is explainable.** A number that decides whether a rural
beekeeper's honey gets flagged must come with the reasons, in words, or it is
not deployable. `RiskAssessment.reasons` is not a debugging aid.

**A high score is not an accusation.** It allocates a test. The test decides.
Nothing here should ever be surfaced to a consumer as "suspicious producer".

**Absence of evidence raises risk, and that is not the producer's fault.** Low
sentinel coverage or offline nodes mean we know less, so we test more. The
system should not confuse "we cannot see" with "they are cheating", and the
reason text says so explicitly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("aurabee.risk")

# Indicative Indian NABL prices; used for budget allocation, not billing.
TIERS = [
    ("screen",   0.00, 0,    "Field gas-sensor / NIR screen only"),
    ("c4_irms",  0.30, 1200, "C3/C4 sugar by EA/LC-IRMS"),
    ("nmr",      0.55, 3000, "1H NMR profile (better C3 coverage)"),
    ("lc_hrms",  0.78, 8000, "LC-HRMS for tailored syrups + pollen DNA origin"),
]


@dataclass
class Signal:
    name: str
    value: float          # 0..1, higher = riskier
    weight: float
    reason: str

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass
class RiskAssessment:
    batch_code: str
    score: float
    tier: str
    tier_cost: int
    tier_label: str
    signals: list[Signal] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        """Human-readable, ordered by how much each actually moved the score."""
        return [
            s.reason for s in
            sorted(self.signals, key=lambda x: x.contribution, reverse=True)
            if s.contribution > 0.01
        ]


def _tier_for(score: float) -> tuple[str, int, str]:
    chosen = TIERS[0]
    for name, threshold, cost, label in TIERS:
        if score >= threshold:
            chosen = (name, threshold, cost, label)
    return chosen[0], chosen[2], chosen[3]


BATCH_SQL = """
SELECT b.batch_code, b.declared_kg, b.current_kg, b.kind,
       b.unverified_surplus_kg, b.owner_actor_id::text AS owner_id,
       b.apiary_id::text AS apiary_id,
       e.p90_kg, e.p50_kg, e.coverage_ratio, e.sentinel_count, e.hives_counted
FROM batches b
LEFT JOIN yield_envelopes e ON e.id = b.envelope_id
WHERE b.batch_code = %s
"""


def assess_batch(conn, batch_code: str) -> RiskAssessment:
    with conn.cursor() as cur:
        cur.execute(BATCH_SQL, (batch_code,))
        b = cur.fetchone()
        if not b:
            raise ValueError(f"unknown batch {batch_code}")

        # composition breadth
        cur.execute(
            "SELECT count(*) AS n, coalesce(max(pct), 100) AS top_pct "
            "FROM batch_composition bc JOIN batches x ON x.id = bc.blend_batch_id "
            "WHERE x.batch_code = %s", (batch_code,))
        comp = cur.fetchone()

        # declared processing loss on this batch
        cur.execute(
            "SELECT coalesce(sum(ce.loss_kg), 0) AS loss, coalesce(sum(ce.qty_kg), 0) AS qty "
            "FROM custody_events ce JOIN batches x ON x.id = ce.batch_id "
            "WHERE x.batch_code = %s AND ce.kind IN ('process','merge')", (batch_code,))
        loss = cur.fetchone()

        # owner history
        cur.execute(
            "SELECT count(*) AS n FROM fraud_cases "
            "WHERE subject_type = 'actor' AND subject_id = %s AND status <> 'dismissed'",
            (b["owner_id"],))
        history = cur.fetchone()

        # seal anomalies on this batch
        cur.execute(
            "SELECT count(*) FILTER (WHERE s.is_anomalous) AS bad, count(*) AS total "
            "FROM scans s JOIN seals sl ON sl.id = s.seal_id "
            "JOIN batches x ON x.id = sl.batch_id WHERE x.batch_code = %s", (batch_code,))
        scans = cur.fetchone()

        # transport integrity at intake
        cur.execute(
            "SELECT count(*) FILTER (WHERE ce.seal_intact IS FALSE) AS broken, "
            "       count(*) FILTER (WHERE ce.seal_intact IS NOT NULL) AS checked "
            "FROM custody_events ce JOIN batches x ON x.id = ce.batch_id "
            "WHERE x.batch_code = %s", (batch_code,))
        transport = cur.fetchone()

    signals: list[Signal] = []

    # --- 1. how close the declaration sat to the modelled ceiling ---------
    #
    # A blend legitimately has no envelope of its own: its *constituents* were
    # each bounded when they were minted. Penalising it for that would make
    # every blend look suspicious for a reason that is not suspicious at all,
    # so it inherits the risk of what went into it instead.
    if b["kind"] == "blend":
        inherited = _inherited_risk(conn, batch_code)
        if inherited is not None:
            signals.append(Signal(
                "inherited_source_risk", inherited, 0.22,
                f"Blend inherits the risk of its constituent batches "
                f"(worst constituent scored {inherited:.2f})."))
    elif b["p90_kg"]:
        ratio = float(b["declared_kg"]) / float(b["p90_kg"])
        v = max(0.0, min(1.0, (ratio - 0.5) / 0.5))
        signals.append(Signal(
            "envelope_utilisation", v, 0.22,
            f"Declared {b['declared_kg']:.0f} kg against a {b['p90_kg']:.0f} kg "
            f"modelled ceiling ({ratio*100:.0f}% of it)."))
    else:
        signals.append(Signal(
            "no_envelope", 1.0, 0.22,
            "No yield envelope backs this batch, so nothing bounds the declared "
            "quantity."))

    # --- 2. surplus minted above what telemetry supported -----------------
    if b["unverified_surplus_kg"] and float(b["unverified_surplus_kg"]) > 0:
        signals.append(Signal(
            "unverified_surplus", 1.0, 0.20,
            f"{float(b['unverified_surplus_kg']):.0f} kg was minted above the "
            f"telemetry-supported ceiling under regulator sign-off."))

    # --- 3. how much of the apiary we actually measured -------------------
    if b["coverage_ratio"] is not None:
        cov = float(b["coverage_ratio"])
        signals.append(Signal(
            "low_coverage", max(0.0, min(1.0, (0.30 - cov) / 0.30)), 0.15,
            f"Only {cov*100:.0f}% of hives are instrumented "
            f"({b['sentinel_count']} of {b['hives_counted']}), so most of the "
            f"estimate is extrapolated. Less evidence, not more suspicion."))

    # --- 4. blend breadth --------------------------------------------------
    if comp and comp["n"]:
        n = int(comp["n"])
        signals.append(Signal(
            "blend_breadth", min(1.0, (n - 1) / 6.0), 0.10,
            f"Blended from {n} sources; more constituents means attribution is "
            f"harder to check."))

    # --- 5. implausible processing loss -----------------------------------
    if loss and float(loss["qty"]) > 0:
        pct = float(loss["loss"]) / (float(loss["loss"]) + float(loss["qty"]))
        # a few percent is normal filtering and settling; 12%+ is a question
        signals.append(Signal(
            "processing_loss", max(0.0, min(1.0, (pct - 0.04) / 0.11)), 0.12,
            f"Declared processing loss {pct*100:.1f}%."))

    # --- 6. owner history --------------------------------------------------
    if history and history["n"]:
        signals.append(Signal(
            "actor_history", min(1.0, int(history["n"]) / 3.0), 0.13,
            f"{history['n']} prior open or confirmed case(s) against this owner."))

    # --- 7. seal anomalies in the field ------------------------------------
    if scans and scans["total"]:
        rate = int(scans["bad"]) / int(scans["total"])
        if rate > 0:
            signals.append(Signal(
                "scan_anomalies", min(1.0, rate * 4), 0.10,
                f"{scans['bad']} of {scans['total']} consumer scans of this "
                f"batch were anomalous."))

    # --- 8. transport integrity --------------------------------------------
    if transport and transport["broken"]:
        signals.append(Signal(
            "broken_transport_seal", 1.0, 0.18,
            f"{transport['broken']} handover(s) recorded a broken or missing "
            f"drum seal — the one gap the ledger cannot see by itself."))

    total_weight = sum(s.weight for s in signals) or 1.0
    score = round(sum(s.contribution for s in signals) / total_weight, 4)
    tier, cost, label = _tier_for(score)

    return RiskAssessment(
        batch_code=batch_code, score=score, tier=tier,
        tier_cost=cost, tier_label=label, signals=signals,
    )


def allocate_testing_budget(
    assessments: list[RiskAssessment], budget_inr: int
) -> dict:
    """Spend a fixed testing budget where it buys the most information.

    Greedy by score: the riskiest batch gets its indicated tier until the money
    runs out, then everything else drops to the free field screen. Crude, and
    honest about being crude -- but it is the difference between "test 3% of
    batches at random" and "test the 3% most likely to be wrong", which is the
    entire argument for having a model at all.
    """
    ranked = sorted(assessments, key=lambda a: a.score, reverse=True)
    spent, plan, downgraded = 0, [], 0

    for a in ranked:
        if spent + a.tier_cost <= budget_inr:
            spent += a.tier_cost
            plan.append({"batch_code": a.batch_code, "score": a.score,
                         "tier": a.tier, "cost": a.tier_cost,
                         "reasons": a.reasons[:3]})
        else:
            if a.tier != "screen":
                downgraded += 1
            plan.append({"batch_code": a.batch_code, "score": a.score,
                         "tier": "screen", "cost": 0,
                         "reasons": ["Budget exhausted; field screen only."]})

    return {
        "budget_inr": budget_inr,
        "spent_inr": spent,
        "batches": len(assessments),
        "tested": sum(1 for p in plan if p["cost"] > 0),
        "downgraded_for_budget": downgraded,
        "plan": plan,
    }


def _inherited_risk(conn, blend_code: str, _depth: int = 0) -> float | None:
    """Worst-constituent risk for a blend.

    Worst, not average: mixing one clean batch with one suspect batch does not
    produce honey that is half suspect. The whole blend inherits the doubt.

    Depth-limited because a blend of blends is legitimate and a cycle is not.
    """
    if _depth > 4:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """SELECT sb.batch_code
               FROM batch_composition bc
               JOIN batches blend ON blend.id = bc.blend_batch_id
               JOIN batches sb    ON sb.id = bc.source_batch_id
               WHERE blend.batch_code = %s""", (blend_code,))
        sources = [r["batch_code"] for r in cur.fetchall()]
    if not sources:
        return None

    worst = 0.0
    for code in sources:
        try:
            worst = max(worst, assess_batch(conn, code).score)
        except (ValueError, RecursionError):
            continue
    return round(worst, 4)
