"""Reference data for T1 - static calibration of temperature sensors.

Writes seven runs into `experiments/T1-temperature-static/data/`:

  cal-ice, cal-t20, cal-t35, cal-t50, cal-t65, cal-t80
      200 records each at 1 kHz, both sensors on separate pins in the same
      run, at a fixed point and five stirred-bath points. Low excitation on
      both sensors, chosen to keep self-heating negligible in the curve that
      is fitted from these six runs.
  selfheat-high
      The "t50" bath point again, both sensors driven at their high
      excitation instead. Comparing this run against cal-t50 through the
      fitted calibration curve is how the dissipation constants are measured.

Both sensors are modelled from a "true" resistance-temperature law -- the
beta equation for the NTC, Callendar-Van Dusen for the Pt1000 -- through their
own analog front end (a divider, a bridge plus instrumentation amplifier) and
then the S1 ADC and timing models, exactly as S1's and S3's own runs are.

Run it with `python tools/synth/generate.py t1`.
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

OUT = Path(__file__).resolve().parents[2] / "experiments" / "T1-temperature-static" / "data"

#: Fixed integer seed offsets, one per run, so that adding a run later never
#: changes the bytes of the ones before it (see s3_aliasing_filtering.py for
#: the same convention and why `hash()` on a str is unsuitable here).
CAL_SEED_BASE = 100          # + point index (0..5)
SELFHEAT_SEED_OFFSET = 900

T_K0 = 273.15


@dataclass
class T1Channel(Channel):
    """A `Channel` carrying T1-specific extra sidecar keys.

    Same pattern as S3's `S3Channel`: the shared schema has no field for an
    excitation voltage or a bridge resistance, so these live in an `extra`
    mapping merged into the channel's dict, rather than widening
    `common.Channel` for one experiment.
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


# --- the "true" sensor physics ------------------------------------------------


def ntc_resistance(t_c, r25_ohm: float, beta_k: float):
    """The beta equation: a physically motivated special case of
    Steinhart-Hart (C = 0) used here as the generator's ground truth, so that
    fitting the full three-parameter model in the analysis is a genuine fit,
    not a tautology.
    """
    t_k = np.asarray(t_c, dtype=np.float64) + T_K0
    t25_k = 25.0 + T_K0
    return r25_ohm * np.exp(beta_k * (1.0 / t_k - 1.0 / t25_k))


def pt1000_resistance(t_c, r0_ohm: float, a_iec: float, b_iec: float):
    """Callendar-Van Dusen, positive branch (t >= 0 degC), IEC 60751 form."""
    t_c = np.asarray(t_c, dtype=np.float64)
    return r0_ohm * (1.0 + a_iec * t_c + b_iec * t_c ** 2)


def divider_voltage(r_sense_ohm, r_fixed_ohm: float, vcc_v: float):
    return vcc_v * r_fixed_ohm / (r_sense_ohm + r_fixed_ohm)


def divider_current(r_sense_ohm, r_fixed_ohm: float, vcc_v: float):
    return vcc_v / (r_sense_ohm + r_fixed_ohm)


def bridge_voltage(r_pt_ohm, r_bridge_ohm: float, v_exc_v: float, gain: float,
                   offset_v: float):
    deflection = r_pt_ohm / (r_pt_ohm + r_bridge_ohm) - 0.5
    return gain * v_exc_v * deflection + offset_v


def bridge_pt_current(r_pt_ohm, r_bridge_ohm: float, v_exc_v: float):
    return v_exc_v / (r_pt_ohm + r_bridge_ohm)


def ntc_selfheated_resistance(t_bath_c: float, r_fixed_ohm: float, vcc_v: float,
                              r25_ohm: float, beta_k: float,
                              dissipation_mw_per_k: float, iterations: int = 4):
    """NTC resistance including its own self-heating above the bath.

    Self-heating raises the element above the bath by P/delta
    (thermal-rig.qmd, @eq-therm-selfheat); P depends on the resistance, which
    depends on the (self-heated) temperature, so this iterates a few times
    rather than solving in closed form -- the correction is small enough that
    it converges well inside four steps.
    """
    t_eff_c = float(t_bath_c)
    r = float(ntc_resistance(t_eff_c, r25_ohm, beta_k))
    dt_sh = 0.0
    for _ in range(iterations):
        i_a = divider_current(r, r_fixed_ohm, vcc_v)
        p_w = i_a ** 2 * r
        dt_sh = (p_w * 1000.0) / dissipation_mw_per_k
        t_eff_c = t_bath_c + dt_sh
        r = float(ntc_resistance(t_eff_c, r25_ohm, beta_k))
    return r, dt_sh


def pt_selfheated_resistance(t_bath_c: float, r_bridge_ohm: float, v_exc_v: float,
                             r0_ohm: float, a_iec: float, b_iec: float,
                             dissipation_mw_per_k: float, iterations: int = 4):
    t_eff_c = float(t_bath_c)
    r = float(pt1000_resistance(t_eff_c, r0_ohm, a_iec, b_iec))
    dt_sh = 0.0
    for _ in range(iterations):
        i_a = bridge_pt_current(r, r_bridge_ohm, v_exc_v)
        p_w = i_a ** 2 * r
        dt_sh = (p_w * 1000.0) / dissipation_mw_per_k
        t_eff_c = t_bath_c + dt_sh
        r = float(pt1000_resistance(t_eff_c, r0_ohm, a_iec, b_iec))
    return r, dt_sh


