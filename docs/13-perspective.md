# Our perspective

Why this project is built the way it is. Architecture is in
[01-architecture.md](01-architecture.md); this document is about the judgement
calls behind it, including the ones we got wrong first.

---

## 1. Six positions we hold

### 1.1 A blockchain that records claims is worse than no blockchain

It gives a lie permanence and a trust badge. If a beekeeper with 50 hives
declares 3,000 kg, a naive traceability chain records that faithfully and
forever, and the QR code on the jar now *vouches* for it.

This is the oracle problem, and it is the unaddressed hole in most
"blockchain for agri-traceability" work. We treat it as the first design
constraint rather than a limitation to mention at the end.

### 1.2 The sensor layer's job is to bound the ledger, not to decorate it

IoT is not a second feature next to the chain. It is the thing that makes the
chain's contents mean something. Hive telemetry produces a yield forecast; the
P90 of that forecast becomes a contract-enforced ceiling on mintable kilograms.
Three technologies, one loop.

If you removed the sensors from this project, the blockchain would stop being
worth deploying. That dependency is the point.

### 1.3 Measurement, not audit

The instinct is to audit the supply chain, which requires access to exactly the
parties with the strongest reason to refuse. That system cannot be built.

We measure production at the hive, record handovers as scans, and attach
results from an **independent** lab. None of those need a manufacturer's
cooperation. We certify their *input*, not their process — which makes us a
property of the honey they buy rather than a burden imposed on them.

### 1.4 The beekeeper has to get value before the system gets any

A traceability platform that helps brands and regulators, and asks the
beekeeper to do data entry for it, will not be used. Disease and swarm alerts
in Hindi arrive on day one; the traceability benefit arrives months later at
harvest. The order matters.

This is also why the beekeeper app never says "blockchain", "wallet" or "mint",
and why alerts carry a sentence of advice rather than a class name. `varroa`
never reaches a person.

### 1.5 Standards over cleverness

A private event format that only we can read is a liability dressed as
engineering. GS1 EPCIS 2.0 for custody events, GS1 Digital Link for the jar QR,
ISO 22095 for chain-of-custody vocabulary, GTIN/GLN/SSCC identifiers with real
check digits.

We implement **Identity Preserved** and **Segregation**, and deliberately do
*not* claim "mass balance" — a weaker model under which honey loses its
identity in the books, and which is often quietly used to mean the opposite of
what a consumer assumes.

### 1.6 A number we cannot reproduce is not a result

Every metric in this repo is regenerated from the artefact that produced it.
`scripts/export_metrics.py` reads each model's `.meta.json` and writes
`metrics/`; nothing is retyped, so the summary cannot drift from the models.
`scripts/verify_ledger.py` re-derives every chain invariant from **chain state
alone** — the database supplies only the list of batch codes, so an error in
our own database cannot make a broken ledger look sound.

The weak numbers are published as prominently as the strong ones, because the
first person to ask "what is your worst class?" will find it in thirty seconds
anyway.

---

## 2. What we refuse to claim

This list is load-bearing. Each item is something we could say, that would help
us, and that is not true.

- **Not** "AuraBee ends honey adulteration in India." We make a positive,
  checkable claim about jars carrying our seal, and no claim at all about jars
  that do not.
- **Not** that an unscanned jar is fake. It may be from a producer who is
  simply not on the platform, and the consumer page says exactly that. The
  verify page has **three** verdict states — verified, unverified, suspicious —
  because collapsing the middle one into "suspicious" would be a lie that
  trains people to ignore the warning.
- **Not** that our model accuracy is field accuracy. Every model number in this
  repo was measured on **simulated** hives. It shows the models learned the
  physics the simulator encodes. It is not evidence they work on real bees.
- **Not** that we can stop a determined adulterator. We raise the cost, shrink
  the surface, and make the absence of proof conspicuous. That is an asymmetry,
  not a cure.
- **Not** that the mass market adopts this voluntarily. Stages 0-2 need nobody's
  consent; stage 4 probably needs regulation. See
  [10-adoption.md](10-adoption.md).

Where evidence is thin, the system says **less evidence**, not **more
suspicion**. That distinction runs through the risk scoring, the consumer page
and the alerting thresholds.

---

## 3. Five mistakes, and what each one changed

These are in the repo because they are the most useful part of it. Each was
caught by building the thing rather than by reasoning about it.

### 3.1 Permutation importance told us to cut the microphone

It ranked mass features on top and acoustics far below. We nearly took that as a
BOM decision — a microphone, its amplifier and DSP duty cycle are a real share
of node cost and power.

**Ablation by sensor group said the opposite.** Without the microphone, macro F1
falls 0.748 → 0.483 and pre-swarm recall collapses 0.990 → 0.365. The 33
acoustic features are correlated, so permuting one at a time barely moves the
score while the other 32 carry the same information. A standard trap, and we
walked straight into it.

