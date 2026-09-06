-- Runs once, on first boot of an empty db volume.
-- gen_random_uuid() is in core since PG13, so we need no uuid-ossp.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
