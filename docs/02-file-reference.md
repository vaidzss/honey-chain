# File reference

Every file in the repo, what it does, and why it exists. Keep this current —
if you add a module, add a row.

---

## Repository layout

```
aurabee/
  apps/
    api/            FastAPI service + telemetry ingest worker
    simulator/      physics-informed hive simulator
    intelligence/   ML inference workers            (empty, planned)
    web/            Next.js consumer verify page
  packages/
    schema/         wire contracts shared by everything
    contracts/      Solidity contracts + Hardhat
  infra/            docker-compose stack + SQL init
  scripts/          seeding and verification utilities
  ml/               training code, notebooks, models (empty, planned)
  firmware/         ESP32-S3 sketch                 (empty, planned)
  docs/             this documentation
```

---

## Root

| File | Purpose |
|---|---|
| `package.json` | npm workspace root. Holds the `infra:*`, `chain:*`, `web:*` scripts. Workspaces are `apps/web`, `packages/contracts`, `packages/schema`. |
| `.env.example` | Every environment variable the system reads, with dev defaults. Copy to `.env`. |
| `.env` | Local config, git-ignored. Note Postgres is on **5433**, not 5432 — 5432 was already taken on this machine. |
| `.gitignore` | Excludes `node_modules`, `.venv`, build output, `.env`, datasets and model weights. |

---

## `packages/schema/` — the wire contracts

The single source of truth for anything that crosses a process boundary. The
simulator, the ingest service, the firmware and the web app all read from here,
which is what stops them drifting apart.

| File | Purpose |
|---|---|
| `telemetry.schema.json` | **Canonical** JSON Schema for a telemetry frame v1. Documents every field, its range, and why it matters. |
| `index.ts` | TypeScript mirror: `TelemetryFrame`, `AcousticFeatures`, MQTT `topics` helper, shared enums. |
| `package.json` | npm package `@aurabee/schema`. |
| `python/aurabee_schema/telemetry.py` | Python mirror **and the signing implementation**. `TelemetryFrame` dataclass, `canonical_string()`, `sign()`, `verify()`, `is_fresh()`. The canonical string format is a wire contract — see [04-telemetry-contract.md](04-telemetry-contract.md). |
| `python/aurabee_schema/gs1.py` | **GS1 identifiers.** GTIN-14, GLN-13, SSCC-18 with mod-10 check digits (validated against the official GS1 worked examples); EPC URNs (SGTIN / LGTIN / SGLN / SSCC); and Digital Link URI construction and parsing. The company prefix is a development placeholder — real keys need a licensed prefix from GS1 India. |
| `python/aurabee_schema/__init__.py` | Re-exports the public surface. |
| `python/pyproject.toml` | Installable as `aurabee-schema`; installed editable so simulator and ingest share one copy. |

---

## `apps/simulator/` — the hive simulator

Generates telemetry indistinguishable from a real sentinel node. Physics-informed
rather than random, because the health models have to learn something real.

| File | Purpose |
|---|---|
| `aurabee_sim/colony.py` | **The core.** Colony biology: population dynamics, brood, stores, varroa, wax moth, queen state, swarm pressure. Owns `read_temps()` (thermoregulation — the single most important signal in the system), `foraging_activity()`, `entrance_counts()`, `step()`, and the discrete events `do_swarm()`, `do_abscond()`, `harvest()`. |
| `aurabee_sim/weather.py` | Ambient conditions from north-Indian monthly normals: diurnal temperature curve, seasonal RH, monsoon rain days, photoperiod. Per-day anomalies are cached so all hives in an apiary see the same weather. |
| `aurabee_sim/flora.py` | Nectar-flow calendar. Raised-cosine bell per flow window (mustard, litchi, eucalyptus…), plus the June–August monsoon dearth. Mirrors `infra/sql/03_reference.sql`. |
| `aurabee_sim/acoustics.py` | Synthesises the *features a node computes on-device* — 20 band energies, 13 MFCCs, peak and centroid frequency — from a physically-built spectrum: worker hum, harmonic, spectral tilt, queenless tooting/quacking bimodality, robbing broadband lift. |
| `aurabee_sim/scenarios.py` | Fault injection. 11 scenario kinds, each chosen because it produces a signature a specific detector must catch. Parses `kind@node@date[@param]`. |
| `aurabee_sim/runner.py` | The simulation loop. Drives colonies, builds and signs frames, publishes to MQTT with backpressure and an end-of-run drain (without the drain, QoS-1 messages queued in the client are discarded on disconnect). |
| `aurabee_sim/__main__.py` | CLI. Synthetic nodes or `--from-db`, scenario parsing, `--auto-scenarios`, `--dump-csv` for labelled training data. |
| `requirements.txt` / `pyproject.toml` | Dependencies: paho-mqtt, numpy, psycopg, python-dotenv. |

