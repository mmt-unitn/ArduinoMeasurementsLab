"""Shared machinery for the AMLab synthetic reference-data generators.

Every generator is seeded, so the same command always writes the same bytes and
a change in a figure is a change in the generator, never noise. Every file it
writes carries `synthetic: true` in its sidecar, and every analysis template
that reads one prints a warning banner.

The models here are deliberately *plausible*, not measured: their parameters
live in `tools/synth/params/*.yaml` and are marked as placeholders. Real
recordings will replace the generated files; the generators stay, because they
are what lets continuous integration render every template with no board
attached.
"""

from __future__ import annotations

import datetime as _dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))

from labtools import write_run  # noqa: E402

PARAMS_DIR = Path(__file__).resolve().parent / "params"

#: Written into every generated sidecar so a reader can tell which revision of
#: the generators produced a file.
GENERATOR_VERSION = "0.1.0"


def load_params(name: str) -> Dict[str, Any]:
    """Reads `tools/synth/params/<name>.yaml`."""
    with open(PARAMS_DIR / f"{name}.yaml", "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --- the board's analog input ------------------------------------------------


@dataclass
class AdcModel:
    """A plausible on-chip SAR converter behind a plausible input stage.

    `bits` and `vref_v` are what the board *reports*, and so set the code scale
    the driver converts with. `enob_noise_v` is the input-referred RMS noise
    that makes the effective resolution smaller than the nominal one -- the
    quantity S1 measures.
    """

    bits: int
    vref_v: float
    offset_v: float = 0.0
    gain_error: float = 0.0
    noise_sigma_v: float = 0.0
    inl_amplitude_v: float = 0.0

    @property
    def full_scale_code(self) -> int:
        return 2 ** self.bits - 1

    @property
    def lsb_v(self) -> float:
        """Volts per code, with the driver's `2**bits - 1` full scale."""
        return self.vref_v / self.full_scale_code

    def convert(self, volts, rng: np.random.Generator) -> np.ndarray:
        """Volts at the pin to raw ADC codes.

        Offset and gain error are applied first (they are properties of the
        input stage and the reference), then integral nonlinearity as a single
        low-order term, then noise, then quantization and clipping.
        """
        v = np.asarray(volts, dtype=np.float64)
        ideal = self.offset_v + (1.0 + self.gain_error) * v
        if self.inl_amplitude_v != 0.0:
            fraction = np.clip(ideal / self.vref_v, 0.0, 1.0)
            ideal = ideal + self.inl_amplitude_v * np.sin(2.0 * np.pi * fraction)
        if self.noise_sigma_v > 0.0:
            ideal = ideal + rng.normal(0.0, self.noise_sigma_v, size=ideal.shape)
        codes = np.rint(ideal / self.lsb_v)
        return np.clip(codes, 0, self.full_scale_code).astype(np.int64)


# --- the sampling grid -------------------------------------------------------


@dataclass
class TimingModel:
    """How the device's `micros()` timestamps depart from a perfect grid.

    Sampling runs from `loop()`, so a record lands on a loop iteration rather
    than on a timer edge: that is `jitter_sd_us`. When a record is late the
    firmware restarts the schedule from that moment instead of emitting catch-up
    records, so a hiccup appears as one long interval followed by a step in the
    grid-fit residuals -- `restart_probability` and `restart_extra_us`.

    The host operating system shows up here: a host that drains the bulk
    endpoint late makes restarts and device overruns more likely. It does not
    change the fine jitter, which is the board's own loop granularity.
    """

    period_us: float
    loop_period_us: float = 0.0
    jitter_sd_us: float = 0.0
    restart_probability: float = 0.0
    restart_extra_us: float = 0.0
    t0_us: int = 1_000_000

    def grid(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """`n` integer microsecond timestamps.

        A record is emitted by the first `loop()` iteration at or after its
        scheduled instant, so `loop_period_us` sets a *bounded* band of lateness
        -- the signature of jitter, as opposed to a slope (a wrong period) or a
        step (a restart). `jitter_sd_us` adds the finer variation of the loop
        iteration itself.
        """
        t = np.empty(n, dtype=np.float64)
        t[0] = float(self.t0_us)
        nominal = float(self.t0_us)
        for i in range(1, n):
            nominal += self.period_us
            late = (self.restart_probability > 0.0
                    and rng.random() < self.restart_probability)
            if late:
                # The record is late by a random part of the extra delay, and
                # the schedule then counts from where it actually landed.
                nominal += self.restart_extra_us * (0.5 + rng.random())
            landed = nominal
            if self.loop_period_us > 0.0:
                landed = np.ceil(landed / self.loop_period_us) * self.loop_period_us
            if self.jitter_sd_us:
                landed += rng.normal(0.0, self.jitter_sd_us)
            t[i] = landed
            if late:
                nominal = t[i]
        return np.rint(t).astype(np.int64)


# --- the sidecar -------------------------------------------------------------


@dataclass
class Channel:
    pin: int
    quantity: str
    unit: str
    sensor: str = ""
    gain: float = 1.0
    offset: float = 0.0
    filter: str = "none"
    calibration_reference: Optional[str] = None
    reference_value: Optional[float] = None
    reference_u: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "pin": self.pin,
            "quantity": self.quantity,
            "unit": self.unit,
        }
        if self.sensor:
            entry["sensor"] = self.sensor
        entry["conditioning"] = {
            "gain": self.gain,
            "offset": self.offset,
            "filter": self.filter,
        }
        if self.calibration_reference is not None:
            entry["calibration_reference"] = self.calibration_reference
        if self.reference_value is not None:
            entry["reference_value"] = float(self.reference_value)
        if self.reference_u is not None:
            entry["reference_u"] = float(self.reference_u)
        return entry


@dataclass
class HostProfile:
    """A host operating system as an influence quantity.

    The two profiles shipped with the generators are the two the platform
    documentation distinguishes: Linux, where the achieved grid is close to the
    requested one, and macOS, where schedule restarts and device overruns are
    more common.
    """

    os: str
    os_version: str
    machine: str
    hub: bool
    jitter_sd_us: float
    restart_probability: float
    restart_extra_us: float
    device_overruns_per_1000: float
    libusb_version: str = "1.0.27"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "os": self.os,
            "os_version": self.os_version,
            "machine": self.machine,
            "libusb_version": self.libusb_version,
            "hub": self.hub,
        }


