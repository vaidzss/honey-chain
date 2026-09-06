"""Colony state and physics.

The one thing this file must get right is **thermoregulation**, because it is
the signal the health models actually learn:

    A queenright colony with brood holds its brood nest at 34-35 C whatever the
    ambient does. A colony that is queenless, collapsing, or too small to form a
    cluster loses that grip and its in-hive temperature drifts toward ambient.

Everything else (weight, entrance traffic, acoustics) is secondary confirmation.
If you change one number in this file, change it here and re-check that a
healthy hive still tracks 34.8 +/- 0.4 through a 12 C night.

Units: kg, degrees C, %RH, individual bees.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .flora import nectar_kg_per_day
from .weather import Weather

# --- physical constants of a Langstroth-ish box ---------------------------
TARE_KG = 11.0          # empty box + frames + bottom board + lid
COMB_KG = 5.5           # drawn comb, does not change much day to day
BEE_MASS_KG = 0.00011   # ~110 mg per adult bee
BROOD_KG_PER_FRAME = 0.55

MEAN_DAYLIGHT = 0.32    # 24h mean of the daylight sine; see step() for why

BROOD_SETPOINT_C = 34.8   # the number the whole health model hangs on
CLUSTER_SETPOINT_C = 27.0 # broodless winter cluster runs cooler
TARGET_RH_IN = 55.0


@dataclass
class Colony:
    """One hive's biological state."""

    hive_id: str
    node_id: str
    lat: float
    lon: float
    flora_profile: list[str]

    population: int = 32000
    brood_frames: float = 5.0
    stores_kg: float = 12.0
    queen_present: bool = True
    queen_age_days: int = 120
    varroa_load: float = 1.0        # mites per 100 bees
    wax_moth: float = 0.0           # 0..1 infestation
    health: float = 1.0             # 0..1 derived summary

    # --- transient / event state ---
    swarm_pressure: float = 0.0     # 0..1, ramps before a swarm
    robbing: bool = False
    absconding: bool = False
    dead: bool = False
    weight_bias_kg: float = 0.0     # load-cell drift, for the sensor_fault scenario
    gps_offset: tuple[float, float] = (0.0, 0.0)
    harvested_kg: float = 0.0       # cumulative, for ground truth vs the yield model
    last_activity: float = 0.0      # cached each step; reused by the frame builder
    queenless_days: float = 0.0     # drives requeening
    requeen_blocked: bool = False   # set by the 'queenless' scenario: the colony
                                    # failed to raise a replacement, which is the
                                    # failure mode we actually want to detect

    rng: random.Random = field(default_factory=random.Random)

    # ------------------------------------------------------------------ #
    # derived quantities
    # ------------------------------------------------------------------ #
    @property
    def bee_mass_kg(self) -> float:
        return self.population * BEE_MASS_KG

    @property
    def brood_mass_kg(self) -> float:
        return self.brood_frames * BROOD_KG_PER_FRAME

    @property
    def gross_weight_kg(self) -> float:
        return (
            TARE_KG + COMB_KG + self.stores_kg
            + self.bee_mass_kg + self.brood_mass_kg
            + self.weight_bias_kg
        )

    @property
    def pop_factor(self) -> float:
        """Colony strength, 0..1. A 25k-bee colony is a full working unit."""
        return min(1.0, self.population / 25000.0)

    @property
    def regulation(self) -> float:
        """How firmly the colony can hold its setpoint, 0..1.

        Queenlessness does not stop thermoregulation outright -- the workers
        are still there and still cluster -- but brood rearing stops, the
        population ages out, and the grip loosens. That gradual decouple, not a
        step change, is what the acoustic+thermal model has to catch.
        """
        if self.dead or self.population < 1500:
            return 0.0
        q = 1.0 if self.queen_present else 0.55
        return max(0.0, min(0.98, self.pop_factor * self.health * q))

    @property
    def setpoint(self) -> float:
        return BROOD_SETPOINT_C if self.brood_frames > 0.5 else CLUSTER_SETPOINT_C

    # ------------------------------------------------------------------ #
    # sensor readings
    # ------------------------------------------------------------------ #
    def read_temps(self, w: Weather) -> tuple[float, float]:
        """(t_in, rh_in) given ambient."""
        if self.dead or self.population < 500:
            # an empty box is just a thermal mass: lags ambient, regulates nothing
            return round(w.t_out + self.rng.gauss(0, 0.3), 2), round(w.rh_out, 2)

        reg = self.regulation
        sp = self.setpoint
        if w.t_out <= sp:
            t_in = w.t_out + (sp - w.t_out) * reg
        else:
            # heat stress: fanning and evaporative cooling, but they lose ground
            # once ambient runs well above the setpoint
            t_in = sp + (w.t_out - sp) * (1.0 - 0.8 * reg)

        t_in += self.rng.gauss(0, 0.18)
        rh_in = w.rh_out + (TARGET_RH_IN - w.rh_out) * reg + self.rng.gauss(0, 1.5)
        return round(t_in, 2), round(max(5.0, min(99.0, rh_in)), 2)

    def foraging_activity(self, w: Weather) -> float:
        """0..1. Gates nectar income and entrance traffic."""
        if self.dead or self.absconding:
            return 0.0
        if w.t_out < 12.0 or w.t_out > 43.0:
            return 0.0          # too cold to fly / too hot to forage
        temp_gate = min(1.0, (w.t_out - 12.0) / 8.0)
        if w.t_out > 38.0:
            temp_gate *= max(0.0, (43.0 - w.t_out) / 5.0)
        q = 1.0 if self.queen_present else 0.75
        return max(0.0, w.daylight * temp_gate * self.pop_factor * self.health * q)

    def entrance_counts(self, w: Weather, dt_hours: float) -> tuple[int, int]:
        act = self.foraging_activity(w)
        base = act * self.population * 0.020 * dt_hours
        if self.robbing:
            base *= 3.2      # robbers pile in; traffic spikes with no weight gain
        out = int(max(0, self.rng.gauss(base, base * 0.12 + 1)))
        # returns lag departures slightly within a sampling window
        inn = int(max(0, out * self.rng.uniform(0.90, 1.02)))
        return inn, out

    # ------------------------------------------------------------------ #
    # biology, stepped once per simulated interval
    # ------------------------------------------------------------------ #
    def step(self, dt, w: Weather, dt_hours: float) -> None:
        if self.dead:
            return

        day_frac = dt_hours / 24.0

        # --- nectar income ------------------------------------------------
        rate, _ = nectar_kg_per_day(self.flora_profile, dt)
        act = self.foraging_activity(w)
        self.last_activity = act
        # `rate` is a whole-day figure but `act` is instantaneous and already
        # carries the day/night cycle, so it has to be normalised by its own
        # daily mean or the two multiply the diurnal term in twice and the
        # colony starves. MEAN_DAYLIGHT is the average of the daylight sine
        # over 24h (~2/pi x half the day).
        gain = rate * (act / MEAN_DAYLIGHT) * self.pop_factor * self.health * day_frac
        # ripening: bees drive moisture off, so stored mass is less than income
        gain *= 0.82

        # --- consumption --------------------------------------------------
        # Brood rearing, not mere existence, is what a colony spends stores on;
        # a broodless winter cluster is remarkably cheap to run.
        burn = (
            0.10 * self.pop_factor
            + 0.045 * self.brood_frames
            + 0.10 * self.pop_factor * max(0.0, (15.0 - w.t_out) / 15.0)
        ) * day_frac

        if self.robbing:
            # robbed stores leave fast, and this is a weight signature nothing
            # else produces: sharp loss with high traffic in fine weather
            burn += 2.5 * day_frac

        self.stores_kg = max(0.0, self.stores_kg + gain - burn)

        # --- population ---------------------------------------------------
        if self.queen_present and self.stores_kg > 0.5 and not self.absconding:
            # a deep frame of brood emerges at roughly 300 bees/day
            births = self.brood_frames * 300 * day_frac
            if self.population > 55000:
                births *= 0.3            # box is full; the colony preps to swarm instead
            # brood expands with forage, contracts in dearth and in cold
            target_brood = 2.0 + 7.0 * min(1.0, rate / 2.0)
            if w.t_out < 12.0:
                target_brood = min(target_brood, 2.5)
            self.brood_frames += (target_brood - self.brood_frames) * 0.06 * day_frac
        else:
            births = 0.0
            self.brood_frames = max(0.0, self.brood_frames - 0.35 * day_frac)

        # Attrition is dominated by *flying*, not by existing: summer foragers
        # wear out in ~5 weeks while broodless winter bees live for months.
        # Modelling this as a flat daily rate collapses every colony over a
        # winter, which is exactly the bug this replaced.
        death_rate = (
            0.008                                   # house-bee baseline
            + 0.030 * act                           # forager wear
            + 0.004 * max(0.0, self.varroa_load - 3.0)
            + 0.020 * self.wax_moth
        )
        if self.stores_kg <= 0.2:
            death_rate += 0.10          # starvation
        deaths = self.population * death_rate * day_frac

        self.population = int(max(0, self.population + births - deaths))

        # --- pests --------------------------------------------------------
        if self.varroa_load > 0:
            # mites track brood availability; they reproduce in capped cells
            self.varroa_load *= (1.0 + 0.02 * day_frac * (0.4 + self.brood_frames / 8.0))
            self.varroa_load = min(40.0, self.varroa_load)
        self.wax_moth = min(1.0, self.wax_moth * (1.0 + 0.03 * day_frac)) if self.wax_moth else 0.0

        # --- queen --------------------------------------------------------
        self.queen_age_days += day_frac
        if self.queen_present:
            self.queenless_days = 0.0
            if self.queen_age_days > 730 and self.rng.random() < 0.002 * day_frac:
                self.queen_present = False   # old queen fails
        else:
            self.queenless_days += day_frac
            # After a swarm the colony raises a virgin, she mates, and laying
            # resumes in roughly 3-4 weeks -- provided there was brood to raise
            # her from. Without this the whole apiary quietly dies over a
            # season, which is not a beekeeping outcome, it is a modelling bug.
            can_requeen = (
                not self.requeen_blocked
                and self.population > 3000
                and self.queenless_days > 21
                and w.t_out > 15.0          # she must be able to take a mating flight
            )
            if can_requeen and self.rng.random() < 0.20 * day_frac:
                self.queen_present = True
                self.queen_age_days = 0
                self.queenless_days = 0.0
                self.brood_frames = max(self.brood_frames, 1.0)

        # --- swarm pressure ------------------------------------------------
        # builds when the colony is strong, crowded and in a good flow
        if self.queen_present and self.population > 42000 and rate > 0.8:
            self.swarm_pressure = min(1.0, self.swarm_pressure + 0.06 * day_frac)
        else:
            self.swarm_pressure = max(0.0, self.swarm_pressure - 0.04 * day_frac)

        # --- health summary -------------------------------------------------
        h = 1.0
        h -= 0.055 * max(0.0, self.varroa_load - 2.0)   # economic threshold ~3/100
        h -= 0.35 * self.wax_moth
        if not self.queen_present:
            h -= 0.30
        if self.stores_kg < 1.5:
            h -= 0.25
        self.health = max(0.05, min(1.0, h))

        if self.population < 400:
            self.dead = True

    # ------------------------------------------------------------------ #
    # discrete events
    # ------------------------------------------------------------------ #
    def do_swarm(self) -> None:
        """A prime swarm takes the old queen and roughly 60% of the workforce,
        plus the honey they can carry. Signature: a step change in weight of
        several kg within minutes -- unmistakable on a load cell."""
        leaving = int(self.population * self.rng.uniform(0.55, 0.65))
        self.population -= leaving
        self.stores_kg = max(0.0, self.stores_kg - leaving * BEE_MASS_KG * 0.35)
        self.queen_present = False        # virgin queen must mate; brood gap follows
        self.queen_age_days = 0
        self.queenless_days = 0.0
        self.swarm_pressure = 0.0
        self.brood_frames *= 0.6

    def do_abscond(self) -> None:
        """The whole colony leaves. Common with A. cerana under stress; the box
        is left with comb and nothing else."""
        self.absconding = True
        self.population = 0
        self.brood_frames = 0.0
        self.stores_kg = max(0.0, self.stores_kg * 0.15)
        self.queen_present = False
        self.dead = True

    def beekeeper_visit(self) -> str | None:
        """A managed colony, inspected and treated.

        Without this the simulator models faults that always run to death,
        because nobody ever feeds a starving colony or treats mites. That
        produced a dataset where 87% of hives ended dead and a third of all
        samples were labelled `dead` -- an apiary no beekeeper would still own,
        and a distribution that would teach a model the wrong dynamics
        entirely. Faults should be *episodes*, not death spirals.

        Detection is deliberately imperfect. A beekeeper opening a box in
        February does not catch everything, and a model trained on
        instantly-remedied faults would never see the multi-week decline that
        is the actual thing worth detecting early.
        """
        if self.dead or self.absconding:
            return None

        # starvation first: it kills in days, everything else kills in weeks
        if self.stores_kg < 3.0 and self.rng.random() < 0.90:
            self.stores_kg += self.rng.uniform(4.0, 7.0)   # emergency sugar feed
            return "fed"

        if self.varroa_load > 4.0 and self.rng.random() < 0.70:
            self.varroa_load *= self.rng.uniform(0.10, 0.25)   # miticide strips
            return "treated_varroa"

        # A colony the scenario marked unrecoverable stays unrecoverable --
        # that is the fault case the detector has to find, so the beekeeper
        # must not quietly heal it.
        if (not self.queen_present and self.queenless_days > 14
                and not self.requeen_blocked and self.rng.random() < 0.6):
            self.queen_present = True
            self.queen_age_days = 0
            self.queenless_days = 0.0
            self.brood_frames = max(self.brood_frames, 1.0)
            return "requeened"

        if self.wax_moth > 0.25 and self.rng.random() < 0.80:
            self.wax_moth *= self.rng.uniform(0.10, 0.30)
            return "cleaned"

        return None

    def harvest(self, kg: float) -> float:
        """Beekeeper removes honey. Returns kg actually taken."""
        take = min(kg, max(0.0, self.stores_kg - 2.0))  # leave the colony a reserve
        self.stores_kg -= take
        self.harvested_kg += take
        return take