*Changed:* the microphone stays in the BOM; `ml/ablation.py` now derives its
written conclusion from its own results table rather than restating it from
memory, so it cannot go stale.

### 3.2 Conformal calibration returned exactly 1.000 and did nothing

The calibration hives shared a weather seed with the fit hives, while the test
run did not. **Conformal guarantees require exchangeability**, and a weather
shift breaks it. The calibration was silently a no-op.

*Changed:* training across four independent weather realisations. Coverage went
74.6% → 91.5% → 100.0% at a factor of ×1.0332. At the first fit, a quarter of
honest beekeepers would have been blocked from selling their own honey.

### 3.3 The simulator had no beekeeper in it

Our first dataset had 33% of samples labelled `dead` and 39 of 45 hives dead by
the end, because faults were modelled with no management — nobody fed a starving
colony, treated mites or requeened, so every fault ran to death. That is not an
apiary anyone would still own, and a model trained on it learns that faults are
terminal.

*Changed:* `Colony.beekeeper_visit()` inspects every ~11 days with *imperfect*
detection. Annual loss fell to a realistic ~9%, with faults appearing as
episodes rather than death spirals.

### 3.4 A blurred photo returned "no mites found" at 0.85 confidence

The most dangerous output the system could produce: the beekeeper concludes the
colony is clean and does not treat.

*Changed:* a Laplacian-variance sharpness gate. The counter now **refuses** and
says why. Refusing is a feature; a confident wrong answer about a treatable
parasite is not.

### 3.5 The consoles reported 290 hives out of 50

A JOIN fan-out. Aggregates multiplied across joined rows, so KVIC saw a 3.86
sentinel share and an FPO showed 2,696 kg against a 1,012 kg ceiling — numbers
that were not merely wrong but *impossible*, and would have been caught on stage
by anyone doing arithmetic.

*Changed:* every aggregate in `consoles.py` is a scalar subquery. The wider
lesson we took: a number that violates its own bound is a bug in the query, not
a finding — and dashboards need invariants as much as ledgers do.

---

### 3.6 A join key that looked right and produced a publishable lie

Testing our yield claim against real colonies (MSPB, 53 hives), the first run
reported honey **R² = +0.136, "beats baseline"**. A modest, believable,
entirely publishable number.

It was an artefact. We joined the sensor stream to the outcomes on
`beehub_name`, which looks like a hive identifier and is actually the *apiary* —
**two values for fifty-three colonies**. Every colony received one of two
feature vectors, so the model was learning an apiary mean and calling it
acoustics. The correct key is `tag_number − 200000`. With the join fixed, R²
moved to **−0.296**: no signal at all.

What caught it was not a test. It was two lines of log output that could not
both be true — *"2 colonies in the sensor stream"* directly above *"53 colonies
join on both sides"*.

*Changed:* the script now asserts that distinct feature vectors are at least
half the number of colonies and refuses to report a score otherwise. The wider
lesson is the one that keeps recurring here: **the dangerous bug is not the one
that crashes, it is the one that returns a plausible number.** A negative result
would have been believed. So would the wrong positive one.

---

## 4. How we decide what to build next

In this order:

1. **Does it close a hole a judge or an auditor could put a finger through?**
   The output cap and the lab-report gate were built because the answer to
   "where would you actually add syrup?" was "at packing, and nothing stops it."
2. **Does the beekeeper get something?** Features that only serve the platform
   wait.
3. **Can we prove it works without being trusted?** If a claim needs our
   database to be believed, it needs an independent verifier before it ships.
4. **Does it survive the field?** Offline-first, low literacy, 2G, a ₹6,000
   phone, and a node that has to run on a small solar panel.

What we deliberately have **not** built: a distributed ingest tier, a message
queue between ingest and the database, a trained YOLO varroa detector. The first
two are correct for one cluster and premature before it; the third is blocked on
an annotated Indian sticky-board dataset that does not exist. We would rather
ship a classical CV counter that refuses bad input than a neural detector with a
domain gap we cannot measure.

---

## 5. The gap we are honest about

Most public bee acoustics are *Apis mellifera*. ***Apis cerana indica***, common
in Indian beekeeping, is acoustically different and **has no public dataset**.

Every model here is trained on simulated hives, and the yield P90 — the number
that becomes an on-chain ceiling and therefore decides whether a real person can
sell their honey — must be refitted on measured harvests from the target
district before any deployment. A ceiling calibrated on a simulator would block
real beekeepers, which is the one failure that ends adoption.

That gap is also the opportunity: every cluster we deploy builds the dataset
India does not have. We would rather say that than quote someone else's accuracy
figure.