### Simulator CLI quick reference

```bash
# stream a season to MQTT against seeded hives
python -m aurabee_sim --from-db --start 2026-11-01 --days 120 --speed 4000

# generate a labelled dataset, no MQTT
python -m aurabee_sim --hives 40 --days 240 --speed 0 --no-publish \
    --auto-scenarios --dump-csv ../../ml/datasets/sim_v1.csv

# exercise one detector
python -m aurabee_sim --from-db --days 20 --speed 0 \
    --scenario queenless@HN-UP-SIT-0003@2026-11-05
```

`--speed 0` means as fast as possible; `--speed 4000` means 4000 simulated
seconds per wall second.

---

## `apps/api/` — ingest and API

| File | Purpose |
|---|---|
| `aurabee_api/ingest.py` | MQTT → TimescaleDB worker. Verifies the HMAC on **every** frame, rejects replayed sequence numbers and unknown nodes, then hands rows to a dedicated writer thread. The writer thread matters: doing DB writes on paho's network loop backs up the broker queue and mosquitto silently drops QoS-1 messages. |
| `aurabee_api/main.py` | FastAPI app. `/health`, the consumer `/api/verify/{serial}`, batch and envelope reads, and admin overview / fraud / hive endpoints. Boots without a chain rather than refusing to start; only verification degrades. |
| `aurabee_api/chain.py` | **The only code that touches the blockchain.** Loads the deploy-time address book (addresses + ABIs), signs as the custodial relayer, and owns every kg-to-gram conversion. Simulates with `eth_call` before broadcasting, so a revert returns a named custom error instead of an RPC blob and no doomed transaction burns a nonce. |
| `aurabee_api/yields.py` | Yield envelope estimator. Pre-dawn hive weights, positive daily deltas, per-hive surplus, extrapolated to the apiary with an uncertainty band that widens as sentinel coverage falls. Emits P10/P50/P90; the **P90 is the on-chain mint ceiling**. Explicitly a statistical baseline, replaced by the ML model in Phase 2 behind the same signature. |
| `aurabee_api/seals.py` | Per-jar seal generation and the sorted-pair Merkle tree (OpenZeppelin compatible). Secret alphabet drops 0/O/1/I/L, because a person reads these off a scratch panel in bad light. |
| `aurabee_api/store.py` | Postgres persistence shared by the API and the demo scripts: batches, custody, composition, seals, scans, fraud cases. Seal secrets are peppered before hashing so a database dump does not hand over every code in circulation. |
| `aurabee_api/epcis.py` | **GS1 EPCIS 2.0 document generation.** Renders a batch's custody history as conformant JSON-LD: ObjectEvents for commissioning/shipping/packing/inspecting, TransformationEvents for processing and blending, hive telemetry in `sensorElementList` and the NABL lab hash in `certificationInfo`. States its one known deviation (event-ID digest is our own canonicalisation, not the normative pre-hash algorithm) rather than hiding it. |
| `aurabee_api/risk.py` | **The VACCP testing ladder.** Eight weighted, individually-explained signals produce a fraud score that selects a laboratory tier, plus a greedy budget allocator. This is where the AI earns its keep — deciding which 5% of batches justify a Rs 3,000 test. |
| `aurabee_api/health.py` | **Colony-health inference and alerting.** Loads the trained classifier, scores the last N telemetry samples per hive, and writes `hive_events`. Holds the alerting policy: persistence over instants, per-class confidence floors set from measured precision, deduplication, and advice text in Hindi and English. Uses a row window rather than a wall-clock one, because simulated telemetry is stamped with simulated time. |
| `aurabee_api/beekeeper.py` | Endpoints for the beekeeper app: one request per screen, advice rather than class names, idempotent inspection writes keyed on a client-generated uuid. `harvest_context()` is the important one — it returns what may be declared *and why*, before the beekeeper types. |
| `aurabee_api/varroa.py` | **Varroa counting from a sticky-board photo.** Classical CV -- adaptive threshold plus size, aspect and solidity filters -- because there is no annotated Indian sticky-board dataset to train on. Refuses on photos that are too distant, out of focus or over-exposed rather than reporting a confident zero, which is the dangerous failure: a beekeeper who reads "no mites" on an unreadable photo does not treat. |
| `aurabee_api/proof.py` | Model metrics read from each training artefact, and the **ledger proof bundle**: every claim with both sides of its arithmetic, the contract function that enforces it, and the Merkle path for a jar. |
| `aurabee_api/consoles.py` | FPO, processor and KVIC console queries. Every aggregate is a scalar subquery rather than a join -- joining hives to envelopes to events multiplies rows, which is exactly how the first version reported 290 hives out of an actual 50. |
| `aurabee_api/verify.py` | The consumer payload and clone detection. Walks the composition graph upstream so a blended jar shows its whole hive-to-jar journey, and keeps *verified* (Merkle proof against the on-chain root) distinct from merely *known*. |
| `aurabee_api/__init__.py` | Package marker. |
| `requirements.txt` | fastapi, uvicorn, psycopg, paho-mqtt, pydantic, web3, eth-account. |

