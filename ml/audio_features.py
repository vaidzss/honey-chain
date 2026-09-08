"""Acoustic feature contract, shared by training and inference.

Imported by `ml/train_acoustic.py` and by the API when it scores a real clip.
Both sides must compute features identically or the model degrades in
production with nothing failing loudly -- the same reason `ml/features.py`
exists for telemetry.

## Why these features

A queenright colony hums; a queenless one changes that hum in ways beekeepers
have described for centuries and instruments can measure:

- **Low-band energy around 100-300 Hz** carries the worker hum. Queen loss
  shifts energy upward and broadens it.
- **Spectral centroid and bandwidth** capture that shift directly, and are
  robust to overall gain -- which matters enormously here, because these
  recordings come from a citizen-science project with wildly different
  microphones and placements.
- **MFCCs** summarise timbre compactly. We keep 20, plus their spread over the
  window, because a colony's sound is not stationary and the *variability* is
  informative.
- **Everything is normalised to a shape rather than a level.** Band energies are
  divided by total energy. An absolute loudness feature would mostly encode how
  close someone taped their phone to the hive.

Sample rate is fixed at 16 kHz: bee acoustics of interest sit below ~2 kHz, so
anything higher is bandwidth we pay for and do not use.
"""
from __future__ import annotations

import numpy as np

SR = 16_000
WINDOW_S = 3.0          # long enough for a stable spectrum, short enough to give
                        # many independent samples per recording
N_MFCC = 20
N_FFT = 1024
HOP = 256

# Bands chosen around the documented worker-hum fundamental and its harmonics.
BANDS_HZ = [(0, 100), (100, 200), (200, 300), (300, 400), (400, 600),
            (600, 900), (900, 1400), (1400, 2200), (2200, 4000)]

FEATURE_COLUMNS: list[str] = (
    [f"mfcc{i}_mean" for i in range(N_MFCC)]
    + [f"mfcc{i}_std" for i in range(N_MFCC)]
    + [f"band{i}_frac" for i in range(len(BANDS_HZ))]
    + ["centroid_mean", "centroid_std",
       "bandwidth_mean", "bandwidth_std",
       "rolloff_mean", "rolloff_std",
       "flatness_mean", "flatness_std",
       "zcr_mean", "zcr_std",
       "rms_rel_std",          # loudness *variability*, not loudness
       "low_high_ratio",       # <300 Hz against 300-2200 Hz
       "peak_freq_hz", "harmonic_ratio"]
)


def clip_features(y: np.ndarray, sr: int = SR) -> np.ndarray:
    """Features for one window of mono audio. Returns FEATURE_COLUMNS order."""
    import librosa

    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    if y.size < N_FFT:
        y = np.pad(y, (0, N_FFT - y.size))

    # guard against a dead or clipped channel producing NaNs downstream
    peak = float(np.max(np.abs(y))) or 1.0
    y = y / peak

    S = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=N_FFT)
    power = S ** 2
    total = power.sum() + 1e-12

    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(
        librosa.feature.melspectrogram(S=power, sr=SR)), n_mfcc=N_MFCC)

    cent = librosa.feature.spectral_centroid(S=S, sr=SR)[0]
    bw = librosa.feature.spectral_bandwidth(S=S, sr=SR)[0]
    roll = librosa.feature.spectral_rolloff(S=S, sr=SR)[0]
    flat = librosa.feature.spectral_flatness(S=S)[0]
    zcr = librosa.feature.zero_crossing_rate(y, hop_length=HOP)[0]
    # frame_length must agree with the STFT we already computed, or librosa
    # rejects the spectrogram it is handed
    rms = librosa.feature.rms(S=S, frame_length=N_FFT)[0]

    band_frac = [power[(freqs >= lo) & (freqs < hi)].sum() / total
                 for lo, hi in BANDS_HZ]

    low = power[(freqs >= 20) & (freqs < 300)].sum()
    high = power[(freqs >= 300) & (freqs < 2200)].sum()

    spec_mean = power.mean(axis=1)
    peak_freq = float(freqs[int(np.argmax(spec_mean))])
    # energy at 2x the dominant peak, relative to the peak: a coherent colony
    # hum is more harmonic than the broadband roar of a disturbed one
    hi_idx = np.argmin(np.abs(freqs - min(peak_freq * 2, freqs[-1])))
    harmonic = float(spec_mean[hi_idx] / (spec_mean.max() + 1e-12))

    feats = (
        list(mfcc.mean(axis=1)) + list(mfcc.std(axis=1)) + band_frac
        + [cent.mean(), cent.std(), bw.mean(), bw.std(),
           roll.mean(), roll.std(), flat.mean(), flat.std(),
           zcr.mean(), zcr.std(),
           float(rms.std() / (rms.mean() + 1e-12)),
           float(low / (high + 1e-12)),
           peak_freq, harmonic]
    )
    out = np.asarray(feats, dtype=np.float32)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def windows(y: np.ndarray, sr: int = SR, window_s: float = WINDOW_S):
    """Yield non-overlapping windows. Non-overlapping on purpose: overlapping
    windows are near-duplicates and would inflate any score computed over them."""
    n = int(window_s * sr)
    for start in range(0, len(y) - n + 1, n):
        yield y[start:start + n]
