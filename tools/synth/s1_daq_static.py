"""Reference data for S1 - static characterization of the DAQ.

Writes five runs into `experiments/S1-daq-static/data/`:

  cal-0v, cal-1v25, cal-2v50   200 records each at 1 kHz on one pin, at the
                               three calibration points a tier-1 bench can
                               produce -- enough for offset and gain.
  noise-linux, noise-macos     5 s at 1 kHz on two pins (the reference and a
                               shorted input), recorded on the two host
                               profiles. Same board, same firmware, same
                               request: the difference is the host.

Run it with `python tools/synth/generate.py s1`.
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

OUT = Path(__file__).resolve().parents[2] / "experiments" / "S1-daq-static" / "data"

#: Fixed per-host seed offsets. Python hashes strings with a per-process seed,
#: so deriving these from the host name would make the generator irreproducible.
HOST_SEED_OFFSET = {"linux": 11, "macos": 22}


def _adc(platform) -> AdcModel:
    return AdcModel(
        bits=platform["board"]["adc_bits"],
        vref_v=platform["board"]["vref_mV"] / 1000.0,
        **platform["adc"],
    )


def generate() -> None:
    platform = load_params("platform")
    params = load_params("s1")
    hosts = host_profiles(platform)
    adc = _adc(platform)
    board = platform["board"]
    dmm = platform["references"]["dmm"]
    vref_ic = platform["references"]["voltage_reference"]

    rng = np.random.default_rng(params["seed"])

    # --- the three calibration points ---------------------------------------
    cal = params["calibration"]
    pin = cal["pin"]
    for point in cal["points"]:
        n = cal["records_per_point"]
        timing = TimingModel(
            period_us=cal["period_us"],
            loop_period_us=platform["loop"]["period_1ch_us"],
            jitter_sd_us=hosts["linux"].jitter_sd_us,
            restart_probability=hosts["linux"].restart_probability,
            restart_extra_us=hosts["linux"].restart_extra_us,
        )
        t_us = timing.grid(n, rng)
        codes = adc.convert(np.full(n, point["value_v"]), rng)
        data = long_frame(t_us, {pin: codes})
        meta = build_meta(
            experiment="S1",
            run=f"cal-{point['name']}",
            board=board,
            host=hosts["linux"],
            pins=[pin],
            requested_period_us=cal["period_us"],
            t_us=t_us,
            channels=[Channel(
                pin=pin,
                quantity="voltage",
                unit="V",
                sensor=point["label"],
                calibration_reference="DMM at the pin",
                reference_value=point["value_v"],
                reference_u=point["u_v"],
            )],
            rng=rng,
            references=[dict(dmm), dict(vref_ic)],
            notes=f"calibration point: {point['label']}",
        )
        print(f"S1 cal-{point['name']}:")
        emit(OUT / f"cal-{point['name']}.csv", data, meta)

    # --- the long run, once per host ----------------------------------------
    noise = params["noise"]
    pin_ref = noise["pins"]["reference"]
    pin_short = noise["pins"]["shorted"]
    n = int(round(noise["seconds"] * 1e6 / noise["period_us"]))

    for host_name in ("linux", "macos"):
        host = hosts[host_name]
        # One generator stream per run, so that adding a run later does not
        # change the bytes of the ones before it.
        run_rng = np.random.default_rng(params["seed"] + HOST_SEED_OFFSET[host_name])
        timing = TimingModel(
            period_us=noise["period_us"],
            loop_period_us=platform["loop"]["period_2ch_us"],
            jitter_sd_us=host.jitter_sd_us,
            restart_probability=host.restart_probability,
            restart_extra_us=host.restart_extra_us,
        )
        t_us = timing.grid(n, run_rng)
        codes_ref = adc.convert(np.full(n, noise["reference_value_v"]), run_rng)
        codes_short = adc.convert(np.zeros(n), run_rng)
        data = long_frame(t_us, {pin_ref: codes_ref, pin_short: codes_short})
        meta = build_meta(
            experiment="S1",
            run=f"noise-{host_name}",
            board=board,
            host=host,
            pins=[pin_ref, pin_short],
            requested_period_us=noise["period_us"],
            t_us=t_us,
            channels=[
                Channel(
                    pin=pin_ref,
                    quantity="voltage",
                    unit="V",
                    sensor="voltage reference IC",
                    calibration_reference="DMM at the pin",
                    reference_value=noise["reference_value_v"],
                    reference_u=noise["reference_u_v"],
                ),
                Channel(
                    pin=pin_short,
                    quantity="voltage",
                    unit="V",
                    sensor="input shorted to GND",
                    reference_value=0.0,
                    reference_u=0.0,
                ),
            ],
            rng=run_rng,
            references=[dict(dmm), dict(vref_ic)],
            notes=f"same board and request, host = {host.os}",
        )
        print(f"S1 noise-{host_name}:")
        emit(OUT / f"noise-{host_name}.csv", data, meta)


if __name__ == "__main__":
    generate()
