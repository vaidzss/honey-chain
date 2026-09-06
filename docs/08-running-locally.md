# Running locally

## Ports

Two defaults were moved because this machine already had services on them.
If yours are free, change them back in `infra/docker-compose.yml`, `.env` and
`apps/web/package.json`.

| Service | Port | Note |
|---|---|---|
| Postgres/TimescaleDB | **5433** | 5432 taken by another project |
| Web (Next.js) | **3001** | 3000 taken by another local app |
| API (FastAPI) | 8000 | |
| Chain (Hardhat) | 8545 | |
| MQTT | 1883, 9001 | |
| MinIO | 9000, 9090 | |

## Prerequisites

Docker Desktop (running), Python 3.11+, Node 20+.

## One-time setup

```bash
cp .env.example .env
npm install                 # hardhat, openzeppelin, next
npm run infra:up            # timescale, mosquitto, redis, minio

python -m venv .venv
./.venv/Scripts/python.exe -m pip install \
    -r apps/simulator/requirements.txt \
    -r apps/api/requirements.txt \
    -e packages/schema/python

./.venv/Scripts/python.exe scripts/seed.py --reset
```

On Linux/macOS use `.venv/bin/python` throughout.

## Bring the whole thing up

Five terminals. Order matters only for the chain (deploy needs a node) and the
simulator (needs seeded nodes).

**1 — chain**
```bash
npm run chain:node
```

**2 — deploy + sync identities**  *(one-off per chain restart)*
```bash
npm run chain:deploy
./.venv/Scripts/python.exe scripts/sync_chain.py
```

**3 — telemetry ingest**
```bash
cd apps/api
PYTHONPATH=. ../../.venv/Scripts/python.exe -m aurabee_api.ingest
```

**4 — simulate a season**
```bash
cd apps/simulator
../../.venv/Scripts/python.exe -m aurabee_sim --from-db \
    --start 2026-10-15 --days 150 --speed 0 --step-minutes 30 \
    --scenario queenless@HN-UP-SIT-0003@2026-12-10 \
    --scenario varroa@HN-UP-SIT-0007@2026-11-20@9
```
Ingest should report `received == accepted == written`, and the row count should
match the frames published. Any gap is data loss, not rounding.

**5 — API and web**
```bash
cd apps/api && PYTHONPATH=. ../../.venv/Scripts/python.exe \
    -m uvicorn aurabee_api.main:app --port 8000
npm run web:dev            # http://localhost:3001
```

## The proof

```bash
./.venv/Scripts/python.exe scripts/demo_flow.py
```

Nine steps against the live stack. It prints a jar serial and its scratch-off
code at the end — open that URL to see the consumer page. The script exits
non-zero if any guarantee it claims to demonstrate does not hold, and it checks
that each rejection came from the *intended* contract error rather than an
unrelated one, so a passing run means something.

Expected shape:

```
2. A believable harvest is accepted
   PASS      minted 167.4 kg as AB-SIT-16915-1
3. An inflated declaration is refused by the contract
   REJECTED  declaring 791.5 kg against a 304.4 kg envelope
4. Salami slicing is refused too -- the cap is cumulative
   REJECTED  a second 109.6 kg slice; only 27.4 kg is left
6. Processing conserves mass
   REJECTED  turning 109.6 kg into 142.5 kg
9. A real jar verifies; a photographed label does not
   REJECTED  same serial, guessed secret
```

The REJECTED lines are the product working.

## Other checks

```bash
# contract invariants (38 tests, no stack needed)
npm run chain:test

# forged / replayed / unregistered telemetry is dropped
./.venv/Scripts/python.exe scripts/verify_ingest_security.py

# yield envelopes without publishing
./.venv/Scripts/python.exe scripts/publish_envelopes.py --dry-run

# the risk ladder escalates monotonically and cleans up after itself
./.venv/Scripts/python.exe scripts/verify_risk_ladder.py
```

### See the signal the models will learn

Queenless colonies lose their grip on the brood-nest setpoint. On cold samples
healthy hives sit ~21 °C above ambient; a queenless one collapses toward it.

```sql
SELECT node_id,
       round(avg(t_in)::numeric,2)       AS t_in,
       round(avg(t_out)::numeric,2)      AS ambient,
       round(avg(t_in-t_out)::numeric,2) AS delta,
       round(avg((features->>'centroid_hz')::numeric),0) AS centroid_hz
FROM telemetry WHERE t_out < 16
GROUP BY node_id ORDER BY delta;
```

```
    node_id     | t_in  | ambient | delta | centroid_hz
----------------+-------+---------+-------+-------------
 HN-UP-SIT-0005 | 18.49 |   12.66 |  5.83 |         375   <- swarmed
 HN-UP-SIT-0003 | 21.03 |   12.68 |  8.35 |         381   <- queen removed
 HN-UP-SIT-0001 | 34.35 |   12.69 | 21.66 |         325
```

## Train the models

