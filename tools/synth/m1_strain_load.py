"""Reference data for M1 - strain gauges and load cell.

Writes into `experiments/M1-strain-load/data/`:

  load-loading-*, load-unloading-*   Five reference masses, loaded then
                                      unloaded, through a standalone load
                                      cell -- linearity and hysteresis.
  load-creep                         One mass held for a stated time --
                                      creep.
  load-zero-return                   A zero reading taken after the full
                                      loading/unloading/creep sequence --
                                      zero drift.
  bridge-quarter-*, bridge-half-*,
  bridge-full-*                      The same small reference masses applied
                                      at the beam tip, read through a quarter,
                                      a half and a full bridge -- bridge
                                      sensitivity, and (the quarter-bridge
                                      points) the load->strain calibration
                                      M2's stiffness cross-check reads back.
  temp-quarter-cold/-hot,
  temp-half-cold/-hot                Beam unloaded, bench at two
                                      temperatures, bare quarter bridge
                                      against a dummy-compensated half bridge
                                      -- temperature compensation.

Every run goes through an instrumentation amplifier (modelled as a simple
gain and offset ahead of the ADC, same shape as S3's conditioning stage) and
then the S1 ADC model, on the S1 timing model. The beam dimensions in
`params/m1.yaml` are placeholders (see hardware/beam-rig.qmd): they are used
here only to keep the generated force/strain/voltage numbers internally
consistent with each other and with params/m2.yaml, never quoted as if
decided.

Run it with `python tools/synth/generate.py m1`.
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

OUT = Path(__file__).resolve().parents[2] / "experiments" / "M1-strain-load" / "data"

GRAVITY_M_S2 = 9.80665

#: Fixed integer seed offsets, one per run, so that adding a run later never
#: changes the bytes of the ones before it (hash() on a str is not stable
#: across Python runs, see tools/synth/s3_aliasing_filtering.py).
LOADING_SEED_BASE = 100        # + point index 0..4
UNLOADING_SEED_BASE = 200      # + point index 0..4
CREEP_SEED_OFFSET = 300
ZERO_RETURN_SEED_OFFSET = 310
BRIDGE_QUARTER_SEED_BASE = 400  # + point index 0..3
BRIDGE_HALF_SEED_BASE = 500     # + point index 0..1
BRIDGE_FULL_SEED_BASE = 600     # + point index 0..1
TEMP_SEED_OFFSET = {
    "temp-quarter-cold": 701, "temp-quarter-hot": 702,
    "temp-half-cold": 703, "temp-half-hot": 704,
}

PIN = 15


@dataclass
class M1Channel(Channel):
    """A `Channel` carrying M1-specific extra sidecar keys.

    Mirrors `S3Channel` in tools/synth/s3_aliasing_filtering.py: the shared
    schema has no field for a gauge factor or a bridge type, so those go in
    an `extra` mapping merged into the channel's dict.
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


def _timing(platform: Dict[str, Any], host, period_us: float) -> TimingModel:
    return TimingModel(
        period_us=period_us,
        loop_period_us=platform["loop"]["period_1ch_us"],
        jitter_sd_us=host.jitter_sd_us,
        restart_probability=host.restart_probability,
        restart_extra_us=host.restart_extra_us,
    )


def _required_gain(v_fs: float, vref_v: float, lo_frac: float, hi_frac: float):
    """Amplifier gain/offset so [0, v_fs] at the sensor maps onto
    [lo_frac, hi_frac] * vref_v at the ADC -- the same construction as
    eq-s3-gain-offset / eq-cond-gain-offset, for a one-sided (not bipolar)
    input.
    """
    v_lo = lo_frac * vref_v
    v_hi = hi_frac * vref_v
    gain = (v_hi - v_lo) / v_fs
    return gain, v_lo