---

## `infra/` — local stack

| File | Purpose |
|---|---|
| `docker-compose.yml` | TimescaleDB (host port **5433**), Mosquitto (1883 + 9001 ws), Redis, MinIO (9000 API / 9090 console) and a one-shot `minio-init` that creates the media bucket. |
| `mosquitto/mosquitto.conf` | Anonymous listener — trust comes from per-frame HMAC, not the transport. `max_queued_messages` raised to 500000 so a fast-forward simulator burst is buffered rather than discarded. |
| `sql/01_extensions.sql` | `timescaledb`, `pgcrypto`. UUIDs use core `gen_random_uuid()`, so no uuid-ossp. |
| `sql/02_schema.sql` | The full schema — 17 tables, the `telemetry` hypertable, and the `telemetry_hourly` continuous aggregate. See [03-data-model.md](03-data-model.md). |
| `sql/03_reference.sql` | Flora calendar seed: 19 rows of flow windows and peak kg/day for Indian conditions. |

Files in `sql/` run **once**, in filename order, on first boot of an empty
volume. After a schema change run `npm run infra:reset`.

---

## `scripts/`

| File | Purpose |
|---|---|
| `seed.py` | Creates the Sitapur reference cluster: 4 beekeepers, 50 hives, 8 sentinel nodes (16% coverage), plus collection centre, FPO, processor, NABL lab, brand, regulator and admin. `--reset` wipes first. Idempotent otherwise. |
| `verify_ledger.py` | **Independent ledger verification.** Reads only from chain state at the deployed addresses and re-derives every invariant, so an error in our database cannot make a broken ledger look sound. The database supplies the list of batch codes and nothing else. |
| `verify_ingest_security.py` | Adversarial test of the ingest path. Sends a forged signature, a replayed sequence, an unregistered node and one legitimate frame, then asserts exactly one row was stored. Requires the stack and the ingest worker running. |
| `sync_chain.py` | Mirrors seeded actors and apiaries on chain. Idempotent. |
| `publish_envelopes.py` | Derives a yield envelope per apiary from telemetry and publishes it to `YieldOracle`. `--dry-run` computes without publishing. |
| `verify_risk_ladder.py` | Applies each risk factor to a real batch in turn, asserts the score rises monotonically and the test tier escalates, then restores the database. Cleans up *before* it starts too — a previous interrupted run once polluted the baseline and made it report three failures that were entirely its own fault. |
| `demo_flow.py` | **The end-to-end proof.** Nine steps against the real stack: envelope, honest mint, inflated mint rejected, cumulative cap rejected, custody, mass conservation and its rejection, blending with origin percentages, Merkle-anchored seals, genuine jar verified and cloned jar refused. Exits non-zero if any guarantee it claims fails, including asserting each rejection came from the *intended* error. Uses a fresh season label per run so it is repeatable. |
| `export_metrics.py` | **Freezes every result into `metrics/` as committed files.** Reads each model's own `.meta.json` plus the ablation, varroa and ledger artefacts, then writes a README, a combined `index.json` and PNG bar charts. Nothing is retyped, so the folder cannot drift from the models; and because it is committed, the numbers survive the stack being down. |

