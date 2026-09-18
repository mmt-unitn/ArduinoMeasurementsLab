"""Reading and writing an AMLab run: one CSV plus one YAML sidecar."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

import pandas as pd
import yaml

# Sidecar keys in the order they are written back, so a file round-tripped
# through write_run() stays readable by a human who knows the specification.
CANONICAL_ORDER = [
    "experiment",
    "run",
    "date",
    "operator",
    "synthetic",
    "derived_from",
    "board",
    "host",
    "driver_version",
    "binding",
    "binding_version",
    "acquisition",
    "stream_stats",
    "channels",
    "environment",
    "references",
    "generator",
]

# Dotted paths of the fields without which an analysis cannot produce a
# defensible number; see the field reference in data-format/index.qmd.
REQUIRED_FIELDS = [
    "experiment",
    "run",
    "date",
    "synthetic",
    "board.model",
    "board.adc_bits",
    "board.vref_mV",
    "acquisition.pins",
]

REQUIRED_CHANNEL_FIELDS = ["pin", "quantity", "unit"]


class Run(NamedTuple):
    """An acquisition: the samples, and everything needed to interpret them.

    Unpacks as `data, meta = read_run(...)`.
    """

    data: pd.DataFrame
    meta: Dict[str, Any]


def _stem_paths(path) -> "tuple[Path, Path]":
    """Resolves a CSV path, a YAML path or a bare stem to the pair.

    A sidecar may be named `.yaml` or `.yml`; an existing `.yml` wins over a
    non-existent `.yaml`.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        stem = p.with_suffix("")
    elif suffix in (".yaml", ".yml"):
        stem = p.with_suffix("")
    else:
        stem = p
    csv = stem.with_suffix(".csv")
    yaml_path = stem.with_suffix(".yaml")
    if not yaml_path.exists() and stem.with_suffix(".yml").exists():
        yaml_path = stem.with_suffix(".yml")
    return csv, yaml_path


def lsb_from_meta(meta: Dict[str, Any]) -> Optional[float]:
    """Volts per ADC code, or None when the sidecar does not say.

    This is the driver's own conversion (`Device::to_volts()`): full scale is
    `2**adc_bits - 1`, not `2**adc_bits`.
    """
    board = meta.get("board") or {}
    bits = board.get("adc_bits")
    vref_mv = board.get("vref_mV")
    if bits is None or vref_mv is None:
        return None
    full_scale = 2 ** int(bits) - 1
    if full_scale <= 0:
        return None
    return float(vref_mv) / 1000.0 / full_scale


def read_run(path) -> Run:
    """Reads a CSV + YAML pair.

    `path` may be either half of the pair or the shared stem. When the CSV
    carries `raw` and the sidecar has `board.adc_bits` and `board.vref_mV`, a
    `volts` column is added. When it carries `volts`, `raw` is not invented.
    """
    csv_path, yaml_path = _stem_paths(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"no samples at {csv_path}")
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"no sidecar at {yaml_path}: a CSV without its sidecar is not a run"
        )

    data = pd.read_csv(csv_path)
    with open(yaml_path, "r", encoding="utf-8") as fh:
        meta = yaml.safe_load(fh) or {}

    missing = [c for c in ("t_us", "pin") if c not in data.columns]
    if missing:
        raise ValueError(f"{csv_path}: missing column(s) {', '.join(missing)}")
    if "raw" not in data.columns and "volts" not in data.columns:
        raise ValueError(f"{csv_path}: needs a 'raw' or a 'volts' column")

    data["t_us"] = data["t_us"].astype("int64")
    data["pin"] = data["pin"].astype("int64")
    if "raw" in data.columns:
        data["raw"] = data["raw"].astype("int64")
        if "volts" not in data.columns:
            lsb = lsb_from_meta(meta)
            if lsb is not None:
                data["volts"] = data["raw"] * lsb

    return Run(data=data, meta=meta)


def write_run(path, data: pd.DataFrame, meta: Dict[str, Any]) -> "tuple[Path, Path]":
    """Writes a CSV + YAML pair, sidecar keys in canonical order.

    Only the raw columns are written: `t_us,pin` and whichever of `raw` and
    `volts` the frame carries -- a `volts` column derived from `raw` on read is
    not written back, because it is not raw.
    """
    csv_path, yaml_path = _stem_paths(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    columns = ["t_us", "pin"]
    if "raw" in data.columns:
        columns.append("raw")
    elif "volts" in data.columns:
        columns.append("volts")
    else:
        raise ValueError("data needs a 'raw' or a 'volts' column")
    data[columns].to_csv(csv_path, index=False)

    ordered = {k: meta[k] for k in CANONICAL_ORDER if k in meta}
    ordered.update({k: v for k, v in meta.items() if k not in ordered})
    with open(yaml_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(ordered, fh, sort_keys=False, default_flow_style=False,
                       allow_unicode=True)
    return csv_path, yaml_path


def _get_path(meta: Dict[str, Any], dotted: str) -> Any:
    node: Any = meta
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def validate_meta(meta: Dict[str, Any]) -> List[str]:
    """Returns one message per problem; an empty list means the sidecar is
    complete enough to analyse."""
    problems: List[str] = []
    for field in REQUIRED_FIELDS:
        if _get_path(meta, field) is None:
            problems.append(f"missing required field: {field}")

    synthetic = meta.get("synthetic")
    if synthetic is not None and not isinstance(synthetic, bool):
        problems.append("synthetic must be true or false, not a string")

    date = meta.get("date")
    if date is not None and not isinstance(date, (_dt.date, _dt.datetime)):
        try:
            _dt.date.fromisoformat(str(date))
        except ValueError:
            problems.append(f"date is not an ISO date (YYYY-MM-DD): {date!r}")

    channels = meta.get("channels")
    if channels is None:
        problems.append("missing required field: channels")
    elif not isinstance(channels, list) or not channels:
        problems.append("channels must be a non-empty list")
    else:
        for i, channel in enumerate(channels):
            if not isinstance(channel, dict):
                problems.append(f"channels[{i}] is not a mapping")
                continue
            for field in REQUIRED_CHANNEL_FIELDS:
                if channel.get(field) is None:
                    problems.append(f"missing required field: channels[{i}].{field}")

    pins = _get_path(meta, "acquisition.pins")
    if isinstance(pins, list) and isinstance(channels, list):
        described = {c.get("pin") for c in channels if isinstance(c, dict)}
        for pin in pins:
            if pin not in described:
                problems.append(f"acquisition.pins has pin {pin} with no channels entry")

    return problems


def synthetic_banner(meta: Dict[str, Any]) -> str:
    """A Quarto warning callout when the run is generated, empty otherwise.

    Emit it from a cell with `#| output: asis`.
    """
    if not meta.get("synthetic"):
        return ""
    experiment = meta.get("experiment", "this experiment")
    run = meta.get("run", "")
    label = f"{experiment}/{run}" if run else str(experiment)
    return (
        "::: {.callout-warning}\n"
        "## Synthetic data\n"
        f"The figures and numbers below come from generated reference data "
        f"(`{label}`), not from a measurement. They exist so this template "
        "renders without hardware and so continuous integration can check it. "
        "Replace the data with your own run before drawing any conclusion "
        "about a real board.\n"
        ":::\n"
    )
