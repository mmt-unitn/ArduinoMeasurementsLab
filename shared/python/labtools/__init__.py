"""labtools - readers and uncertainty helpers for AMLab experiments.

One acquisition is one CSV plus one YAML sidecar with the same stem; see
`data-format/index.qmd` for the specification these functions implement.

The R implementation in `shared/R/labtools.R` mirrors this one function for
function, name for name, and definition for definition: a number computed here
and the same number computed there must agree. The test suites check it.
"""

from .gum import Budget, budget_table, enob, lsb_volts, u_quantization
from .io import Run, read_run, synthetic_banner, validate_meta, write_run
from .timing import TimingSummary, pivot_channels, timing_summary

__version__ = "0.1.0"

__all__ = [
    "Run",
    "read_run",
    "write_run",
    "validate_meta",
    "synthetic_banner",
    "pivot_channels",
    "TimingSummary",
    "timing_summary",
    "lsb_volts",
    "u_quantization",
    "enob",
    "Budget",
    "budget_table",
]
