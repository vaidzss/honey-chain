"""MQTT -> TimescaleDB telemetry ingest.

Run it directly:

    python -m aurabee_api.ingest

Responsibilities, in order of importance:

 1. **Authenticate every frame.** The broker is anonymous by design; trust comes
    from the per-node HMAC, not from the transport. A frame whose signature does
    not verify is counted and dropped, never stored -- telemetry is the oracle
    that bounds on-chain minting, so unauthenticated telemetry is the one thing
    that must never reach the database.
 2. **Reject replays** via the monotonic `seq` counter.
 3. Buffer and batch-insert, because a 2000-hive cluster at a 15-minute duty
    cycle is a lot of tiny writes.

Frames are keyed (node_id, ts); a duplicate delivery from MQTT QoS 1 is
absorbed by ON CONFLICT DO NOTHING rather than erroring.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from collections import defaultdict
from queue import Empty, Full, Queue

import paho.mqtt.client as mqtt
import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from aurabee_schema import TELEMETRY_WILDCARD, TelemetryFrame, is_fresh

load_dotenv()
log = logging.getLogger("aurabee.ingest")

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
REPLAY_WINDOW_S = int(os.getenv("TELEMETRY_REPLAY_WINDOW_S", "300"))
BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "500"))
QUEUE_MAX = int(os.getenv("INGEST_QUEUE_MAX", "100000"))
FLUSH_SECONDS = float(os.getenv("INGEST_FLUSH_SECONDS", "2.0"))

# The simulator publishes frames stamped with *simulated* time, usually months
# from the wall clock, so freshness checking would reject every one of them.
# This flag turns that check off. The monotonic seq check still applies and is
# what actually stops replay, but freshness is a real second control for real
# hardware.
#
# It defaults to OFF. A flag whose own comment says "never in production" must
# not be something you get by forgetting to set it -- opting in to a weaker
# check should be a decision someone made, not a default they inherited.
ALLOW_TIME_TRAVEL = os.getenv("ALLOW_TIME_TRAVEL", "false").lower() == "true"

INSERT_SQL = """
INSERT INTO telemetry (ts, node_id, hive_id, weight_kg, t_in, rh_in, t_out, rh_out,
                       sound_rms, entrance_in, entrance_out, batt_v, lat, lon, features)
VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (node_id, ts) DO NOTHING
"""


class NodeRegistry:
    """Node keys and replay state, cached in memory and refreshed periodically."""

    def __init__(self, dsn: str, ttl: float = 60.0):
        self._dsn = dsn
        self._ttl = ttl
        self._loaded_at = 0.0
        self._nodes: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _load(self) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, hive_id::text, hmac_key, last_seq, node_type FROM nodes WHERE status='active'"
            )
            self._nodes = {
                r[0]: {"hive_id": r[1], "key": r[2], "last_seq": r[3], "type": r[4]}
                for r in cur.fetchall()
            }
        self._loaded_at = time.time()
        log.info("node registry loaded: %d active nodes", len(self._nodes))

    def get(self, node_id: str) -> dict | None:
        with self._lock:
            if time.time() - self._loaded_at > self._ttl:
                self._load()
            return self._nodes.get(node_id)

    def bump_seq(self, node_id: str, seq: int) -> None:
        with self._lock:
            n = self._nodes.get(node_id)
            if n and seq > n["last_seq"]:
                n["last_seq"] = seq


class Ingest:
    def __init__(self) -> None:
        if not DSN:
            sys.exit("DATABASE_URL not set")
        self.registry = NodeRegistry(DSN)
        self.queue: Queue = Queue(maxsize=QUEUE_MAX)
        self.stats: dict[str, int] = defaultdict(int)
        self.running = True
        self.conn = psycopg.connect(DSN, autocommit=True)

    # ---------------------------------------------------------------- mqtt --
    def on_connect(self, client, _ud, _flags, rc, _props=None):
        log.info("connected to broker rc=%s, subscribing %s", rc, TELEMETRY_WILDCARD)
        client.subscribe(TELEMETRY_WILDCARD, qos=1)

    def on_message(self, _client, _ud, msg):
        self.stats["received"] += 1
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            self.stats["bad_json"] += 1
            return

        try:
            frame = TelemetryFrame.from_dict(payload)
        except TypeError:
            self.stats["bad_shape"] += 1
            return

        node = self.registry.get(frame.node_id)
        if node is None:
            # An unregistered node is not an error worth crashing over, but it
            # is worth seeing: it usually means the seed script has not run.
            self.stats["unknown_node"] += 1
            return

        if not frame.verify(node["key"]):
            self.stats["bad_sig"] += 1
            log.warning("signature failure from %s (seq %s)", frame.node_id, frame.seq)
            return

        if frame.seq <= node["last_seq"]:
            self.stats["replay"] += 1
            return

        if not ALLOW_TIME_TRAVEL and not is_fresh(frame.ts, REPLAY_WINDOW_S):
            self.stats["stale"] += 1
            return

        row = (
            frame.ts, frame.node_id, node["hive_id"],
            frame.weight_kg, frame.t_in, frame.rh_in, frame.t_out, frame.rh_out,
            frame.sound_rms, frame.entrance_in, frame.entrance_out,
            frame.batt_v, frame.lat, frame.lon,
            Jsonb(frame.features) if frame.features else None,
        )
        try:
            # Hand off to the writer thread and return immediately. Doing the
            # INSERT here would block paho's network loop, the broker's
            # per-client queue would back up, and mosquitto would silently drop
            # QoS-1 messages -- which cost us a quarter of every run until we
            # spotted the row count not matching the publish count.
            self.queue.put_nowait((row, frame.node_id, frame.seq))
        except Full:
            self.stats["dropped_backpressure"] += 1
            return

        self.registry.bump_seq(frame.node_id, frame.seq)
        self.stats["accepted"] += 1

    # --------------------------------------------------------------- write --
    def writer_loop(self) -> None:
        """Owns all database writes. Batches whatever has accumulated, so a
        burst costs one round trip instead of hundreds."""
        while self.running or not self.queue.empty():
            rows, seqs = [], {}
            deadline = time.time() + FLUSH_SECONDS
            while len(rows) < BATCH_SIZE:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    row, node_id, seq = self.queue.get(timeout=remaining)
                except Empty:
                    break
                rows.append(row)
                if seq > seqs.get(node_id, 0):
                    seqs[node_id] = seq
            if rows:
                self._write(rows, seqs)

    def _write(self, rows: list[tuple], seqs: dict[str, int]) -> None:
        try:
            with self.conn.cursor() as cur:
                cur.executemany(INSERT_SQL, rows)
                cur.executemany(
                    """UPDATE nodes SET last_seq = GREATEST(last_seq, %s), last_seen = now()
                       WHERE id = %s""",
                    [(seq, nid) for nid, seq in seqs.items()],
                )
            self.stats["written"] += len(rows)
        except psycopg.Error as exc:
            # Logged loudly rather than retried blindly: a schema mismatch
            # should surface now, not hide behind a growing queue.
            self.stats["write_errors"] += 1
            log.error("batch insert failed (%d rows): %s", len(rows), exc)

    # ---------------------------------------------------------------- loop --
    def run(self) -> None:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="aurabee-ingest")
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()

        def shutdown(*_):
            self.running = False
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        writer = threading.Thread(target=self.writer_loop, name="ingest-writer", daemon=True)
        writer.start()

        last_report = time.time()
        try:
            while self.running:
                time.sleep(0.2)
                if time.time() - last_report >= 10:
                    log.info("stats %s queue=%d", dict(self.stats), self.queue.qsize())
                    last_report = time.time()
        finally:
            client.loop_stop()
            client.disconnect()
            self.running = False
            writer.join(timeout=30)      # let it drain what is still queued
            self.conn.close()
            log.info("final stats %s", dict(self.stats))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    if ALLOW_TIME_TRAVEL:
        log.warning("ALLOW_TIME_TRAVEL=true: frame freshness is not enforced "
                    "(fine for simulated data, never for production)")
    Ingest().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
