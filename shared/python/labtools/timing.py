"""Sampling-grid statistics: is the grid regular, and if not, how does it fail?

The definitions here are the ones written out in `data-format/index.qmd`
(section "Timing statistics") and mirrored in `shared/R/labtools.R`.
"""

from __future__ import annotations

from typing import Any, Dict, NamedTuple

import numpy as np
import pandas as pd

#: An interval longer than this multiple of the median is counted as a
#: schedule restart rather than as jitter.
LONG_INTERVAL_FACTOR = 1.5


class TimingSummary(NamedTuple):
    """Statistics, plus the vectors behind them for plotting."""

    stats: Dict[str, float]
    intervals_us: np.ndarray
    residuals_us: np.ndarray

    def __repr__(self) -> str:  # pragma: no cover - convenience only
        items = ", ".join(f"{k}={v:.4g}" for k, v in self.stats.items())
        return f"TimingSummary({items})"


def pivot_channels(data: pd.DataFrame, value: str = "volts") -> pd.DataFrame:
    """One row per timestamp, one column per pin.

    Samples of one record share a timestamp, so this is the natural shape for
    anything that compares channels.
    """
    if value not in data.columns:
        raise ValueError(f"no '{value}' column; have {list(data.columns)}")
    wide = data.pivot_table(index="t_us", columns="pin", values=value,
                            aggfunc="mean")
    wide.columns = [int(c) for c in wide.columns]
    return wide.sort_index()


def timing_summary(data, pin=None) -> TimingSummary:
    """Interval statistics and the least-squares grid fit.

    `data` is a run's data frame (or a bare sequence of timestamps). With
    several pins streamed together the timestamps repeat, one per sample in a
    record, so the distinct values are used -- or those of a single `pin`.
    """
    if isinstance(data, pd.DataFrame):
        frame = data if pin is None else data[data["pin"] == pin]
        t = np.asarray(sorted(pd.unique(frame["t_us"])), dtype=np.float64)
    else:
        t = np.asarray(sorted(set(np.asarray(data).ravel().tolist())),
                       dtype=np.float64)

    n = int(t.size)
    if n < 3:
        raise ValueError(f"need at least 3 distinct timestamps, got {n}")

    intervals = np.diff(t)
    duration_s = float(t[-1] - t[0]) / 1e6

    # Ordinary least squares of t_i on the index i, written out rather than
    # delegated so the R implementation can match it exactly.
    idx = np.arange(n, dtype=np.float64)
    i_mean = idx.mean()
    t_mean = t.mean()
    period = float(((idx - i_mean) * (t - t_mean)).sum()
                   / ((idx - i_mean) ** 2).sum())
    t0 = float(t_mean - period * i_mean)
    residuals = t - (t0 + idx * period)

    median_interval = float(np.median(intervals))
    stats: Dict[str, Any] = {
        "n": n,
        "duration_s": duration_s,
        "mean_interval_us": float(intervals.mean()),
        "sd_interval_us": float(intervals.std(ddof=1)),
        "min_interval_us": float(intervals.min()),
        "max_interval_us": float(intervals.max()),
        "median_interval_us": median_interval,
        "achieved_rate_hz": float((n - 1) / duration_s) if duration_s > 0 else float("nan"),
        "period_us": period,
        "t0_us": t0,
        "residual_rms_us": float(np.sqrt(np.mean(residuals ** 2))),
        "residual_max_abs_us": float(np.max(np.abs(residuals))),
        "n_long_intervals": int(
            np.count_nonzero(intervals > LONG_INTERVAL_FACTOR * median_interval)
        ),
    }
    return TimingSummary(stats=stats, intervals_us=intervals, residuals_us=residuals)
