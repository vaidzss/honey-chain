# Blockchain design

> **Status: implemented and tested.** Five contracts in
> `packages/contracts/contracts/`, 38 tests passing (`npm run chain:test`),
> deployed locally and exercised end to end by `scripts/demo_flow.py`.
>
> Solidity 0.8.28, pinned to the **Cancun** EVM because OpenZeppelin 5.6 uses
> the `mcopy` opcode. Deployment targets must be Cancun-capable; Besu 24.x and
> Polygon Amoy both are.

## Why a chain is justified here

A judge will ask, and "immutability" is not a sufficient answer. The honest one:

- **Multiple mutually-distrusting parties** write to the same record —
  beekeeper, collection centre, FPO, processor, NABL lab, brand, KVIC, and a
  foreign importer. No single one of them should own the database that decides
  whose honey is real.
- **A foreign regulator needs verifiable origin percentages.** EU Directive
  2024/1438 (applying from 14 June 2026) requires blends to declare countries of
  origin in descending order with percentages. A tamper-evident custody graph
  produces that as a by-product.
- **Liability needs to survive the vendor.** An adulteration finding two years
  later must be traceable even if the platform operator has changed.

And what a chain does **not** solve, said plainly: it cannot tell whether the
kilograms entering it were real. That is what the yield oracle is for.

## Chain choice

**Hyperledger Besu**, permissioned IBFT2. Validators run by KVIC, NBB, a state
Khadi board and an FPO federation — a governance model a reviewer can picture,
rather than "we deployed to a testnet".

Because Besu is EVM, the same Solidity runs on a local Anvil node during
development and on Polygon Amoy for a public demo. One codebase, three targets.

## Contracts

Five contracts, not seven. `ActorRegistry` and `ApiaryRegistry` merged into
`Registry` (they share an authorisation model), and `Custody` merged into
`HoneyBatch` (splitting them would have meant a cross-contract operator dance
for no gain).

| Contract | Responsibility |
|---|---|
| `Registry` | Identities and apiaries, role-gated. `canActAs` is the single choke point for the custodial model. `agristackHash` is a salted hash, never the raw Farmer ID. |
| `YieldOracle` | Accepts envelopes `(apiaryId, season, maxGrams, evidenceHash, coverageBps, modelVersion)` from registered oracle keys. Immutable per revision. |
| `HoneyBatch` (ERC-1155) | Grams as balance per batch id. **Both invariants live here**: envelope-capped minting and mass-conserving process/blend. Raw ERC-1155 transfers are disabled. |
| `SealRegistry` | One Merkle root per packing run plus a jar count, rather than a storage slot per jar. |
| `Attestation` | `(subject, kind, docHash, issuer, expiry)` plus scaled integer metrics, for lab reports and certificates. |

**Units are grams, as integers, everywhere.** There is no float in Solidity and
a mint ceiling is the last place to discover a rounding difference. The
kg-to-gram conversion happens in exactly one place, `apps/api/aurabee_api/chain.py`,
and rounds *down* so it can never invent a gram telemetry did not support.

## The two invariants that matter

### 1. Yield-bound issuance

```solidity
// HoneyBatch.mintHarvest
uint256 cap = oracle.maxGrams(apiaryId, season);
uint256 used = _mintedBySeason[apiaryId][season];
uint256 remaining = cap > used ? cap - used : 0;
if (grams > remaining) revert ExceedsYieldEnvelope(grams, remaining);
```

`_mintedBySeason` is the part that matters. Capping each individual declaration
is easy and useless: ten batches each comfortably under the ceiling add up to
well over it. The cap is cumulative per apiary per season, and there is a test
for exactly that attack.

Note also that **no envelope means nothing may be minted**, not "unlimited". An
apiary with no telemetry cannot sell through the platform, which is the safe
default.

The P90 of the forecast distribution is the ceiling, not the P50 — the point is
to catch fraud, not to punish a good season. An apiary that genuinely
outperforms can still mint, flagged as `unverified_surplus_kg`, which appears as
a visible caveat on the consumer page. **Blocking honest beekeepers would kill
adoption faster than fraud would.**

### 2. Mass conservation

**Terminology, corrected.** We called this "mass balance" for a while. Under
**ISO 22095:2020** — the international chain-of-custody standard — that is the
wrong word, and it understates what the contract does. Mass Balance permits
mixing certified material with *non-certified* material provided quantities are
controlled. `HoneyBatch.blend()` refuses that outright: every input must already
be a verified on-chain batch.

| Our artefact | ISO 22095 model |
|---|---|
| Raw batch from a single apiary | **Identity Preserved** |
| Blend of several verified apiaries | **Segregation** |
| Verified mixed with unverified | not permitted |

Identity Preserved and Segregation are the two strongest of the five models.
Using the standard vocabulary costs nothing and lets a certification auditor
place the system immediately.


