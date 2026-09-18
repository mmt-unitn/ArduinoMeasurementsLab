"""Reference data for S3 - aliasing, conditioning and filtering.

Writes into `experiments/S3-aliasing-filtering/data/`:

  alias-fNNNN (16 runs)   The aliasing sweep: one generator frequency per run,
                          fixed low fs = 500 Hz, spanning several folding
                          orders k = round(f/fs). Reconstructing the folding
                          triangle f_a = |f - k fs| from these runs is the
                          point of the aliasing section.
  filter-off, filter-on   The same 650 Hz tone (which aliases to 150 Hz at
                          fs = 500 Hz) with no analog anti-alias filter and
                          with one, modelled as a scalar attenuation of the
                          sinusoid's amplitude by |H(650 Hz)| ahead of the ADC.
  oversampled             A 150 Hz signal plus a 1800 Hz interferer at
                          fs = 5000 Hz, high enough that neither tone folds --
                          the input to the digital-filtering section.

Every run goes through the conditioning stage (`conditioning.gain`,
`conditioning.offset_v` in `params/s3.yaml`, derived in _theory.qmd) and then
the S1 ADC model, on the S1 timing model, exactly as S1's own runs do.

Run it with `python tools/synth/generate.py s3`.
"""

from __future__ import annotations

import math
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

OUT = Path(__file__).resolve().parents[2] / "experiments" / "S3-aliasing-filtering" / "data"

#: Fixed integer seed offsets, one per run, so that adding a run later never
#: changes the bytes of the ones before it. `hash()` on a str is not stable
#: across Python runs, so these are spelled out rather than derived.
SWEEP_SEED_BASE = 100          # + sweep index i (0..15)
FILTER_PAIR_SEED = {"filter-off": 201, "filter-on": 202}
OVERSAMPLED_SEED_OFFSET = 301


