"""Reference data for S2 - dynamic characterization of the DAQ (sine fit).

Writes one run per swept generator frequency into
`experiments/S2-daq-dynamic/data/`, each a single channel recording a sine
from a bench signal generator, offset to mid-scale by the conditioning board,
through the S1 ADC model and the S1 timing model.

Two things happen that a static model does not need:

  Coherent sampling. Each run has the same number of samples `n_samples`
  (prime, see params/s2.yaml), and the generator frequency is chosen as
  `cycles * fs_hz / n_samples` -- an exact, non-repeating number of cycles in
  the record, and never an exact submultiple of fs_hz (IEEE Std 1057).

  Aperture jitter. The instant at which the signal is actually sampled is
  perturbed, independently of the timestamp the device reports in t_us. This
  is on top of the ordinary loop/host timing jitter the S1 TimingModel
  already produces for t_us itself, and it is what makes dynamic ENOB fall
  with frequency -- the mechanism the whole experiment is about.

The device's own sample clock also carries a small fixed error relative to
the generator's reference (`device_clock_ppm`): the firmware reports t_us as
though its own oscillator were exact, so a clock offset shows up as a scale
factor between "device time" (t_us) and true elapsed time. Because the
four-parameter fit estimates frequency freely, it recovers this scaling as
f_hat/f_gen - 1 -- the clock-accuracy result of the handbook.

Run it with `python tools/synth/generate.py s2`.
"""

from __future__ import annotations

from pathlib import Path

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

OUT = Path(__file__).resolve().parents[2] / "experiments" / "S2-daq-dynamic" / "data"

#: Fixed per-run seed offsets, one per sweep point, in sweep order. Python
#: hashes strings with a per-process seed, so deriving these from the run
#: label would make the generator irreproducible -- fixed integers only, as
#: in s1_daq_static.py.
SWEEP_SEED_OFFSET = [101, 102, 103, 104, 105, 106, 107, 108, 109]


def _adc(platform) -> AdcModel:
    return AdcModel(
        bits=platform["board"]["adc_bits"],
        vref_v=platform["board"]["vref_mV"] / 1000.0,
        **platform["adc"],
    )


def generate() -> None:
    platform = load_params("platform")
    params = load_params("s2")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    host = hosts["linux"]

    sampling = params["sampling"]
    fs_hz = float(sampling["fs_hz"])
    n = int(sampling["n_samples"])
    pin = int(sampling["pin"])
    period_us = 1.0e6 / fs_hz

    signal = params["signal"]
    vref_v = board["vref_mV"] / 1000.0
    amplitude_v = 0.5 * signal["amplitude_fraction_of_fsr"] * vref_v
    offset_v = vref_v / 2.0
    phase0 = float(signal["phase0_rad"])

    clock_scale = 1.0 + params["device_clock_ppm"] * 1e-6
    aperture_jitter_s = params["aperture_jitter_ns"] * 1e-9

    gen = params["generator"]
    sweep = params["sweep"]
    if len(sweep) != len(SWEEP_SEED_OFFSET):
        raise ValueError("SWEEP_SEED_OFFSET must have one entry per sweep point")

    for point, seed_offset in zip(sweep, SWEEP_SEED_OFFSET):
        rng = np.random.default_rng(params["seed"] + seed_offset)
        cycles = int(point["cycles"])
        f_gen = cycles * fs_hz / n  # exact, incommensurate with fs_hz

        timing = TimingModel(
            period_us=period_us,
            loop_period_us=platform["loop"]["period_1ch_us"],
            jitter_sd_us=host.jitter_sd_us,
            restart_probability=host.restart_probability,
            restart_extra_us=host.restart_extra_us,
        )
        t_us = timing.grid(n, rng)

        # True elapsed time: the device believes its own oscillator, so it
        # reports t_us as if those were exact microseconds; a fixed clock
        # error rescales how much real time actually elapsed. The aperture
        # jitter is added on top, and is *not* reflected in t_us -- it is the
        # analog sample-and-hold's own timing uncertainty.
        true_time_s = (t_us.astype(np.float64) * 1e-6) / clock_scale
        sample_time_s = true_time_s + rng.normal(0.0, aperture_jitter_s, size=n)

        volts = offset_v + amplitude_v * np.sin(
            2.0 * np.pi * f_gen * sample_time_s + phase0
        )
        codes = adc.convert(volts, rng)
        data = long_frame(t_us, {pin: codes})

        u_freq = f_gen * gen["frequency_u_ppm"] * 1e-6
        u_amp = amplitude_v * gen["amplitude_u_fraction"]

        meta = build_meta(
            experiment="S2",
            run=f"sweep-{point['label_hz']:04d}hz",
            board=board,
            host=host,
            pins=[pin],
            requested_period_us=round(period_us),
            t_us=t_us,
            channels=[Channel(
                pin=pin,
                quantity="voltage",
                unit="V",
                sensor="sine from bench signal generator, through conditioning board",
                gain=1.0,
                offset=offset_v,
                calibration_reference="signal generator, see references",
            )],
            rng=rng,
            references=[
                {
                    "instrument": "signal generator",
                    "model": gen["model"],
                    "calibration": gen["calibration"],
                    "quantity": "frequency",
                    "unit": "Hz",
                    "reference_value": round(f_gen, 6),
                    "reference_u": round(u_freq, 6),
                },
                {
                    "instrument": "signal generator",
                    "model": gen["model"],
                    "calibration": gen["calibration"],
                    "quantity": "amplitude",
                    "unit": "V",
                    "reference_value": round(amplitude_v, 6),
                    "reference_u": round(u_amp, 6),
                },
            ],
            notes=(
                f"sine sweep point: {cycles} cycles in {n} samples "
                f"(f_gen = {f_gen:.4f} Hz, nominal {point['label_hz']} Hz)"
            ),
        )
        print(f"S2 sweep-{point['label_hz']:04d}hz (f_gen = {f_gen:.4f} Hz):")
        emit(OUT / f"sweep-{point['label_hz']:04d}hz.csv", data, meta)


if __name__ == "__main__":
    generate()
