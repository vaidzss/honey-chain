# AuraBee — Honey Chain

Blockchain + AI + IoT trust layer for Indian honey.
**SIH 2026, problem statement SIH26021** (KVIC, Ministry of MSME).

---

## The idea in one paragraph

Blockchain does not stop honey fraud — it makes a lie immutable. If a beekeeper
with 50 hives declares 3,000 kg, a naive traceability chain records that fraud
faithfully and forever. So in AuraBee the IoT layer is not a feature sitting
beside the chain; **it is the oracle that bounds what the chain will accept.**
Hive telemetry feeds a yield forecast, the forecast's P90 becomes a
cryptographically enforced ceiling on mintable kilograms, and mass conservation
is enforced at every custody hop thereafter. Blending records constituent origin
percentages, which produces the EU 2024/1438 origin declaration as a by-product.

Full reasoning: [docs/00-overview.md](docs/00-overview.md).

---

## Status

Stages 0-2 are working end to end: telemetry spine, chain and traceability,
and the intelligence layer. Sequence and exit gates are in
[docs/14-roadmap.md](docs/14-roadmap.md).

| Layer | State |
|---|---|
| Infra: TimescaleDB, Mosquitto, Redis, MinIO | working |
| Schema, 17 tables + hypertable + reference data | working |
| Telemetry contract + HMAC signing | working, adversarially tested |
| Physics-informed hive simulator | working |
| Ingest MQTT to TimescaleDB | working, zero frame loss verified |
| Seeded Sitapur cluster (50 hives, 8 sentinels) | working |
| Smart contracts (5, Solidity 0.8.28) | working, 38 tests passing |
| Quantile yield forecaster | **trained**, conformal-calibrated ceiling |
| Per-jar seals + Merkle anchoring | working |
| Consumer verify page | working |
| Fraud detection (clone / geo / velocity) | working |
| GS1 EPCIS 2.0 interchange + Digital Link QR | working |
| VACCP risk ladder + testing budget allocator | working, verified |
| Colony-health classifier | **trained**, serving — 97.4% acc, queenless recall 0.981 |
| Anomaly detector (one-class) | **trained** — robbing AUC 0.997, 1.0% false alarms |
| Beekeeper app (Hindi + English) | working |
| Varroa counter (classical CV) | working — refuses unusable photos |
| Metrics page, ledger proofs, 3 consoles | working — 25 invariants verified, chain-only |
| Offline harvest queue | working |
| ESP32 firmware | not started — [stage 3](docs/14-roadmap.md) |

---

## Quick start

```bash
cp .env.example .env
npm run infra:up

python -m venv .venv
./.venv/Scripts/python.exe -m pip install \
    -r apps/simulator/requirements.txt -r apps/api/requirements.txt \
    -e packages/schema/python

./.venv/Scripts/python.exe scripts/seed.py --reset
```

Then, in two terminals:

```bash
# 1. ingest
cd apps/api && PYTHONPATH=. ../../.venv/Scripts/python.exe -m aurabee_api.ingest

# 2. simulator
cd apps/simulator && ../../.venv/Scripts/python.exe -m aurabee_sim --from-db \
    --start 2026-11-01 --days 10 --speed 0 \
    --scenario queenless@HN-UP-SIT-0003@2026-11-03 \
    --scenario swarm@HN-UP-SIT-0005@2026-11-06
```

Details and verification queries: [docs/08-running-locally.md](docs/08-running-locally.md).

> Two ports were moved because this machine already had services on them:
> Postgres is on **5433** and the web app on **3001**.

---

## The proof

```bash
python scripts/demo_flow.py
```

Nine steps against the live stack — every line below is a real transaction:

```
2. A believable harvest is accepted
   PASS      minted 167.4 kg as AB-SIT-16915-1
3. An inflated declaration is refused by the contract
   REJECTED  declaring 791.5 kg against a 304.4 kg envelope
4. Salami slicing is refused too -- the cap is cumulative
   REJECTED  a second 109.6 kg slice; only 27.4 kg is left
6. Processing conserves mass
   REJECTED  turning 109.6 kg into 142.5 kg
7. Blending records every constituent
   PASS      157.4 kg  72.4%  Ramesh Apiary
             60.0 kg  27.6%  Sunita Apiary
9. A real jar verifies; a photographed label does not
   REJECTED  same serial, guessed secret
```

The REJECTED lines are the product working. The script exits non-zero if any
guarantee it claims fails, and asserts each rejection came from the *intended*
contract error rather than an unrelated one.

---

## What the simulator actually produces

Not `random.uniform()`. A queenright colony with brood holds its brood nest at
34–35 °C regardless of ambient; a queenless one loses that grip and drifts
toward ambient. That is the highest-value signal in the system, and it falls out
of the physics rather than being painted on:

```
    node_id     | t_in  | ambient | delta | centroid_hz
----------------+-------+---------+-------+-------------
 HN-UP-SIT-0005 | 18.49 |   12.66 |  5.83 |         375   <- swarmed, queenless
 HN-UP-SIT-0003 | 21.03 |   12.68 |  8.35 |         381   <- queen removed
 HN-UP-SIT-0001 | 34.35 |   12.69 | 21.66 |         325
 HN-UP-SIT-0002 | 34.36 |   12.69 | 21.67 |         325
 HN-UP-SIT-0004 | 34.37 |   12.66 | 21.71 |         325
```

The same code path serves a real ESP32-S3 node: identical frame schema,
identical HMAC signing, same MQTT topic. Hardware arriving later changes nothing
downstream.

---

## Documentation

Start at [docs/README.md](docs/README.md) for the full index. The four worth
reading first:

| | |
|---|---|
| [**Overview**](docs/00-overview.md) | The problem, the core design idea, and what makes this different |
| [**Our perspective**](docs/13-perspective.md) | The positions behind the design, what we refuse to claim, and five mistakes that changed the build |
| [**How we enter the chain**](docs/10-adoption.md) | Market-entry diagrams, the four adoption wedges, and why anyone opts in |
| [**Roadmap**](docs/14-roadmap.md) | Stages, exit gates, and what is deliberately not built |

A file-by-file reference for the whole repo is in
[docs/02-file-reference.md](docs/02-file-reference.md). Measured results are
committed in [metrics/](metrics/README.md) and regenerated by
`python scripts/export_metrics.py` — nothing there is typed by hand.

---

## Layout

```
apps/api/          FastAPI service + telemetry ingest worker
apps/simulator/    physics-informed hive simulator
apps/intelligence/ ML inference workers
apps/web/          Next.js consumer verify page
packages/schema/   wire contracts shared by everything
packages/contracts/ Solidity contracts + Hardhat
infra/             docker-compose stack + SQL init
scripts/           seeding and verification utilities
ml/                training code, 4 models, ablation
firmware/          ESP32-S3 sketch                 (stage 3)
docs/              documentation
metrics/           committed model + ledger results
```
