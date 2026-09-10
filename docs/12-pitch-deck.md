# AuraBee — Honey Chain

### A blockchain that refuses claims the physics does not support

**Problem Statement ID:** SIH26021
**Title:** Blockchain-based traceability and smart beekeeping ecosystem for KVIC's Honey Mission
**Theme:** Agriculture, FoodTech & Rural Development · **Category:** Software
**Team ID / Team Name:** [fill in]

Working prototype, not a concept — **821,448 telemetry rows · 5 smart contracts, 38 tests passing · 25 on-chain invariants independently re-verified · 5 models serving · 3 licence-verified real datasets.**

---

# Proposed Solution

## Blockchain does not stop honey fraud. It makes a lie immutable.

A beekeeper with 50 hives declares 3,000 kg. A traceability chain records it faithfully, forever, and hands it a trust badge. This is the **oracle problem** — the unaddressed hole in nearly every blockchain-for-traceability project.

## We make the sensor layer the oracle

`hive telemetry → AI yield forecast → P90 signed on-chain as a mint ceiling → declaration above it REVERTS in the contract`

Mass conservation is then enforced at every custody hop. Blending computes constituent origin percentages — producing the EU 2024/1438 declaration as a by-product, not a form someone fills in.

## What each gate stops

| Fraud route | What stops it |
|---|---|
| Inflated harvest at source | Declared kg ≤ P90 from that apiary's own sensors |
| Many small over-declarations | The cap is **cumulative** across the season |
| Honey that never entered | Transfers must come from the holder of record |
| Dilution while processing | `Σ out ≤ Σ in × (1 − declaredLoss)` |
| **Dilution at packing — where it really happens** | Jars × net weight ≤ honey that exists **AND** a passing independent lab report |
| Photocopied QR | Per-jar serialised seal + HMAC under scratch-off, first-scan anchored |

## Why it is different

- **Yield-bound issuance** — others record claims, we bound them. One loop, not three parallel demos.
- **Sentinel economics** — instrument 10–16% and extrapolate. This is what makes 2.46 lakh boxes affordable.
- **Standards-native** — GS1 EPCIS 2.0, GS1 Digital Link, ISO 22095, not a private format.
- **Independently verifiable** — every claim re-derivable from chain state alone.
- **Integrates with Madhukranti** rather than duplicating it.

---

# Technical Approach

## Three interlocking loops

- **A — Sense→Bound:** `node → signed MQTT frame → TimescaleDB → health + yield models → signed YieldEnvelope → mint gate`
- **B — Custody:** `beekeeper → collection centre → FPO → processor → brand → consumer`, gated on every arrow
- **C — Verify (feeds back):** `scan → first-scan anchor → clone/geo check → risk score → VACCP test tier → lab attestation → into the packing gate`

Loop C returning into Loop B is what most designs miss: scans and lab results are **inputs to the next gate**, not just a public page.

## Custody workflow — what is enforced, and whose permission we need

| | 1 BEEKEEPER | 2 COLLECTION | 3 FPO | 4 PROCESSOR | 5 BRAND | 6 CONSUMER |
|---|---|---|---|---|---|---|
| **Captured** | Hourly telemetry, harvest declaration | Lot weight, moisture, photo | Constituent lot IDs + kg | Input lots, output jars, loss % | SSCC pallet/carton IDs | First-scan time + coarse geo |
| **Enforced** | ≤ P90 ceiling | From holder of record | `Σout ≤ Σin×(1−loss)`, origin % computed | Jars × wt ≤ honey that exists **AND** passing lab report | Seal binding immutable | Seal exists, activated, not scanned elsewhere |
| **Permission** | None — took a KVIC subsidy | None — a scan, not an audit | None — FPO is KVIC's own unit | **Not needed — we certify their input, not their process** | None | None |

## Stack

