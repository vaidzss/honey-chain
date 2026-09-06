# Telemetry contract

The device wire format. Implemented three times — Python simulator, Python
ingest, and (planned) ESP32 C firmware — so it is written down here rather than
inferred from whichever copy you happened to open.

Canonical definitions:
- `packages/schema/telemetry.schema.json` — field shapes and ranges
- `packages/schema/python/aurabee_schema/telemetry.py` — signing implementation
- `packages/schema/index.ts` — TypeScript mirror

## Transport

| | |
|---|---|
| Protocol | MQTT 3.1.1, QoS 1 |
| Topic | `aurabee/telemetry/{node_id}` |
| Wildcard (ingest) | `aurabee/telemetry/+` |
| Payload | compact JSON, no spaces |
| Node id | `HN-<STATE:2>-<DISTRICT:3>-<SERIAL:4>`, e.g. `HN-UP-SIT-0007` |

The broker accepts anonymous connections **by design**. Trust does not come
from the transport; it comes from the per-frame HMAC below. In a real cluster
deployment the broker additionally gets TLS and per-node credentials, but the
signature remains the thing that matters.

## Frame

```json
{
  "v": 1,
  "node_id": "HN-UP-SIT-0007",
  "ts": 1793491200,
  "seq": 4412,
  "weight_kg": 42.315,
  "t_in": 34.8, "rh_in": 58.2,
  "t_out": 31.4, "rh_out": 49.0,
  "sound_rms": 0.3112,
  "entrance_in": 128, "entrance_out": 141,
  "batt_v": 3.92,
  "lat": 27.57010, "lon": 80.68120,
  "features": {
    "mfcc": [13 floats],
    "band_energy": [20 floats, 100 Hz bands, 0-2 kHz],
    "peak_hz": 250.0,
    "centroid_hz": 341.8
  },
  "sig": "8f50a823af8d6b52042388a60af3a35b"
}
```

Null fields are omitted to keep frames small on a LoRa link. A missing value is
legitimate — a node with a dead load cell still reports temperature.

### Why `t_in` is the field that matters

A queenright colony with brood holds its brood nest at **34–35 °C whatever the
ambient does**. A queenless, collapsing or under-strength colony loses that grip
and drifts toward ambient. That decoupling is the highest-value signal in the
whole system, and everything else — weight, traffic, acoustics — is secondary
confirmation.

### Why `centroid_hz` exists alongside `peak_hz`

`peak_hz` is `argmax` over 100 Hz bands. On a near-flat spectrum (a dead or
empty hive) argmax is effectively uniform random, producing wild values that
look like signal. The energy-weighted centroid degrades gracefully. Prefer it
as the scalar summary; the 20-band vector is what a model should actually
consume.

## Signing

```
sig = hex(HMAC_SHA256(node_key, canonical_string))[:32]
```

Truncated to 16 bytes to keep frames small. The key is per-node, stored in
`nodes.hmac_key`, generated at provisioning.

### The canonical string

Pipe-delimited, **fixed field order, fixed decimal precision**:

```
v1|{node_id}|{ts}|{seq}|{weight_kg:.3f}|{t_in:.2f}|{rh_in:.2f}|{t_out:.2f}|{rh_out:.2f}|{sound_rms:.4f}|{entrance_in}|{entrance_out}|{batt_v:.2f}|{lat:.5f}|{lon:.5f}
```

A `None` value renders as an empty field, so consecutive pipes are meaningful.

Worked example:

```
v1|HN-UP-SIT-0007|1756900000|4412|42.315|34.80|58.20|31.40|49.00|0.3112|128|141|3.92|27.57010|80.68120
```

**Why a delimited string and not signed JSON.** Signing serialised JSON would
require byte-identical output from a Python dict and an ArduinoJson document —
key order, float formatting, whitespace all have to match exactly. That is a
trap. Deterministic string formatting is reproducible in C in about fifteen
lines.

### Known limitation (v1)

`features` is **not covered by the signature**. Variable-length arrays are
awkward to hash identically across Python and C, and getting it subtly wrong
would be worse than leaving it out honestly.

The consequence is bounded deliberately: acoustic features influence model
inference, but **no event derived from them alone can move a yield envelope**.
Anything that affects an on-chain write is cross-checked against signed scalars
(`weight_kg`, `t_in`). v2 moves to a CBOR frame with a whole-payload signature
once the firmware is real.

## Ingest validation order

`apps/api/aurabee_api/ingest.py` applies these in order, counting each rejection:

1. **JSON parses** → else `bad_json`
2. **Shape matches the frame dataclass** → else `bad_shape`
3. **Node is registered and active** → else `unknown_node`
4. **HMAC verifies** (constant-time compare) → else `bad_sig`
5. **`seq` strictly greater than `nodes.last_seq`** → else `replay`
6. **Timestamp within `TELEMETRY_REPLAY_WINDOW_S`** → else `stale`
   *(skipped when `ALLOW_TIME_TRAVEL=true`; see below)*

Only then is the row queued for the writer thread. Duplicate deliveries from
QoS 1 are absorbed by `ON CONFLICT (node_id, ts) DO NOTHING`.

Verify with `python scripts/verify_ingest_security.py`, which sends a forged
signature, a replayed sequence, an unregistered node and one legitimate frame,
then asserts exactly one row landed.

### `ALLOW_TIME_TRAVEL`

The simulator stamps frames with *simulated* time, typically months from the
wall clock, so freshness checking would reject everything. The flag disables
step 6 only. Replay protection still holds, because it rests on the monotonic
`seq` counter rather than the clock.

**It defaults to off.** A flag whose own documentation says "never in
production" should not be something an operator gets by forgetting to set it,
so running the simulator now requires opting in explicitly.

## Duty cycle

Real nodes sample every **15 minutes** — the simulator default. That is 96
frames per node per day; a 200-hive cluster instrumented at 15% sentinel
coverage is ~2,880 frames/day, which is nothing. The constraint is LoRa airtime
and battery, not the database.
