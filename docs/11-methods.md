# Methods, standards, and what everyone else is doing

A review of the verification methods actually available, the standards this
sector has already converged on, and where our design should change as a result.

---

## 1. The five independent axes of verification

The critical property is that **each axis fails differently**. No single method
is sufficient, and the reason a layered system works is that beating all of them
at once costs more than the fraud is worth.

| # | Axis | What it proves | Beaten by | Marginal cost |
|---|---|---|---|---|
| 1 | **Volumetric** (what we built) | Declared quantity is consistent with measured production and with every downstream handover | Lying at the source within the envelope; dilution at packing | ~zero, continuous |
| 2 | **Chemical** | The liquid is honey, not syrup | Tailored syrups engineered against the specific test | ₹750–3,200+/sample |
| 3 | **Biological origin** (pollen DNA) | It came from *these* plants in *this* region | Nothing cheap | ~₹3,000–8,000/sample |
| 4 | **Geochemical origin** (trace elements, Sr isotopes) | It came from *this* soil | Nothing cheap; needs a reference database | Lab-grade |
| 5 | **Physical** (seals) | This container was not opened; this jar is from this run | Not sealing at all | ₹0.30–15/unit |

We currently do 1 and 5, and consume 2 as an attestation. **We do nothing on 3
and 4, and that is the gap worth closing** — because axes 3 and 4 verify the
*origin claim our own ledger makes*, from a completely independent direction.

### 1a. Chemical testing is a ladder, not a test

This is what the industry actually does, and it directly answers "who pays":

```
goods-in        → NIR / Raman / e-nose screen      ~₹0 per sample after hardware
suspicious      → C4 sugar, EA/LC-IRMS             ~₹750–1,500
high risk       → NMR profiling                    ~₹3,000+
tailored syrup  → LC-HRMS                          highest, specialist labs
periodic        → pollen analysis                  routine verification
```

Key facts that shape the design:

- **The mandated C4/IRMS test is structurally blind to C3 syrups** — rice, beet,
  wheat, inulin — because their isotopic signature overlaps genuine honey. This
  is not a gap in enforcement, it is a gap in physics. It is exactly how
  Honeygate worked.
- **LC-IRMS is the recognised gold standard** (Global Honey Organisation), but
  **NMR is better on C3** and **LC-HRMS is what catches syrups tailored to beat
  both**. No single assay is sufficient.
- Nobody tests everything. A practical programme is risk-based (**VACCP** —
  Vulnerability Assessment and Critical Control Points, the food-industry
  standard) and focuses testing where fraud is "both plausible and
  consequential."

**This is where our AI earns its keep.** Not by classifying bee sounds — by
deciding which 5% of batches get the ₹3,000 test. A fraud risk score that
allocates a fixed testing budget optimally is worth more to a regulator than any
individual detector.

### 1b. Field screening: what is actually deployable

| Method | Hardware cost | Reported performance | Honest caveat |
|---|---|---|---|
| **BME688 gas sensor + ML** | ~₹1,500 | distinguishes mixtures to ~5% resolution; one CNN study reports 100% | Small lab datasets; 100% on a lab set is a warning sign, not a result |
| **Portable NIR** (e.g. MicroNIR) | ₹3–8 L | 100% sensitivity/specificity in one study | **Calibration transfer between instruments is a real failure mode**; needs large datasets spanning botanical and geographic variability; **degrades at ≤10% adulteration** |
| **Hyperspectral imaging** | high | benchtop-comparable | Not field kit |

The ≤10% weakness matters less than it sounds: real adulteration runs 40–80%,
which is what makes it profitable. But it means a field screen is **triage that
decides what gets sent to a lab**, never a verdict. Any pitch claiming otherwise
is wrong.

### 1c. Origin verification — the axis we are missing

**Pollen DNA metabarcoding** identifies both botanical *and* geographical origin,
detects more taxa than microscopy with better resolution and repeatability, and
is described in the literature as easily scalable. Chemometrics/ML improves it
further.

**Trace elements** — Sr, P, Mn, K — cluster honey by geographic origin, and
strontium isotope work on Indian samples exists as a feasibility study.