---

---

## `packages/contracts/` — Solidity

Hardhat, not Foundry: `forge` is not installed here and Foundry on Windows is
friction. Solidity 0.8.28 pinned to the **Cancun** EVM, because OpenZeppelin 5.6
uses the `mcopy` opcode.

| File | Purpose |
|---|---|
| `contracts/Registry.sol` | Identities and apiaries. Roles, the relayer whitelist, and `canActAs` — the single choke point for the custodial model. AgriStack IDs are stored only as salted hashes; PII on a permanent ledger cannot be withdrawn. |
| `contracts/YieldOracle.sol` | Signed yield envelopes per apiary per season. Immutable once published: a revised forecast is a new revision and the history stays. `evidenceHash` is mandatory. |
| `contracts/HoneyBatch.sol` | **The core.** ERC-1155 balances in grams. Invariant 1: minting is capped by the oracle envelope, counted *cumulatively per season*. Invariant 2: process and blend conserve mass, with declared loss bounded by `maxLossBps`. Raw ERC-1155 transfers are deliberately disabled so honey cannot move without provenance. |
| `contracts/SealRegistry.sol` | One Merkle root per packing run rather than a storage slot per jar; a lakh of jars would otherwise cost more than the honey. `claimedGrams` makes over-packing arithmetic rather than opinion. |
| `contracts/Attestation.sol` | Lab reports and certificates as `(subject, kind, docHash, issuer)` plus scaled integer metrics. Only Labs, Regulators and Admins may attest — a brand attesting to its own purity is a press release. |
| `test/HoneyChain.test.js` | 38 tests. Includes the subtle one: the cap holds across many small batches, not only against a single large declaration. |
| `scripts/deploy.js` | Deploys all five and writes `deployments/<network>.json` with addresses **and ABIs**, so the Python API can never run against a stale ABI. |
| `hardhat.config.js` | Four targets running identical Solidity: in-process, localhost, Besu (permissioned, zero gas), Polygon Amoy. |

---

## `apps/web/` — consumer verify page

Next.js 15 App Router, server-rendered. No CSS framework and no web fonts: this
page is opened by a phone camera in a shop on 3G, and shipping a framework to
render one card is a cost someone else pays in seconds.

| File | Purpose |
|---|---|
| `app/verify/[serial]/page.js` | The verification page. Holds three states apart rather than blurring them into a green tick: **verified** (Merkle proof checked against the chain), **known** (issued, but nobody entered the hidden code), **unknown** (not ours, which is *not* the same as counterfeit). Renders the origin declaration, journey, checks, and any unverified-surplus caveat. |
| `app/01/[gtin]/21/[serial]/page.js` | **GS1 Digital Link resolver.** The literal `/01/{gtin}/21/{serial}` path a GS1 QR encodes, handed off to the verification page. One code serves the retail scanner, the phone and a compliance system. |
| `app/beekeeper/[actorId]/page.js` | **The beekeeper home screen.** Alerts first, hives second — someone opening this in a field wants to know what needs doing today. Language toggle is a plain `?lang=hi` link rather than client-side i18n, so the page works with JavaScript disabled on a very old browser. |
| `app/beekeeper/[actorId]/harvest/page.js` | **The harvest declaration.** Shows the allowed amount, and where it came from, *before* the input box. A screen that lets someone type 800 kg and then shows a red error tells a rural producer the system thinks they are lying; this one turns the constraint into information. |
| `public/manifest.json`, `public/icon.svg` | PWA manifest — installable to a home screen without a Play Store download over 2G. |
| `app/metrics/page.js` | **The metrics page.** Every number read from each model's `.meta.json`, so it cannot drift from the model actually loaded. Shows the weak numbers as prominently as the strong ones -- varroa recall is on screen because the first person to ask will find it anyway. |
| `app/proof/[batch]/page.js` | The ledger proof: each claim with its arithmetic, the enforcing contract function, transaction hashes and the jar's Merkle path. A green tick without the sum is our assertion with better typography, so nothing here shows one. |
| `app/console/admin/page.js` | KVIC console. Leads with **scheme attribution** -- which distributed bee boxes are producing -- because that is the question a ministry is judged on. Also carries the risk-based testing plan. |
| `app/console/fpo/[actorId]/page.js` | FPO console: member headroom, season capacity, lots in custody. |
| `app/console/processor/[actorId]/page.js` | Processor worklist, organised by what is *blocking* each batch rather than what is owned. |
| `app/beekeeper/[actorId]/HarvestForm.js` | The only client component in the app. Adds an offline queue: a declaration made with no signal is kept in localStorage with a device-generated uuid and replayed when the network returns. The server de-duplicates on that uuid, because a phone drifting in and out of signal will send it several times. |
| `app/page.js` | Serial lookup, for when the QR is scratched or the camera will not focus. |
| `app/layout.js` | Shell and metadata. |
| `app/globals.css` | Hand-written, about 5 kB, with a dark-mode block. |
| `lib/api.js` | Server-side fetch of the verification payload. Degrades to a plain message rather than a stack trace. |

