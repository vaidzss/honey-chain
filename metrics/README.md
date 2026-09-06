# Model results

Generated 2026-09-05T20:45:27Z by `scripts/export_metrics.py`.
Every number is read from the artefact that produced it — nothing here
is retyped, so this cannot drift from the models actually loaded.

---

## Summary

| Model | Version | Headline | Weakest point |
|---|---|---|---|
| Colony health | `health-hgb-0.1.0` | 97.4% accuracy, queenless recall 0.981 | varroa recall 0.510 |
| Yield forecaster | `yield-gbq-0.1.0` | P90 covers 100.0% after calibration | +45% mean headroom — a generous ceiling |
| Anomaly detector | `anomaly-iforest-0.1.0` | robbing AUC 0.997, 1.0% false alarms | varroa AUC 0.619 |
| Varroa counter | `varroa-cv-0.1.0` | 12.5% mean error on synthetic boards | no real-board data to measure against |

---

## Colony health classifier

- **97.4%** accuracy on 121,422 rows from **20 hives in a separate simulation run** (different seed, different weather).
- Split by hive, never by row: consecutive hourly frames from one colony are near-duplicates, and a random row split produces a fake 99% that collapses on a new hive.

| Class | Recall |
|---|---|
| pre_swarm | 0.989 |
| healthy | 0.982 |
| queenless | 0.981 |
| robbing | 0.972 |
| starvation | 0.551 |
| varroa | 0.510 |
| wax_moth | 0.000 *(no test examples)* |

Accuracy is the least useful number here — the set is ~70% healthy, so always predicting "healthy" scores 0.70. Recall per class is what matters, and **varroa is the weak one**: it is a three-week decline that looks like "slightly less healthy". The alerting layer therefore requires 0.88 confidence before varroa may interrupt a beekeeper.

![recall](colony_health_recall.png)

---

## Sensor ablation

| Sensor set | macro F1 | accuracy | queenless | pre_swarm | varroa |
|---|---|---|---|---|---|
| everything | **0.748** | 0.975 | 0.983 | 0.990 | 0.531 |
| no load cell | **0.739** | 0.955 | 0.905 | 0.988 | 0.620 |
| no thermometers | **0.710** | 0.970 | 0.987 | 0.990 | 0.247 |
| microphone only | **0.693** | 0.935 | 0.925 | 0.990 | 0.013 |
| no microphone | **0.483** | 0.929 | 0.979 | 0.365 | 0.223 |
| scale + thermometer only | **0.477** | 0.927 | 0.978 | 0.326 | 0.223 |
| scale only | **0.433** | 0.909 | 0.948 | 0.426 | 0.118 |

Removing the microphone costs 0.265 macro F1 and 0.624 of pre_swarm recall (0.990 -> 0.365) -- swarm prediction is almost entirely acoustic. The best single-sensor configuration is 'microphone only' at 0.693; the worst is 'scale only' at 0.433. Permutation importance ranked mass features on top and was misleading, because the 33 acoustic features are correlated and permuting one at a time barely moves the score. Ablation by sensor GROUP is the honest measurement.

![ablation](sensor_ablation.png)

---

## Yield forecaster

- P90 coverage **100.0%** after conformal calibration (×1.0332), from 91.5% before.
- Trained on 397 hive-windows, tested on 59.

The P90 becomes the **on-chain mint ceiling**, so this is the one model whose error costs a real person money. The two errors are not symmetric: a ceiling that is too low blocks an honest beekeeper from selling their own honey and ends adoption, while one that is too high lets some fraud through to the chemical testing ladder. The ceiling is deliberately generous.

The first fit covered only 74.6% of actual yields — a quarter of honest beekeepers would have been blocked. Conformal calibration on hives the model had never seen fixed it, but only after training across four independent weather realisations: the first calibration attempt returned a factor of exactly 1.000 and did nothing, because the calibration hives shared a weather seed with the fit hives while the test run did not. Conformal guarantees require exchangeability, and a weather shift breaks it.

---

## Anomaly detector

Fitted on healthy windows only, so it answers *"unlike anything normal"* rather than naming a known fault — which is what catches faults the classifier has no class for.

| Fault | AUC |
|---|---|
| robbing | 0.997 — strong |
| pre_swarm | 0.951 — strong |
| queenless | 0.913 — strong |
| starvation | 0.784 — useful |
| varroa | 0.619 — weak |

False alarm rate 1.0% at a threshold set from the *healthy* distribution — not tuned against known faults, because in the field there is no label to tune against and a threshold fitted to known faults would not survive an unknown one.

![auc](anomaly_auc.png)

---

## Varroa counter

- Method: classical CV: adaptive threshold + size/shape/solidity filters
- Mean relative error: **12.5%** against synthetic boards with known counts

| Case | True | Found | Error |
|---|---|---|---|
| clean board, no mites | 0 | 0 | +0 |
| light infestation | 8 | 6 | -2 |
| moderate | 25 | 23 | -2 |
| heavy | 60 | 50 | -10 |
| heavy + lots of debris | 60 | 54 | -6 |
| low resolution photo | 25 | 30 | +5 |
| high resolution photo | 25 | 23 | -2 |
| mildly blurred | — | refused | The photo is out of focus. Hold the phone steady, ab |
| very blurred | — | refused | The photo is out of focus. Hold the phone steady, ab |

Measured against SYNTHETIC boards with known counts. This validates the algorithm's mechanics and serves as a regression test; it is NOT field accuracy. There is no annotated Indian sticky-board dataset to measure that against yet.

---

## Ledger verification

- 16 batches, 25 invariant checks **all held**, 0 broken.
- Verified against chain state alone. The database supplied only the list of batch codes; every value was read from the deployed contracts.

The invariants checked are: issuance bounded by the telemetry-derived ceiling, mass conserved through every transformation, jars issued bounded by the honey that exists, and no seals without a passing independent lab report.

Reproduce with `python scripts/verify_ledger.py`.

---

## Caveat

Every model number here was measured on SIMULATED hives. It shows the models learned the physics the simulator encodes; it is not evidence they work on real bees. Most public bee acoustics are Apis mellifera, and Apis cerana indica -- common in Indian beekeeping -- is a further gap with no public dataset.
