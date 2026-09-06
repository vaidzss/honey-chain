# Pitch deck content — AuraBee / Honey Chain

SIH 2026 · PS **SIH26021** · KVIC, Ministry of MSME

**Part A** is the six-slide idea-submission deck, written to the official SIH
template. **Part B** is reserve material — everything that does not fit in six
slides, kept for the finale demo and for Q&A.

Every number is quoted from `metrics/` or from a cited public source. Charts to
drop in are already rendered as PNGs in `metrics/`.

---

# PART A — the six-slide submission

The SIH template fixes both the slide count and the headings. Do not rename
them; evaluators score against these five sections.

| # | Fixed heading |
|---|---|
| 1 | Title / team details |
| 2 | **Proposed Solution** |
| 3 | **Technical Approach** |
| 4 | **Feasibility and Viability** |
| 5 | **Impact and Benefits** |
| 6 | **Research and References** |

**The single most important thing about this deck:** you have a *working
prototype*, which almost nobody does at idea-submission stage. Every section
below is written to make that visible with a measured number rather than an
adjective.

---

## Slide 1 — Title

Template fields only. Fill in:

- **Problem Statement ID:** SIH26021
- **Problem Statement Title:** Blockchain-based traceability and smart
  beekeeping ecosystem for KVIC's Honey Mission
- **Theme:** Agriculture, FoodTech & Rural Development
- **PS Category:** Software
- **Team ID / Team Name:** [fill]

If the template leaves any room, add one line under the title:

> *Working prototype: 821,448 telemetry rows, 4 trained models, 5 smart
> contracts, 25 ledger invariants verified.*

That line does more work than anything else on the slide.

---

## Slide 2 — Proposed Solution

### Layout

Left two-thirds: the text below. Right third: the yield-bound issuance diagram.
Keep the diagram — it is the whole idea in five boxes.

### The problem with every other answer (2 lines, top of slide)

> **Blockchain does not stop honey fraud — it makes a lie immutable.** A
> beekeeper with 50 hives declares 3,000 kg; a traceability chain records that
> fraud faithfully, forever, and issues it a trust badge.

This is the **oracle problem**. It is the hole in essentially every "blockchain
for agri-traceability" project, and stating it first is what separates this from
the other submissions on this PS.

### Our solution

**We make the IoT layer the oracle that bounds what the chain will accept.**

```
   hive telemetry  ->  AI yield forecast  ->  signed YieldEnvelope (P90 kg)
                                                        |
                                                        v
   harvest declaration  ------------------>  HoneyBatch.mint()
                                                REVERTS if declared > P90
```

Then **mass conservation is enforced at every custody hop**: what leaves a
processor cannot exceed what entered it, minus a declared loss. Blending records
constituent origin percentages — which produces the EU origin declaration as a
by-product rather than a form someone fills in.

### How it addresses the problem (4 bullets, keep them this short)

- **Adulteration at source** — declared kilograms are capped by that apiary's
  own sensor-derived forecast.
- **Dilution at packing** — jars issued cannot exceed the honey that exists, and
  no seals are issued at all without a passing independent lab report.
- **Counterfeit jars** — per-jar serialised seals with first-scan anchoring, not
  a photocopyable batch QR.
- **Hive losses** — the same telemetry drives disease and swarm alerts in Hindi,
  so the beekeeper gets value on day one, before any traceability benefit
  arrives.

### Innovation and uniqueness

- **Yield-bound issuance** — the differentiator. Others record claims; we bound
  them. This fuses blockchain, AI and IoT into **one loop** instead of three
  parallel demos.
- **Sentinel economics** — instrument ~10–16% of boxes, not all of them, and
  extrapolate. This is what makes 2.46 lakh boxes arithmetically possible.
- **Standards-native, not a private format** — GS1 EPCIS 2.0 events, GS1 Digital
  Link QR, ISO 22095 Identity Preserved custody.
- **Integrates with Madhukranti rather than duplicating it.**

---

## Slide 3 — Technical Approach

### Layout

**One combined diagram across the top half** — the supply chain horizontally,
the technology stack beneath it. This is the slide where your architecture and
your farmer-to-consumer flow have to share space, and a single diagram doing
both is stronger than two cramped ones.

