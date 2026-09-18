"""Reference data for T3 - long-term stability and drift.

Writes one run into `experiments/T3-long-term-stability/data/`:

  log-24h   24 h of logging at one sample every few seconds (params/t3.yaml,
            `log.period_s`), one channel, a temperature sensor wired straight
            to the ADC. The point of the experiment is not the sampling rate
            -- it is what happens when you average this record for longer and
            longer, so the file stays small (tens of thousand rows) and the
            interesting structure is in the noise, not in the count.

The synthetic temperature is the sum of four things, added in physical
(kelvin) units before the sensor and the ADC ever see them:

  - a mean level and a slow linear drift -- the instrument, which is what the
    experiment exists to characterize;
  - a diurnal sinusoid at exactly 24 h -- the room, which the insulated box
    attenuates but does not remove, and which is deliberately the largest
    feature in the record;
  - a random walk (Wiener process): the cumulative sum of independent
    Gaussian increments, `rw[i] = rw[i-1] + N(0, random_walk_step_K)`. This is
    the textbook generator of "random walk frequency noise" in Allan-variance
    terms, and it is what makes the overlapping Allan deviation of the
    detrended record rise again at long averaging times -- without it the
    curve would fall monotonically and there would be nothing to find a
    minimum of.
  - the ADC's own input-referred white noise (`platform.yaml`, `adc.
    noise_sigma_v`), referred back through the sensor's V/K sensitivity. No
    separate white-noise term is injected in temperature units: the ADC model
    already adds one, and reusing it is what "S1's effective quantization"
    means when it reappears in T3's uncertainty budget.

Run it with `python tools/synth/generate.py t3`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

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

OUT = Path(__file__).resolve().parents[2] / "experiments" / "T3-long-term-stability" / "data"

#: Fixed integer seed offset for the single run. A second run added later
#: (e.g. a shorter comparison log) gets its own fixed offset rather than one
#: derived from its name -- `hash()` on a str is not stable across Python
#: runs, so only literal integers keep the generator reproducible, as in
#: s1_daq_static.py.
LOG_SEED_OFFSET = 401


@dataclass
class T3Channel(Channel):
    """A `Channel` that also carries the sensor's own linear transduction.

    `conditioning.gain`/`offset` describe a board-level stage between a
    sensor's native output and the ADC pin; here the sensor drives the pin
    directly (gain = 1, offset = 0 there), and its own V = a + b*T_celsius
    transduction -- what an analysis needs to get back to kelvin -- has no
    field in the shared schema, so it goes into `extra`, exactly as S3Channel
    adds a generator frequency.
    """

    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        entry = super().as_dict()
        entry.update(self.extra)
        return entry


def _adc(platform: Dict[str, Any]) -> AdcModel:
    return AdcModel(
        bits=platform["board"]["adc_bits"],
        vref_v=platform["board"]["vref_mV"] / 1000.0,
        **platform["adc"],
    )


def generate() -> None:
    platform = load_params("platform")
    params = load_params("t3")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    host = hosts["linux"]  # T3 requires a Linux host that stays on

    log = params["log"]
    period_s = float(log["period_s"])
    period_us = period_s * 1.0e6
    n = int(round(log["duration_h"] * 3600.0 / period_s))
    pin = int(log["pin"])

    sensor = params["sensor"]
    b_v_per_k = float(sensor["sensitivity_V_per_K"])
    t_ref_c = float(sensor["reference_T_C"])
    v_mid = float(sensor["midscale_V"])
    a_v = v_mid - b_v_per_k * t_ref_c  # V = a_v + b_v_per_k * T_celsius

    sig = params["signal"]
    seed = params["seed"]
    rng = np.random.default_rng(seed + LOG_SEED_OFFSET)

    timing = TimingModel(
        period_us=period_us,
        loop_period_us=platform["loop"]["period_1ch_us"],
        jitter_sd_us=host.jitter_sd_us,
        restart_probability=host.restart_probability,
        restart_extra_us=host.restart_extra_us,
    )
    t_us = timing.grid(n, rng)
    t_s = t_us / 1.0e6

    diurnal = sig["diurnal_amplitude_K"] * np.sin(
        2.0 * np.pi * t_s / sig["diurnal_period_s"] + sig["diurnal_phase_rad"]
    )
    drift = (sig["drift_K_per_h"] / 3600.0) * t_s

    # Random walk: cumulative sum of independent Gaussian increments (a
    # discrete Wiener process), started at zero. See the module docstring.
    rw = np.cumsum(rng.normal(0.0, sig["random_walk_step_K"], size=n))

    white_extra = 0.0
    if sig.get("white_sigma_K", 0.0):
        white_extra = rng.normal(0.0, sig["white_sigma_K"], size=n)

    true_temp_c = sig["mean_T_C"] + diurnal + drift + rw + white_extra
    volts = a_v + b_v_per_k * true_temp_c
    codes = adc.convert(volts, rng)
    data = long_frame(t_us, {pin: codes})

    channel = T3Channel(
        pin=pin, quantity="temperature", unit="degC",
        sensor="linear analog temperature sensor, direct to pin",
        gain=1.0, offset=0.0,
        calibration_reference="see T1 (offset/gain against a reference thermometer)",
        extra={
            "sensor_offset_V": float(a_v),
            "sensor_sensitivity_V_per_K": float(b_v_per_k),
        },
    )
    meta = build_meta(
        experiment="T3", run="log-24h", board=board, host=host, pins=[pin],
        requested_period_us=int(round(period_us)), t_us=t_us,
        channels=[channel], rng=rng,
        references=[],
        notes=(f"{log['duration_h']:g} h continuous log at {period_s:g} s "
              "intervals, insulated box, Linux host that stayed on"),
    )
    print("T3 log-24h:")
    emit(OUT / "log-24h.csv", data, meta)


if __name__ == "__main__":
    generate()
