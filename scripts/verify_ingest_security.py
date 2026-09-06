"""Adversarial check on the telemetry ingest path.

Telemetry is the oracle that bounds on-chain minting, so if forged frames can
reach the database the entire fraud-prevention argument collapses. This script
attacks the running ingest service and asserts that exactly one of four frames
survives -- the legitimate one.

Requires the stack up and `python -m aurabee_api.ingest` running:

    python scripts/verify_ingest_security.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import psycopg
from dotenv import load_dotenv

from aurabee_schema import TelemetryFrame, topic_for

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

BASE = dict(
    t_in=34.8, rh_in=55.0, t_out=12.0, rh_out=70.0, weight_kg=40.0,
    sound_rms=0.3, entrance_in=10, entrance_out=10, batt_v=3.9,
    lat=27.57, lon=80.68,
)


def main() -> int:
    if not DSN:
        sys.exit("DATABASE_URL not set")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, hmac_key, last_seq FROM nodes ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if not row:
            sys.exit("no nodes registered; run scripts/seed.py first")
        node_id, key, last_seq = row
        cur.execute("SELECT count(*) FROM telemetry")
        before = cur.fetchone()[0]

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="aurabee-attack-test")
    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()

    def send(frame: TelemetryFrame, label: str) -> None:
        info = client.publish(
            topic_for(frame.node_id),
            json.dumps(frame.to_dict(), separators=(",", ":")),
            qos=1,
        )
        info.wait_for_publish(timeout=5)
        print(f"  sent  {label}")

    now = int(time.time())
    print("attacking ingest:")

    # 1. right node, wrong key -- the basic forgery
    f = TelemetryFrame(node_id=node_id, ts=now, seq=last_seq + 1000, **BASE)
    send(f.sign("wrong-key-entirely"), "forged signature      -> expect REJECT")

    # 2. genuinely signed but an already-used sequence number: a captured frame
    #    replayed back at the broker
    f = TelemetryFrame(node_id=node_id, ts=now, seq=max(1, last_seq - 1), **BASE)
    send(f.sign(key), "replayed seq          -> expect REJECT")

    # 3. a node that was never provisioned
    f = TelemetryFrame(node_id="HN-XX-ZZZ-9999", ts=now, seq=1, **BASE)
    send(f.sign(key), "unregistered node     -> expect REJECT")

    # 4. control: proves the pipe is actually open, so a green result above
    #    cannot be an artefact of nothing being delivered at all
    f = TelemetryFrame(node_id=node_id, ts=now, seq=last_seq + 5000, **BASE)
    send(f.sign(key), "legitimate frame      -> expect ACCEPT")

    client.loop_stop()
    client.disconnect()

    time.sleep(4)  # let the writer thread flush
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM telemetry")
        after = cur.fetchone()[0]

    delta = after - before
    print(f"\nrows {before} -> {after}  (delta {delta}, expected 1)")
    if delta == 1:
        print("PASS: only the legitimate frame was stored")
        return 0
    print("FAIL: ingest accepted something it should have rejected")
    return 1


if __name__ == "__main__":
    sys.exit(main())
