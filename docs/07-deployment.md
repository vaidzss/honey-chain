# Deployment framework

A required deliverable of SIH26021 ("a scalable deployment framework for
implementation across rural beekeeping clusters"), and the part most teams treat
as an afterthought.

## The unit is a cluster, not a hive

**One cluster = 50–200 beekeepers around one KVIC or FPO collection centre.**

| Component | Quantity | Notes |
|---|---|---|
| Sentinel nodes | ~10–15% of hives | ESP32-S3, ~₹2,500–4,000 BOM |
| LoRa gateway | 1 | ~₹8,000–15,000, covers 5–10 km of apiaries |
| Madhu Mitra coordinator | 1 | An existing KVIC trainer or CSC village entrepreneur |
| Seals | per jar | ₹0.30–1 each |

### The sentinel idea is what makes national scale affordable

You do **not** instrument every box. KVIC has distributed 2,46,099 bee boxes; at
₹3,000 per node that is ₹738 crore of hardware, which is not happening.

Instead instrument ~10–15% as sentinels and extrapolate health and yield
envelopes to sibling hives in the same apiary, using beekeeper app inspections
as low-frequency ground truth. This cuts hardware cost roughly 7–10x.

The honesty requirement: extrapolation is recorded. Every `yield_envelopes` row
carries a `coverage_ratio` saying how much of the envelope was measured versus
inferred. A batch from a low-coverage apiary is not fraudulent, but it is less
strongly attested, and the consumer page can say so.

The seeded reference cluster (`scripts/seed.py`) runs at 16% coverage: 8 nodes
across 50 hives.

## Rollout path

1. **One pilot cluster** — Sitapur, UP. Mustard belt, real KVIC territory.
2. **Five clusters across two states** — validates that the flora calendar and
   yield model generalise beyond one district, which is the main technical risk.
3. **State-wide via Khadi Boards** — they already have the field staff.
4. **National**, integrated with NBHM.

## Built for this from day one

- **Multi-tenant.** Cluster and state isolation is in the schema, not retrofitted.
- **Offline-tolerant.** LoRa store-and-forward at the gateway, an offline-first
  PWA with a local queue, and SMS/IVR fallback for feature phones. Rural
  connectivity is the norm to design for, not an edge case.
- **i18n from the start.** Adding Marathi later must not be a refactor.
- **Low digital literacy.** Voice-first vernacular prompts, icon-driven flows,
  zero crypto vocabulary, and a human coordinator in the loop. The Madhu Mitra
  is not a nice-to-have — a human layer is what makes rural technology actually
  get adopted.

## Integrate, do not duplicate

| System | How we connect |
|---|---|
| **Madhukranti** (NBB + Indian Bank) | Publish and reconcile registrations. It is a registry; we are the sensing and verification layer above it. |
| **AgriStack Farmer ID** | Identity anchor, stored as a salted hash. |
| **ONDC** | Seller node for market linkage. |
| **APEDA / EIC lab network** | Source of `attestations`. |
| **Open API** | So brands and state boards can build on top rather than around. |

On a government-sponsored problem statement, "we integrate with your existing
portal" scores considerably better than "we built a second one".

## Unit economics, per cluster of 100 beekeepers / ~1,000 hives

| Item | Cost |
|---|---|
| Sentinel nodes (120 at ₹3,000) | ₹3.6 L |
| LoRa gateways (2) | ₹0.25 L |
| Installation and training | ₹0.5 L |
| **Capex** | **~₹4.35 L** |
| Platform SaaS | per beekeeper per month |
| Seals | ₹0.30–1 per jar |

Against this: NBHM carries a ₹500 crore outlay and KVIC's Honey Mission is an
ongoing budget line. **We are not creating a funding requirement, we are
plugging into one that already exists.**

## Governance

Besu validators run by KVIC, the National Bee Board, a state Khadi board and an
FPO federation. Four parties who do not fully trust each other, each running a
node — which is the actual reason a chain is warranted here, and a model a
reviewer can picture.