Why this matters for us specifically: our chain asserts *"72.4% Sitapur,
Uttar Pradesh."* Today nothing independently checks that assertion. Pollen
metabarcoding does, from a direction that has nothing to do with our sensors,
our custody records, or anyone's honesty.

---

## 2. What everyone else is doing

### True Source Certified (the incumbent, US-led)

Voluntary hive-to-table traceability with third-party audit, lab testing and
record-keeping. Annual recertification; **one audit in three is unannounced**.

*What to take from it:* the unannounced audit is the enforcement teeth. Our risk
score should **trigger** unannounced physical audits rather than replace them.
*Where we differ:* they audit paperwork once a year; we hold continuous sensor
evidence, which changes what an audit can check.

### The EU (enforcement-led)

The **"From the Hives" coordinated action** (18 member states, OLAF-led,
JRC-analysed) found **46% of 320 border samples strongly suspected of
adulteration** — up from 14% in 2015. 44 operators investigated, 7 sanctioned.

Two things worth carrying: (a) this is a *more recent and more international*
datapoint than Honeygate, and (b) the Global Honey Organisation publicly called
the report "a scientific debacle," so cite it as *suspicion* rather than proven
adulteration. Overstating it is the kind of thing an informed judge catches.

### GS1 — EPCIS 2.0 and Digital Link

**This is the finding that should change our build.**

EPCIS 2.0 is the open global standard for recording supply-chain events —
Critical Tracking Events, the same model FDA FSMA Rule 204 requires — and 2.0
explicitly added **sensor data and certifications** to event messages. GS1
Digital Link turns a QR code into a resolvable URI that serves checkout, the
consumer, and a product passport at once. **Retail is migrating to 2D GS1 QR by
around 2027.**

We invented our own `custody_events` model and our own QR URL scheme. Both do
the job, and both make us an island.

### ISO 22095 — chain of custody vocabulary

Published 2020. Five models: **Identity Preserved, Segregation, Mass Balance,
Book and Claim, Controlled Blending.**

We have been calling our invariant "mass balance." **In ISO terms that is the
wrong word, and it undersells us.** Mass Balance permits mixing certified with
*non-certified* material as long as quantities are controlled. Our contract does
not allow that at all — `blend()` only accepts inputs that are already on-chain
verified batches.

Correctly stated:

| Our artefact | ISO 22095 model |
|---|---|
| Raw batch from one apiary | **Identity Preserved** |
| Blend of several verified apiaries | **Segregation** |
| What we do *not* permit | Mass Balance / Book and Claim |

Using the standard vocabulary correctly is free, and it lets a certification
auditor place us immediately.

### India, pharmaceuticals — the precedent that answers "why would they"

India **already mandates** per-unit QR authentication on a mass-market consumer
product: barcodes/QR on the **top 300 drug brands from 1 August 2023**, and on
all APIs from 1 January 2023, originating with a DTAB proposal in 2019.

This is the single best answer to *"why would a manufacturer ever use our QR?"*
Because in a directly comparable sector, the government made it a condition of
licence — and the industry, which also had no commercial incentive, complied. The
mechanism exists, the precedent exists, and the regulator is the same kind of
body.

### Madhukranti (the incumbent we must not duplicate)

NBB + Indian Bank, blockchain traceability "under development." As of
14 October 2025: **14,859 beekeepers, 269 societies, 150 firms, 206 companies**
against 20 lakh+ registered colonies. A registry with thin adoption, not a
chain of custody.

---

## 3. Why blockchain agri-traceability projects fail

The literature is unusually blunt, and it reads as a checklist of how we could
fail. Worth quoting because each line has a design answer:

| Documented failure mode | Our position |
|---|---|
| "Lack of standardization" is a **primary** barrier | **We are guilty.** Fix: adopt GS1 EPCIS 2.0 + ISO 22095 vocabulary |
| Semantic interoperability failures, 23% metadata degradation across chains | Same fix — a standard schema, not ours |
| "Consensus mechanisms unsuited for high-frequency agricultural data" | Already right: telemetry stays in TimescaleDB, only hashes anchor |
| "Economic models excluding 70% of smallholder producers" | Already right: gasless custodial keys, no per-farmer cost, 10–15% sentinel coverage |
| "Most describe conceptual frameworks or pilot implementations rather than fully realized systems" | Already right: ours runs end to end |
| "Blockchain treated as a mere technological component rather than the foundational trust orchestration layer" | Already right: the chain enforces invariants, it is not a log |

