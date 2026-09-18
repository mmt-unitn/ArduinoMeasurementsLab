"""Reference data for M3 - vibration and modal analysis.

Writes two runs into `experiments/M3-vibration/data/`:

  tap-noaccel   Strain gauge only (pin_strain), no accelerometer mounted.
                The beam's free response after a tap, at its true (unloaded)
                modal frequencies -- the baseline for the Euler-Bernoulli
                ratio check and for the two damping estimates.
  tap-accel     Strain gauge and accelerometer together. The generator gives
                each channel its own frequency input: the strain channel
                keeps the true frequencies (it adds no mass, so it is
                unaffected by the accelerometer's presence -- see
                hardware/beam-rig.qmd's note on tip mass), while the
                accelerometer channel's first mode is generated at the
                mass-loaded frequency f1' = f1 * mass_loading.f1_ratio_loaded
                (params/m3.yaml). A real beam would show the shift on every
                channel, because mass loading is a property of the whole
                structure, not of one sensor; giving the two channels
                different frequency inputs in the same synthetic file is a
                deliberate simplification that lets a single recording
                demonstrate the comparison directly, and it is stated here so
                it is not mistaken for a measurement.

Each channel is the sum of three modes released from rest at t = 0 (a tap):
a decaying sine per mode, x(t) = sum_k A_k * exp(-zeta_k * 2 pi f_k * t) *
sin(2 pi * f_dk * t), with f_dk = f_k * sqrt(1 - zeta_k^2) the damped
frequency -- a pure sine and no cosine term, because an impulsive release
from x(0) = 0 has no term in phase with the mode shape at t = 0. Frequencies
follow the exact Euler-Bernoulli ratios (lambda_n / lambda_1)^2; damping is
mode-dependent; amplitudes are independent per channel and per mode, because
they depend on mode shapes this rig's undecided dimensions do not fix.

Run it with `python tools/synth/generate.py m3`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from common import (
    AdcModel,
    Channel,
    TimingModel,
    build_meta,
    emit,
    host_profiles,
    load_params,
    long_frame,
)

OUT = Path(__file__).resolve().parents[2] / "experiments" / "M3-vibration" / "data"

#: Fixed integer seed offsets, one per run -- literal integers, not derived
#: from the run name, so the generator stays reproducible (s1_daq_static.py).
SEED_OFFSET = {"tap-noaccel": 501, "tap-accel": 502}


def _adc(platform: Dict[str, Any]) -> AdcModel:
    return AdcModel(
        bits=platform["board"]["adc_bits"],
        vref_v=platform["board"]["vref_mV"] / 1000.0,
        **platform["adc"],
    )


def _mode_frequencies(params: Dict[str, Any]) -> List[float]:
    ev = params["eigenvalues"]
    l1, l2, l3 = ev["lambda1"], ev["lambda2"], ev["lambda3"]
    f1 = params["modal"]["f1_hz"]
    return [f1, f1 * (l2 / l1) ** 2, f1 * (l3 / l1) ** 2]


def _decay_sum(t_s: np.ndarray, freqs_hz: List[float], zetas: List[float],
               amplitudes: List[float]) -> np.ndarray:
    """Sum of decaying sines released from rest at t = 0 -- see module docstring."""
    x = np.zeros_like(t_s)
    for f, zeta, amp in zip(freqs_hz, zetas, amplitudes):
        omega = 2.0 * np.pi * f
        omega_d = omega * np.sqrt(max(1.0 - zeta ** 2, 0.0))
        x = x + amp * np.exp(-zeta * omega * t_s) * np.sin(omega_d * t_s)
    return x


def generate() -> None:
    platform = load_params("platform")
    params = load_params("m3")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    host = hosts["linux"]  # M3 requires a Linux host (streaming board)

    acq = params["acquisition"]
    fs_hz = float(acq["fs_hz"])
    period_us = 1.0e6 / fs_hz
    n = int(round(acq["duration_s"] * fs_hz))
    pin_strain = int(acq["pin_strain"])
    pin_accel = int(acq["pin_accel"])

    modal = params["modal"]
    zetas = list(modal["zeta"])
    freqs_true = _mode_frequencies(params)
    strain_amp = list(modal["strain_amplitude"])
    accel_amp = list(modal["accel_amplitude_ms2"])

    loaded_ratio = params["mass_loading"]["f1_ratio_loaded"]
    freqs_loaded = [freqs_true[0] * loaded_ratio, freqs_true[1], freqs_true[2]]

    sens = params["sensors"]
    strain_sens = float(sens["strain_sensitivity_V_per_strain"])
    accel_sens = float(sens["accel_sensitivity_V_per_ms2"])
    offset_v = float(sens["offset_V"])
    seed = params["seed"]

    # --- tap-noaccel: strain gauge only, true frequencies -------------------
    run = "tap-noaccel"
    rng = np.random.default_rng(seed + SEED_OFFSET[run])
    timing = TimingModel(
        period_us=period_us, loop_period_us=platform["loop"]["period_1ch_us"],
        jitter_sd_us=host.jitter_sd_us, restart_probability=host.restart_probability,
        restart_extra_us=host.restart_extra_us,
    )
    t_us = timing.grid(n, rng)
    t_s = t_us / 1.0e6
    strain = _decay_sum(t_s, freqs_true, zetas, strain_amp)
    v_strain = offset_v + strain_sens * strain
    codes = adc.convert(v_strain, rng)
    data = long_frame(t_us, {pin_strain: codes})

    channel = Channel(
        pin=pin_strain, quantity="strain", unit="1",
        sensor="foil strain gauge bridge, root of the beam",
        gain=1.0, offset=0.0,
        calibration_reference="see M1 (bridge sensitivity against reference masses)",
    )
    meta = build_meta(
        experiment="M3", run=run, board=board, host=host, pins=[pin_strain],
        requested_period_us=int(round(period_us)), t_us=t_us,
        channels=[channel], rng=rng, references=[],
        notes="tap test, no accelerometer mounted -- true (unloaded) modal frequencies",
    )
    print(f"M3 {run}:")
    emit(OUT / f"{run}.csv", data, meta)

    # --- tap-accel: strain (true) + accelerometer (f1 mass-loaded) ----------
    run = "tap-accel"
    rng = np.random.default_rng(seed + SEED_OFFSET[run])
    timing = TimingModel(
        period_us=period_us, loop_period_us=platform["loop"]["period_2ch_us"],
        jitter_sd_us=host.jitter_sd_us, restart_probability=host.restart_probability,
        restart_extra_us=host.restart_extra_us,
    )
    t_us = timing.grid(n, rng)
    t_s = t_us / 1.0e6
    strain = _decay_sum(t_s, freqs_true, zetas, strain_amp)
    accel = _decay_sum(t_s, freqs_loaded, zetas, accel_amp)
    v_strain = offset_v + strain_sens * strain
    v_accel = offset_v + accel_sens * accel
    codes_strain = adc.convert(v_strain, rng)
    codes_accel = adc.convert(v_accel, rng)
    data = long_frame(t_us, {pin_strain: codes_strain, pin_accel: codes_accel})

    channels = [
        Channel(
            pin=pin_strain, quantity="strain", unit="1",
            sensor="foil strain gauge bridge, root of the beam",
            gain=1.0, offset=0.0,
            calibration_reference="see M1 (bridge sensitivity against reference masses)",
        ),
        Channel(
            pin=pin_accel, quantity="acceleration", unit="m/s^2",
            sensor="accelerometer, tip mounted",
            gain=1.0, offset=0.0,
            calibration_reference="accelerometer datasheet sensitivity",
        ),
    ]
    meta = build_meta(
        experiment="M3", run=run, board=board, host=host, pins=[pin_strain, pin_accel],
        requested_period_us=int(round(period_us)), t_us=t_us,
        channels=channels, rng=rng, references=[],
        notes=("tap test, accelerometer mounted at the tip -- its channel's first "
              "mode is mass-loaded, the strain channel's is not (see module "
              "docstring of m3_vibration.py)"),
    )
    print(f"M3 {run}:")
    emit(OUT / f"{run}.csv", data, meta)


if __name__ == "__main__":
    generate()
