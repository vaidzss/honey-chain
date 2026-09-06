"""Yield envelope estimation.

Turns sentinel-hive telemetry into the P10/P50/P90 distribution whose P90
becomes the on-chain mint ceiling.

**This is a statistical baseline, not the ML model.** It is deliberately simple
and inspectable: scale-derived nectar accumulation, extrapolated from sentinels
to the apiary, with an uncertainty band that widens as sentinel coverage falls.
Phase 2 replaces the point estimate with a LightGBM quantile regressor behind
this same interface -- `estimate_envelope()` keeps its signature, and everything
downstream (the oracle, the contract, the UI) is unaffected.

Calling it a baseline matters. A number that bounds what a beekeeper may sell
should never be a black box nobody in the room can explain.

Two measurement decisions worth knowing:

* **Pre-dawn weighing.** Daily hive mass is sampled between 03:00 and 05:00
  local, when the whole colony is home and no foragers are out. Weighing at
  midday measures the weather and the forager population as much as the honey.
  This is standard practice in precision apiculture and it removes several kg
  of diurnal noise for free.

* **Positive deltas only.** Summing day-over-day *gains* captures nectar
  income; the step drops from harvests and swarms are excluded rather than
  netted off. A colony that gained 30 kg and had 12 kg taken produced 30 kg of
  surplus, not 18.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date

from psycopg.rows import tuple_row
from web3 import Web3

log = logging.getLogger("aurabee.yields")

MODEL_VERSION = "yield-baseline-0.1.0"

# Normal-approximation z-scores for the reported quantiles.
Z_P90 = 1.2816
Z_P10 = -1.2816

# A colony consumes stores as well as producing them. Gross scale gain
# overstates what is actually harvestable, because some of that mass is brood
# and bee biomass that never becomes honey.
HARVESTABLE_FRACTION = 0.78

# Floor on relative spread. Even with many sentinels in close agreement, hives
# vary more than a handful of samples suggests, and a suspiciously tight band
# would produce a ceiling that rejects honest declarations.
MIN_CV = 0.18

# Extra uncertainty for hives we never measured. At 15% coverage most of the
# apiary is inferred, and the envelope must say so rather than pretend the
# sentinels speak for everyone.
EXTRAPOLATION_CV = 0.35


@dataclass
class HiveGain:
    node_id: str
    hive_id: str
    gross_gain_kg: float
    days_observed: int
    samples: int


@dataclass
class Envelope:
    apiary_id: str
    season: str
    window_start: date
    window_end: date
    p10_kg: float
    p50_kg: float
    p90_kg: float
    hives_counted: int
    sentinel_count: int
    coverage_ratio: float
    model_version: str
    evidence_hash: bytes
    per_hive: list[HiveGain] = field(default_factory=list)

    @property
    def coverage_bps(self) -> int:
        return int(round(self.coverage_ratio * 10000))


DAILY_WEIGHT_SQL = """
WITH predawn AS (
    SELECT
        t.node_id,
        t.hive_id,
        (t.ts AT TIME ZONE 'Asia/Kolkata')::date AS day,
        avg(t.weight_kg)  AS weight_kg,
        count(*)          AS samples
    FROM telemetry t
    JOIN hives h ON h.id = t.hive_id
    WHERE h.apiary_id = %(apiary_id)s
      AND t.ts >= %(start)s AND t.ts < %(end)s
      AND t.weight_kg IS NOT NULL
      -- colony fully home, no foragers in the field
      AND extract(hour FROM (t.ts AT TIME ZONE 'Asia/Kolkata')) BETWEEN 3 AND 5
    GROUP BY 1, 2, 3
)
SELECT node_id, hive_id::text, day, weight_kg, samples
FROM predawn
ORDER BY node_id, day
"""


def _gains_from_series(rows: list[tuple]) -> list[HiveGain]:
    """Sum day-over-day increases per hive."""
    by_node: dict[str, list[tuple]] = {}
    for node_id, hive_id, day, weight, samples in rows:
        by_node.setdefault(node_id, []).append((day, float(weight), hive_id, samples))

    out: list[HiveGain] = []
    for node_id, series in by_node.items():
        series.sort(key=lambda r: r[0])
        if len(series) < 2:
            continue
        gross = 0.0
        total_samples = 0
        for i in range(1, len(series)):
            delta = series[i][1] - series[i - 1][1]
            if delta > 0:
                gross += delta
            total_samples += series[i][3]
        out.append(HiveGain(
            node_id=node_id,
            hive_id=series[0][2],
            gross_gain_kg=round(gross, 3),
            days_observed=len(series),
            samples=total_samples,
        ))
    return out


def _evidence_hash(apiary_id: str, season: str, gains: list[HiveGain]) -> bytes:
    """A commitment to exactly which measurements produced this envelope.

    Not a Merkle tree yet -- a hash over the ordered per-hive summary is enough
    to prove the inputs were not altered after the fact, which is what the
    on-chain `evidenceHash` is for. It becomes a Merkle root when we need proofs
    of individual readings rather than of the set.
    """
    parts = [apiary_id, season, MODEL_VERSION]
    for g in sorted(gains, key=lambda x: x.node_id):
        parts.append(f"{g.node_id}:{g.gross_gain_kg:.3f}:{g.days_observed}:{g.samples}")
    return Web3.keccak(text="|".join(parts))


def estimate_envelope(
    conn,
    apiary_id: str,
    season: str,
    window_start: date,
    window_end: date,
) -> Envelope:
    """Derive the yield envelope for one apiary and season.

    Raises ValueError when there is not enough telemetry to say anything. That
    is the correct outcome: **no envelope means nothing may be minted**, which
    is safer than guessing a ceiling and calling a fraud legitimate.
    """
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(DAILY_WEIGHT_SQL, {
            "apiary_id": apiary_id, "start": window_start, "end": window_end,
        })
        rows = cur.fetchall()

        cur.execute(
            """SELECT count(*) FILTER (WHERE status = 'active'),
                      count(*) FILTER (WHERE status = 'active' AND is_sentinel)
               FROM hives WHERE apiary_id = %s""",
            (apiary_id,),
        )
        hive_count, sentinel_count = cur.fetchone()

    gains = _gains_from_series(rows)
    if not gains:
        raise ValueError(
            f"no usable pre-dawn weight series for apiary {apiary_id} "
            f"in {window_start}..{window_end}"
        )
    if not hive_count:
        raise ValueError(f"apiary {apiary_id} has no active hives")

    per_hive = [g.gross_gain_kg * HARVESTABLE_FRACTION for g in gains]
    mean_per_hive = statistics.fmean(per_hive)

    # Spread across sentinels, floored so a lucky agreement cannot produce an
    # unrealistically tight ceiling.
    if len(per_hive) > 1:
        sd = statistics.stdev(per_hive)
        cv = max(MIN_CV, sd / mean_per_hive if mean_per_hive else MIN_CV)
    else:
        cv = MIN_CV + EXTRAPOLATION_CV  # a single sentinel tells us very little

    coverage = min(1.0, sentinel_count / hive_count) if hive_count else 0.0
    # Uninstrumented hives carry their own uncertainty, weighted by how many of
    # them there are.
    combined_cv = (cv ** 2 + ((1 - coverage) * EXTRAPOLATION_CV) ** 2) ** 0.5

    total_p50 = mean_per_hive * hive_count
    sigma = total_p50 * combined_cv

    envelope = Envelope(
        apiary_id=apiary_id,
        season=season,
        window_start=window_start,
        window_end=window_end,
        p10_kg=round(max(0.0, total_p50 + Z_P10 * sigma), 3),
        p50_kg=round(total_p50, 3),
        p90_kg=round(total_p50 + Z_P90 * sigma, 3),
        hives_counted=hive_count,
        sentinel_count=sentinel_count,
        coverage_ratio=round(coverage, 4),
        model_version=MODEL_VERSION,
        evidence_hash=_evidence_hash(apiary_id, season, gains),
        per_hive=gains,
    )
    log.info(
        "envelope %s/%s: P50 %.1f kg, P90 %.1f kg over %d hives "
        "(%d sentinels, %.0f%% coverage, cv %.2f)",
        apiary_id[:8], season, envelope.p50_kg, envelope.p90_kg,
        hive_count, sentinel_count, coverage * 100, combined_cv,
    )
    return envelope


def persist_envelope(conn, env: Envelope, tx_hash: str | None = None) -> str:
    """Upsert into yield_envelopes. Returns the row id."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """
            INSERT INTO yield_envelopes (
                apiary_id, season, window_start, window_end,
                p10_kg, p50_kg, p90_kg, hives_counted, sentinel_count,
                coverage_ratio, model_version, evidence_hash, tx_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (apiary_id, season) DO UPDATE SET
                window_start = EXCLUDED.window_start,
                window_end   = EXCLUDED.window_end,
                p10_kg = EXCLUDED.p10_kg,
                p50_kg = EXCLUDED.p50_kg,
                p90_kg = EXCLUDED.p90_kg,
                hives_counted = EXCLUDED.hives_counted,
                sentinel_count = EXCLUDED.sentinel_count,
                coverage_ratio = EXCLUDED.coverage_ratio,
                model_version = EXCLUDED.model_version,
                evidence_hash = EXCLUDED.evidence_hash,
                tx_hash = EXCLUDED.tx_hash
            RETURNING id::text
            """,
            (env.apiary_id, env.season, env.window_start, env.window_end,
             env.p10_kg, env.p50_kg, env.p90_kg, env.hives_counted,
             env.sentinel_count, env.coverage_ratio, env.model_version,
             "0x" + env.evidence_hash.hex(), tx_hash),
        )
        return cur.fetchone()[0]