Five of six we are on the correct side of. The sixth — standardisation — is the
one we should fix now, while it is cheap.

---

## 4. The strategic reframe: build the reference database India does not have

FSSAI declined to mandate NMR testing citing, among other things, **"lack of a
database for Indian honey."** That is the stated blocker, from the regulator,
in public.

Meanwhile, every method in section 1 — NMR models, NIR chemometrics, pollen
metabarcoding, trace-element and strontium origin attribution — **requires a
reference database of authentic samples with known provenance to calibrate
against.** For Indian honey, that database is thin: one global study of 612
samples included just 42 from India.

Look at what our system produces as a by-product. For every batch it knows:

- the exact apiaries and GPS coordinates it came from
- the flora profile and the season
- the hive telemetry across the whole production window
- the custody chain, unbroken, to the sealed jar

**That is precisely a labelled authentic-reference sample.** We are structurally
the best-placed entity in India to build the reference dataset, because we are
the only one that knows, with sensor evidence, where each sample actually came
from.

This changes the positioning:

> Not "another honey traceability app," but **the instrument that generates the
> national honey authenticity reference dataset** — the thing FSSAI itself named
> as the blocker to better testing.

It is also a defensible moat: a competitor can copy the app in a month and
cannot copy four seasons of provenance-verified samples.

---

## 5. What I would change, in priority order

### 5.1 Adopt GS1 identifiers, EPCIS 2.0 events and Digital Link QR — **DONE**

Cheap now, expensive to retrofit, and it converts us from a bespoke island into
something an exporter, a retailer, or a regulator can already read.

- **GLN** for actors and locations, **GTIN** for packed products, **SSCC** for
  logistic units (drums), **GTIN + serial** for the jar.
- Map `custody_events` to EPCIS **Critical Tracking Events** (Commission,
  Aggregation, Transformation, Observation). Our mint/transfer/process/blend/pack
  map onto these one-for-one — we effectively rediscovered them.
- Emit the consumer QR as a **GS1 Digital Link URI** so the same code serves the
  shelf scanner and the consumer page.
- Keep the chain exactly as it is. EPCIS is the *interchange* format; the
  contract stays the enforcement layer.

### 5.2 Correct the chain-of-custody vocabulary to ISO 22095 — **DONE**

A documentation and messaging change, not a code change. We do Identity
Preserved and Segregation — say so.

### 5.3 Add origin verification as an independent axis

Pollen DNA metabarcoding, triggered by risk score rather than run on everything.
Anchor the result as an `Attestation`. This is the first check that can catch a
beekeeper or aggregator lying about *where* honey came from, which nothing in
the current design detects.

### 5.4 Make the risk score drive a testing ladder (VACCP) — **DONE**

Implemented in `apps/api/aurabee_api/risk.py`. Eight weighted signals —
envelope utilisation, unverified surplus, sentinel coverage, blend breadth,
processing loss, actor history, scan anomalies, broken transport seals — produce
a score that selects a tier:

```
score < 0.30   screen     Rs 0      field gas sensor / NIR
      < 0.55   c4_irms    Rs 1,200  C3/C4 by EA/LC-IRMS
      < 0.78   nmr        Rs 3,000  1H NMR profile
      >= 0.78  lc_hrms    Rs 8,000  LC-HRMS + pollen DNA origin
```

`GET /api/admin/testing-plan?budget_inr=` spends a fixed budget greedily by
score — the difference between testing 3% of batches at random and testing the
3% most likely to be wrong.

Three constraints are load-bearing and enforced in the code:

- **Every score is explainable.** `reasons` returns the signals in words,
  ordered by how much each actually moved the number. A score that decides a
  rural beekeeper's honey gets flagged is not deployable as a bare float.
- **A high score is not an accusation.** It allocates a test; the test decides.
- **Absence of evidence raises risk, and that is not the producer's fault.** Low
  sentinel coverage means we know less, so we test more — and the reason text
  says exactly that rather than implying suspicion.

