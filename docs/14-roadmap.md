# Roadmap

Where this is, what comes next, and what has to be **true** before each stage
starts. Every stage below has an exit gate; a stage is not finished because the
code runs, it is finished when the gate passes.

Status of individual components lives in [README.md](README.md) — this document
is about sequence and dependency.

---

## Stage 0 — Telemetry spine · **done**

The device layer as a swappable interface, and data that survives the trip.

- Physics-informed hive simulator with 9 fault scenarios and fast-forward replay
- Signed frame contract in `packages/schema/`, shared by simulator and firmware
- MQTT QoS 1 ingest with HMAC verification, replay rejection and a dedicated
  writer thread
- TimescaleDB hypertable + continuous aggregates
- Seeded Sitapur reference cluster: 4 beekeepers, 50 hives, 8 sentinel nodes

**Gate passed:** zero frame loss under sustained load, and
`scripts/verify_ingest_security.py` proves a forged signature, a replayed
sequence and an unregistered node are all rejected while exactly one legitimate
frame is stored.

---

## Stage 1 — Chain and traceability spine · **done**

- 5 Solidity contracts (`Registry`, `YieldOracle`, `HoneyBatch`, `Attestation`,
  `SealRegistry`), 38 tests passing
- Yield-bound issuance, cumulative across a season
- Mass conservation through transfer, processing and blending, with constituent
  origin percentages computed rather than declared
- Per-jar serialised seals under a Merkle root, one root per packing run
- Output cap and passing-lab-report gate on seal issuance
- Consumer verification, clone detection, three honest verdict states
- GS1 EPCIS 2.0 events, GS1 Digital Link QR, GTIN/GLN/SSCC identifiers
- VACCP risk ladder and testing budget allocator

**Gate passed:** `scripts/verify_ledger.py` re-derives 25 invariants across 16
batches from **chain state alone**, 0 broken. `scripts/demo_flow.py` runs 9
steps end to end and asserts each rejection came from the *intended* contract
error.

---

## Stage 2 — Intelligence · **done**

- Colony health classifier — 97.4% accuracy, queenless recall 0.981, tested on a
  separate simulation run
- Quantile yield forecaster with conformal calibration — P90 coverage 100%
- One-class anomaly detector — robbing AUC 0.997, 1.0% false alarms
- Classical-CV varroa counter that refuses unusable photos
- Sensor ablation study
- Alerting layer with per-class confidence floors, persistence and deduplication
- Beekeeper app in Hindi and English, offline harvest queue
- FPO, processor and KVIC consoles; `/metrics` and `/proof/[batch]`

**Gate passed:** every number reproducible from `metrics/`, regenerated from the
training artefacts rather than retyped.

---

## Stage 3 — One real node · **next, ~30 days**

The single highest-value remaining item, because it converts "the simulator
says" into "this hive says".

- ESP32-S3 sketch (PlatformIO) reimplementing `canonical_string()` and the HMAC
  in C, publishing to the same MQTT topic
- HX711 + load cell calibration against known masses
- On-node acoustic feature extraction, so only features cross the LoRa link —
  a bandwidth argument that is also a privacy argument
- Field power budget on a small solar panel

**Also in this window:** service worker for offline *reads* (the write queue
already works), inspection logging with photos.

**Exit gate:** a physical hive node on the table, publishing frames
indistinguishable from the simulator's to everything above MQTT, with nothing
downstream changed to accommodate it. If any code above the broker had to
change, the device abstraction failed and that is the finding.

---

## Stage 4 — Pilot cluster · **blocked on access, not on code**

One KVIC or FPO cluster: 50-200 beekeepers, ~10-16% sentinel coverage, one LoRa
gateway, one trained Madhu Mitra coordinator.

The work here is **not** software:

- Refit the yield P90 on **measured harvests from that district**. Until this
  happens the ceiling is calibrated on a simulator and must not gate a real
  person's income.
- Collect the first *Apis cerana indica* acoustic data. No public dataset
  exists; this is where it starts.
- Validate the varroa counter against real sticky boards with counts done by
  hand. The current 12.5% is against synthetic boards and is a regression test,
  not field accuracy.
- Measure what the Madhu Mitra layer actually costs in hours per beekeeper.

**Exit gate — the honest one:** the refitted P90 must not block a beekeeper who
declared truthfully. We would rather let some fraud through to the testing
ladder than block one honest harvest, because the first is a cost and the second
ends adoption.

**What we need:** a cluster to work in, and Madhukranti registry API access for
reconciliation.

---

## Stage 5 — Multi-cluster and integration

- 5 clusters across 2 states, multi-tenant isolation exercised for real
- Publish into and reconcile with **Madhukranti**; AgriStack Farmer ID as the
  identity anchor
- Migrate from local Anvil to **Hyperledger Besu** with real validators — KVIC,
  NBB, a state Khadi board and an FPO federation. A governance model someone can
  picture, not "we deployed to a testnet"
- Spatial extrapolation model from sentinel to sibling hives, validated against
  manual inspections, with a stated uncertainty
- EU 2024/1438 dossier generation exercised against a real consignment before
  **14 June 2026**

**Exit gate:** a second cluster onboards without engineering involvement. If it
needs us, it does not scale.

---

## Stage 6 — Scale

State-wide via Khadi Boards, then national. Regional ingest, per-cluster
gateways, ONDC-ready market linkage. Nothing in the current design blocks this,
but nothing in it is proven at that size either.

---

## Deliberately not built, and why

| Not built | Why not, and what would change it |
|---|---|
| Distributed ingest tier, queue between ingest and DB | Correct for many clusters, premature for one. Build it when a second region exists, not before |
| Trained YOLO varroa detector | Blocked on an annotated Indian sticky-board dataset that does not exist. A model trained on European boards carries a domain gap we could not measure. Stage 4 creates the data |
| Fraud-graph ML model | The rule layer is honest and explainable today. A learned model needs real fraud cases, and we have none — synthetic fraud would train a detector for our own imagination |
| Spectral / BME688 adulteration screening | Genuinely promising, but it is a *screening triage* and must never be presented as a lab replacement. Needs its own calibration set of pure honey and graded syrup spikes |
| Pollen DNA metabarcoding for origin | The strongest independent origin axis that exists, and out of reach at this budget. Worth naming as the direction rather than pretending the current origin claim is as strong |
| On-node TinyML acoustic inference | Stage 3 does feature extraction on-node; full inference waits until there is real audio to quantise against |

---

## The dependency that governs everything

```
  real hive node  -->  real telemetry  -->  refitted P90  -->  a ceiling that
   (stage 3)            (stage 4)            (stage 4)         can gate income
```

Every model number in this repo is simulator performance. The chain gates are
real, the contracts are real, the invariants are verified — but the *ceiling*
those gates enforce is only as good as the forecast behind it, and that forecast
has never seen a real harvest. Stage 4 is not a scaling milestone. It is the
point at which this becomes true rather than demonstrable.
