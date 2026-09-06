# Data model

17 tables. Defined in `infra/sql/02_schema.sql`; reference data in `03_reference.sql`.

## Conventions

- **Enum-ish columns are `TEXT` + `CHECK`, not PG `ENUM`.** The schema changes
  daily right now and `ALTER TYPE` is painful.
- **Every table with an on-chain counterpart carries `tx_hash`**, so off-chain
  and chain state can be reconciled.
- **Anything an offline client can write carries `client_uuid`**, making a
  replayed sync idempotent rather than duplicating rows.
- UUID primary keys via core `gen_random_uuid()` (PG13+), so no uuid-ossp.

## Identity and geography

| Table | Notes |
|---|---|
| `actors` | Everyone: beekeeper, collection_centre, fpo, processor, lab, brand, regulator, admin. Holds a custodial `wallet_address` (beekeepers never hold keys), `agristack_hash` — a salted hash, never the raw Farmer ID, which is PII — and `madhukranti_reg`. |
| `clusters` | The deployment unit: 50-200 beekeepers around one collection centre, with a coordinator (the "Madhu Mitra"). |
| `apiaries` | Location, a `flora_profile` array driving the yield model flow calendar, and hive count. |
| `hives` | `species` including `apis_cerana_indica`; `source`, where `kvic_distributed` lets KVIC attribute production back to boxes it handed out; and `is_sentinel`. |
| `nodes` | Sensor nodes. `hmac_key` per node, `last_seq` for replay protection, `node_type` in (`sim`, `esp32`). |

## Telemetry

| Table | Notes |
|---|---|
| `telemetry` | **Hypertable**, 7-day chunks, PK `(node_id, ts)`. Scalars plus a `features` JSONB, so the acoustic feature set can evolve without a migration. Append-only, never updated. |
| `telemetry_hourly` | Continuous aggregate. Dashboards and the yield model read this, not the raw table. |
| `hive_events` | Detected events. `source` distinguishes `model` / `rule` / `manual`, which matters later when beekeeper inspections become the ground truth we retrain on. |
| `inspections` | Manual hive checks from the beekeeper app, with `client_uuid` for offline sync. |

## The trust chain

| Table | Notes |
|---|---|
| `yield_envelopes` | **The oracle output.** P10/P50/P90 kg per apiary per season, with `coverage_ratio` (how much is measured versus extrapolated from sentinels), an `evidence_hash` and an oracle `signature`. P90 is the mint ceiling. |
| `batches` | `declared_kg`, `current_kg`, `envelope_id`, and `unverified_surplus_kg` — non-zero when a declaration exceeded the envelope and was minted anyway, which surfaces as a visible warning on the consumer page rather than being quietly hidden. |
| `custody_events` | mint / transfer / split / merge / process / pack / test / sell / void, with `qty_kg` and a declared `loss_kg`. |
| `batch_composition` | What a blend is made of, with percentages and origin district and state. **This is what generates the EU 2024/1438 origin declaration.** |
| `seals` | One row per physical jar: `serial`, `secret_hash`, `first_scan_at`, `first_scan_geohash`, `scan_count`. |
| `scans` | Every scan, with a **coarse** geohash (5 characters, roughly 5 km). We verify honey; we do not track people. |
| `attestations` | Lab reports, certificates and inspections as `(subject, kind, doc_hash, issuer)`. The file lives in S3; only its hash is authoritative. |
| `fraud_cases` | over_declaration, mass_balance, seal_clone, scan_velocity, geo_mismatch, yield_outlier, node_tamper. |

## Reference data

`flora_calendar` holds 19 rows of flow windows and peak kg per hive per day for
Indian conditions: mustard, litchi, eucalyptus, sunflower, coriander, berseem,
acacia, jamun, neem, rubber, apple, multiflora, plus the monsoon dearth. These
are cold-start priors for the simulator and the yield model. Once a cluster is
live, per-district curves get fitted from real hive-weight data and these rows
become the fallback only.

`apps/simulator/aurabee_sim/flora.py` keeps its own copy so the simulator can
run with no database at all. Keep the two in step.

## Schema changes

`infra/sql/*.sql` runs **once**, on first boot of an empty volume. After editing:

```bash
npm run infra:reset
./.venv/Scripts/python.exe scripts/seed.py --reset
```

There is no migration tool yet. Add one (Alembic) before there is data worth
keeping.