def host_profiles(params: Dict[str, Any]) -> Dict[str, HostProfile]:
    return {name: HostProfile(**spec) for name, spec in params["hosts"].items()}


def build_meta(
    *,
    experiment: str,
    run: str,
    board: Dict[str, Any],
    host: HostProfile,
    pins: Sequence[int],
    requested_period_us: int,
    t_us: np.ndarray,
    channels: Iterable[Channel],
    rng: np.random.Generator,
    environment: Optional[Dict[str, Any]] = None,
    references: Optional[List[Dict[str, Any]]] = None,
    notes: str = "",
    date: str = "2026-09-18",
) -> Dict[str, Any]:
    """Assembles a sidecar for a generated run.

    `stream_stats` is filled from the host profile rather than left at zero: a
    template that refuses to draw conclusions from a lossy run has to be able to
    see a lossy run in the reference data too.
    """
    n_records = int(t_us.size)
    duration_s = float(t_us[-1] - t_us[0]) / 1e6
    achieved = (n_records - 1) / duration_s if duration_s > 0 else float("nan")
    overruns = int(rng.poisson(host.device_overruns_per_1000 * n_records / 1000.0))

    meta: Dict[str, Any] = {
        "experiment": experiment,
        "run": run,
        "date": _dt.date.fromisoformat(date),
        "operator": "tools/synth (generated)",
        "synthetic": True,
        "board": dict(board),
        "host": host.as_dict(),
        "driver_version": "0.4.0",
        "binding": "python",
        "binding_version": "0.4.0",
        "acquisition": {
            "pins": [int(p) for p in pins],
            "requested_period_us": int(requested_period_us),
            "achieved_rate_hz": round(achieved, 3),
            "duration_s": round(duration_s, 6),
        },
        "stream_stats": {
            "records": n_records,
            "device_overruns": overruns,
            "seq_gaps": 0,
            "host_drops": 0,
            "resyncs": 0,
            "stale_records": 0,
        },
        "channels": [c.as_dict() for c in channels],
        "environment": environment or {
            "ambient_temperature_C": 23.0,
            "notes": "generated, not measured",
        },
        "references": references or [],
        "generator": {
            "tool": "tools/synth",
            "version": GENERATOR_VERSION,
            "note": "Parameters are placeholders; see tools/synth/params/",
        },
    }
    if notes:
        meta["environment"]["notes"] = notes
    return meta


def long_frame(t_us: np.ndarray, per_pin: Dict[int, np.ndarray]):
    """Interleaves per-pin code arrays into the long CSV layout.

    Samples of one record share a timestamp and appear in ascending pin order,
    which is what the device sends and what `arduino-io stream --csv` writes.
    """
    import pandas as pd

    pins = sorted(per_pin)
    n = len(t_us)
    rows_t = np.repeat(t_us, len(pins))
    rows_pin = np.tile(np.array(pins, dtype=np.int64), n)
    stacked = np.column_stack([per_pin[p] for p in pins]).reshape(-1)
    return pd.DataFrame({"t_us": rows_t, "pin": rows_pin, "raw": stacked})


def emit(path: Path, data, meta: Dict[str, Any]) -> None:
    csv_path, yaml_path = write_run(path, data, meta)
    print(f"  {csv_path.relative_to(ROOT)}  ({len(data)} rows)")
    print(f"  {yaml_path.relative_to(ROOT)}")
