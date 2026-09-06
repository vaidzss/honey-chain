"""Ambient weather model.

Deliberately simple but *seasonally correct* for north Indian conditions. The
yield and health models must learn that in-hive temperature is regulated
against a moving ambient baseline; if ambient were flat noise there would be
nothing to learn and the whole simulator would be a lying oracle.

Monthly normals below are indicative for the Indo-Gangetic plain (Sitapur, UP
is the reference cluster). Swap in real IMD or Open-Meteo history per district
before training anything you intend to deploy -- see docs/06-ml.md.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# month -> (mean daily min C, mean daily max C, mean RH %)
_NORMALS: dict[int, tuple[float, float, float]] = {
    1:  (8.0, 23.0, 62),
    2:  (11.0, 26.0, 55),
    3:  (16.0, 33.0, 43),
    4:  (22.0, 39.0, 32),
    5:  (26.0, 41.0, 33),
    6:  (27.5, 38.0, 52),
    7:  (26.5, 33.0, 78),
    8:  (26.0, 32.0, 82),
    9:  (25.0, 33.0, 76),
    10: (20.0, 32.5, 62),
    11: (13.0, 29.0, 60),
    12: (9.0, 24.0, 64),
}

# Coldest just before dawn, hottest mid-afternoon.
_MIN_HOUR = 5.5
_MAX_HOUR = 15.0


@dataclass
class Weather:
    """Ambient conditions at a point in time."""

    t_out: float
    rh_out: float
    is_raining: bool
    daylight: float  # 0..1, drives foraging


class WeatherModel:
    def __init__(self, seed: int = 0, warming_offset: float = 0.0):
        self._rng = random.Random(seed)
        # per-run bias so two apiaries in a demo are not bit-identical
        self._bias = self._rng.uniform(-0.8, 0.8) + warming_offset
        self._day_anomaly: dict[int, float] = {}
        self._rain_days: dict[int, bool] = {}

    def _daily(self, day_ordinal: int, month: int) -> tuple[float, bool]:
        """A per-day anomaly, cached so the same day is consistent across calls
        and across hives in the same apiary."""
        if day_ordinal not in self._day_anomaly:
            r = random.Random(day_ordinal * 7919 + self._rng.randint(0, 1000))
            self._day_anomaly[day_ordinal] = r.gauss(0, 2.2)
            monsoon = month in (7, 8, 9)
            p_rain = 0.55 if monsoon else (0.08 if month in (6, 10) else 0.03)
            self._rain_days[day_ordinal] = r.random() < p_rain
        return self._day_anomaly[day_ordinal], self._rain_days[day_ordinal]

    def at(self, dt) -> Weather:
        month = dt.month
        t_min, t_max, rh_mean = _NORMALS[month]
        anomaly, raining = self._daily(dt.toordinal(), month)

        hour = dt.hour + dt.minute / 60.0
        # cosine between the daily min and max, phase-shifted to real clock time
        span = (t_max - t_min) / 2.0
        mid = (t_max + t_min) / 2.0
        phase = 2 * math.pi * (hour - _MIN_HOUR) / 24.0
        t_out = mid - span * math.cos(phase) + anomaly + self._bias

        rh = rh_mean + (t_max - t_out) * 1.4
        if raining:
            t_out -= 3.5
            rh += 18
        rh_out = max(12.0, min(99.0, rh))

        # crude photoperiod: longer days in summer
        half_day = 5.6 + 0.9 * math.sin(2 * math.pi * (dt.timetuple().tm_yday - 80) / 365.0)
        sunrise, sunset = 12 - half_day, 12 + half_day
        if hour <= sunrise or hour >= sunset:
            daylight = 0.0
        else:
            daylight = math.sin(math.pi * (hour - sunrise) / (sunset - sunrise))
        if raining:
            daylight *= 0.25   # bees mostly stay in

        return Weather(
            t_out=round(t_out, 2),
            rh_out=round(rh_out, 2),
            is_raining=raining,
            daylight=round(daylight, 4),
        )