@dataclass
class S3Channel(Channel):
    """A `Channel` that also carries S3-specific extra sidecar keys.

    The shared sidecar schema (`data-format/index.qmd`) has no field for a
    generator frequency or a filter's corner frequency; it explicitly allows
    generator-specific extensions (see its `generator:` top-level mapping), so
    this subclass adds an `extra` mapping merged into the channel's dict
    rather than widening `common.Channel` for every experiment.
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


def _condition(v_gen: np.ndarray, gain: float, offset_v: float,
              atten: float = 1.0) -> np.ndarray:
    """The conditioning stage: V_adc = gain * atten * V_in + offset_v.

    `atten` is the analog anti-alias filter's |H(f)| at the signal's
    frequency, applied as a scalar amplitude attenuation ahead of the ADC --
    the honest model of an analog filter acting on a single sinusoid, since
    the filter and the gain stage are both linear and commute.
    """
    return gain * atten * v_gen + offset_v


def generate() -> None:
    platform = load_params("platform")
    params = load_params("s3")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    host = hosts["linux"]
    seed = params["seed"]
    gen_ref = dict(params["references"]["generator"])

    cond = params["conditioning"]
    gain = cond["gain"]
    offset_v = cond["offset_v"]
    vg = cond["generator_amplitude_v"]
    u_ref_scale = cond["u_amplitude_rel"]

    filt = params["filter"]
    order = int(filt["order"])
    fc = filt["fc_hz"]

    # --- the aliasing sweep --------------------------------------------------
    sweep = params["aliasing_sweep"]
    fs_sw = sweep["fs_hz"]
    period_us_sw = 1.0e6 / fs_sw
    n_sw = int(round(sweep["duration_s"] * fs_sw))
    pin_sw = sweep["pin"]

    for i, f in enumerate(sweep["frequencies_hz"]):
        rng = np.random.default_rng(seed + SWEEP_SEED_BASE + i)
        timing = _timing(platform, host, period_us_sw)
        t_us = timing.grid(n_sw, rng)
        t_s = t_us / 1.0e6
        v_gen = vg * np.sin(2.0 * np.pi * f * t_s)
        v_cond = _condition(v_gen, gain, offset_v)
        codes = adc.convert(v_cond, rng)
        data = long_frame(t_us, {pin_sw: codes})

        channel = S3Channel(
            pin=pin_sw, quantity="voltage", unit="V",
            sensor="signal generator (bipolar)",
            gain=gain, offset=offset_v, filter="none",
            calibration_reference="signal generator amplitude setting",
            reference_value=vg, reference_u=vg * u_ref_scale,
            extra={"generator_frequency_hz": float(f)},
        )
        k_pred = round(f / fs_sw)
        run = f"alias-f{int(f):04d}"
        meta = build_meta(
            experiment="S3", run=run, board=board, host=host, pins=[pin_sw],
            requested_period_us=int(round(period_us_sw)), t_us=t_us,
            channels=[channel], rng=rng, references=[gen_ref],
            notes=(f"aliasing sweep point: f = {f:g} Hz, fs = {fs_sw:g} Hz, "
                  f"nominal k = {k_pred}"),
        )
        print(f"S3 {run}:")
        emit(OUT / f"{run}.csv", data, meta)

    # --- unfiltered vs filtered pair ------------------------------------------
    fp = params["filter_pair"]
    fs_fp = fp["fs_hz"]
    period_us_fp = 1.0e6 / fs_fp
    n_fp = int(round(fp["duration_s"] * fs_fp))
    pin_fp = fp["pin"]
    f_fp = fp["signal_freq_hz"]
    h_mag = 1.0 / math.sqrt(1.0 + (f_fp / fc) ** (2 * order))
    fa_fp = abs(f_fp - round(f_fp / fs_fp) * fs_fp)

    runs_fp = (
        ("filter-off", 1.0, "none", {}),
        ("filter-on", h_mag,
         f"{order}-pole Butterworth-style (op-amp buffered), fc = {fc:g} Hz",
         {"filter_fc_hz": fc, "filter_order": order,
          "filter_u_fc_rel": float(params["filter"]["u_fc_rel"])}),
    )
    for run, atten, filt_desc, extra_filt in runs_fp:
        rng = np.random.default_rng(seed + FILTER_PAIR_SEED[run])
        timing = _timing(platform, host, period_us_fp)
        t_us = timing.grid(n_fp, rng)
        t_s = t_us / 1.0e6
        v_gen = vg * np.sin(2.0 * np.pi * f_fp * t_s)
        v_cond = _condition(v_gen, gain, offset_v, atten=atten)
        codes = adc.convert(v_cond, rng)
        data = long_frame(t_us, {pin_fp: codes})

        # The uncertainty of the conditioning stage and of the filter corner
        # belongs in the sidecar, not only in this generator's parameter file:
        # a student analysing a real bench has the sidecar and nothing else,
        # and the budget in the handbook needs all three of these.
        extra = {
            "generator_frequency_hz": float(f_fp),
            "conditioning_u_gain_rel": float(cond["u_gain_rel"]),
            "conditioning_u_offset_v": float(cond["u_offset_v"]),
        }
        extra.update(extra_filt)
        channel = S3Channel(
            pin=pin_fp, quantity="voltage", unit="V",
            sensor="signal generator (bipolar)",
            gain=gain, offset=offset_v, filter=filt_desc,
            calibration_reference="signal generator amplitude setting",
            reference_value=vg, reference_u=vg * u_ref_scale,
            extra=extra,
        )
        meta = build_meta(
            experiment="S3", run=run, board=board, host=host, pins=[pin_fp],
            requested_period_us=int(round(period_us_fp)), t_us=t_us,
            channels=[channel], rng=rng, references=[gen_ref],
            notes=(f"f = {f_fp:g} Hz aliases to {fa_fp:g} Hz at "
                  f"fs = {fs_fp:g} Hz; filter = {filt_desc}"),
        )
        print(f"S3 {run}:")
        emit(OUT / f"{run}.csv", data, meta)

    # --- oversampled run: signal + high-frequency interferer -----------------
    ov = params["oversampled"]
    fs_ov = ov["fs_hz"]
    period_us_ov = 1.0e6 / fs_ov
    n_ov = int(round(ov["duration_s"] * fs_ov))
    pin_ov = ov["pin"]

    rng = np.random.default_rng(seed + OVERSAMPLED_SEED_OFFSET)
    timing = _timing(platform, host, period_us_ov)
    t_us = timing.grid(n_ov, rng)
    t_s = t_us / 1.0e6
    v_gen = (ov["signal_amplitude_v"] * np.sin(2.0 * np.pi * ov["signal_freq_hz"] * t_s)
            + ov["interferer_amplitude_v"] * np.sin(2.0 * np.pi * ov["interferer_freq_hz"] * t_s))
    v_cond = _condition(v_gen, gain, offset_v)
    codes = adc.convert(v_cond, rng)
    data = long_frame(t_us, {pin_ov: codes})

    channel = S3Channel(
        pin=pin_ov, quantity="voltage", unit="V",
        sensor="signal generator (two-tone: signal + interferer)",
        gain=gain, offset=offset_v, filter="none",
        calibration_reference="signal generator amplitude setting",
        reference_value=ov["signal_amplitude_v"],
        reference_u=ov["signal_amplitude_v"] * u_ref_scale,
        extra={
            "signal_frequency_hz": float(ov["signal_freq_hz"]),
            "signal_amplitude_v": float(ov["signal_amplitude_v"]),
            "interferer_frequency_hz": float(ov["interferer_freq_hz"]),
            "interferer_amplitude_v": float(ov["interferer_amplitude_v"]),
        },
    )
    meta = build_meta(
        experiment="S3", run="oversampled", board=board, host=host,
        pins=[pin_ov], requested_period_us=int(round(period_us_ov)), t_us=t_us,
        channels=[channel], rng=rng, references=[gen_ref],
        notes="signal + high-frequency interferer, fs well above Nyquist for both",
    )
    print("S3 oversampled:")
    emit(OUT / "oversampled.csv", data, meta)


if __name__ == "__main__":
    generate()