```bash
# labelled data: four independent weather realisations
cd apps/simulator
for s in 11 23 37 51; do
  ../../.venv/Scripts/python.exe -m aurabee_sim --hives 30 --days 260 \
      --step-minutes 60 --speed 0 --no-publish --auto-scenarios --seed $s \
      --dump-csv ../../ml/datasets/run_s$s.csv
done

# then, from the repo root
./.venv/Scripts/python.exe ml/train_health.py --train ml/datasets/train_v2.csv
./.venv/Scripts/python.exe ml/train_yield.py  --train ml/datasets/train_v2.csv
./.venv/Scripts/python.exe ml/train_anomaly.py
./.venv/Scripts/python.exe ml/ablation.py      # which sensors earn their place
./.venv/Scripts/python.exe ml/eval_varroa.py   # varroa counter regression test

# collect every result into metrics/ as durable artefacts
./.venv/Scripts/python.exe scripts/export_metrics.py
```

`metrics/` holds the saved results: `README.md` (human-readable summary),
`index.json` (everything combined), one JSON per model, PNG charts, and the
ledger verification verdict. It is committed, so the numbers are available
without the stack running — which matters in a room with no network.

## Screens

| URL | Who |
|---|---|
| `/verify/<SERIAL>` | consumer |
| `/01/<GTIN>/21/<SERIAL>` | consumer, via a GS1 Digital Link QR |
| `/beekeeper/<ACTOR_UUID>` | beekeeper (add `?lang=hi`) |
| `/beekeeper/<ACTOR_UUID>/harvest` | beekeeper, offline-capable |
| `/console/fpo/<ACTOR_UUID>` | FPO |
| `/console/processor/<ACTOR_UUID>` | processor |
| `/console/admin` | KVIC |
| `/metrics` | model metrics |
| `/proof/<BATCH_CODE>` | ledger proof for one batch |

## Proving the ledger

```bash
# reads ONLY chain state and re-derives every invariant
./.venv/Scripts/python.exe scripts/verify_ledger.py
./.venv/Scripts/python.exe scripts/verify_ledger.py --batch AB-SIT-1234-B
```

Then score every sentinel hive and raise alerts:

```bash
curl -X POST localhost:8000/api/admin/scan-hives
curl localhost:8000/api/model/health          # accuracy, per-class recall, caveat
curl "localhost:8000/api/hive/<HIVE_UUID>/assess"
```

## The beekeeper app

```
http://localhost:3001/beekeeper/<BEEKEEPER_ACTOR_UUID>
http://localhost:3001/beekeeper/<BEEKEEPER_ACTOR_UUID>?lang=hi
http://localhost:3001/beekeeper/<BEEKEEPER_ACTOR_UUID>/harvest
```

Find an actor id with:

```sql
SELECT a.actor_id, act.name FROM apiaries a
JOIN actors act ON act.id = a.actor_id
JOIN hives h ON h.apiary_id = a.id
JOIN nodes n ON n.hive_id = h.id
GROUP BY a.actor_id, act.name ORDER BY count(*) DESC;
```

## Generate a training dataset

```bash
cd apps/simulator
../../.venv/Scripts/python.exe -m aurabee_sim --hives 40 --days 240 \
    --speed 0 --no-publish --auto-scenarios \
    --dump-csv ../../ml/datasets/sim_v1.csv
```

The CSV carries `gt_*` ground-truth columns. Those are labels only — never
available to a model at inference time in the field.

## API quick reference

```bash
curl localhost:8000/health
curl "localhost:8000/api/verify/<SERIAL>"                  # known, journey only
curl "localhost:8000/api/verify/<SERIAL>?secret=<CODE>"    # verified, with proof
curl localhost:8000/api/admin/overview
curl localhost:8000/api/admin/fraud
curl localhost:8000/api/admin/hives

# GS1 EPCIS 2.0 document for a batch
curl "localhost:8000/api/batch/<CODE>/epcis"

# fraud score + which laboratory test it indicates
curl "localhost:8000/api/batch/<CODE>/risk"

# spend a fixed testing budget where it buys the most information
curl "localhost:8000/api/admin/testing-plan?budget_inr=5000"
```

A jar's GS1 Digital Link QR resolves at
`http://localhost:3001/01/{GTIN}/21/{serial}` — `demo_flow.py` prints one.

Interactive docs at <http://localhost:8000/docs>.

## Gotchas

- **`infra/sql/*.sql` runs only on first boot of an empty volume.** Editing the
  schema does nothing until `npm run infra:reset` (which destroys data), followed
  by `scripts/seed.py --reset`.
- **Redeploying the chain invalidates the address book.** Re-run
  `scripts/sync_chain.py` **and** `scripts/publish_envelopes.py` afterwards --
  without the second, every harvest is refused with `NoEnvelope` because the
  ceilings live on the old contracts.
  A long-running API also caches the book at startup, so restart it too;
  `/health` reports `"contracts": false` when its addresses have no code, and
  chain reads degrade to unverified rather than 500.
- **`ALLOW_TIME_TRAVEL=true`** disables frame-freshness checking, because
  simulated frames carry simulated timestamps months from now. **It defaults to
  off**, so set it in your `.env` when running the simulator or ingest will
  reject every simulated frame. The monotonic `seq` check still blocks replays
  either way. Never enable it in production.
- **`demo_flow.py` uses a fresh season label per run** so a previous run cannot
  exhaust the envelope and make the next one fail confusingly.
- First page load in `next dev` takes ~20 s to compile, then ~150 ms. Production
  builds do not have this.
- **Do not run `npm run build -w @aurabee/web` while `next dev` is serving.**
  They share `.next`, and the build overwrites what the dev server has loaded,
  which turns every route into a 500 with `MODULE_NOT_FOUND`. Stop dev, or
  `rm -rf apps/web/.next` and restart it.