**Device** ESP32-S3 · HX711 load cell · 2× SHT41 · I²S mic · IR counters · solar — **₹2,500–4,000/node**
**Transport** MQTT QoS 1, **per-frame HMAC** (broker is the least trusted hop) · LoRa
**Data** TimescaleDB hypertables · **ML** scikit-learn + PyTorch CNN + classical CV
**Chain** Hyperledger Besu IBFT2, Solidity 0.8.28 — permissioned governance *and* the same Solidity as public chains
**Apps** FastAPI · Next.js SSR verify page + offline-first Hindi PWA

## Every required element, delivered

| Required | Delivered | Evidence |
|---|---|---|
| Blockchain traceability, hive→jar | 5 contracts, 4 gates, Merkle-anchored seals | 25 invariants, 0 broken |
| QR consumer verification | SSR page, 3 verdicts, clone + geo detection | GS1 Digital Link |
| Batch traceability | Mint → transfer → process → blend → pack | `/proof/[batch]` |
| **Hive disease detection** | 8-class classifier + anomaly detector + varroa CNN | 97.4% acc; queenless **0.981** |
| **Environmental monitoring** | In-hive/ambient temp-RH, weight, acoustics, traffic | 821,448 signed rows |
| **Yield prediction** | P10/P50/P90 + conformal calibration | P90 covers **100%** of actual yields |
| **Rural deployment framework** | Sentinel sampling, cluster economics, Madhu Mitra | 8 nodes / 50 hives = 16% |

## Measured results

| Model | Result |
|---|---|
| Colony health (8 classes) | **97.4%** on 121,422 rows from 20 hives in a **separate run**; queenless 0.981, pre-swarm 0.989 |
| Yield forecaster | P90 coverage **100%** after conformal calibration; +45% headroom |
| Anomaly detector | robbing **0.997** AUC, queenless 0.913; **1.0%** false alarms |
| Varroa on **real** images (CC BY 4.0) | **89.3% accuracy, 0.921 AUC** on 3,408 images from 2 unseen sessions |
| Sensor ablation | Dropping the mic: 0.748 → 0.483 macro F1; swarm recall 0.990 → 0.365 |

---

# Feasibility and Viability

## It already runs

Simulator → signed ingest → TimescaleDB → 5 models → on-chain gates → FPO/processor/KVIC consoles → consumer verification → independent verifier → committed metrics. The device layer is an **interface**: simulator and firmware emit identical signed frames, so nothing above MQTT changes when hardware arrives.

## Verification you can run yourself

Most prototypes ask to be believed. Every claim has a command behind it.

- `verify_ledger.py` — re-derives 25 invariants from **chain state alone**; our database supplies only batch codes, so our own error cannot make a broken ledger look sound. **16 batches, 25 invariants, 0 broken.**
- `demo_flow.py` — 9 steps end to end, **exits non-zero if any guarantee fails**, and asserts each rejection came from the *intended* contract error.
- **38 contract tests** · `export_metrics.py` regenerates every number from training artefacts, so nothing is retyped.

## Economics

Per cluster of 50–200 beekeepers: capex ≈ (0.1 × hives × ₹3,000) + ₹12,000 gateway; opex = SaaS/beekeeper + **₹0.30–1 per jar seal**. Funded from **NBHM's ₹500 crore** outlay — we plug into a budget rather than create one.

## Risks and mitigations

| Risk | Strategy |
|---|---|
| Beekeeper lies at source | Ceiling from their own telemetry; sentinels extrapolate; high risk score triggers audit + higher test tier |
| QR photocopied | Serialised per-jar seal, HMAC under scratch-off, first-scan anchor, geo-velocity checks |
| Why would a brand adopt? | KVIC funds the boxes and can condition the subsidy; EU deadline **14 June 2026**; honest processors want the gate; verified origin earns a premium |
| Testing cannot cover everything | VACCP ladder — screen → C4/IRMS ₹1,200 → NMR ₹3,000 → LC-HRMS ₹8,000 by risk score |
| Rural connectivity | LoRa store-and-forward, offline-first PWA, SMS/IVR fallback |
| Low digital literacy | Voice-first vernacular UX, advice not class names, Madhu Mitra coordinator per cluster |
| Calibration before it gates income | P90 refitted on measured district harvests during the pilot; +45% headroom so honest beekeepers are never the constrained ones |

