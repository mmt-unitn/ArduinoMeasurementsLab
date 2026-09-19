"""GUM uncertainty budgets, and the small quantities every experiment needs.

Definitions follow JCGM 100:2008; the divisor table and the combination rule
are written out in `data-format/index.qmd` and mirrored in
`shared/R/labtools.R`.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

import pandas as pd

#: Standard uncertainty is `half_width / DIVISORS[distribution]`, except for
#: "normal", whose divisor is the component's own coverage factor.
DIVISORS = {
    "rectangular": math.sqrt(3.0),
    "triangular": math.sqrt(6.0),
    "u-shaped": math.sqrt(2.0),
    "arcsine": math.sqrt(2.0),
}

DEFAULT_NORMAL_COVERAGE = 2.0


class Budget(NamedTuple):
    """One uncertainty budget: the per-component table and its totals."""

    table: pd.DataFrame
    u_c: float
    k: float
    U: float
    unit: str

    def to_markdown(self, digits: int = 4, caption: Optional[str] = None,
                    label: Optional[str] = None) -> str:
        """A markdown table plus the two total lines, for `#| output: asis`.

        Written out by hand rather than through `DataFrame.to_markdown()`,
        which needs `tabulate`, so that the R implementation can produce the
        same string with no extra dependency either.
        """
        header = ["Quantity", "Value", "Unit", "Distribution",
                  "$u(x_i)$", "$c_i$", "$\\lvert c_i\\rvert u(x_i)$",
                  "$h_i$ (%)"]
        lines = []
        if caption:
            lines.append(f": {caption}" + (f" {{#{label}}}" if label else ""))
            lines.append("")
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        for row in self.table.to_dict("records"):
            lines.append("| " + " | ".join([
                str(row["quantity"]),
                f"{row['value']:.{digits}g}",
                str(row["unit"]),
                str(row["distribution"]),
                f"{row['u']:.{digits}g}",
                f"{row['c']:.{digits}g}",
                f"{row['cu']:.{digits}g}",
                f"{row['index_pct']:.1f}",
            ]) + " |")
        unit = f"\\ \\mathrm{{{self.unit}}}" if self.unit else ""
        lines += [
            "",
            f"Combined standard uncertainty $u_c = {self.u_c:.{digits}g}{unit}$. "
            f"Expanded uncertainty $U = k\\,u_c = {self.U:.{digits}g}{unit}$ "
            f"with $k = {self.k:g}$.",
            "",
        ]
        return "\n".join(lines)

    def _repr_markdown_(self) -> str:  # pragma: no cover - notebook convenience
        return self.to_markdown()


def lsb_volts(meta: Dict[str, Any]) -> float:
    """Volts per ADC code for the board that produced a run.

    The driver's own conversion: full scale is `2**adc_bits - 1`.
    """
    board = meta.get("board") or {}
    bits = board.get("adc_bits")
    vref_mv = board.get("vref_mV")
    if bits is None or vref_mv is None:
        raise ValueError("sidecar has no board.adc_bits / board.vref_mV")
    return float(vref_mv) / 1000.0 / (2 ** int(bits) - 1)


def u_quantization(step: float) -> float:
    """Standard uncertainty of a value rounded to a grid of `step`.

    A rectangular distribution of half-width `step/2`, so `step/sqrt(12)`.
    Applies equally to an ADC code and to an integer microsecond timestamp.
    """
    return float(step) / math.sqrt(12.0)


def enob(fsr: float, sigma_r: float) -> float:
    """Effective number of bits from the residual standard deviation.

    `log2(FSR / (sqrt(12) * sigma_r))`, the IEEE Std 1241 / 1057 definition
    with `sigma_r` the RMS residual of a fitted sine.
    """
    if sigma_r <= 0:
        raise ValueError("sigma_r must be positive")
    return math.log2(float(fsr) / (math.sqrt(12.0) * float(sigma_r)))


def _standard_uncertainty(component: Dict[str, Any]) -> float:
    distribution = str(component.get("distribution", "type-a")).strip().lower()
    if distribution in ("type-a", "type a", "normal-std", "std"):
        std = component.get("std")
        if std is None:
            raise ValueError(
                f"component {component.get('quantity')!r}: a type-a component "
                "needs 'std'"
            )
        return abs(float(std))

    half_width = component.get("half_width")
    if half_width is None:
        std = component.get("std")
        if std is not None:
            return abs(float(std))
        raise ValueError(
            f"component {component.get('quantity')!r}: needs 'half_width' "
            f"for a {distribution} distribution"
        )
    half_width = abs(float(half_width))

    if distribution == "normal":
        coverage = float(component.get("coverage", DEFAULT_NORMAL_COVERAGE))
        if coverage <= 0:
            raise ValueError("coverage must be positive")
        return half_width / coverage
    if distribution in DIVISORS:
        return half_width / DIVISORS[distribution]
    raise ValueError(
        f"unknown distribution {distribution!r}; use one of: normal, "
        "rectangular, triangular, u-shaped, type-a"
    )


def budget_table(components: Sequence[Dict[str, Any]], k: float = 2.0,
                 unit: Optional[str] = None) -> Budget:
    """Builds a GUM uncertainty budget from a list of components.

    Each component is a mapping with `quantity`, optionally `value` and `unit`,
    a `distribution` (see the module docstring), the parameter that
    distribution needs (`half_width`, or `std` for `type-a`), and a
    `sensitivity` coefficient `c_i` that defaults to 1.

    Inputs are assumed uncorrelated and the model linear at the operating
    point; both assumptions belong in the text of any handbook that uses this.
    """
    if not components:
        raise ValueError("a budget needs at least one component")

    rows: List[Dict[str, Any]] = []
    for component in components:
        u_i = _standard_uncertainty(component)
        c_i = float(component.get("sensitivity", 1.0))
        rows.append({
            "quantity": str(component.get("quantity", "")),
            "value": float(component.get("value", 0.0)),
            "unit": str(component.get("unit", unit or "")),
            "distribution": str(component.get("distribution", "type-a")),
            "u": u_i,
            "c": c_i,
            "cu": abs(c_i * u_i),
        })

    u_c = math.sqrt(sum(row["cu"] ** 2 for row in rows))
    for row in rows:
        row["index_pct"] = 100.0 * (row["cu"] ** 2) / (u_c ** 2) if u_c > 0 else 0.0

    table = pd.DataFrame(rows, columns=[
        "quantity", "value", "unit", "distribution", "u", "c", "cu", "index_pct",
    ])
    if unit is None:
        units = [u for u in table["unit"].unique() if u]
        unit = units[0] if len(units) == 1 else ""
    return Budget(table=table, u_c=u_c, k=float(k), U=float(k) * u_c, unit=unit)
