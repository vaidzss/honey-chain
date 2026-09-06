"""Simulation loop: drives colonies, signs frames, publishes to MQTT."""

from __future__ import annotations

import json
import logging
import random
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from aurabee_schema import TelemetryFrame, topic_for

from .acoustics import features as acoustic_features
from .colony import Colony
from .scenarios import Scenario, apply as apply_scenario
from .weather import WeatherModel

log = logging.getLogger("aurabee.sim")


@dataclass
class NodeSpec:
    node_id: str
    hive_id: str
    hmac_key: str
    lat: float
    lon: float
    flora_profile: list[str]
    # staggered starting conditions so a cluster is not 20 identical hives
    population: int = 32000
    stores_kg: float = 12.0
    varroa_load: float = 1.0


@dataclass
class Simulation:
    nodes: list[NodeSpec]
    start: datetime
    step_minutes: int = 15
    speed: float = 1.0            # sim seconds per wall second; 1 = real time
    seed: int = 42
    scenarios: list[Scenario] = field(default_factory=list)
    publish: bool = True
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    max_inflight: int = 500       # publishes buffered before we wait for acks
    inspection_days: float = 11.0 # how often the beekeeper opens each box

    def __post_init__(self) -> None:
        rng = random.Random(self.seed)
        self.clock = self.start
        self.weather = WeatherModel(seed=self.seed)
        self.colonies: dict[str, Colony] = {}
        self.state: dict[str, dict] = {}
        self.seq: dict[str, int] = {}

        for spec in self.nodes:
            c = Colony(
                hive_id=spec.hive_id,
                node_id=spec.node_id,
                lat=spec.lat,
                lon=spec.lon,
                flora_profile=spec.flora_profile,
                population=spec.population,
                stores_kg=spec.stores_kg,
                varroa_load=spec.varroa_load,
                rng=random.Random(rng.randint(0, 10**9)),
            )
            # jitter so hives are individuals, not clones
            c.population = int(c.population * rng.uniform(0.75, 1.25))
            c.stores_kg *= rng.uniform(0.6, 1.4)
            c.brood_frames = rng.uniform(3.0, 7.0)
            c.queen_age_days = rng.randint(30, 500)
            self.colonies[spec.node_id] = c
            self.state[spec.node_id] = {
                # staggered: nobody inspects 200 hives on the same morning
                "next_visit": self.start + timedelta(
                    days=rng.uniform(0, self.inspection_days)),
            }
            self.seq[spec.node_id] = 0

        self._client = None
        self._inflight: deque = deque()
        if self.publish:
            self._connect()

    # ------------------------------------------------------------------ #
    def _connect(self) -> None:
        import paho.mqtt.client as mqtt

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"aurabee-sim-{int(time.time())}"
        )
        self._client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
        self._client.loop_start()
        log.info("connected to mqtt %s:%s", self.mqtt_host, self.mqtt_port)

    # ------------------------------------------------------------------ #
    def _fire_due_scenarios(self) -> None:
        for s in self.scenarios:
            if s.fired or s.when > self.clock:
                continue
            colony = self.colonies.get(s.node_id)
            if colony is None:
                log.warning("scenario targets unknown node %s, skipping", s.node_id)
                s.fired = True
                continue
            msg = apply_scenario(s, colony, self.state[s.node_id])
            s.fired = True
            log.warning("[%s] SCENARIO %s on %s: %s",
                        self.clock.date(), s.kind, s.node_id, msg)

    def _expire_transients(self) -> None:
        now_ts = self.clock.timestamp()
        for node_id, st in self.state.items():
            until = st.get("robbing_until")
            if until and now_ts > until:
                self.colonies[node_id].robbing = False
                st.pop("robbing_until")

    # ------------------------------------------------------------------ #
    def build_frame(self, spec: NodeSpec) -> TelemetryFrame | None:
        """None means the node is silent this tick (offline / stolen)."""
        st = self.state[spec.node_id]
        off_from = st.get("offline_from")
        if off_from and self.clock.timestamp() >= off_from:
            return None

        colony = self.colonies[spec.node_id]
        w = self.weather.at(self.clock)
        dt_hours = self.step_minutes / 60.0

        colony.step(self.clock, w, dt_hours)

        # natural swarming once pressure saturates -- not every swarm is scripted
        if colony.swarm_pressure >= 1.0 and colony.rng.random() < 0.25 * dt_hours:
            colony.do_swarm()
            log.warning("[%s] natural swarm on %s", self.clock.date(), spec.node_id)

        # the beekeeper's rounds
        nxt = st.get("next_visit")
        if nxt and self.clock >= nxt:
            action = colony.beekeeper_visit()
            st["next_visit"] = self.clock + timedelta(
                days=self.inspection_days * colony.rng.uniform(0.7, 1.3))
            if action:
                log.info("[%s] %s: beekeeper %s", self.clock.date(),
                         spec.node_id, action)

        # load-cell drift, if the sensor_drift scenario is active
        drift = st.get("drift_per_day")
        if drift:
            colony.weight_bias_kg += drift * (dt_hours / 24.0)

        t_in, rh_in = colony.read_temps(w)
        activity = colony.foraging_activity(w)
        inn, out = colony.entrance_counts(w, dt_hours)
        rms, feats = acoustic_features(colony, activity)

        self.seq[spec.node_id] += 1
        dlat, dlon = colony.gps_offset

        # battery: solar-charged, sags overnight and in rain
        batt = 4.05 - 0.25 * (1.0 - w.daylight) - (0.1 if w.is_raining else 0.0)

        frame = TelemetryFrame(
            node_id=spec.node_id,
            ts=int(self.clock.replace(tzinfo=timezone.utc).timestamp()),
            seq=self.seq[spec.node_id],
            weight_kg=round(colony.gross_weight_kg, 3),
            t_in=t_in,
            rh_in=rh_in,
            t_out=w.t_out,
            rh_out=w.rh_out,
            sound_rms=rms,
            entrance_in=inn,
            entrance_out=out,
            batt_v=round(batt + colony.rng.gauss(0, 0.02), 2),
            lat=round(spec.lat + dlat, 5),
            lon=round(spec.lon + dlon, 5),
            features=feats,
        )
        return frame.sign(spec.hmac_key)

    # ------------------------------------------------------------------ #
    def run(self, until: datetime | None = None, max_ticks: int | None = None,
            on_frame=None) -> int:
        tick, published = 0, 0
        wall_per_tick = (self.step_minutes * 60.0) / max(self.speed, 1e-9)
        log.info("start=%s step=%dmin speed=%gx (%.3fs wall per tick) nodes=%d",
                 self.start.isoformat(), self.step_minutes, self.speed,
                 wall_per_tick, len(self.nodes))

        try:
            while True:
                if until and self.clock >= until:
                    break
                if max_ticks and tick >= max_ticks:
                    break

                self._fire_due_scenarios()
                self._expire_transients()

                for spec in self.nodes:
                    frame = self.build_frame(spec)
                    if frame is None:
                        continue
                    if self._client:
                        info = self._client.publish(
                            topic_for(spec.node_id),
                            json.dumps(frame.to_dict(), separators=(",", ":")),
                            qos=1,
                        )
                        self._inflight.append(info)
                    if on_frame:
                        on_frame(frame, self.colonies[spec.node_id])
                    published += 1

                # Backpressure. At --speed 0 we generate frames far faster than
                # the socket drains them; without this the client queue grows
                # unbounded and everything still queued is silently discarded on
                # disconnect. (That bug cost us two thirds of a run once.)
                if len(self._inflight) >= self.max_inflight:
                    self._drain(timeout=10.0)

                tick += 1
                self.clock += timedelta(minutes=self.step_minutes)

                if tick % 96 == 0:   # once per simulated day at 15-min steps
                    alive = sum(1 for c in self.colonies.values() if not c.dead)
                    log.info("%s  frames=%d  alive=%d/%d  median_wt=%.1fkg",
                             self.clock.date(), published, alive, len(self.colonies),
                             _median([c.gross_weight_kg for c in self.colonies.values()]))

                if wall_per_tick > 0.001:
                    time.sleep(wall_per_tick)
        except KeyboardInterrupt:
            log.info("interrupted at %s after %d frames", self.clock, published)
        finally:
            if self._client:
                # drain before disconnecting, or QoS-1 messages still in the
                # client queue are thrown away
                pending = len(self._inflight)
                if pending:
                    log.info("draining %d queued publishes...", pending)
                self._drain(timeout=30.0)
                self._client.loop_stop()
                self._client.disconnect()

        return published

    def _drain(self, timeout: float = 10.0) -> None:
        """Block until the broker has acknowledged everything we published."""
        deadline = time.time() + timeout
        while self._inflight:
            info = self._inflight.popleft()
            remaining = deadline - time.time()
            if remaining <= 0:
                log.warning("drain timed out with %d publishes unconfirmed",
                            len(self._inflight) + 1)
                self._inflight.clear()
                return
            try:
                info.wait_for_publish(timeout=remaining)
            except (ValueError, RuntimeError) as exc:
                log.warning("publish not confirmed: %s", exc)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)
