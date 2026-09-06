# Overview

## The problem

KVIC's Honey Mission has put **2,46,099 bee boxes** into rural India since
2017-18, generating ~₹325 crore of beekeeper income. The honey those boxes
produce enters a market with a trust problem:

- CSE's 2020 "Honeygate" investigation found **77% of samples from 13 leading
  brands** adulterated with sugar syrup. Only 3 of 13 passed NMR.
- The syrups are *engineered to beat the statutory tests*. Chinese suppliers
  advertised fructose syrup on Alibaba as C3/C4-test-evading and shipped it to
  India mislabelled as paint pigment. Rice syrup is a C3 sugar; the mandated C4
  (IRMS) test structurally cannot see it.
- FSSAI declined to mandate NMR, citing high cost, no Indian reference database
  and heavy skill requirements. **So no lab-first solution scales** to a quarter
  of a million hives.
- Beekeepers capture roughly **25% of retail price**. Direct market linkage has
  been shown to lift their income ~40%, so the margin exists and is being taken
  by intermediaries.
- India is now the **world's #2 honey exporter**. From 14 June 2026, EU
  Directive 2024/1438 requires every blend to list countries of origin in
  descending order with percentages. Indian exporters largely cannot produce
  that today.

## The core design idea

> **Blockchain does not stop honey fraud.** It makes a lie immutable. If a
> beekeeper with 50 hives declares 3,000 kg, a naive traceability chain records
> that fraud faithfully and forever. This is the classic oracle problem, and it
> is the hole in essentially every "blockchain for agri-traceability" project.

So the IoT layer is not a separate feature bolted on next to the chain. **It is
the oracle that bounds what the chain will accept.**

```
   hive telemetry  ──▶  AI yield forecast  ──▶  signed YieldEnvelope (P90 kg)
                                                        │
                                                        ▼
   beekeeper declares harvest  ──────────▶  BatchToken.mint()  ── reverts if
                                                                  declared > P90
```

Mass conservation is then enforced at every custody hop: what leaves a
processor cannot exceed what entered it, minus a declared loss factor. Blending
records constituent origin percentages — which is exactly what generates the EU
origin declaration as a by-product.

This is what fuses the three technologies into one loop instead of three
parallel demos, and it is the thing to lead with.

### Second differentiator: seals that resist cloning

A batch-level QR code is a photocopy away from worthless. Every jar instead
gets a **serialised** seal with a short HMAC code under a scratch-off. The first
scan binds time and coarse geography; the public page then shows "first scanned
in Sitapur on 12 Aug, scanned 4 times". Scans from implausibly distant places
within a short window, or scan counts exceeding the batch's jar count, raise a
public warning and open a fraud case.

## What we are not claiming

Worth saying plainly, because judges reward candour and punish overreach:

- This **reduces the fraud surface; it does not eliminate fraud.** A determined
  beekeeper can still under-report, and adulteration downstream of a verified
  batch remains possible. Random physical audits triggered by risk score are
  part of the design, not an afterthought.
- The spectral adulteration screen (stretch goal) is a **field triage**, not a
  lab replacement. NABL lab attestations remain the authority.
- Our acoustic models are trained largely on *Apis mellifera* data. Most Indian
  beekeeping under KVIC also uses *A. mellifera*, but *A. cerana indica* is
  acoustically different and we have no public dataset for it. That gap is
  named, not hidden — and closing it is our data moat.

## Positioning against what already exists

| Category | Who | Their gap |
|---|---|---|
| Smart hive hardware | BeeHero, ApisProtect, Arnia, Pollenity, Beewise | Priced for US/EU commercial pollination fleets. No provenance, no consumer verification. |
| Agri traceability SaaS | TraceX, Farmonaut, SourceTrace | Self-declared data entry — the oracle problem, unsolved. No hive sensing, no AI agronomy. |
| Government | **Madhukranti** portal (NBB + Indian Bank) | A registry, not a chain of custody. ~14,859 beekeepers registered against 20 lakh+ colonies. No telemetry, no jar-level QR. |

Our stance towards Madhukranti is **integrate, not compete**: AuraBee is the
sensing and verification layer that writes into it, anchored on AgriStack Farmer
ID. On a government-sponsored problem statement, that reads far better than
building a duplicate.

Full evidence and sources: [09-research.md](09-research.md).