def _make_channels(t1: Dict[str, Any], t_bath_c: float, ref_u: float,
                   excitation: str, pin_ntc: int, pin_pt: int):
    """Builds the NTC and Pt1000 channel entries for one run.

    `excitation` selects "low" (every calibration run) or "high" (the
    self-heating run only) on both sensors at once.
    """
    ntc = t1["ntc"]
    pt = t1["pt1000"]
    vcc = ntc["divider"][f"vcc_{excitation}_v"]
    v_exc = pt["bridge"][f"v_exc_{excitation}_v"]

    r_ntc, dt_sh_ntc = ntc_selfheated_resistance(
        t_bath_c, ntc["divider"]["r_fixed_ohm"], vcc, ntc["r25_ohm"],
        ntc["beta_k"], ntc["self_heating"]["dissipation_mw_per_k"])
    r_pt, dt_sh_pt = pt_selfheated_resistance(
        t_bath_c, pt["bridge"]["r_bridge_ohm"], v_exc, pt["r0_ohm"],
        pt["a_iec"], pt["b_iec"], pt["self_heating"]["dissipation_mw_per_k"])

    v_ntc = float(divider_voltage(r_ntc, ntc["divider"]["r_fixed_ohm"], vcc))
    v_pt = float(bridge_voltage(r_pt, pt["bridge"]["r_bridge_ohm"], v_exc,
                                pt["bridge"]["inamp_gain"],
                                pt["bridge"]["bias_offset_v"]))

    ch_ntc = T1Channel(
        pin=pin_ntc, quantity="temperature", unit="degC",
        sensor="NTC thermistor, resistive divider",
        gain=1.0, offset=0.0, filter="none",
        calibration_reference="reference thermometer at the bath",
        reference_value=t_bath_c, reference_u=ref_u,
        extra={
            "excitation_v": vcc,
            "divider_r_fixed_ohm": ntc["divider"]["r_fixed_ohm"],
            "self_heating_shift_c": round(dt_sh_ntc, 6),
        },
    )
    ch_pt = T1Channel(
        pin=pin_pt, quantity="temperature", unit="degC",
        sensor="Pt1000, bridge + instrumentation amplifier",
        gain=pt["bridge"]["inamp_gain"], offset=pt["bridge"]["bias_offset_v"],
        filter="none",
        calibration_reference="reference thermometer at the bath",
        reference_value=t_bath_c, reference_u=ref_u,
        extra={
            "excitation_v": v_exc,
            "bridge_r_ohm": pt["bridge"]["r_bridge_ohm"],
            "self_heating_shift_c": round(dt_sh_pt, 6),
        },
    )
    return ch_ntc, ch_pt, v_ntc, v_pt


def _emit_run(*, run: str, seed_offset: int, t1: Dict[str, Any],
             platform: Dict[str, Any], host, adc: AdcModel, board,
             t_bath_c: float, ref_u: float, excitation: str, n: int,
             period_us: int, references) -> None:
    rng = np.random.default_rng(t1["seed"] + seed_offset)
    pin_ntc = t1["ntc"]["pin"]
    pin_pt = t1["pt1000"]["pin"]

    timing = TimingModel(
        period_us=period_us,
        loop_period_us=platform["loop"]["period_2ch_us"],
        jitter_sd_us=host.jitter_sd_us,
        restart_probability=host.restart_probability,
        restart_extra_us=host.restart_extra_us,
    )
    t_us = timing.grid(n, rng)

    ch_ntc, ch_pt, v_ntc, v_pt = _make_channels(
        t1, t_bath_c, ref_u, excitation, pin_ntc, pin_pt)

    codes_ntc = adc.convert(np.full(n, v_ntc), rng)
    codes_pt = adc.convert(np.full(n, v_pt), rng)
    data = long_frame(t_us, {pin_ntc: codes_ntc, pin_pt: codes_pt})

    meta = build_meta(
        experiment="T1", run=run, board=board, host=host,
        pins=[pin_ntc, pin_pt], requested_period_us=period_us, t_us=t_us,
        channels=[ch_ntc, ch_pt], rng=rng, references=references,
        notes=(f"bath = {t_bath_c:g} degC, excitation = {excitation}"),
    )
    print(f"T1 {run}:")
    emit(OUT / f"{run}.csv", data, meta)


def generate() -> None:
    platform = load_params("platform")
    t1 = load_params("t1")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    host = hosts["linux"]
    ref_u = t1["reference_thermometer"]["u_c"]
    references = [dict(t1["references"]["thermometer"])]

    cal = t1["calibration"]
    n = cal["records_per_point"]
    period_us = cal["period_us"]

    points_by_name = {p["name"]: p for p in cal["points"]}
    for i, point in enumerate(cal["points"]):
        _emit_run(
            run=f"cal-{point['name']}", seed_offset=CAL_SEED_BASE + i, t1=t1,
            platform=platform, host=host, adc=adc, board=board,
            t_bath_c=point["bath_temperature_c"], ref_u=ref_u,
            excitation="low", n=n, period_us=period_us, references=references,
        )

    sh = t1["self_heating"]
    bath_point = points_by_name[sh["bath_point"]]
    _emit_run(
        run="selfheat-high", seed_offset=SELFHEAT_SEED_OFFSET, t1=t1,
        platform=platform, host=host, adc=adc, board=board,
        t_bath_c=bath_point["bath_temperature_c"], ref_u=ref_u,
        excitation="high", n=sh["records"], period_us=sh["period_us"],
        references=references,
    )


if __name__ == "__main__":
    generate()
