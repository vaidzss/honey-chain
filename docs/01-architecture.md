# Architecture

```
+-- DEVICE LAYER (swappable) --------------------------------------------+
|  Python simulator            <-->            ESP32-S3 sentinel node    |
|  identical frame schema, identical HMAC signing                        |
+------------------------------------------------------------------------+
                    | MQTT QoS 1, aurabee/telemetry/{node_id}
                    v
+-- INGEST --------------------------------------------------------------+
|  verify HMAC -> reject replay -> queue -> writer thread -> TimescaleDB  |
|  apps/api/aurabee_api/ingest.py                                        |
+------------------------------------------------------------------------+
                    |
                    v
+-- INTELLIGENCE (planned) ----------------------------------------------+
|  acoustic state CNN | pest vision YOLO | yield forecaster (quantile)   |
|  anomaly detector   | fraud risk scorer                                |
|  -> signs YieldEnvelope(apiary, season, P90 kg, evidence_hash)         |
+------------------------------------------------------------------------+
          |                                        |
          v                                        v
+-- CORE API (planned) ---------+      +-- CHAIN (planned) ---------------+
|  FastAPI + Postgres           |      |  Hyperledger Besu (IBFT2)        |
|  MinIO/S3 for photos, PDFs    | ---> |  Solidity; local Anvil for dev   |
|  Redis queue                  |      |  gasless relayer (ERC-4337)      |
+-------------------------------+      +----------------------------------+
                    |
                    v
+-- CLIENTS (planned) ---------------------------------------------------+
|  Beekeeper PWA (offline-first, Hindi, voice)                           |
|  Processor / FPO console | KVIC dashboard | consumer /verify/[seal]    |
+------------------------------------------------------------------------+
```

## Why the device layer is an interface, not an implementation

The simulator and the firmware emit identical frame shapes and sign them the
same way. Nothing downstream can tell them apart, so hardware arriving later
changes no code above the MQTT topic. That is the whole reason the telemetry
contract lives in `packages/schema/` rather than inside either producer.

## Technology choices and the reasoning

| Choice | Why | Alternative rejected |
|---|---|---|
| **TimescaleDB** | Telemetry is append-only time series with heavy range queries; hypertables and continuous aggregates give hourly rollups for free. Still plain Postgres for everything relational. | A separate InfluxDB: a second datastore to join across, for no benefit at this scale. |
| **MQTT (Mosquitto)** | What real LoRa gateways and ESP32 nodes actually speak. Topic-per-node maps cleanly onto per-node auth later. | HTTP POST per reading, which is wasteful on a constrained radio link. |
| **Hyperledger Besu** | Permissioned IBFT2 gives KVIC/NBB the governance and data-residency story a government problem statement needs, while running **the same Solidity** we develop against a local Anvil node. One codebase, two deployment stories. | Fabric: chaincode, channels and MSP cost days we do not have. A public L2 alone: no permissioning, and a weak answer on data sovereignty. |
| **Per-frame HMAC** | The broker is the least trustworthy hop. Signing at the device means a compromised broker cannot forge telemetry, and telemetry is what bounds minting. | TLS only, which protects the pipe but not the payload. |
| **FastAPI + Next.js** | The models are Python; the consumer verify page needs SSR and must render in under a second on a cheap phone. | A full-JS backend would push ML into a separate service anyway. |
| **Hardhat** | `forge` is not installed here and Foundry on Windows is friction. Hardhat is npm-native. | Foundry: better tooling, worse setup on this machine. |

## Data flow, concretely

1. A node (or the simulator) reads its sensors, computes acoustic features
   on-device, builds a frame, signs it, and publishes to its own topic.
2. `ingest.py` verifies the signature against `nodes.hmac_key`, checks the
   sequence counter, and hands the row to a writer thread. **The writer thread
   is load-bearing**: doing inserts on the paho network loop backs the broker
   queue up and mosquitto then silently discards QoS-1 messages.
3. Rows land in the `telemetry` hypertable; `telemetry_hourly` aggregates them.
4. *(planned)* The intelligence layer reads hourly rollups, infers colony state,
   forecasts yield as a P10/P50/P90 envelope, and signs the P90 as the mint
   ceiling.
5. *(planned)* A harvest declaration above that ceiling reverts on-chain.

## On-chain vs off-chain

Only state transitions, hashes and invariants go on-chain. Photos, lab PDFs and
raw telemetry stay off-chain in S3/MinIO, with periodic Merkle-root anchoring of
telemetry windows so raw data can be proven un-tampered without paying to store
it.

## What is deliberately not distributed yet

There is one ingest process, one database, no queue between them beyond an
in-process `queue.Queue`. That is correct for a single cluster and a demo. The
scaling story (per-cluster gateways, regional ingest) is in
[07-deployment.md](07-deployment.md), and nothing in the current design blocks
it — the MQTT topic space is already partitioned by node.