Production build: the page itself is 3.4 kB. About 103 kB of the first load is
the React runtime, which arrives *after* the server-rendered content has
painted.

---

---

## `ml/` — models

| File | Purpose |
|---|---|
| `features.py` | **The contract between training and inference.** Both import `FEATURE_COLUMNS` and `build_features` from here; if they diverged the model would silently degrade in production with nothing failing loudly. Thermal delta and setpoint error do most of the work; band energies are normalised to a shape rather than a level; rolling windows exist because most faults are trajectories, not instants. |
| `train_health.py` | Trains the 8-class colony-health classifier. Splits by hive and tests on an entirely separate simulation run. Writes the model plus a `.meta.json` carrying its own accuracy caveat. |
| `train_yield.py` | Trains P10/P50/P90 quantile regressors with conformal calibration of the ceiling. The P90 becomes the on-chain mint limit, so it warns loudly if coverage would block more than 15% of honest declarations. |
| `train_anomaly.py` | One-class IsolationForest fitted on healthy windows only. Catches "unlike anything normal", which is what finds faults the classifier has no class for. Reported as AUC per fault class, because ordinary accuracy is meaningless for a detector that never saw a label. |
| `eval_varroa.py` | Generates synthetic sticky boards with known mite counts plus realistic debris, and scores the counter against them. Validates the algorithm's mechanics, not field accuracy -- and is the regression test if anyone changes a threshold. |
| `ablation.py` | Which sensors earn their place on the node. Overturned our own reading of permutation importance: the microphone is the highest-value single sensor, not the load cell. |
| `datasets/` | Simulator output. `train_v2.csv` concatenates four independent weather realisations; `holdout_v1.csv` is never trained on. |
| `models/` | `health_clf.joblib`, `yield_p{10,50,90}.joblib` and their metadata. Git-ignored. |

---

## `metrics/` — the committed record

Regenerated by `python scripts/export_metrics.py`. Every value is read from the
artefact that produced it, so this folder cannot disagree with the models.

| File | Purpose |
|---|---|
| `README.md` | Human-readable summary of every model, with the caveats attached to the numbers rather than kept in a separate section. |
| `index.json` | All of the below combined, for anything that wants to consume the results programmatically. |
| `colony_health.json`, `yield_forecast.json`, `anomaly_detector.json` | One per trained model, copied from its training `.meta.json`. |
| `varroa_counter.json` | Synthetic sticky-board regression results, written by `ml/eval_varroa.py`. |
| `sensor_ablation.json` | Per-sensor-set scores from `ml/ablation.py`, with the finding derived from the table rather than written by hand. |
| `ledger_verification.json` | The verdict from `scripts/verify_ledger.py` — invariants checked, invariants broken. |
| `colony_health_recall.png`, `sensor_ablation.png`, `anomaly_auc.png` | Bar charts drawn with Pillow, supersampled then downsampled because Pillow does not anti-alias. PNG rather than SVG so they paste into slide decks and chat. The AUC chart draws a dashed line at 0.5 — chance for AUC — so a bar from zero cannot flatter a near-chance score. |

---

## Not yet written

| Path | Planned contents |
|---|---|
| `apps/web/` (more) | Service worker for offline *reads*, voice prompts, inspection logging with photos. |
| `ml/` (more) | A trained YOLO varroa detector once annotated Indian sticky-board photos exist; the fraud-graph model. |
| `firmware/` | ESP32-S3 PlatformIO sketch reimplementing `canonical_string()` in C. |
