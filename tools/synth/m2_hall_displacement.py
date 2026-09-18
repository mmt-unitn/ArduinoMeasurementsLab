"""Reference data for M2 - Hall-effect displacement measurement.

Writes into `experiments/M2-hall-displacement/data/`:

  cal-dNNNN (12 runs)     A magnet on the micrometer stage, swept toward and
                          away from the sensor -- the nonlinear V(d) curve.
  supply-drift            Displacement held fixed while Vcc is made to drift
                          -- ratiometric vs. absolute reading.
  crosscheck-* (4 runs)   The same small reference masses as
                          params/m1.yaml's bridge_demo.quarter_masses_kg,
                          applied at the beam tip; the Hall sensor reads the
                          resulting displacement -- M1's stiffness
                          cross-check.

Every run goes through the S1 ADC model, on the S1 timing model, with no
extra gain stage: a ratiometric sensor's output already spans most of
[0, Vcc]. The beam dimensions in `params/m2.yaml` are placeholders (see
hardware/beam-rig.qmd), used here only to keep the generated displacement
numbers consistent with params/m1.yaml's strain numbers, never quoted as if
decided.

Run it with `python tools/synth/generate.py m2`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

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

OUT = Path(__file__).resolve().parents[2] / "experiments" / "M2-hall-displacement" / "data"

GRAVITY_M_S2 = 9.80665

#: Fixed integer seed offsets, one per run -- see tools/synth/m1_strain_load.py
#: for why these are spelled out rather than derived from a name.
SWEEP_SEED_BASE = 100          # + sweep index 0..11
SUPPLY_DRIFT_SEED_OFFSET = 300
CROSSCHECK_SEED_BASE = 400     # + point index 0..3


@dataclass
class M2Channel(Channel):
    """A `Channel` carrying M2-specific extra sidecar keys, mirroring
    `S3Channel` / `M1Channel`."""

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
        loop_period_us=platform["loop"]["period_2ch_us"],
        jitter_sd_us=host.jitter_sd_us,
        restart_probability=host.restart_probability,
        restart_extra_us=host.restart_extra_us,
    )


def _hall_volts(d_mm: np.ndarray, vcc_v, hall: Dict[str, Any]) -> np.ndarray:
    """V_out = Vcc * (0.5 + A / (1 + d/d0)^n), the ratiometric ADC-referred
    output of eq-m2-ratiometric, with the far-field dipole decay of
    eq-m2-decay."""
    shape = 1.0 + d_mm / hall["d0_mm"]
    return vcc_v * (0.5 + hall["sensitivity_amplitude"]
                   / shape ** hall["decay_exponent"])


def _beam_displacement_mm(f_n: float, beam: Dict[str, Any]) -> float:
    # eq-beam-deflection, hardware/beam-rig.qmd: delta = F L^3 / (3 E I).
    i_area = beam["width_m"] * beam["thickness_m"] ** 3 / 12.0
    delta_m = f_n * beam["length_m"] ** 3 / (3.0 * beam["youngs_modulus_pa"] * i_area)
    return delta_m * 1000.0


def generate() -> None:
    platform = load_params("platform")
    params = load_params("m2")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    host = hosts["linux"]
    seed = params["seed"]
    hall = params["hall"]
    pin_out = params["pins"]["hall_out"]
    pin_vcc = params["pins"]["vcc_sense"]
    micrometer_ref = dict(params["references"]["micrometer"])
    masses_ref = dict(params["references"]["masses"])

    def emit_two_channel(run: str, n: int, period_us: float, seed_offset: int,
                         d_mm, vcc_v, reference_value, reference_u,
                         calibration_reference, quantity, unit, sensor,
                         extra: Dict[str, Any], notes: str,
                         meta_extra: Optional[Dict[str, Any]] = None) -> None:
        rng = np.random.default_rng(seed + seed_offset)
        timing = _timing(platform, host, period_us)
        t_us = timing.grid(n, rng)
        # Broadcast a scalar Vcc (the calibration sweep, the crosscheck) to the
        # record count, so both channels always carry n samples even when the
        # supply-drift run instead passes an already-length-n array.
        vcc_arr = np.broadcast_to(np.asarray(vcc_v, dtype=float), (n,))
        v_out = _hall_volts(d_mm, vcc_arr, hall)
        v_sense = hall["vcc_sense_gain"] * vcc_arr
        codes_out = adc.convert(v_out, rng)
        codes_vcc = adc.convert(v_sense, rng)
        data = long_frame(t_us, {pin_out: codes_out, pin_vcc: codes_vcc})
        # sensor_temperature_coefficient_per_k is an influence quantity the
        # budget bounds rather than measures (see handbook.qmd#sec-m2-budget),
        # so every Hall-output channel carries it, not only the ones that
        # exercise it.
        full_extra = {"sensor_temperature_coefficient_per_k":
                      hall["temperature_coefficient_per_k"]}
        full_extra.update(extra)
        ch_out = M2Channel(
            pin=pin_out, quantity=quantity, unit=unit, sensor=sensor,
            calibration_reference=calibration_reference,
            reference_value=reference_value, reference_u=reference_u,
            extra=full_extra,
        )
        ch_vcc = M2Channel(
            pin=pin_vcc, quantity="voltage", unit="V",
            sensor="supply-voltage sense",
            gain=hall["vcc_sense_gain"],
            calibration_reference="supply rail",
            reference_value=hall["vcc_nominal_v"], reference_u=0.0,
        )
        meta = build_meta(
            experiment="M2", run=run, board=board, host=host,
            pins=[pin_out, pin_vcc], requested_period_us=period_us, t_us=t_us,
            channels=[ch_out, ch_vcc], rng=rng,
            references=[micrometer_ref, masses_ref], notes=notes,
        )
        if meta_extra:
            meta.update(meta_extra)
        print(f"M2 {run}:")
        emit(OUT / f"{run}.csv", data, meta)

    # --- calibration sweep: magnet swept toward/away from the sensor ---------
    sweep = params["calibration_sweep"]
    for i, d in enumerate(sweep["distances_mm"]):
        n = sweep["records_per_point"]
        emit_two_channel(
            f"cal-d{int(round(d)):04d}", n, sweep["period_us"],
            SWEEP_SEED_BASE + i,
            np.full(n, float(d)), hall["vcc_nominal_v"],
            reference_value=float(d), reference_u=sweep["distance_u_mm"],
            calibration_reference="micrometer stage reading",
            quantity="displacement", unit="mm",
            sensor="linear Hall sensor (ratiometric)",
            extra={"decay_exponent": hall["decay_exponent"]},
            notes=f"d = {d:g} mm",
        )

    # --- supply-voltage drift, displacement held fixed ------------------------
    sd = params["supply_drift"]
    n_sd = int(round(sd["duration_s"] * 1.0e6 / sd["period_us"]))
    rng = np.random.default_rng(seed + SUPPLY_DRIFT_SEED_OFFSET)
    timing = _timing(platform, host, sd["period_us"])
    t_us = timing.grid(n_sd, rng)
    t_s = (t_us - t_us[0]) / 1.0e6
    vcc_t = sd["vcc_start_v"] + (sd["vcc_end_v"] - sd["vcc_start_v"]) * (
        t_s / max(t_s[-1], 1e-9))
    v_out = _hall_volts(np.full(n_sd, sd["distance_mm"]), vcc_t, hall)
    v_sense = hall["vcc_sense_gain"] * vcc_t
    codes_out = adc.convert(v_out, rng)
    codes_vcc = adc.convert(v_sense, rng)
    data = long_frame(t_us, {pin_out: codes_out, pin_vcc: codes_vcc})
    ch_out = M2Channel(
        pin=pin_out, quantity="displacement", unit="mm",
        sensor="linear Hall sensor (ratiometric)",
        calibration_reference="micrometer stage reading, held fixed",
        reference_value=sd["distance_mm"], reference_u=0.0,
        extra={"decay_exponent": hall["decay_exponent"]},
    )
    ch_vcc = M2Channel(
        pin=pin_vcc, quantity="voltage", unit="V",
        sensor="supply-voltage sense", gain=hall["vcc_sense_gain"],
        calibration_reference="supply rail, deliberately drifted",
        reference_value=hall["vcc_nominal_v"], reference_u=0.0,
        extra={"vcc_start_v": sd["vcc_start_v"], "vcc_end_v": sd["vcc_end_v"]},
    )
    meta = build_meta(
        experiment="M2", run="supply-drift", board=board, host=host,
        pins=[pin_out, pin_vcc], requested_period_us=sd["period_us"], t_us=t_us,
        channels=[ch_out, ch_vcc], rng=rng,
        references=[micrometer_ref, masses_ref],
        notes=f"d held at {sd['distance_mm']:g} mm, Vcc {sd['vcc_start_v']:g} -> {sd['vcc_end_v']:g} V",
    )
    print("M2 supply-drift:")
    emit(OUT / "supply-drift.csv", data, meta)

    # --- on-the-beam cross-check with M1 --------------------------------------
    beam = params["beam"]
    cc = params["beam_crosscheck"]
    for i, m in enumerate(cc["masses_kg"]):
        n = cc["records_per_point"]
        f_n = m * GRAVITY_M_S2
        delta_mm = _beam_displacement_mm(f_n, beam)
        d_mm = cc["baseline_distance_mm"] - delta_mm
        emit_two_channel(
            f"crosscheck-{int(round(m * 1000)):03d}", n, cc["period_us"],
            CROSSCHECK_SEED_BASE + i,
            np.full(n, d_mm), hall["vcc_nominal_v"],
            reference_value=f_n, reference_u=cc["mass_u_kg"] * GRAVITY_M_S2,
            calibration_reference="reference mass at the beam tip",
            quantity="force", unit="N",
            sensor="linear Hall sensor (ratiometric), beam tip target",
            extra={
                "decay_exponent": hall["decay_exponent"],
                "baseline_distance_mm": cc["baseline_distance_mm"],
            },
            notes=f"beam tip, m = {m * 1000:.0f} g",
            # The stiffness cross-check turns a fitted slope into a Young's
            # modulus, which needs the beam's geometry. A student measures it
            # at the bench, so it belongs in the sidecar rather than only in
            # this generator's parameter file.
            meta_extra={"beam": {
                "length_m": beam["length_m"],
                "width_m": beam["width_m"],
                "thickness_m": beam["thickness_m"],
                "gauge_position_m": beam["gauge_position_m"],
            }},
        )


if __name__ == "__main__":
    generate()
