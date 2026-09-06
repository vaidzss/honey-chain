-- ============================================================================
-- AuraBee core schema
--
-- Design notes:
--  * Off-chain is the system of record for *content*; the chain holds state
--    transitions + hashes. Every table with an on-chain counterpart carries a
--    tx_hash so the two can be reconciled (see docs/05-blockchain.md).
--  * Enum-ish columns are TEXT + CHECK, not PG ENUM: this schema changes daily
--    right now and ALTER TYPE is painful.
--  * Anything written by an offline client carries client_uuid so a replayed
--    sync is idempotent.
-- ============================================================================

-- ---------------------------------------------------------------- actors ----
CREATE TABLE actors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL CHECK (kind IN
                      ('beekeeper','collection_centre','fpo','processor','lab','brand','regulator','admin')),
    name            TEXT NOT NULL,
    phone           TEXT UNIQUE,
    district        TEXT,
    state           TEXT,
    -- custodial wallet: beekeepers never hold keys themselves
    wallet_address  TEXT UNIQUE,
    -- AgriStack Farmer ID is PII; store only a salted hash as an identity anchor
    agristack_hash  TEXT,
    -- Madhukranti (National Bee Board) registration number, when present
    madhukranti_reg TEXT,
    -- GS1 Global Location Number. Allocated from a licensed company prefix in
    -- production; see packages/schema/python/aurabee_schema/gs1.py.
    gln             TEXT UNIQUE,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','pending')),
    meta            JSONB NOT NULL DEFAULT '{}',
    tx_hash         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_actors_kind ON actors(kind);
CREATE INDEX idx_actors_district ON actors(state, district);