**Data provenance is enforced in code** — the fetcher refuses any dataset lacking a licence, DOI and attribution. We rejected the best hive-weight dataset available because it declares no licence.

---

# Impact and Benefits

## The problem, in numbers

| Pressure | Evidence |
|---|---|
| Adulteration is systemic | CSE "Honeygate": **77% of samples from 13 leading brands** failed; only 3 of 13 passed NMR |
| Tests are beatable by design | Rice syrup is a **C3** sugar; the mandated **C4 (IRMS)** test structurally cannot see it |
| Lab-first cannot scale | FSSAI declined to mandate NMR on cost and skill. No lab answer exists for **2,46,099** bee boxes |
| Producers lose the value | Farmers realise ~**25%** of retail price; direct linkage has lifted income ~**40%** |
| A hard export deadline | **#2 global exporter**, USD **177.55 M** FY24. EU 2024/1438 applies **14 June 2026** |
| Existing portal is a registry | Madhukranti lists ~**14,859 beekeepers** against **20 lakh+** colonies — no telemetry, no jar QR |

## Who benefits

- **Beekeeper** — disease and swarm alerts in Hindi from day one, *before* any traceability benefit. Queenless recall **0.981** catches colony loss while it is still recoverable.
- **KVIC / NBB** — scheme attribution: which of the 2,46,099 boxes actually produce. Today that is invisible.
- **FPO / processor** — provable mass conservation on their own inbound supply.
- **Exporter / brand** — the EU origin declaration generated from the custody graph.
- **Consumer** — a jar checkable in under a second, with three honest verdicts.
- **Regulator** — a brand whose verified inbound is a fraction of declared output is an enforcement **lead**.

## Social · Economic · Environmental

**Social** — restores trust in a product bought for health; gives smallholders a verifiable identity; Madhu Mitra creates local skilled work.
**Economic** — shifts margin from intermediaries to producers; protects a USD 177 M export line; market grows ₹28.89 B (2025) → ₹51.9 B (2034).
**Environmental** — early swarm, queenless and varroa detection reduces colony loss, and colony survival is a **pollination** outcome long before it is a honey one.

**We are also building the dataset India does not have.** Every public bee dataset is *Apis mellifera*. ***Apis cerana indica*** has none. Every cluster we deploy creates one.

---

# Research and References

**Problem evidence** — CSE Food Fraud / Honeygate investigation · *Down To Earth*, "how the magic syrup beat Indian testing protocols" · FSSAI response on NMR cost and utility

**Policy** — KVIC Honey Mission · NBHM ₹500 crore outlay (PIB) · Madhukranti portal, National Bee Board · AgriStack Farmer Registry

**Regulation** — EU Directive **2024/1438** on honey origin labelling · CBI, stricter EU traceability requirements

**Standards adopted** — GS1 **EPCIS 2.0** + Core Business Vocabulary · GS1 **Digital Link** · **ISO 22095** chain of custody (Identity Preserved, Segregation) · **VACCP** vulnerability assessment

**Datasets used — all licence-verified and MD5-checked**
· To bee or not to bee — Zenodo `10.5281/zenodo.1321278`, CC BY 4.0
· VarroaDataset — Zenodo `10.5281/zenodo.4085043`, CC BY 4.0
· MSPB — Zenodo `10.5281/zenodo.8371700`, CC BY-NC 4.0
· Open Source Beehives — Zenodo `10.5281/zenodo.321345`, CC BY 4.0

**Technical literature** — MDPI *Sensors* review of smart-beehive technologies (2025) · *Bee Together*, Sensors 2024, hive-level evaluation methodology · UrBAN, *Nature Scientific Data* 2025 · Varroa detection by deep learning (PMC) · TinyML for hive acoustics (PMC)

**Reproduce every number in this deck**
```
python ml/fetch_datasets.py --all      # licence-checked, MD5-verified
python scripts/export_metrics.py       # regenerates metrics/ from artefacts
python scripts/verify_ledger.py        # 25 invariants from chain state alone
python scripts/demo_flow.py            # 9-step proof, exits non-zero on failure
```
