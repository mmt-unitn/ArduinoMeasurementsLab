"""Tests for the Python labtools.

The expected numbers here are the same constants the R suite checks in
tests/testthat/test-labtools.R. They are written out rather than recomputed
from the implementation, which is the only way the two implementations can be
shown to agree rather than merely to agree with themselves.
"""

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "python"))

from labtools import (  # noqa: E402
    budget_table,
    enob,
    lsb_volts,
    pivot_channels,
    read_run,
    synthetic_banner,
    timing_summary,
    u_quantization,
    validate_meta,
    write_run,
)

FIXTURES = ROOT / "tests" / "fixtures"

# --- shared constants, checked identically by the R suite ------------------

LSB = 5.035477225909819e-05          # 3.3 V / (2**16 - 1)
VOLTS_FIRST = 0.40787365529869535    # raw 8100 through eq-to-volts

T_N = 12
T_DURATION_S = 0.012502
T_MEAN_INTERVAL = 1136.5454545454545
T_SD_INTERVAL = 451.8788252698645
T_MEDIAN_INTERVAL = 1001.0
T_MIN_INTERVAL = 998.0
T_MAX_INTERVAL = 2499.0
T_ACHIEVED_RATE = 879.8592225243962
T_PERIOD = 1188.8181818181818
T_T0 = 711.833333333333
T_RESID_RMS = 370.6675216635007
T_RESID_MAX = 655.2575757575769
T_N_LONG = 1

B_U1 = 0.0002
B_U2 = 2.8867513459481293e-05
B_U3 = 0.00012
B_UC = 0.00023501772982763094
B_U_EXPANDED = 0.0004700354596552619
B_INDEX = [72.42003621001811, 1.5087507543753778, 26.071213035606522]

REL = 1e-9


# --- read_run --------------------------------------------------------------

def test_read_run_from_csv_path():
    run = read_run(FIXTURES / "run-ok.csv")
    assert list(run.data.columns) == ["t_us", "pin", "raw", "volts"]
    assert len(run.data) == 24
    assert run.meta["experiment"] == "TEST"
    assert run.meta["synthetic"] is True


def test_read_run_accepts_yaml_path_and_bare_stem():
    from_csv = read_run(FIXTURES / "run-ok.csv")
    from_yaml = read_run(FIXTURES / "run-ok.yaml")
    from_stem = read_run(FIXTURES / "run-ok")
    assert from_csv.data.equals(from_yaml.data)
    assert from_csv.data.equals(from_stem.data)


def test_read_run_derives_volts_with_the_driver_conversion():
    run = read_run(FIXTURES / "run-ok.csv")
    first = run.data.iloc[0]
    assert first["raw"] == 8100
    assert first["volts"] == pytest.approx(VOLTS_FIRST, rel=REL)


def test_read_run_without_sidecar_is_an_error(tmp_path):
    csv = tmp_path / "orphan.csv"
    csv.write_text("t_us,pin,raw\n1000,15,10\n")
    with pytest.raises(FileNotFoundError, match="sidecar"):
        read_run(csv)


def test_read_run_rejects_a_csv_without_samples(tmp_path):
    (tmp_path / "x.csv").write_text("t_us,pin\n1000,15\n")
    (tmp_path / "x.yaml").write_text("experiment: X\n")
    with pytest.raises(ValueError, match="'raw' or a 'volts'"):
        read_run(tmp_path / "x.csv")


# --- write_run -------------------------------------------------------------

def test_write_run_round_trips(tmp_path):
    run = read_run(FIXTURES / "run-ok.csv")
    write_run(tmp_path / "copy.csv", run.data, run.meta)
    back = read_run(tmp_path / "copy.csv")
    assert back.data[["t_us", "pin", "raw"]].equals(run.data[["t_us", "pin", "raw"]])
    assert back.meta == run.meta


def test_write_run_does_not_write_derived_volts(tmp_path):
    run = read_run(FIXTURES / "run-ok.csv")
    write_run(tmp_path / "copy.csv", run.data, run.meta)
    header = (tmp_path / "copy.csv").read_text().splitlines()[0]
    assert header == "t_us,pin,raw"


def test_write_run_puts_sidecar_keys_in_canonical_order(tmp_path):
    run = read_run(FIXTURES / "run-ok.csv")
    scrambled = dict(reversed(list(run.meta.items())))
    write_run(tmp_path / "copy.csv", run.data, scrambled)
    text = (tmp_path / "copy.yaml").read_text()
    assert text.index("experiment:") < text.index("synthetic:") < text.index("board:")


# --- validate_meta ---------------------------------------------------------