-- -------------------------------------------------------------- clusters ----
-- The unit of deployment: ~50-200 beekeepers around one collection centre.
CREATE TABLE clusters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    state           TEXT NOT NULL,
    district        TEXT NOT NULL,
    centre_actor_id UUID REFERENCES actors(id),
    -- the "Madhu Mitra" cluster coordinator
    coordinator_id  UUID REFERENCES actors(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -------------------------------------------------------------- apiaries ----
CREATE TABLE apiaries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id      UUID NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
    cluster_id    UUID REFERENCES clusters(id),
    name          TEXT NOT NULL,
    lat           DOUBLE PRECISION,
    lon           DOUBLE PRECISION,
    geohash       TEXT,
    district      TEXT,
    state         TEXT,
    -- dominant floral sources; drives the yield model's flow calendar
    flora_profile TEXT[] NOT NULL DEFAULT '{}',
    hive_count    INTEGER NOT NULL DEFAULT 0,
    gln           TEXT UNIQUE,      -- the apiary is a GS1 location in its own right
    tx_hash       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_apiaries_actor ON apiaries(actor_id);
CREATE INDEX idx_apiaries_cluster ON apiaries(cluster_id);

-- ----------------------------------------------------------------- hives ----
CREATE TABLE hives (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    apiary_id    UUID NOT NULL REFERENCES apiaries(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    species      TEXT NOT NULL DEFAULT 'apis_mellifera'
                   CHECK (species IN ('apis_mellifera','apis_cerana_indica','apis_florea','trigona')),
    -- lets KVIC attribute production back to distributed boxes
    source       TEXT NOT NULL DEFAULT 'own' CHECK (source IN ('own','kvic_distributed','nbhm','other')),
    installed_on DATE,
    status       TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','absconded','dead','merged','sold')),
    -- sentinel hives carry a sensor node; the rest are extrapolated from them
    is_sentinel  BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (apiary_id, label)
);
CREATE INDEX idx_hives_apiary ON hives(apiary_id);
CREATE INDEX idx_hives_sentinel ON hives(is_sentinel) WHERE is_sentinel;

-- ----------------------------------------------------------------- nodes ----
CREATE TABLE nodes (
    id               TEXT PRIMARY KEY,             -- e.g. HN-UP-SIT-0007
    hive_id          UUID REFERENCES hives(id) ON DELETE SET NULL,
    node_type        TEXT NOT NULL DEFAULT 'sim' CHECK (node_type IN ('sim','esp32')),
    firmware_version TEXT,
    -- per-node HMAC secret; simulator and firmware sign payloads with it
    hmac_key         TEXT NOT NULL,
    last_seen        TIMESTAMPTZ,
    last_seq         BIGINT NOT NULL DEFAULT 0,    -- replay protection
    batt_v           REAL,
    status           TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active','offline','retired','tamper')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_nodes_hive ON nodes(hive_id);

-- ------------------------------------------------------------- telemetry ----
-- Hypertable. High write volume, never updated.
CREATE TABLE telemetry (
    ts           TIMESTAMPTZ      NOT NULL,
    node_id      TEXT             NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    hive_id      UUID,
    weight_kg    REAL,
    t_in         REAL,   -- in-hive (brood nest) temperature; the key health signal
    rh_in        REAL,
    t_out        REAL,   -- ambient
    rh_out       REAL,
    sound_rms    REAL,
    entrance_in  INTEGER,
    entrance_out INTEGER,
    batt_v       REAL,
    lat          DOUBLE PRECISION,
    lon          DOUBLE PRECISION,
    -- mfcc / band energies as jsonb so the acoustic model can evolve without
    -- a migration
    features     JSONB,
    PRIMARY KEY (node_id, ts)
);
SELECT create_hypertable('telemetry', 'ts', chunk_time_interval => INTERVAL '7 days');
CREATE INDEX idx_telemetry_hive_ts ON telemetry (hive_id, ts DESC);

-- Hourly rollup. Dashboards and the yield model read this, not the raw table.
CREATE MATERIALIZED VIEW telemetry_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts) AS bucket,
    node_id,
    hive_id,
    avg(weight_kg)                  AS weight_kg,
    max(weight_kg) - min(weight_kg) AS weight_range,
    avg(t_in)                       AS t_in,
    avg(rh_in)                      AS rh_in,
    avg(t_out)                      AS t_out,
    avg(rh_out)                     AS rh_out,
    avg(sound_rms)                  AS sound_rms,
    sum(entrance_in)                AS entrance_in,
    sum(entrance_out)               AS entrance_out,
    min(batt_v)                     AS batt_v,
    count(*)                        AS samples
FROM telemetry
GROUP BY bucket, node_id, hive_id
WITH NO DATA;

-- ------------------------------------------------------------ hive events ----
CREATE TABLE hive_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hive_id     UUID NOT NULL REFERENCES hives(id) ON DELETE CASCADE,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type  TEXT NOT NULL CHECK (event_type IN
                  ('queenless','swarm','pre_swarm','absconding','robbing','varroa',
                   'wax_moth','starvation','theft','node_offline','sensor_fault',
                   'healthy','dead')),
    severity    TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warn','critical')),
    confidence  REAL,
    -- 'model' = AI inference, 'rule' = threshold, 'manual' = beekeeper inspection.
    -- Keeping these distinguishable matters when inspections later become the
    -- ground truth we retrain on.
    source      TEXT NOT NULL DEFAULT 'model' CHECK (source IN ('model','rule','manual')),
    model_version TEXT,
    detail      JSONB NOT NULL DEFAULT '{}',
    acknowledged_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_hive_events_hive_ts ON hive_events(hive_id, ts DESC);
CREATE INDEX idx_hive_events_open ON hive_events(severity, ts DESC) WHERE acknowledged_at IS NULL;

-- ------------------------------------------------------------ inspections ----
CREATE TABLE inspections (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- client-generated; makes offline replay idempotent
    client_uuid  UUID UNIQUE NOT NULL,
    hive_id      UUID NOT NULL REFERENCES hives(id) ON DELETE CASCADE,
    actor_id     UUID NOT NULL REFERENCES actors(id),
    ts           TIMESTAMPTZ NOT NULL,
    queen_seen   BOOLEAN,
    brood_frames REAL,
    honey_frames REAL,
    pests        TEXT[] NOT NULL DEFAULT '{}',
    varroa_count INTEGER,
    fed          BOOLEAN NOT NULL DEFAULT false,
    treated_with TEXT,
    notes        TEXT,
    photo_keys   TEXT[] NOT NULL DEFAULT '{}',   -- S3/MinIO object keys
    synced_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_inspections_hive_ts ON inspections(hive_id, ts DESC);

-- -------------------------------------------------------- yield envelopes ----
-- The oracle output. This is what bounds on-chain minting.
CREATE TABLE yield_envelopes (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    apiary_id      UUID NOT NULL REFERENCES apiaries(id) ON DELETE CASCADE,
    season         TEXT NOT NULL,               -- e.g. 2026-mustard
    window_start   DATE NOT NULL,
    window_end     DATE NOT NULL,
    p10_kg         REAL NOT NULL,
    p50_kg         REAL NOT NULL,
    p90_kg         REAL NOT NULL,               -- the mint ceiling
    hives_counted  INTEGER NOT NULL,
    sentinel_count INTEGER NOT NULL,
    -- how much of the envelope is measured vs extrapolated
    coverage_ratio REAL NOT NULL DEFAULT 0,
    model_version  TEXT NOT NULL,
    evidence_hash  TEXT NOT NULL,               -- hash of telemetry window + features
    signature      TEXT,                        -- oracle signature over the envelope
    tx_hash        TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (apiary_id, season)
);

-- --------------------------------------------------------------- batches ----
CREATE TABLE batches (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_code     TEXT UNIQUE NOT NULL,        -- e.g. AB-UP-SIT-2026-0042
    apiary_id      UUID REFERENCES apiaries(id),
    owner_actor_id UUID NOT NULL REFERENCES actors(id),
    envelope_id    UUID REFERENCES yield_envelopes(id),
    kind           TEXT NOT NULL DEFAULT 'raw' CHECK (kind IN ('raw','processed','blend','packed')),
    declared_kg    REAL NOT NULL,
    current_kg     REAL NOT NULL,
    floral_source  TEXT,
    harvest_date   DATE,
    moisture_pct   REAL,
    status         TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','minted','in_transit','processing','packed','sold','flagged','void')),
    -- non-zero when declared_kg exceeded the envelope and was minted anyway;
    -- surfaces as a visible warning on the consumer page
    unverified_surplus_kg REAL NOT NULL DEFAULT 0,
    chain_token_id BIGINT,
    -- GS1: the bulk lot is an LGTIN class (quantity of honey, uom KGM); the
    -- retail jar is a different trade item and gets its own GTIN when packed.
    gtin           TEXT,
    jar_gtin       TEXT,
    tx_hash        TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_batches_owner ON batches(owner_actor_id);
CREATE INDEX idx_batches_status ON batches(status);

-- --------------------------------------------------------- custody events ----
CREATE TABLE custody_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id      UUID NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN
                    ('mint','transfer','split','merge','process','pack','test','sell','void')),
    from_actor_id UUID REFERENCES actors(id),
    to_actor_id   UUID REFERENCES actors(id),
    qty_kg        REAL NOT NULL,
    loss_kg       REAL NOT NULL DEFAULT 0,      -- declared processing loss
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    location      TEXT,
    -- Physical tamper-evident seal on the drum: a numbered one-time plastic
    -- security tie, the same thing cash-in-transit and pharma logistics use.
    -- The digital ledger cannot see a drum being topped up on the highway; a
    -- cut seal can. Costs a few rupees and closes the transport gap that no
    -- amount of contract logic reaches.
    lot_seal_code TEXT,
    seal_intact   BOOLEAN,       -- checked by the receiving party at intake
    -- 10-second digital refractometer reading at intake. Not conclusive on its
    -- own -- honey moisture legitimately runs 16-22% -- but a cheap field
    -- screen, and a step change between despatch and receipt is a real signal.
    moisture_pct  REAL,
    -- Weight measured by the receiver, against what the sender declared.
    received_kg   REAL,
    tx_hash       TEXT,
    meta          JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_custody_batch_ts ON custody_events(batch_id, ts);

-- ------------------------------------------------------ batch composition ----
-- What a blend is made of. Generates the EU 2024/1438 origin declaration
-- with descending percentages.
CREATE TABLE batch_composition (
    blend_batch_id  UUID NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    source_batch_id UUID NOT NULL REFERENCES batches(id),
    kg              REAL NOT NULL,
    pct             REAL NOT NULL,
    origin_country  TEXT NOT NULL DEFAULT 'IN',
    origin_state    TEXT,
    origin_district TEXT,
    PRIMARY KEY (blend_batch_id, source_batch_id)
);

-- ----------------------------------------------------------------- seals ----
-- One row per physical jar. A batch-level QR is photocopyable; a serialised
-- seal with a first-scan anchor is not.
CREATE TABLE seals (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    serial             TEXT UNIQUE NOT NULL,
    batch_id           UUID NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    -- hash of the short code printed under the scratch-off / inside the cap
    secret_hash        TEXT NOT NULL,
    -- keccak256(batchId, serial, secret): the Merkle leaf. Stored because we
    -- keep only hashed secrets, so leaves cannot be recomputed later, and the
    -- tree has to be rebuildable to generate a proof on demand.
    leaf_hex           TEXT,
    net_weight_g       INTEGER,
    status             TEXT NOT NULL DEFAULT 'issued'
                         CHECK (status IN ('issued','activated','scanned','flagged','void')),
    activated_at       TIMESTAMPTZ,
    first_scan_at      TIMESTAMPTZ,
    first_scan_geohash TEXT,
    scan_count         INTEGER NOT NULL DEFAULT 0,
    tx_hash            TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_seals_batch ON seals(batch_id);

CREATE TABLE scans (
    id             BIGSERIAL PRIMARY KEY,
    seal_id        UUID NOT NULL REFERENCES seals(id) ON DELETE CASCADE,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    geohash        TEXT,          -- coarse (5 chars, ~5km); we do not track people
    ip_hash        TEXT,
    ua_hash        TEXT,
    secret_ok      BOOLEAN,
    is_anomalous   BOOLEAN NOT NULL DEFAULT false,
    anomaly_reason TEXT
);
CREATE INDEX idx_scans_seal_ts ON scans(seal_id, ts DESC);

-- ---------------------------------------------------------- attestations ----
CREATE TABLE attestations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_type    TEXT NOT NULL CHECK (subject_type IN ('batch','actor','apiary','hive')),
    subject_id      UUID NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN
                      ('lab_report','organic_cert','fssai_licence','gi_tag','inspection',
                       'export_dossier','origin_test')),
    issuer_actor_id UUID REFERENCES actors(id),
    doc_hash        TEXT NOT NULL,   -- sha256 of the file; the file lives in S3
    doc_key         TEXT,
    summary         JSONB NOT NULL DEFAULT '{}',  -- {"c4_pct":0.4,"moisture":18.2,"verdict":"pass"}
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    tx_hash         TEXT
);
CREATE INDEX idx_attestations_subject ON attestations(subject_type, subject_id);

-- ----------------------------------------------------------- fraud cases ----
CREATE TABLE fraud_cases (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         TEXT NOT NULL CHECK (kind IN
                   ('over_declaration','mass_balance','seal_clone','scan_velocity',
                    'geo_mismatch','yield_outlier','node_tamper')),
    subject_type TEXT NOT NULL,
    subject_id   UUID NOT NULL,
    score        REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open','investigating','confirmed','dismissed')),
    detail       JSONB NOT NULL DEFAULT '{}',
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);
CREATE INDEX idx_fraud_open ON fraud_cases(status, opened_at DESC);

-- ------------------------------------------------------------- GS1 refs ----
-- GS1 references must be numeric and short, so application UUIDs cannot be used
-- directly. These sequences allocate stable numeric references; hashing a UUID
-- into the space would risk collisions, and a GTIN collision means two
-- different products sharing an identity.
CREATE SEQUENCE gs1_location_ref_seq START 1;
CREATE SEQUENCE gs1_item_ref_seq     START 1;
CREATE SEQUENCE gs1_sscc_ref_seq     START 1;

-- ------------------------------------------------------- flora calendar -----
-- Reference data. Drives both the simulator's nectar-flow curves and the
-- yield model's seasonal features.
CREATE TABLE flora_calendar (
    id              SERIAL PRIMARY KEY,
    flora           TEXT NOT NULL,
    state           TEXT,
    start_month     SMALLINT NOT NULL CHECK (start_month BETWEEN 1 AND 12),
    end_month       SMALLINT NOT NULL CHECK (end_month BETWEEN 1 AND 12),
    -- typical peak nectar gain, kg per hive per day, during the flow
    peak_kg_per_day REAL NOT NULL,
    notes           TEXT
);