```
sum(outputs) <= sum(inputs) * (1 - declaredLossFactor)
```

Applied at every custody hop. Processing loss is declared per operation and
bounded; a processor who consistently declares an implausible loss becomes a
fraud case rather than a silent leak. Merges record constituent percentages,
which is what generates the EU origin declaration.

### 3. The output cap, and the lab gate

Two more conditions on `SealRegistry.issueSeals`, both added after working
through where a determined packer could still cheat:

```solidity
if (!attestations.hasValid(batchId, Attestation.Kind.LabReport))
    revert NoPassingLabReport();

uint256 claimed   = uint256(jarCount) * uint256(netWeightG);
uint256 available = batches.totalSupplyOf(batchId);
if (claimed > available) revert OverPacking(claimed, available);
```

**Why this matters more than it looks.** Mass conservation alone catches volume
fraud but *not dilution at packing*: buy 100 kg verified, add 60 kg syrup, pack
160 kg, and the ledger stays internally consistent while being materially false.

Capping seal issuance at the mass that actually exists closes it from the other
side. The packer can still physically bottle 160 kg — but only 100 kg of it can
carry a seal. The surplus becomes unsealed stock, which is worthless in any
channel that requires a seal.

That last clause is the honest caveat: this bites only where seals are
*required* (KVIC's own retail, EU export). The contract creates the asymmetry;
the channel makes it cost something. See [10-adoption.md](10-adoption.md).

The lab gate is the companion: no passing, unexpired, unrevoked NABL report,
no QR codes at all. `Attestation` refuses attestations from a `Brand` role, so
nobody clears their own honey.

## Gasless UX

Beekeepers never touch crypto. Custodial keys are derived per-actor and held
server-side, bound to a phone number; transactions go through an ERC-4337 style
relayer. On Besu, gas is zero anyway — the relayer exists for key custody and UX,
not for fees.

The word "blockchain" should not appear anywhere in the beekeeper app.

## Anti-clone seals

A batch-level QR is one photocopy from worthless. Per jar:

```
serial   : printed openly, encodes batch + jar index
secret   : short HMAC code under a scratch-off or inside the cap
on-chain : H(serial || secret) only
```

Verification flow:

1. Scan the open QR — public journey renders immediately.
2. Optionally enter the scratch-off code for a cryptographic proof of
   authenticity, not just of existence.
3. The first scan anchors timestamp and coarse geohash.
4. Later scans display "first scanned in Sitapur on 12 Aug, scanned 4 times".

Fraud signals: scans from two distant districts inside an implausible window,
scan count far exceeding the batch jar count, or seals appearing after the
batch volume is exhausted. Each raises a public warning banner and opens a
`fraud_cases` row.

## Interoperability: GS1 EPCIS 2.0

The contract is the enforcement layer. **EPCIS 2.0 is the interchange layer**,
and `GET /api/batch/{code}/epcis` renders any batch as a conformant JSON-LD
document a buyer, customs system or ERP can read with no AuraBee-specific
integration.

Our custody kinds turned out to be a rediscovery of EPCIS Critical Tracking
Events, so the mapping is one-for-one:

| Custody kind | EPCIS event | bizStep |
|---|---|---|
| mint | ObjectEvent ADD | commissioning |
| transfer | ObjectEvent OBSERVE | shipping |
| process / merge | TransformationEvent | transforming |
| pack | ObjectEvent ADD | packing |
| lab test | ObjectEvent OBSERVE | inspecting |

Two details worth noting. A batch is an **LGTIN class** in `quantityList` with
`uom: KGM`, because honey in a drum is a mass, not an item; only a sealed jar
becomes a serialised SGTIN. And EPCIS 2.0 added `sensorElementList` and
`certificationInfo`, so the hive telemetry that bounded a declaration and the
NABL lab result that cleared it both travel *inside* the event — the buyer
receives the evidence rather than being asked to trust the number.

The consumer QR is a **GS1 Digital Link** URI:

```
https://aurabee.in/01/{GTIN-14}/21/{jar serial}
```

One code that a retail scanner reads as a GTIN and a phone opens as a web page.
Retail is migrating to 2D codes on exactly this basis by around 2027, so a
bespoke QR is one that has to be reprinted.

> **The company prefix is not ours to invent.** GTINs and GLNs require a
> licensed GS1 Company Prefix (GS1 India for an Indian issuer). The default in
> `packages/schema/python/aurabee_schema/gs1.py` is a development placeholder;
> shipping with invented GS1 keys would collide with someone else's real ones.

## Off-chain anchoring

Raw telemetry is far too voluminous for on-chain storage. Instead, each
`YieldEnvelope` carries an `evidenceHash`: the Merkle root over the telemetry
window and feature vectors that produced the forecast. The raw data stays in
TimescaleDB and can be proven un-tampered on demand without ever being written
to the chain.
