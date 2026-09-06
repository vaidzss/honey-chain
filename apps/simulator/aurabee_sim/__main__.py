"""CLI entry point.

    # 20 hives, one simulated season in about two minutes, streaming to MQTT
    python -m aurabee_sim --hives 20 --start 2026-11-01 --days 120 --speed 4000

    # generate a labelled training set without touching MQTT
    python -m aurabee_sim --hives 40 --days 240 --no-publish --speed 0 \
        --dump-csv ../../ml/datasets/sim_v1.csv

    # exercise a specific detector
    python -m aurabee_sim --hives 8 --days 40 --speed 3000 \
        --scenario queenless@HN-UP-SIT-0003@2026-11-12 \
        --scenario swarm@HN-UP-SIT-0005@2026-11-20
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import sys
from datetime import datetime, timedelta

from dotenv import find_dotenv, load_dotenv

from .acoustics import N_BANDS, N_MFCC
from .runner import NodeSpec, Simulation
from .scenarios import KINDS, Scenario

# walks up to the repo root, so the simulator works from any cwd
load_dotenv(find_dotenv(usecwd=True))

# Reference cluster: Sitapur, Uttar Pradesh -- mustard belt, real KVIC territory.
DEFAULT_LAT, DEFAULT_LON = 27.5700, 80.6800
DEFAULT_FLORA = ["mustard", "acacia", "multiflora"]

ACOUSTIC_BANDS = range(N_BANDS)


def colony_state(colony) -> str:
    """The single label a health classifier is trained to predict.

    Derived from ground truth, priority-ordered because a colony can be several
    things at once and the beekeeper needs the one that matters most today. A
    starving colony with mites should be told about the starving.

    Queenlessness only counts after five days: every swarm leaves a colony
    briefly queenless and it requeens itself within about three weeks. Labelling
    that transient as a fault would teach the model to cry wolf after every
    natural swarm.
    """
    if colony.dead:
        return "dead"
    if colony.robbing:
        return "robbing"
    if colony.stores_kg < 1.5:
        return "starvation"
    if not colony.queen_present and colony.queenless_days > 5:
        return "queenless"
    if colony.swarm_pressure > 0.7:
        return "pre_swarm"
    if colony.varroa_load > 5.0:
        return "varroa"
    if colony.wax_moth > 0.3:
        return "wax_moth"
    return "healthy"


def _device_key() -> str:
    """The key synthetic nodes sign with. Fails closed, like the seal pepper.

    Telemetry is what bounds minting, so a signing key with a published default
    would let anyone forge the frames that set a beekeeper's ceiling.
    """
    key = os.getenv("DEVICE_HMAC_KEY")
    if key:
        return key
    if os.getenv("AURABEE_ENV", "").lower() == "development":
        return "dev-device-key-not-for-any-real-deployment"
    raise SystemExit(
        "DEVICE_HMAC_KEY is not set. Set it, or set AURABEE_ENV=development "
        "to use the throwaway development key.")


def synthetic_nodes(n: int, seed: int, hmac_key: str) -> list[NodeSpec]:
    rng = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        out.append(
            NodeSpec(
                node_id=f"HN-UP-SIT-{i:04d}",
                hive_id=f"sim-hive-{i:04d}",
                hmac_key=hmac_key,
                # apiaries are clustered, not scattered across the district
                lat=round(DEFAULT_LAT + rng.gauss(0, 0.012), 5),
                lon=round(DEFAULT_LON + rng.gauss(0, 0.012), 5),
                flora_profile=DEFAULT_FLORA,
                population=rng.randint(24000, 42000),
                stores_kg=rng.uniform(6.0, 18.0),
                varroa_load=rng.uniform(0.4, 2.5),
            )
        )
    return out


def nodes_from_db(dsn: str) -> list[NodeSpec]:
    """Load registered sim nodes so telemetry lands against real hive rows."""
    import psycopg

    sql = """
        SELECT n.id, n.hive_id::text, n.hmac_key, a.lat, a.lon, a.flora_profile
        FROM nodes n
        JOIN hives h   ON h.id = n.hive_id
        JOIN apiaries a ON a.id = h.apiary_id
        WHERE n.node_type = 'sim' AND n.status = 'active'
        ORDER BY n.id
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(
            "no sim nodes registered. Run `python scripts/seed.py` first, "
            "or use --hives N for standalone synthetic nodes."
        )
    return [
        NodeSpec(
            node_id=r[0], hive_id=r[1], hmac_key=r[2],
            lat=r[3] or DEFAULT_LAT, lon=r[4] or DEFAULT_LON,
            flora_profile=list(r[5]) or DEFAULT_FLORA,
        )
        for r in rows
    ]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aurabee_sim",
        description="Physics-informed beehive telemetry simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--hives", type=int, default=20,
                     help="number of synthetic hives (default 20)")
    src.add_argument("--from-db", action="store_true",
                     help="load registered sim nodes from the database instead")

    p.add_argument("--start", default="2026-11-01",
                   help="simulated start date, YYYY-MM-DD (default: mustard flow onset)")
    p.add_argument("--days", type=float, default=90, help="simulated days to run")
    p.add_argument("--step-minutes", type=int, default=15,
                   help="sampling interval, matches a real node duty cycle")
    p.add_argument("--speed", type=float, default=2000,
                   help="sim seconds per wall second; 0 = as fast as possible")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--inspection-days", type=float, default=11.0,
                   help="how often the beekeeper inspects each hive; "
                        "0 disables management entirely")

    p.add_argument("--scenario", action="append", default=[], metavar="SPEC",
                   help=f"kind@node@YYYY-MM-DD[@param]; kinds: {', '.join(KINDS)}")
    p.add_argument("--auto-scenarios", action="store_true",
                   help="scatter a plausible mix of faults across the run")

    p.add_argument("--no-publish", action="store_true", help="do not connect to MQTT")
    p.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST", "localhost"))
    p.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    p.add_argument("--dump-csv", metavar="PATH",
                   help="also write every frame plus ground-truth labels to CSV")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def auto_scenarios(nodes: list[NodeSpec], start: datetime, days: float,
                   seed: int) -> list[Scenario]:
    """A believable mix: most hives are fine, a few are not. Real apiaries are
    not uniformly diseased, and a model trained on balanced classes will
    over-alert in the field."""
    rng = random.Random(seed + 99)
    picks = [
        ("queenless", 0.10), ("swarm", 0.10), ("varroa", 0.12),
        ("robbing", 0.05), ("wax_moth", 0.05), ("abscond", 0.03),
        ("sensor_drift", 0.04), ("offline", 0.04), ("harvest", 0.35),
    ]
    out: list[Scenario] = []
    for spec in nodes:
        for kind, prob in picks:
            if rng.random() < prob:
                day = rng.uniform(days * 0.15, days * 0.9)
                out.append(Scenario(
                    kind=kind,
                    node_id=spec.node_id,
                    when=start + timedelta(days=day),
                    param={"varroa": rng.uniform(6, 14),
                           "harvest": rng.uniform(5, 14),
                           "wax_moth": rng.uniform(0.3, 0.7)}.get(kind),
                ))
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    start = datetime.fromisoformat(args.start)

    if args.from_db:
        dsn = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not dsn:
            raise SystemExit("--from-db needs DATABASE_URL in the environment")
        nodes = nodes_from_db(dsn)
    else:
        nodes = synthetic_nodes(args.hives, args.seed, _device_key())

    scenarios = [Scenario.parse(s) for s in args.scenario]
    if args.auto_scenarios:
        scenarios += auto_scenarios(nodes, start, args.days, args.seed)
    scenarios.sort(key=lambda s: s.when)

    sim = Simulation(
        nodes=nodes,
        start=start,
        step_minutes=args.step_minutes,
        speed=args.speed if args.speed > 0 else 1e12,
        seed=args.seed,
        scenarios=scenarios,
        publish=not args.no_publish,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        inspection_days=args.inspection_days,
    )

    writer = None
    fh = None
    if args.dump_csv:
        os.makedirs(os.path.dirname(os.path.abspath(args.dump_csv)), exist_ok=True)
        fh = open(args.dump_csv, "w", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        # Everything prefixed gt_ is ground truth. It is what makes this a
        # *labelled* set and it is never available to a model in the field --
        # a model that reads gt_ columns at inference is cheating.
        n_bands = len(ACOUSTIC_BANDS)
        writer.writerow(
            ["ts", "node_id", "weight_kg", "t_in", "rh_in", "t_out", "rh_out",
             "sound_rms", "peak_hz", "centroid_hz",
             "entrance_in", "entrance_out", "batt_v"]
            + [f"be_{i}" for i in range(n_bands)]
            + [f"mfcc_{i}" for i in range(N_MFCC)]
            + ["gt_state", "gt_population", "gt_stores_kg", "gt_brood_frames",
               "gt_queen_present", "gt_queenless_days", "gt_varroa_load",
               "gt_wax_moth", "gt_health", "gt_swarm_pressure", "gt_robbing",
               "gt_dead", "gt_harvested_kg"]
        )

        def on_frame(frame, colony):
            feats = frame.features or {}
            be = feats.get("band_energy") or [0.0] * n_bands
            mf = feats.get("mfcc") or [0.0] * N_MFCC
            writer.writerow(
                [frame.ts, frame.node_id, frame.weight_kg, frame.t_in, frame.rh_in,
                 frame.t_out, frame.rh_out, frame.sound_rms,
                 feats.get("peak_hz"), feats.get("centroid_hz"),
                 frame.entrance_in, frame.entrance_out, frame.batt_v]
                + list(be) + list(mf)
                + [colony_state(colony),
                   colony.population, round(colony.stores_kg, 3),
                   round(colony.brood_frames, 2), int(colony.queen_present),
                   round(colony.queenless_days, 2),
                   round(colony.varroa_load, 3), round(colony.wax_moth, 3),
                   round(colony.health, 3), round(colony.swarm_pressure, 3),
                   int(colony.robbing), int(colony.dead),
                   round(colony.harvested_kg, 2)]
            )
    else:
        on_frame = None

    try:
        n = sim.run(until=start + timedelta(days=args.days), on_frame=on_frame)
    finally:
        if fh:
            fh.close()

    logging.info("done: %d frames over %g simulated days", n, args.days)
    if args.dump_csv:
        logging.info("wrote %s", args.dump_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