```
 BEEKEEPER  --(1)->  COLLECTION  --(2)->   FPO    --(3)->  PROCESSOR --(4)->  RETAIL --(5)->  CONSUMER
 50 hives            weigh-in,             pools           filters,           cartons,        scans
 8 sentinel          scan-to-receive       lots            blends, packs      shelf           the jar
 nodes (16%)

 GATES ENFORCED ON CHAIN
 (1) declared kg <= P90 ceiling from that apiary's own telemetry
 (2) transfer must come FROM the holder of record -- no orphan lots
 (3) sum(out) <= sum(in) x (1 - declaredLoss); origin % computed, not typed
 (4) jars x net weight <= honey that exists  AND  a PASSING lab attestation exists
 (5) seal is serialised per jar, first scan anchors time + coarse geography

 ------------------------------------------------------------------------------------------

 DEVICE          INGEST              INTELLIGENCE          CHAIN + API           CLIENTS
 ESP32-S3 /      HMAC verify ->      health classifier     Besu (IBFT2)          beekeeper PWA
 simulator       replay reject ->    yield forecaster      5 Solidity contracts  FPO / processor
 MQTT QoS 1      TimescaleDB         anomaly detector      FastAPI + Postgres    KVIC dashboard
 signed frames   hypertable          varroa counter (CV)   MinIO for PDFs        /verify/[serial]
```

**Draw gate 4 with a heavier border.** Dilution actually happens at packing, and
that is why gate 4 carries two independent conditions instead of one.

### Technologies (compact list, small type)

- **Device:** ESP32-S3, HX711 + load cell, 2× SHT41, INMP441 I²S mic, IR
  entrance counters, GPS, solar + LiPo — **~₹2,500–4,000/node**
- **Transport:** MQTT (Mosquitto) QoS 1, topic per node, per-frame HMAC; LoRa
  gateway in field
- **Data:** TimescaleDB hypertables + continuous aggregates (Postgres for
  relational)
- **AI/ML:** scikit-learn — HistGradientBoosting classifier, quantile regressors
  with conformal calibration, one-class IsolationForest, OpenCV-style classical
  CV
- **Chain:** Hyperledger Besu (IBFT2, permissioned), Solidity 0.8.28, Hardhat;
  `Registry`, `YieldOracle`, `HoneyBatch`, `Attestation`, `SealRegistry`
- **Backend/Frontend:** FastAPI · Next.js (SSR verify page, offline-first PWA,
  Hindi + English)
- **Standards:** GS1 EPCIS 2.0, GS1 Digital Link, GTIN-14/GLN-13/SSCC-18, ISO
  22095

### Methodology — and what already runs

Say this as evidence, not as a plan. **All of the following is built and
measured**, with results committed in `metrics/`:

| Component | Measured result |
|---|---|
| Colony health classifier | **97.4%** accuracy; queenless recall **0.981**, pre-swarm **0.989**; tested on a *separate* simulation run (20 hives, 121,422 rows) |
| Yield forecaster | P90 ceiling covers **100%** of actual yields after conformal calibration (91.5% before), +45% headroom |
| Anomaly detector | robbing AUC **0.997**, queenless 0.913; **1.0%** false-alarm rate |
| Varroa counter | 12.5% mean error on synthetic boards; **refuses** blurred photos rather than guessing |
| Smart contracts | **38 tests passing** |
| Ledger verification | **25 invariants across 16 batches, 0 broken** — re-derived from *chain state only* |

**Weakest number, stated deliberately:** varroa recall is **0.510**. It is a
three-week decline that looks like "slightly less healthy", so the alerting
layer requires 0.88 confidence before varroa may interrupt a beekeeper.

---

## Slide 4 — Feasibility and Viability

### Feasibility — the argument is that it already exists

- **A full vertical slice runs today:** simulator → signed ingest →
  TimescaleDB → 4 models → on-chain gates → FPO/processor/KVIC consoles →
  consumer verification → an independent ledger verifier.
- **The device layer is an interface, not a dependency.** The simulator and the
  ESP32 firmware emit identical signed frames, so nothing above MQTT changes
  when hardware arrives.
- **Cost is the real feasibility question, and sentinel sampling answers it.**
  Per cluster of 50–200 beekeepers: capex ≈ (0.1 × hives × ₹3,000) + ₹12,000
  gateway; opex = SaaS per beekeeper + ₹0.30–1 per jar seal.
- **The money already exists.** NBHM's ₹500 crore outlay and the Honey Mission
  line item. We are not creating a budget, we are plugging into one.

### Challenges and how we handle them

Use a two-column table. This is the section evaluators read most carefully —
naming a real risk scores better than claiming none.