A blend has no envelope of its own (its constituents were each bounded when
minted), so it **inherits the worst score among its inputs** rather than being
penalised for a gap that is not a gap. Verified by
`scripts/verify_risk_ladder.py`, which applies each factor in turn, asserts the
score rises and the tier escalates, and restores the database afterwards.

### 5.5 Field screen at intake

A BME688 gas sensor (~₹1,500) per collection centre, with a model trained on
graded syrup spikes. Feeds the risk score. Presented as triage, never a verdict.

### 5.6 Then the ML models

Unchanged from the plan, but re-ordered *behind* the risk ladder, because the
ladder is what makes the models actionable.

---

## Sources

**Detection methods**
- [Eurofins honey authenticity testing](https://www.eurofins.com/en-de/food-testing/services/authenticity/honey-authenticity-testing/) · [NMR + LC-IRMS platform (Food Chemistry)](https://www.sciencedirect.com/science/article/abs/pii/S0308814623004429)
- [LC-IRMS as gold standard — Global Honey Organisation](https://www.globalhoney.org/news-gho/lc-irms-validated-gold-standard-for-honey-authenticity/)
- [Portable vs benchtop NIR and hyperspectral (Sensors 2026)](https://www.mdpi.com/1424-8220/26/9/2750) · [Portable NIR for origin + adulteration (Foods)](https://www.mdpi.com/2304-8158/13/19/3062)
- [Gas sensor + ML (npj Science of Food, 2025)](https://www.nature.com/articles/s41538-025-00440-9)
- [Pollen DNA metabarcoding, Indonesia case](https://pmc.ncbi.nlm.nih.gov/articles/PMC11211895/) · [regional provenance, Australia](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8258210/) · [chemical/molecular/AI review 2025](https://www.sciencedirect.com/science/article/abs/pii/S0889157525007896)
- [Carbon isotopes + trace elements for origin (Sci Reports)](https://www.nature.com/articles/s41598-018-32764-w) · [strontium isotopes — PNNL](https://www.pnnl.gov/publications/analysis-explores-honey-strontium-isotopes-determine-geographic-origin)

**Standards**
- [GS1 EPCIS & CBV](https://www.gs1.org/standards/epcis) · [EPCIS 2.0 guide](https://www.qrstuff.com/feeds/blog/gs1-epcis-20-traceability-food-supply-chain) · [OpenEPCIS FAQ](https://openepcis.io/faq/)
- [ISO 22095 chain of custody — preview](https://webstore.ansi.org/preview-pages/ISO/preview_ISO+22095-2020.pdf) · [the five models explained](https://www.circularise.com/blogs/four-chain-of-custody-models-explained)
- [EU Digital Product Passport](https://single-market-economy.ec.europa.eu/single-market/digital-product-passport_en) — note: **food and feed are outside ESPR scope**; the architecture is a pattern, not an obligation

**What others do**
- [True Source Certified standards V8](https://www.truesourcehoney.com/assets/docs/June%201%202025%20TSC%20Operating%20Standards%20V8.pdf)
- [EU "From the Hives" results](https://eucrim.eu/news/from-the-hives-results-of-the-eu-action-against-honey-adulteration/) · [JRC](https://joint-research-centre.ec.europa.eu/jrc-news-and-updates/food-fraud-how-genuine-your-honey-2023-03-23_en) · [GHO rebuttal](https://www.globalhoney.org/news-gho/eu-from-the-hives-report-a-scientific-debacle/)
- [India pharma QR mandate, top 300 brands](https://thehealthmaster.com/2023/07/27/bar-code-or-qr-code-for-300-brand/) · [RAPS on phased transition](https://www.raps.org/resource/asia-pacific-roundup-india-expands-mandatory-use-of-qr-codes-for-traceability-starts-phased-transition.html)

**Failure modes**
- [Survey of blockchain agricultural traceability evaluation](https://www.sciencedirect.com/science/article/pii/S0168169924009396) · [systematic review of frameworks](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10453023/) · [digitalisation in EU agri-food (Frontiers 2025)](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2025.1701872/full)
- [VACCP food fraud vulnerability assessment](https://www.foodengineeringmag.com/articles/95205-vaccp-haccp-for-vulnerability-assessments)