def _hysteresis_v(frac_of_capacity: np.ndarray, sign: float, v_fs: float,
                  hysteresis_frac: float) -> np.ndarray:
    """A path-dependent offset: zero at 0 % and 100 % of capacity, peaking at
    hysteresis_frac * v_fs at 50 % -- the textbook loop shape -- with opposite
    sign on loading and unloading.
    """
    bell = 4.0 * frac_of_capacity * (1.0 - frac_of_capacity)
    return sign * hysteresis_frac * v_fs * bell


def generate() -> None:
    platform = load_params("platform")
    params = load_params("m1")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    host = hosts["linux"]
    seed = params["seed"]
    masses_ref = dict(params["references"]["masses"])

    # --- the standalone load cell: linearity, hysteresis, creep, zero drift -
    lc = params["load_cell"]
    fcap = lc["capacity_n"]
    v_fs_lc = lc["sensitivity_mv_per_v"] * 1.0e-3 * lc["excitation_v"]
    la = params["load_amplifier"]
    gain_lc, offset_lc = _required_gain(
        v_fs_lc, board["vref_mV"] / 1000.0, la["target_lo_frac"], la["target_hi_frac"])

    def bridge_reading_v(f_n: float, sign: float) -> float:
        frac = f_n / fcap
        ideal = v_fs_lc * frac * (1.0 + lc["nonlinearity_frac"] * frac)
        hyst = _hysteresis_v(frac, sign, v_fs_lc, lc["hysteresis_frac"])
        return ideal + hyst

    def emit_load_point(run: str, mass_kg: float, sign: float, step_index: int,
                        seed_offset: int) -> None:
        n = lc["records_per_point"]
        rng = np.random.default_rng(seed + seed_offset)
        timing = _timing(platform, host, lc["period_us"])
        t_us = timing.grid(n, rng)
        f_n = mass_kg * GRAVITY_M_S2
        v_bridge = bridge_reading_v(f_n, sign)
        v_adc = (gain_lc * v_bridge + offset_lc
                + lc["zero_drift_v_per_step"] * step_index)
        codes = adc.convert(np.full(n, v_adc), rng)
        data = long_frame(t_us, {PIN: codes})
        channel = M1Channel(
            pin=PIN, quantity="force", unit="N", sensor="load cell (bridge)",
            gain=gain_lc, offset=offset_lc,
            calibration_reference="reference mass on the load cell",
            reference_value=f_n, reference_u=lc["mass_u_kg"] * GRAVITY_M_S2,
            extra={
                "load_cell_capacity_n": fcap,
                "load_cell_sensitivity_mv_per_v": lc["sensitivity_mv_per_v"],
                "bridge_excitation_v": lc["excitation_v"],
                "session_step_index": step_index,
            },
        )
        meta = build_meta(
            experiment="M1", run=run, board=board, host=host, pins=[PIN],
            requested_period_us=lc["period_us"], t_us=t_us, channels=[channel],
            rng=rng, references=[masses_ref],
            notes=f"m = {mass_kg * 1000:.0f} g, session step {step_index}",
        )
        print(f"M1 {run}:")
        emit(OUT / f"{run}.csv", data, meta)

    masses = lc["masses_kg"]
    for i, m in enumerate(masses):
        emit_load_point(f"load-loading-{int(round(m * 1000)):03d}", m, -1.0, i,
                        LOADING_SEED_BASE + i)
    for i, m in enumerate(reversed(masses)):
        emit_load_point(f"load-unloading-{int(round(m * 1000)):03d}", m, +1.0,
                        len(masses) + i, UNLOADING_SEED_BASE + i)

    # --- creep: one mass, held, sampled over time ----------------------------
    creep = lc["creep"]
    n_creep = int(round(creep["duration_s"] * 1.0e6 / creep["period_us"]))
    rng = np.random.default_rng(seed + CREEP_SEED_OFFSET)
    timing = _timing(platform, host, creep["period_us"])
    t_us = timing.grid(n_creep, rng)
    t_s = (t_us - t_us[0]) / 1.0e6
    f_hold = creep["hold_mass_kg"] * GRAVITY_M_S2
    frac_hold = f_hold / fcap
    v_ideal_hold = v_fs_lc * frac_hold * (1.0 + lc["nonlinearity_frac"] * frac_hold)
    v_bridge_t = v_ideal_hold * (1.0 + creep["amplitude_frac"]
                                 * (1.0 - np.exp(-t_s / creep["tau_s"])))
    step_creep = len(masses) * 2
    v_adc = (gain_lc * v_bridge_t + offset_lc
            + lc["zero_drift_v_per_step"] * step_creep)
    codes = adc.convert(v_adc, rng)
    data = long_frame(t_us, {PIN: codes})
    channel = M1Channel(
        pin=PIN, quantity="force", unit="N", sensor="load cell (bridge)",
        gain=gain_lc, offset=offset_lc,
        calibration_reference="reference mass on the load cell",
        reference_value=f_hold, reference_u=lc["mass_u_kg"] * GRAVITY_M_S2,
        extra={
            "load_cell_capacity_n": fcap,
            "load_cell_sensitivity_mv_per_v": lc["sensitivity_mv_per_v"],
            "bridge_excitation_v": lc["excitation_v"],
            "session_step_index": step_creep,
        },
    )
    meta = build_meta(
        experiment="M1", run="load-creep", board=board, host=host, pins=[PIN],
        requested_period_us=creep["period_us"], t_us=t_us, channels=[channel],
        rng=rng, references=[masses_ref],
        notes=f"m = {creep['hold_mass_kg'] * 1000:.0f} g held for {creep['duration_s']:.0f} s",
    )
    print("M1 load-creep:")
    emit(OUT / "load-creep.csv", data, meta)

    # --- zero return: after the whole session --------------------------------
    zr = lc["zero_return"]
    step_zr = step_creep + 1
    emit_load_point("load-zero-return", 0.0, +1.0, step_zr,
                    ZERO_RETURN_SEED_OFFSET)

    # --- the beam-mounted gauges: quarter / half / full bridge demo ----------
    beam = params["beam"]
    gauge = params["gauge"]
    ba = params["beam_amplifier"]
    dv_fs = (gauge["gauge_factor"] * gauge["full_scale_strain"]
            * gauge["excitation_v"] / 4.0)
    gain_beam, offset_beam = _required_gain(
        dv_fs, board["vref_mV"] / 1000.0, ba["target_lo_frac"], ba["target_hi_frac"])

    def strain_at_gauge(f_n: float) -> float:
        # eq-beam-strain at x = x_gauge, hardware/beam-rig.qmd.
        return (6.0 * f_n * (beam["length_m"] - beam["gauge_position_m"])
               / (beam["youngs_modulus_pa"] * beam["width_m"] * beam["thickness_m"] ** 2))

    def bridge_v(multiplier: float, eps: float) -> float:
        return multiplier * gauge["gauge_factor"] * eps * gauge["excitation_v"] / 4.0

    def emit_bridge_point(run: str, bridge_type: str, multiplier: float,
                          mass_kg: float, seed_offset: int) -> None:
        bd = params["bridge_demo"]
        n = bd["records_per_point"]
        rng = np.random.default_rng(seed + seed_offset)
        timing = _timing(platform, host, bd["period_us"])
        t_us = timing.grid(n, rng)
        f_n = mass_kg * GRAVITY_M_S2
        eps = strain_at_gauge(f_n)
        v_bridge = bridge_v(multiplier, eps)
        v_adc = gain_beam * v_bridge + offset_beam
        codes = adc.convert(np.full(n, v_adc), rng)
        data = long_frame(t_us, {PIN: codes})
        channel = M1Channel(
            pin=PIN, quantity="force", unit="N",
            sensor=f"strain gauge, {bridge_type} bridge",
            gain=gain_beam, offset=offset_beam,
            calibration_reference="reference mass at the beam tip",
            reference_value=f_n, reference_u=bd["mass_u_kg"] * GRAVITY_M_S2,
            extra={
                "bridge_type": bridge_type,
                "gauge_factor": gauge["gauge_factor"],
                "u_gauge_factor_rel": gauge["u_gauge_factor_rel"],
                "bridge_excitation_v": gauge["excitation_v"],
                "u_excitation_rel": gauge["u_excitation_rel"],
                "amplifier_u_gain_rel": ba["u_gain_rel"],
                "amplifier_u_offset_v": ba["u_offset_v"],
            },
        )
        meta = build_meta(
            experiment="M1", run=run, board=board, host=host, pins=[PIN],
            requested_period_us=bd["period_us"], t_us=t_us, channels=[channel],
            rng=rng, references=[masses_ref],
            notes=f"{bridge_type} bridge, m = {mass_kg * 1000:.0f} g",
        )
        print(f"M1 {run}:")
        emit(OUT / f"{run}.csv", data, meta)

    bd = params["bridge_demo"]
    for i, m in enumerate(bd["quarter_masses_kg"]):
        emit_bridge_point(f"bridge-quarter-{int(round(m * 1000)):03d}",
                          "quarter", 1.0, m, BRIDGE_QUARTER_SEED_BASE + i)
    for i, m in enumerate(bd["half_full_masses_kg"]):
        emit_bridge_point(f"bridge-half-{int(round(m * 1000)):03d}",
                          "half", 2.0, m, BRIDGE_HALF_SEED_BASE + i)
    for i, m in enumerate(bd["half_full_masses_kg"]):
        emit_bridge_point(f"bridge-full-{int(round(m * 1000)):03d}",
                          "full", 4.0, m, BRIDGE_FULL_SEED_BASE + i)

    # --- temperature compensation: quarter vs. half bridge, beam unloaded ----
    tt = params["temperature_test"]

    def emit_temp_point(run: str, bridge_type: str, multiplier: float,
                        alpha_per_k: float, delta_t_c: float) -> None:
        n = tt["records_per_point"]
        rng = np.random.default_rng(seed + TEMP_SEED_OFFSET[run])
        timing = _timing(platform, host, tt["period_us"])
        t_us = timing.grid(n, rng)
        eps_apparent = alpha_per_k * delta_t_c
        v_bridge = bridge_v(multiplier, eps_apparent)
        v_adc = gain_beam * v_bridge + offset_beam
        codes = adc.convert(np.full(n, v_adc), rng)
        data = long_frame(t_us, {PIN: codes})
        channel = M1Channel(
            pin=PIN, quantity="force", unit="N",
            sensor=f"strain gauge, {bridge_type} bridge",
            gain=gain_beam, offset=offset_beam,
            calibration_reference="beam unloaded (F = 0)",
            reference_value=0.0, reference_u=0.0,
            extra={
                "bridge_type": bridge_type,
                "gauge_factor": gauge["gauge_factor"],
                "bridge_excitation_v": gauge["excitation_v"],
                "applied_delta_t_c": delta_t_c,
            },
        )
        meta = build_meta(
            experiment="M1", run=run, board=board, host=host, pins=[PIN],
            requested_period_us=tt["period_us"], t_us=t_us, channels=[channel],
            rng=rng, references=[masses_ref],
            notes=f"{bridge_type} bridge, beam unloaded, delta_T = {delta_t_c:g} C",
        )
        print(f"M1 {run}:")
        emit(OUT / f"{run}.csv", data, meta)

    emit_temp_point("temp-quarter-cold", "quarter", 1.0,
                    tt["alpha_apparent_quarter_per_k"], 0.0)
    emit_temp_point("temp-quarter-hot", "quarter", 1.0,
                    tt["alpha_apparent_quarter_per_k"], tt["delta_t_c"])
    emit_temp_point("temp-half-cold", "half", 2.0,
                    tt["alpha_apparent_half_per_k"], 0.0)
    emit_temp_point("temp-half-hot", "half", 2.0,
                    tt["alpha_apparent_half_per_k"], tt["delta_t_c"])


if __name__ == "__main__":
    generate()