def test_validate_meta_accepts_a_complete_sidecar():
    run = read_run(FIXTURES / "run-ok.csv")
    assert validate_meta(run.meta) == []


def test_validate_meta_reports_every_problem():
    import yaml
    meta = yaml.safe_load((FIXTURES / "run-bad.yaml").read_text())
    problems = validate_meta(meta)
    joined = "\n".join(problems)
    assert "board.adc_bits" in joined
    assert "board.vref_mV" in joined
    assert "date is not an ISO date" in joined
    assert "synthetic must be true or false" in joined
    assert "channels[0].unit" in joined
    assert "pin 16 with no channels entry" in joined


# --- synthetic_banner ------------------------------------------------------

def test_synthetic_banner_warns_for_generated_data():
    run = read_run(FIXTURES / "run-ok.csv")
    banner = synthetic_banner(run.meta)
    assert banner.startswith("::: {.callout-warning}")
    assert "Synthetic data" in banner
    assert "TEST/run-ok" in banner
    assert banner.rstrip().endswith(":::")


def test_synthetic_banner_is_empty_for_real_data():
    run = read_run(FIXTURES / "run-ok.csv")
    meta = dict(run.meta, synthetic=False)
    assert synthetic_banner(meta) == ""


# --- pivot_channels --------------------------------------------------------

def test_pivot_channels_gives_one_column_per_pin():
    run = read_run(FIXTURES / "run-ok.csv")
    wide = pivot_channels(run.data, "raw")
    assert list(wide.columns) == [15, 16]
    assert len(wide) == T_N
    assert wide.iloc[0][15] == 8100
    assert wide.iloc[0][16] == 19800


# --- timing_summary --------------------------------------------------------

def test_timing_summary_interval_statistics():
    run = read_run(FIXTURES / "run-ok.csv")
    s = timing_summary(run.data).stats
    assert s["n"] == T_N
    assert s["duration_s"] == pytest.approx(T_DURATION_S, rel=REL)
    assert s["mean_interval_us"] == pytest.approx(T_MEAN_INTERVAL, rel=REL)
    assert s["sd_interval_us"] == pytest.approx(T_SD_INTERVAL, rel=REL)
    assert s["median_interval_us"] == pytest.approx(T_MEDIAN_INTERVAL, rel=REL)
    assert s["min_interval_us"] == pytest.approx(T_MIN_INTERVAL, rel=REL)
    assert s["max_interval_us"] == pytest.approx(T_MAX_INTERVAL, rel=REL)
    assert s["achieved_rate_hz"] == pytest.approx(T_ACHIEVED_RATE, rel=REL)


def test_timing_summary_grid_fit():
    run = read_run(FIXTURES / "run-ok.csv")
    s = timing_summary(run.data).stats
    assert s["period_us"] == pytest.approx(T_PERIOD, rel=REL)
    assert s["t0_us"] == pytest.approx(T_T0, rel=REL)
    assert s["residual_rms_us"] == pytest.approx(T_RESID_RMS, rel=REL)
    assert s["residual_max_abs_us"] == pytest.approx(T_RESID_MAX, rel=REL)
    assert s["n_long_intervals"] == T_N_LONG


def test_timing_summary_uses_distinct_timestamps_only():
    """Two pins share every timestamp: the grid has 12 points, not 24."""
    run = read_run(FIXTURES / "run-ok.csv")
    both = timing_summary(run.data)
    one = timing_summary(run.data, pin=15)
    assert both.stats["n"] == one.stats["n"] == T_N
    assert both.intervals_us.size == T_N - 1
    assert both.residuals_us.size == T_N


def test_timing_summary_residuals_are_a_perfect_grid_when_the_grid_is_perfect():
    import pandas as pd
    t = list(range(0, 10_000, 500))
    frame = pd.DataFrame({"t_us": t, "pin": 15, "raw": 0})
    s = timing_summary(frame).stats
    assert s["period_us"] == pytest.approx(500.0, rel=REL)
    assert s["residual_rms_us"] == pytest.approx(0.0, abs=1e-9)
    assert s["n_long_intervals"] == 0


def test_timing_summary_needs_three_timestamps():
    import pandas as pd
    frame = pd.DataFrame({"t_us": [0, 1000], "pin": 15, "raw": 0})
    with pytest.raises(ValueError, match="at least 3"):
        timing_summary(frame)


# --- small quantities ------------------------------------------------------

def test_lsb_volts_is_the_driver_conversion():
    run = read_run(FIXTURES / "run-ok.csv")
    assert lsb_volts(run.meta) == pytest.approx(LSB, rel=REL)


