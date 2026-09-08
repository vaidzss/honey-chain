# AuraBee / Honey Chain — documentation

Blockchain + AI + IoT trust layer for Indian honey.
Built for **SIH 2026 problem statement SIH26021** (KVIC, Ministry of MSME).

## Read in this order

| # | Document | What it covers |
|---|---|---|
| 00 | [Overview](00-overview.md) | The problem, the core design idea, what makes this different |
| 01 | [Architecture](01-architecture.md) | System layers, data flow, technology choices |
| 02 | [**File reference**](02-file-reference.md) | Every file in the repo and what it does |
| 03 | [Data model](03-data-model.md) | Database schema, table by table |
| 04 | [Telemetry contract](04-telemetry-contract.md) | The device wire format and signing scheme |
| 05 | [Blockchain](05-blockchain.md) | Contracts, the two invariants, seals |
| 06 | [ML models & datasets](06-ml.md) | What we train, on what data |
| 07 | [Deployment framework](07-deployment.md) | Cluster economics and national scaling |
| 08 | [Running locally](08-running-locally.md) | Get the whole stack up |
| 09 | [Research & market](09-research.md) | Evidence base, competitors, sources |
| 10 | [**How we enter the chain**](10-adoption.md) | Adoption wedges, why anyone opts in, and what we cannot claim |
| 11 | [**Methods & standards**](11-methods.md) | The five verification axes, what others do, why these projects fail, what to change |
| 12 | [**Pitch deck content**](12-pitch-deck.md) | The six-slide SIH submission deck written to the official template, plus reserve material for the finale |
| 13 | [**Our perspective**](13-perspective.md) | The positions behind the design, what we refuse to claim, and five mistakes that changed the build |
| 14 | [**Roadmap**](14-roadmap.md) | Stages, exit gates, and what is deliberately not built |
| 15 | [**Real data**](15-real-data.md) | Licensed datasets fetched, three models trained on them, and the two that failed |

## Status

**Phase 0 (telemetry spine) and Phase 1 (chain + traceability) are working end to end.**

| Layer | State |
|---|---|
| Infra: TimescaleDB, Mosquitto, Redis, MinIO | working |
| Database schema, 17 tables + hypertable | working |
| Telemetry contract + HMAC signing | working, adversarially tested |
| Hive simulator (physics-informed) | working |
| Telemetry ingest → TimescaleDB | working, zero frame loss verified |
| Smart contracts (5, Solidity 0.8.28) | working, **38 tests passing** |
| Yield envelope estimator | working — **statistical baseline, not yet ML** |
| Chain gateway (web3.py) + relayer | working |
| Per-jar seals + Merkle anchoring | working, Python/Solidity encodings cross-checked |
| Consumer verify page (Next.js) | working |
| Fraud detection (clone, geo, velocity) | working |
| GS1 identifiers, EPCIS 2.0, Digital Link QR | working |
| VACCP risk ladder + testing budget allocator | working, verified |
| Colony-health classifier | **trained** — 97.4% acc, queenless recall 0.98 |
| Quantile yield forecaster | **trained** — conformal-calibrated ceiling |
| Sensor ablation study | done — microphone is the top sensor |
| Beekeeper app (hives, alerts, harvest) | working, Hindi + English |
| Anomaly detector (one-class) | **trained** — robbing AUC 0.997 |
| Varroa counter (classical CV) | working — 12.5% error on synthetic boards |
| **Varroa CNN on real images** | **trained** — 0.893 acc / 0.921 AUC, CC-BY data |
| **Queen detection on real audio** | **measured and NOT shipped** — fails cross-hive |
| **MSPB colony check (real hives)** | **measured** — no signal from season averages |
| Licensed dataset registry + fetcher | working — licence enforced in code |
| Metrics page (`/metrics`) | working |
| Ledger proofs + independent verifier | working — 25 invariants over 16 batches, chain-only |
| FPO / processor / KVIC consoles | working |
| Offline harvest queue | working, idempotent |

| ESP32 firmware | not started |

Reproduce all of it: [08-running-locally.md](08-running-locally.md).
The one-command proof: `python scripts/demo_flow.py`.

## Ports

Two defaults were moved because this machine already had services on them.

| Service | Port | Note |
|---|---|---|
| Postgres/TimescaleDB | **5433** | 5432 is taken by another project's container |
| Web (Next.js) | **3001** | 3000 is taken by another local app |
| API (FastAPI) | 8000 | |
| Chain (Hardhat) | 8545 | |
| MQTT | 1883 / 9001 | |
| MinIO | 9000 / 9090 | |
