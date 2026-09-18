"""Reference data for T2 - dynamic response of temperature sensors.

Writes six runs into `experiments/T2-temperature-dynamic/data/`:

  step-still-air, step-forced-air, step-still-water, step-stirred-water
      The bare NTC divider (same "true" sensor as T1's), plunged from the
      same room temperature into the same bath temperature, one medium per
      run, first-order response, tau the only thing that differs.
  step-sheathed
      The Pt1000 bridge (same "true" sensor as T1's), packaged in a sheath:
      two cascaded first-order stages, giving a genuine second-order step
      response with an inflection at t = 0 rather than a corner.
  ramp-stirred-water
      The bare NTC again, this time tracking a linearly ramping bath
      temperature in stirred water, to show the steady-state lag e_inf = tau
      * ramp_rate.

Every trajectory is the analytic solution of the stated ODE (a clean
instantaneous step, or a clean ramp -- @sec-t2-transition explains why the
generator does not blur the step itself), run through the same sensor
front-end and ADC/timing models as T1 and S1, so the noise a student fits
against is the same kind of noise as everywhere else on this site.

Run it with `python tools/synth/generate.py t2`.
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

# The T1 generator carries the resistance-temperature models and the front-end
# circuit equations (divider, bridge) that T2 reuses unchanged -- same sensors,
# same rig.
from t1_temperature_static import (
    bridge_voltage,
    divider_voltage,
    ntc_resistance,
    pt1000_resistance,
)

OUT = Path(__file__).resolve().parents[2] / "experiments" / "T2-temperature-dynamic" / "data"

#: Fixed integer seed offsets, one per run (see t1_temperature_static.py and
#: s3_aliasing_filtering.py for the same convention).
PLUNGE_SEED = {
    "still-air": 100, "forced-air": 101, "still-water": 102,
    "stirred-water": 103,
}
SHEATHED_SEED_OFFSET = 200
RAMP_SEED_OFFSET = 300


def _adc(platform: Dict[str, Any]) -> AdcModel:
    return AdcModel(
        bits=platform["board"]["adc_bits"],
        vref_v=platform["board"]["vref_mV"] / 1000.0,
        **platform["adc"],
    )


@dataclass
class T2Channel(Channel):
    """A `Channel` carrying T2-specific extra sidecar keys.

    `target_temperature_c` and `initial_temperature_c` are genuinely known at
    the bench (the bath setpoint, the room before the plunge); tau is not --
    it is what the experiment measures -- so no fitted or "true" time constant
    is written here, unlike S3's `generator_frequency_hz`.
    """

    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        entry = super().as_dict()
        entry.update(self.extra)
        return entry


# --- the "true" trajectories --------------------------------------------------


def first_order_step(t_s, t0_c: float, t_inf_c: float, tau_s: float,
                     plunge_time_s: float):
    """T(t) = T0 for t < t_plunge, T_inf + (T0 - T_inf) exp(-(t-t_plunge)/tau)
    afterwards -- @eq-t2-step in the handbook."""
    t_s = np.asarray(t_s, dtype=np.float64)
    elapsed = np.clip(t_s - plunge_time_s, 0.0, None)
    after = t_inf_c + (t0_c - t_inf_c) * np.exp(-elapsed / tau_s)
    return np.where(t_s < plunge_time_s, t0_c, after)


def second_order_step(t_s, t0_c: float, t_inf_c: float, tau1_s: float,
                      tau2_s: float, plunge_time_s: float):
    """Two cascaded first-order stages (sheath, then element) -- @eq-t2-second
    in the handbook. Zero slope at the plunge instant is the signature this
    experiment asks students to recognise."""
    t_s = np.asarray(t_s, dtype=np.float64)
    elapsed = np.clip(t_s - plunge_time_s, 0.0, None)
    bracket = (tau1_s / (tau1_s - tau2_s) * np.exp(-elapsed / tau1_s)
              - tau2_s / (tau1_s - tau2_s) * np.exp(-elapsed / tau2_s))
    after = t_inf_c + (t0_c - t_inf_c) * bracket
    return np.where(t_s < plunge_time_s, t0_c, after)


def ramp_response(t_s, t0_c: float, rate_c_per_s: float, tau_s: float,
                  ramp_start_s: float):
    """First-order response to a ramp starting at `ramp_start_s`, sensor at
    rest at T0 beforehand -- @eq-t2-ramp-response in the handbook. Approaches
    the bath ramp with a constant steady-state lag tau * rate."""
    t_s = np.asarray(t_s, dtype=np.float64)
    elapsed = np.clip(t_s - ramp_start_s, 0.0, None)
    after = (t0_c + rate_c_per_s * elapsed
            - rate_c_per_s * tau_s * (1.0 - np.exp(-elapsed / tau_s)))
    return np.where(t_s < ramp_start_s, t0_c, after)


# --- run emitters --------------------------------------------------------------


def _ntc_channel(ntc: Dict[str, Any], extra: Dict[str, Any]) -> T2Channel:
    return T2Channel(
        pin=ntc["pin"], quantity="temperature", unit="degC",
        sensor="NTC thermistor, resistive divider",
        gain=1.0, offset=0.0, filter="none", extra=extra,
    )


def _pt_channel(pt1000: Dict[str, Any], extra: Dict[str, Any]) -> T2Channel:
    return T2Channel(
        pin=pt1000["pin"], quantity="temperature", unit="degC",
        sensor="Pt1000, bridge + instrumentation amplifier, sheathed",
        gain=pt1000["bridge"]["inamp_gain"],
        offset=pt1000["bridge"]["bias_offset_v"], filter="none", extra=extra,
    )


def generate() -> None:
    platform = load_params("platform")
    t2 = load_params("t2")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    host = hosts["linux"]
    references = [dict(t2["references"]["thermometer"])]

    ntc = t2["ntc"]
    pt1000 = t2["pt1000"]

    # --- the four bare-NTC plunges, one medium each --------------------------
    plunge = t2["plunge"]
    t0_c = plunge["initial_temperature_c"]
    t_inf_c = plunge["target_temperature_c"]
    for medium in plunge["media"]:
        name = medium["name"]
        rng = np.random.default_rng(t2["seed"] + PLUNGE_SEED[name])
        timing = TimingModel(
            period_us=medium["period_us"],
            loop_period_us=platform["loop"]["period_1ch_us"],
            jitter_sd_us=host.jitter_sd_us,
            restart_probability=host.restart_probability,
            restart_extra_us=host.restart_extra_us,
        )
        n = int(round(medium["duration_s"] * 1.0e6 / medium["period_us"]))
        t_us = timing.grid(n, rng)
        # Relative to the FIRST sample, not to the device clock: the
        # timing model starts t_us at the board's uptime, while every
        # declared plunge_time_s / ramp_start_time_s in the sidecar is an
        # offset into the record. Using the absolute axis would shift
        # every trajectory by that uptime.
        t_s = (t_us - t_us[0]) / 1.0e6
        t_true = first_order_step(t_s, t0_c, t_inf_c, medium["tau_s"],
                                  medium["baseline_s"])

        r_ntc = ntc_resistance(t_true, ntc["r25_ohm"], ntc["beta_k"])
        v_ntc = divider_voltage(r_ntc, ntc["divider"]["r_fixed_ohm"],
                                ntc["divider"]["vcc_v"])
        codes = adc.convert(v_ntc, rng)
        data = long_frame(t_us, {ntc["pin"]: codes})

        channel = _ntc_channel(ntc, extra={
            "medium": name,
            "initial_temperature_c": t0_c,
            "target_temperature_c": t_inf_c,
            "plunge_time_s": medium["baseline_s"],
            "excitation_v": ntc["divider"]["vcc_v"],
            "divider_r_fixed_ohm": ntc["divider"]["r_fixed_ohm"],
        })
        run = f"step-{name}"
        meta = build_meta(
            experiment="T2", run=run, board=board, host=host, pins=[ntc["pin"]],
            requested_period_us=medium["period_us"], t_us=t_us,
            channels=[channel], rng=rng, references=references,
            notes=f"plunge test, medium = {name}, {t0_c:g} -> {t_inf_c:g} degC",
        )
        print(f"T2 {run}:")
        emit(OUT / f"{run}.csv", data, meta)

    # --- the sheathed Pt1000: second-order plunge -----------------------------
    sh = t2["sheathed"]
    rng = np.random.default_rng(t2["seed"] + SHEATHED_SEED_OFFSET)
    timing = TimingModel(
        period_us=sh["period_us"],
        loop_period_us=platform["loop"]["period_1ch_us"],
        jitter_sd_us=host.jitter_sd_us,
        restart_probability=host.restart_probability,
        restart_extra_us=host.restart_extra_us,
    )
    n = int(round(sh["duration_s"] * 1.0e6 / sh["period_us"]))
    t_us = timing.grid(n, rng)
    # Relative to the first sample; see the note in the plunge emitter.
    t_s = (t_us - t_us[0]) / 1.0e6
    t_true = second_order_step(t_s, sh["initial_temperature_c"],
                               sh["target_temperature_c"], sh["tau_sheath_s"],
                               sh["tau_element_s"], sh["baseline_s"])

    r_pt = pt1000_resistance(t_true, pt1000["r0_ohm"], pt1000["a_iec"],
                             pt1000["b_iec"])
    v_pt = bridge_voltage(r_pt, pt1000["bridge"]["r_bridge_ohm"],
                          pt1000["bridge"]["v_exc_v"],
                          pt1000["bridge"]["inamp_gain"],
                          pt1000["bridge"]["bias_offset_v"])
    codes = adc.convert(v_pt, rng)
    data = long_frame(t_us, {pt1000["pin"]: codes})

    channel = _pt_channel(pt1000, extra={
        "medium": "stirred-water",
        "initial_temperature_c": sh["initial_temperature_c"],
        "target_temperature_c": sh["target_temperature_c"],
        "plunge_time_s": sh["baseline_s"],
        "excitation_v": pt1000["bridge"]["v_exc_v"],
        "bridge_r_ohm": pt1000["bridge"]["r_bridge_ohm"],
    })
    meta = build_meta(
        experiment="T2", run="step-sheathed", board=board, host=host,
        pins=[pt1000["pin"]], requested_period_us=sh["period_us"], t_us=t_us,
        channels=[channel], rng=rng, references=references,
        notes=(f"sheathed second-order plunge, stirred water, "
              f"{sh['initial_temperature_c']:g} -> {sh['target_temperature_c']:g} degC"),
    )
    print("T2 step-sheathed:")
    emit(OUT / "step-sheathed.csv", data, meta)

    # --- the ramp test: bare NTC, stirred water -------------------------------
    ramp = t2["ramp"]
    rng = np.random.default_rng(t2["seed"] + RAMP_SEED_OFFSET)
    timing = TimingModel(
        period_us=ramp["period_us"],
        loop_period_us=platform["loop"]["period_1ch_us"],
        jitter_sd_us=host.jitter_sd_us,
        restart_probability=host.restart_probability,
        restart_extra_us=host.restart_extra_us,
    )
    total_s = ramp["baseline_s"] + ramp["ramp_duration_s"]
    n = int(round(total_s * 1.0e6 / ramp["period_us"]))
    t_us = timing.grid(n, rng)
    # Relative to the first sample; see the note in the plunge emitter.
    t_s = (t_us - t_us[0]) / 1.0e6
    t_true = ramp_response(t_s, ramp["initial_temperature_c"],
                           ramp["ramp_rate_c_per_s"], ramp["tau_s"],
                           ramp["baseline_s"])

    r_ntc = ntc_resistance(t_true, ntc["r25_ohm"], ntc["beta_k"])
    v_ntc = divider_voltage(r_ntc, ntc["divider"]["r_fixed_ohm"],
                            ntc["divider"]["vcc_v"])
    codes = adc.convert(v_ntc, rng)
    data = long_frame(t_us, {ntc["pin"]: codes})

    channel = _ntc_channel(ntc, extra={
        "medium": "stirred-water",
        "initial_temperature_c": ramp["initial_temperature_c"],
        "ramp_rate_c_per_s": ramp["ramp_rate_c_per_s"],
        "ramp_start_time_s": ramp["baseline_s"],
        "excitation_v": ntc["divider"]["vcc_v"],
        "divider_r_fixed_ohm": ntc["divider"]["r_fixed_ohm"],
    })
    meta = build_meta(
        experiment="T2", run="ramp-stirred-water", board=board, host=host,
        pins=[ntc["pin"]], requested_period_us=ramp["period_us"], t_us=t_us,
        channels=[channel], rng=rng, references=references,
        notes=(f"ramp test, stirred water, rate = "
              f"{ramp['ramp_rate_c_per_s']:g} degC/s"),
    )
    print("T2 ramp-stirred-water:")
    emit(OUT / "ramp-stirred-water.csv", data, meta)


if __name__ == "__main__":
    generate()