| Challenge | Our strategy |
|---|---|
| **The beekeeper could still lie at source** | We reduce the fraud surface, we do not claim to eliminate it: the ceiling comes from that apiary's own telemetry, sentinels extrapolate to uninstrumented hives, and a high risk score triggers a physical audit and a higher test tier |
| **A QR code can be photocopied** | Serialised per-jar seal + short HMAC under a scratch-off; first scan anchors time and coarse geography; scan-count and geo-velocity anomalies raise a public warning |
| **Why would a processor or brand adopt this?** | Four wedges: KVIC funds the boxes and can make it a condition; EU 2024/1438 gives exporters a June-2026 deadline we generate the dossier for; honest processors want the gate as a defence against their own supply being diluted; verified origin earns a consumer premium. **We never ask to enter anyone's lab — only for the lab's signed report** |
| **Rural connectivity** | LoRa store-and-forward, offline-first PWA with a device-generated idempotency key, SMS/IVR fallback |
| **Low digital literacy** | Voice-first vernacular UX, advice sentences not class names, and a trained "Madhu Mitra" coordinator per cluster — the human layer is what makes rural tech actually adopt |
| **Model generalisation** | Every number we report is **simulator** performance. Most public bee acoustics are *Apis mellifera*; *Apis cerana indica* has no public dataset. The P90 must be refitted on measured district harvests before deployment — a ceiling calibrated on a simulator would block real beekeepers |
| **Testing cannot cover everything** | VACCP risk ladder: screen → C4/IRMS ₹1,200 → NMR ₹3,000 → LC-HRMS ₹8,000, with the tier chosen by the batch's risk score |

---

## Slide 5 — Impact and Benefits

### Target audience and impact

| Who | What changes |
|---|---|
| **Beekeeper** | Disease and swarm alerts in Hindi from day one — value before any traceability benefit. Verified provenance is a route out of the ~**25% of retail price** farmers currently capture; direct linkage has lifted beekeeper income ~**40%** |
| **KVIC / NBB** | Scheme attribution: which of the **2,46,099** distributed boxes are actually producing. Today that is invisible |
| **FPO / processor** | A defensible document that their own supply was not diluted upstream |
| **Exporter / brand** | The **EU 2024/1438** origin-percentage declaration, generated from custody, before the **14 June 2026** deadline. India is the **#2 global exporter** (~1.07 lakh MT, **USD 177.55 M** FY24) |
| **Consumer** | A jar that can be checked in under a second, with three honest verdicts — verified, unverified, suspicious. Against a market where CSE found **77% of samples from 13 leading brands** adulterated |

### Benefits

- **Social** — restores trust in a product bought largely for health reasons;
  gives smallholders a verifiable identity in the value chain.
- **Economic** — moves margin from intermediaries to producers; protects a
  **USD 177 M** export line that a single EU rejection could damage; the market
  grows ₹28.89 B (2025) → ₹51.9 B (2034).
- **Environmental** — early swarm, queenless and varroa detection reduces colony
  loss, and colony survival is a pollination outcome long before it is a honey
  outcome.

### One line to close the slide

> We are also building the dataset India does not have: there is no public
> *Apis cerana indica* acoustic corpus, and every cluster we deploy creates one.

---

## Slide 6 — Research and References

Group them; do not paste a flat list of twenty URLs.

**Problem evidence**
- CSE Food Fraud / Honeygate investigation — https://www.cseindia.org/food-fraud-10493
- "It's not honey: how the magic syrup beat Indian testing protocols", Down To Earth — https://www.downtoearth.org.in/health/it-s-not-honey-how-the-magic-syrup-beat-indian-testing-protocols-74496
- FSSAI response on NMR cost and utility — https://businesstoday.in/current/corporate/fssai-clarifies-on-cse-claims-regarding-adulteration-in-honey-samples/story/423867.html

**Policy and scheme context**
- KVIC Honey Mission — https://www.drishtiias.com/daily-updates/daily-news-analysis/kvics-honey-mission
- NBHM (₹500 crore outlay), PIB — https://www.pib.gov.in/PressReleasePage.aspx?PRID=2185400
- Madhukranti portal, National Bee Board — https://www.drishtiias.com/daily-updates/daily-news-analysis/madhu-kranti-portal-honey-corners

**Regulatory driver**
- EU honey origin-labelling rules (Directive 2024/1438) — https://agriculture.ec.europa.eu/farming/animal-products/honey_en
- CBI: stricter traceability in the EU honey market — https://www.cbi.eu/news/stricter-traceability-requirements-are-taking-over-european-honey-market

**Standards adopted**
- GS1 EPCIS 2.0 and CBV — https://www.gs1.org/standards/epcis
- ISO 22095 chain of custody — https://webstore.ansi.org/preview-pages/ISO/preview_ISO+22095-2020.pdf

**Technical literature**
- MDPI *Sensors*, review of smart-beehive technologies — https://www.mdpi.com/1424-8220/25/17/5359
- CNNs on constrained devices for hive acoustics — https://pmc.ncbi.nlm.nih.gov/articles/PMC11479142/
- Varroa counting by deep learning — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11207890/
- UrBAN multimodal beehive dataset — https://arxiv.org/pdf/2406.03657
- Blockchain adoption in beekeeping, *Frontiers* — https://www.frontiersin.org/journals/sustainable-food-systems/articles/10.3389/fsufs.2025.1566341/full