def test_u_quantization():
    assert u_quantization(1.0) == pytest.approx(1.0 / math.sqrt(12.0), rel=REL)
    assert u_quantization(LSB) == pytest.approx(LSB / math.sqrt(12.0), rel=REL)


def test_enob():
    assert enob(3.3, 1e-3) == pytest.approx(9.8957690587726, rel=REL)


def test_enob_rejects_a_non_positive_sigma():
    with pytest.raises(ValueError):
        enob(3.3, 0.0)


# --- budget_table ----------------------------------------------------------

def _components():
    return [
        dict(quantity="Reference voltage", value=2.5, unit="V",
             distribution="normal", half_width=0.0004, coverage=2,
             sensitivity=1.0),
        dict(quantity="Quantization", value=0.0, unit="V",
             distribution="rectangular", half_width=0.00005, sensitivity=1.0),
        dict(quantity="Repeatability", value=0.0, unit="V",
             distribution="type-a", std=0.00012, sensitivity=1.0),
    ]


def test_budget_table_standard_uncertainties():
    budget = budget_table(_components())
    u = list(budget.table["u"])
    assert u[0] == pytest.approx(B_U1, rel=REL)
    assert u[1] == pytest.approx(B_U2, rel=REL)
    assert u[2] == pytest.approx(B_U3, rel=REL)


def test_budget_table_combination():
    budget = budget_table(_components(), k=2)
    assert budget.u_c == pytest.approx(B_UC, rel=REL)
    assert budget.U == pytest.approx(B_U_EXPANDED, rel=REL)
    assert budget.k == 2
    assert budget.unit == "V"


def test_budget_table_indices_sum_to_one_hundred():
    budget = budget_table(_components())
    indices = list(budget.table["index_pct"])
    for got, expected in zip(indices, B_INDEX):
        assert got == pytest.approx(expected, rel=REL)
    assert sum(indices) == pytest.approx(100.0, rel=1e-12)


def test_budget_table_sensitivity_scales_the_contribution():
    components = _components()
    components[0]["sensitivity"] = 2.0
    budget = budget_table(components)
    assert budget.table["cu"][0] == pytest.approx(2.0 * B_U1, rel=REL)


def test_budget_table_divisors():
    budget = budget_table([
        dict(quantity="tri", distribution="triangular", half_width=0.006),
        dict(quantity="u", distribution="u-shaped", half_width=0.006),
    ])
    u = list(budget.table["u"])
    assert u[0] == pytest.approx(0.006 / math.sqrt(6.0), rel=REL)
    assert u[1] == pytest.approx(0.006 / math.sqrt(2.0), rel=REL)


def test_budget_table_rejects_an_unknown_distribution():
    with pytest.raises(ValueError, match="unknown distribution"):
        budget_table([dict(quantity="x", distribution="lognormal",
                           half_width=1.0)])


def test_budget_table_rejects_a_type_a_component_without_std():
    with pytest.raises(ValueError, match="needs 'std'"):
        budget_table([dict(quantity="x", distribution="type-a")])


def test_budget_table_markdown_carries_the_totals():
    text = budget_table(_components()).to_markdown()
    assert "$h_i$ (%)" in text
    assert "Combined standard uncertainty" in text
    assert "Expanded uncertainty" in text


# --- timestamps past 32 bits ------------------------------------------------
#
# A device that has been up for more than 35.8 minutes reports t_us above
# 2**31 - 1. R's integers are 32-bit signed, so as.integer() silently returns
# NA there; both implementations must carry these values exactly. 3e9 us is
# only 50 minutes of uptime, and the driver's clock runs to 49.7 days.

LATE_T0 = 3_000_000_000


def test_read_run_keeps_timestamps_past_32_bits():
    run = read_run(FIXTURES / "run-late.csv")
    assert run.data["t_us"].iloc[0] == LATE_T0
    assert run.data["t_us"].max() == LATE_T0 + 5000
    assert run.data["t_us"].notna().all()


def test_timing_summary_on_late_timestamps():
    run = read_run(FIXTURES / "run-late.csv")
    s = timing_summary(run.data).stats
    assert s["n"] == 6
    assert s["mean_interval_us"] == pytest.approx(1000.0, rel=REL)
    assert s["t0_us"] == pytest.approx(float(LATE_T0), rel=1e-15)
    assert s["residual_rms_us"] == pytest.approx(0.0, abs=1e-6)


def test_pivot_channels_on_late_timestamps():
    run = read_run(FIXTURES / "run-late.csv")
    wide = pivot_channels(run.data, "raw")
    assert list(wide.columns) == [15, 16]
    assert len(wide) == 6
    assert wide.index[0] == LATE_T0
