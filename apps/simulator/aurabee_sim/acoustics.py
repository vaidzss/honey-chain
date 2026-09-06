"""Synthetic hive acoustics.

We do not synthesise audio. We synthesise the *features a node would compute
on-device* -- band energies and MFCCs -- because that is what actually crosses
the LoRa link in the field (see docs/04-telemetry-contract.md).

The spectrum is built from physically-motivated components rather than noise,
so the downstream classifier learns something real:

  * settled colony   -- narrow worker hum around 230 Hz plus a first harmonic
  * pre-swarm        -- hum broadens and its centroid climbs toward 300-500 Hz
  * queenless        -- workers 'quack' low while a virgin queen 'toots' near
                        350-450 Hz; the tell is a *bimodal* spectrum
  * robbing/fanning  -- broadband energy lifts across the whole band

If you swap this for real audio later, keep the same feature contract and
nothing downstream changes.
"""

from __future__ import annotations

import math

import numpy as np

N_BANDS = 20          # 20 x 100 Hz bands, 0-2 kHz
BAND_HZ = 100.0
N_MFCC = 13

_CENTRES = np.array([(i + 0.5) * BAND_HZ for i in range(N_BANDS)])
# Hive noise is not white: energy falls off with frequency. A flat floor drags
# the spectral centroid up around 150 Hz too high, which reads wrong to anyone
# who has actually looked at a hive spectrogram.
_TILT = (_CENTRES[0] / _CENTRES) ** 0.6


def _peak(centre_hz: float, width_hz: float, amp: float) -> np.ndarray:
    return amp * np.exp(-0.5 * ((_CENTRES - centre_hz) / width_hz) ** 2)


def spectrum(colony, activity: float) -> np.ndarray:
    """Band energies, linear scale."""
    if colony.dead or colony.population < 500:
        # an empty box still picks up wind and structure noise
        return np.full(N_BANDS, 1e-4) + np.random.default_rng().normal(0, 2e-5, N_BANDS).clip(0)

    strength = colony.pop_factor
    swarm = colony.swarm_pressure

    # worker hum: centre climbs and broadens as swarm pressure builds
    hum_hz = 228.0 + 90.0 * swarm + 12.0 * activity
    hum_w = 45.0 + 55.0 * swarm
    spec = _peak(hum_hz, hum_w, 1.0 * strength)
    spec += _peak(hum_hz * 2, hum_w * 1.4, 0.35 * strength)   # first harmonic

    # flight/fanning broadband floor
    spec += 0.06 * strength * (0.3 + activity) * _TILT

    if not colony.queen_present:
        # Bimodal queenless signature. The workers' coordinated hum weakens --
        # a queenless colony "roars" rather than hums -- which is what makes
        # the 400 Hz tooting band visible at all. Without damping the
        # fundamental the tooting stays buried under it and the feature is
        # useless to the classifier.
        spec *= 0.72
        spec += _peak(400.0, 32.0, 0.55 * strength)   # virgin queen tooting
        spec += _peak(215.0, 26.0, 0.38 * strength)   # worker quacking
        spec += 0.07 * strength * _TILT               # restless, noisier overall

    if colony.robbing:
        spec += 0.28 * strength * _TILT
        spec += _peak(320.0, 120.0, 0.35 * strength)

    if colony.health < 0.55:
        spec *= 0.55 + 0.45 * colony.health           # a sick colony is a quiet one

    rng = np.random.default_rng()
    spec = spec * rng.normal(1.0, 0.06, N_BANDS)
    return np.clip(spec, 1e-6, None)


def mfcc(band_energy: np.ndarray, n: int = N_MFCC) -> list[float]:
    """DCT-II of the log band energies -- the standard cepstral step, done by
    hand so the node can run it without a DSP library."""
    log_e = np.log(band_energy)
    k = np.arange(len(log_e))
    out = []
    for i in range(n):
        out.append(float(np.sum(log_e * np.cos(math.pi * i * (2 * k + 1) / (2 * len(log_e))))))
    # scale to a sane range for the model
    return [round(v / len(log_e), 4) for v in out]


def centroid(band_energy: np.ndarray) -> float:
    """Energy-weighted mean frequency.

    Preferred over argmax(peak) as a scalar summary: argmax quantises to a
    100 Hz band and goes uniformly random on a near-flat spectrum, so a dead
    hive produces wild values that look like signal. The centroid degrades
    gracefully.
    """
    total = float(band_energy.sum())
    if total <= 0:
        return 0.0
    return float((band_energy * _CENTRES).sum() / total)


def features(colony, activity: float) -> tuple[float, dict]:
    """Returns (sound_rms, feature dict for the telemetry frame)."""
    spec = spectrum(colony, activity)
    total = float(spec.sum())
    rms = round(min(1.0, math.sqrt(total / N_BANDS) * 0.55), 4)
    return rms, {
        "mfcc": mfcc(spec),
        "band_energy": [round(float(v), 5) for v in spec],
        "peak_hz": round(float(_CENTRES[int(np.argmax(spec))]), 1),
        "centroid_hz": round(centroid(spec), 1),
    }