**Datasets used or evaluated**
- Zenodo *To bee or not to bee* · Kaggle *Smart Bee Colony Monitor* · TFDS
  `bee_dataset` · Roboflow varroa sets

---

# PART B — reserve material

Not for the six slides. This is what you say when asked, and what goes into the
finale deck once the slide limit lifts.

## The four questions you will be asked

**"Why blockchain at all? A database would do."**
Multiple parties who do not trust each other — beekeeper, FPO, processor, brand,
domestic regulator, foreign importer — plus an EU regulator who from June 2026
needs verifiable origin percentages, and a liability trail nobody can quietly
edit after the fact. Then say plainly what it does *not* solve. That candour
scores better than a defensive answer.

**"What stops the beekeeper lying at source?"**
Nothing stops it entirely, and we do not claim otherwise. The ceiling comes from
that apiary's own telemetry, sentinel extrapolation covers uninstrumented hives,
and a high risk score triggers a physical audit and a higher test tier.

**"Your QR can be photocopied."**
A batch-level QR could be. Ours is serialised per jar with a short HMAC under a
scratch-off, first-scan anchored to time and coarse geography, with scan-count
and geo-velocity anomalies raising a public warning.

**"Isn't this just Madhukranti again?"**
Madhukranti is a registry of who exists — ~14,859 beekeepers against 20 lakh+
colonies, with no telemetry and no jar-level QR. We measure what those hives
actually produced and refuse claims beyond it. We want to write into
Madhukranti, not replace it.

## The ablation result — your best "real science" moment

`metrics/sensor_ablation.png`

| Sensor set | macro F1 | pre_swarm recall |
|---|---|---|
| everything | **0.748** | 0.990 |
| no load cell | 0.739 | 0.988 |
| microphone only | 0.693 | 0.990 |
| **no microphone** | **0.483** | **0.365** |
| scale only | 0.433 | 0.426 |

The microphone is the highest-value single sensor and swarm prediction is almost
entirely acoustic (0.990 → 0.365 without it). **Permutation importance told us
the opposite and was wrong** — it ranked mass features on top, because the 33
acoustic features are correlated and permuting one at a time barely moves the
score while the other 32 carry the same information. Ablation by sensor *group*
is the honest measurement. We walked into a standard trap and this is how we
caught it.

## The conformal calibration bug

Our first calibration returned a factor of exactly **1.000** and did nothing.
The calibration hives shared a weather seed with the fit hives while the test
run did not — and **conformal guarantees require exchangeability**, which a
weather shift breaks. Training across four independent weather realisations
fixed the underlying gap. Coverage went 74.6% → 91.5% → **100%** at ×1.0332.
100% on 59 windows is consistent with true coverage anywhere above ~95%; it is
not evidence of perfection, only of erring in the safe direction.

## The demo script — 9 steps, exits non-zero on failure

`scripts/demo_flow.py`: publish envelope → honest mint succeeds → **inflated
mint reverts** → **cumulative cap reverts** → custody transfer → blending under
mass conservation **and its rejection** → origin percentages → Merkle-anchored
seals → **genuine jar verifies, cloned jar refused**. It asserts each rejection
came from the *intended* error, not merely that something failed.

## Competition, if asked

| Who | The gap we occupy |
|---|---|
| BeeHero, ApisProtect, Pollenity, Arnia, Beewise | Priced for US/EU commercial pollination fleets. None do provenance or consumer verification |
| TraceX, Farmonaut, SourceTrace | Generic B2B traceability SaaS. No hive-side sensing — traceability is self-declared data entry, which *is* the oracle problem |
| Madhukranti | A registry, not a chain of custody. Integrate, do not compete |
| Other SIH teams | form → Postgres → hash → testnet → QR page. They record claims; we bound them |

## Assets

| Use | File |
|---|---|
| Model results | `metrics/colony_health_recall.png` |
| Anomaly / ablation | `metrics/anomaly_auc.png`, `metrics/sensor_ablation.png` |
| Ledger proof | screenshot of `/proof/[batch]` |
| Consumer flow | `/verify/[serial]` — genuine and cloned verdicts |
| Admin view | `/console/admin` |

Chart palette, so the deck matches: ink `#1a1a17`, muted `#6b6b63`, rules
`#e6e2d6`, ground `#fbfaf6`, green `#1a7f4b`, honey `#d98c00`, red `#b3261e`.
