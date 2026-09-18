# labtools.R - readers and uncertainty helpers for AMLab experiments.
#
# One acquisition is one CSV plus one YAML sidecar with the same stem; see
# `data-format/index.qmd` for the specification these functions implement.
#
# This is the R mirror of `shared/python/labtools`: same functions, same
# names, same numerical definitions. A number computed in Python and the same
# number computed here must agree; the test suites in both languages check
# it. Sourced as a plain script -- `source(".../labtools.R")` -- not loaded
# as a package, so it depends on nothing beyond base R and `yaml`.

suppressPackageStartupMessages(library(yaml))

# Sidecar keys in the order they are written back, so a file round-tripped
# through write_run() stays readable by a human who knows the specification.
CANONICAL_ORDER <- c(
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
  "references"
)

# Dotted paths of the fields without which an analysis cannot produce a
# defensible number; see the field reference in data-format/index.qmd.
REQUIRED_FIELDS <- c(
  "experiment",
  "run",
  "date",
  "synthetic",
  "board.model",
  "board.adc_bits",
  "board.vref_mV",
  "acquisition.pins"
)

REQUIRED_CHANNEL_FIELDS <- c("pin", "quantity", "unit")

#' Resolves a CSV path, a YAML path or a bare stem to the pair.
#'
#' A sidecar may be named `.yaml` or `.yml`; an existing `.yml` wins over a
#' non-existent `.yaml`.
#' @param path A CSV path, a YAML path, or the shared stem (character).
#' @return A list with `$csv` and `$yaml` paths (character).
.stem_paths <- function(path) {
  path <- as.character(path)
  ext <- tolower(tools::file_ext(path))
  if (ext %in% c("csv", "yaml", "yml")) {
    stem <- substring(path, 1, nchar(path) - nchar(ext) - 1)
  } else {
    stem <- path
  }
  csv_path <- paste0(stem, ".csv")
  yaml_path <- paste0(stem, ".yaml")
  if (!file.exists(yaml_path) && file.exists(paste0(stem, ".yml"))) {
    yaml_path <- paste0(stem, ".yml")
  }
  list(csv = csv_path, yaml = yaml_path)
}

#' Volts per ADC code, or NULL when the sidecar does not say.
#'
#' This is the driver's own conversion (`Device::to_volts()`): full scale is
#' `2^adc_bits - 1`, not `2^adc_bits`.
#' @param meta Sidecar metadata (list).
#' @return Volts per code (numeric), or NULL.
lsb_from_meta <- function(meta) {
  board <- meta$board
  if (is.null(board)) return(NULL)
  bits <- board$adc_bits
  vref_mv <- board$vref_mV
  if (is.null(bits) || is.null(vref_mv)) return(NULL)
  full_scale <- 2^as.integer(bits) - 1
  if (full_scale <= 0) return(NULL)
  as.numeric(vref_mv) / 1000 / full_scale
}

#' Reads a CSV + YAML pair.
#'
#' `path` may be either half of the pair or the shared stem. When the CSV
#' carries `raw` and the sidecar has `board.adc_bits` and `board.vref_mV`, a
#' `volts` column is added. When it carries `volts`, `raw` is not invented.
#' @param path A CSV path, a YAML path, or the shared stem.
#' @return A list with `$data` (data.frame: t_us, pin, raw, volts) and
#'   `$meta` (list from the YAML).
read_run <- function(path) {
  paths <- .stem_paths(path)
  csv_path <- paths$csv
  yaml_path <- paths$yaml
  if (!file.exists(csv_path)) {
    stop(sprintf("no samples at %s", csv_path))
  }
  if (!file.exists(yaml_path)) {
    stop(sprintf(
      "no sidecar at %s: a CSV without its sidecar is not a run", yaml_path
    ))
  }

  data <- utils::read.csv(csv_path, stringsAsFactors = FALSE)
  meta <- yaml::yaml.load_file(yaml_path)
  if (is.null(meta)) meta <- list()

  missing_cols <- setdiff(c("t_us", "pin"), names(data))
  if (length(missing_cols) > 0) {
    stop(sprintf(
      "%s: missing column(s) %s", csv_path, paste(missing_cols, collapse = ", ")
    ))
  }
  if (!("raw" %in% names(data)) && !("volts" %in% names(data))) {
    stop(sprintf("%s: needs a 'raw' or a 'volts' column", csv_path))
  }

  # t_us is a DOUBLE, not an integer: R's integers are 32-bit signed, so
  # as.integer() silently returns NA above 2147483647 us -- only 35.8 minutes
  # of device uptime, while the driver rebuilds a 64-bit microsecond clock
  # good for 49.7 days. A double represents every integer up to 2^53 exactly,
  # which is 285 years of microseconds, and needs no extra package.
  data$t_us <- as.numeric(data$t_us)
  data$pin <- as.integer(data$pin)
  if ("raw" %in% names(data)) {
    data$raw <- as.integer(data$raw)
    if (!("volts" %in% names(data))) {
      lsb <- lsb_from_meta(meta)
      if (!is.null(lsb)) data$volts <- data$raw * lsb
    }
  }

  list(data = data, meta = meta)
}

#' Writes a CSV + YAML pair, sidecar keys in canonical order.
#'
#' Only the raw columns are written: `t_us,pin` and whichever of `raw` and
#' `volts` the frame carries -- a `volts` column derived from `raw` on read
#' is not written back, because it is not raw.
#' @param path A CSV path, a YAML path, or the shared stem.
#' @param data A data.frame with t_us, pin, and raw and/or volts.
#' @param meta Sidecar metadata (list).
#' @return A list with `$csv` and `$yaml` paths, invisibly.
write_run <- function(path, data, meta) {
  paths <- .stem_paths(path)
  csv_path <- paths$csv
  yaml_path <- paths$yaml
  out_dir <- dirname(csv_path)
  if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE)

  columns <- c("t_us", "pin")
  if ("raw" %in% names(data)) {
    columns <- c(columns, "raw")
  } else if ("volts" %in% names(data)) {
    columns <- c(columns, "volts")
  } else {
    stop("data needs a 'raw' or a 'volts' column")
  }
  utils::write.csv(data[, columns, drop = FALSE], file = csv_path,
                    row.names = FALSE, quote = FALSE)

  # Same reordering as the Python side: canonical keys first, in canonical
  # order, then whatever else the caller put in meta, in its own order.
  ordered <- list()
  for (k in CANONICAL_ORDER) if (k %in% names(meta)) ordered[[k]] <- meta[[k]]
  remaining <- setdiff(names(meta), names(ordered))
  for (k in remaining) ordered[[k]] <- meta[[k]]

  yaml::write_yaml(ordered, yaml_path)
  invisible(list(csv = csv_path, yaml = yaml_path))
}

#' Looks up a dotted path in a nested list, or NULL when any step is absent.
#' @param meta A (possibly nested) list.
#' @param dotted A dotted path, e.g. "board.adc_bits".
#' @return The value found, or NULL.
.get_path <- function(meta, dotted) {
  node <- meta
  for (key in strsplit(dotted, ".", fixed = TRUE)[[1]]) {
    if (!is.list(node) || is.null(node[[key]])) return(NULL)
    node <- node[[key]]
  }
  node
}

#' Returns one message per problem; an empty vector means the sidecar is
#' complete enough to analyse.
#' @param meta Sidecar metadata (list).
#' @return character vector of problems, `character(0)` when there are none.
validate_meta <- function(meta) {
  problems <- character(0)
  for (field in REQUIRED_FIELDS) {
    if (is.null(.get_path(meta, field))) {
      problems <- c(problems, paste0("missing required field: ", field))
    }
  }

  synthetic <- meta$synthetic
  if (!is.null(synthetic) && !is.logical(synthetic)) {
    problems <- c(problems, "synthetic must be true or false, not a string")
  }

  date <- meta$date
  if (!is.null(date) && !inherits(date, "Date") && !inherits(date, "POSIXt")) {
    parsed <- tryCatch(
      as.Date(as.character(date), format = "%Y-%m-%d", tryFormats = "%Y-%m-%d"),
      error = function(e) NA
    )
    if (is.na(parsed) || as.character(parsed) != as.character(date)) {
      problems <- c(problems, sprintf(
        "date is not an ISO date (YYYY-MM-DD): %s", deparse(date)
      ))
    }
  }

  channels <- meta$channels
  if (is.null(channels)) {
    problems <- c(problems, "missing required field: channels")
  } else if (!is.list(channels) || length(channels) == 0) {
    problems <- c(problems, "channels must be a non-empty list")
  } else {
    for (i in seq_along(channels)) {
      channel <- channels[[i]]
      idx <- i - 1L  # Python's message uses a 0-based index; keep it so the
                     # two languages emit the identical string.
      if (!is.list(channel)) {
        problems <- c(problems, sprintf("channels[%d] is not a mapping", idx))
        next
      }
      for (field in REQUIRED_CHANNEL_FIELDS) {
        if (is.null(channel[[field]])) {
          problems <- c(problems, sprintf(
            "missing required field: channels[%d].%s", idx, field
          ))
        }
      }
    }
  }

  pins <- .get_path(meta, "acquisition.pins")
  if (!is.null(pins) && is.list(channels)) {
    described <- unlist(lapply(channels, function(c) {
      if (is.list(c)) c$pin else NULL
    }))
    for (pin in pins) {
      if (!(pin %in% described)) {
        problems <- c(problems, sprintf(
          "acquisition.pins has pin %d with no channels entry", as.integer(pin)
        ))
      }
    }
  }

  problems
}

#' A Quarto warning callout when the run is generated, empty otherwise.
#'
#' Emit it from a chunk with `#| output: asis`.
#' @param meta Sidecar metadata (list).
#' @return A markdown string, or `""` when `synthetic` is not TRUE.
synthetic_banner <- function(meta) {
  if (!isTRUE(meta$synthetic)) return("")
  experiment <- if (is.null(meta$experiment)) "this experiment" else meta$experiment
  run <- if (is.null(meta$run)) "" else meta$run
  label <- if (nzchar(run)) paste0(experiment, "/", run) else as.character(experiment)
  paste0(
    "::: {.callout-warning}\n",
    "## Synthetic data\n",
    "The figures and numbers below come from generated reference data ",
    "(`", label, "`), not from a measurement. They exist so this template ",
    "renders without hardware and so continuous integration can check it. ",
    "Replace the data with your own run before drawing any conclusion ",
    "about a real board.\n",
    ":::\n"
  )
}

#' One row per timestamp, one column per pin.
#'
#' Samples of one record share a timestamp, so this is the natural shape for
#' anything that compares channels.
#' @param data A run's data.frame.
#' @param value Which column to spread across pins ("volts" by default).
#' @return A matrix, rows sorted by t_us, one column per pin (named by pin
#'   number), values averaged when a (t_us, pin) pair repeats.
pivot_channels <- function(data, value = "volts") {
  if (!(value %in% names(data))) {
    stop(sprintf("no '%s' column; have %s", value, paste(names(data), collapse = ", ")))
  }
  # Group by the *index* of each distinct timestamp rather than by the
  # timestamp itself: tapply coerces its grouping to character, and
  # as.character() on a large t_us gives "3e+09", which collapses distinct
  # timestamps into one row. The row names are written back afterwards with
  # scientific notation disabled, for the same reason.
  t_levels <- sort(unique(data$t_us))
  pin_levels <- sort(unique(data$pin))
  wide <- tapply(data[[value]],
                 list(match(data$t_us, t_levels), match(data$pin, pin_levels)),
                 FUN = mean)
  wide <- wide[order(as.numeric(rownames(wide))),
               order(as.numeric(colnames(wide))), drop = FALSE]
  rownames(wide) <- format(t_levels, scientific = FALSE, trim = TRUE)
  colnames(wide) <- as.character(pin_levels)
  # A data.frame rather than tapply's matrix, so a channel can be taken by
  # name -- wide[["15"]] -- the way the Python implementation allows wide[15].
  as.data.frame(wide, check.names = FALSE)
}

#: An interval longer than this multiple of the median is counted as a
#: schedule restart rather than as jitter.
LONG_INTERVAL_FACTOR <- 1.5

#' Interval statistics and the least-squares grid fit.
#'
#' `data` is a run's data.frame (or a bare vector of timestamps). With
#' several pins streamed together the timestamps repeat, one per sample in a
#' record, so the distinct values are used -- or those of a single `pin`.
#' @param data A run's data.frame, or a numeric vector of timestamps.
#' @param pin Restrict to one pin's timestamps (or NULL for all).
#' @return A list with `$stats` (named list), `$intervals_us`, `$residuals_us`.
timing_summary <- function(data, pin = NULL) {
  if (is.data.frame(data)) {
    frame <- if (is.null(pin)) data else data[data$pin == pin, ]
    t <- sort(unique(as.numeric(frame$t_us)))
  } else {
    t <- sort(unique(as.numeric(data)))
  }

  n <- length(t)
  if (n < 3) {
    stop(sprintf("need at least 3 distinct timestamps, got %d", n))
  }

  intervals <- diff(t)
  duration_s <- (t[n] - t[1]) / 1e6

  # Ordinary least squares of t_i on the index i, written out rather than
  # delegated so the Python implementation can match it exactly.
  idx <- seq(0, n - 1)
  i_mean <- mean(idx)
  t_mean <- mean(t)
  period <- sum((idx - i_mean) * (t - t_mean)) / sum((idx - i_mean)^2)
  t0 <- t_mean - period * i_mean
  residuals <- t - (t0 + idx * period)

  median_interval <- median(intervals)
  stats <- list(
    n = n,
    duration_s = duration_s,
    mean_interval_us = mean(intervals),
    sd_interval_us = sd(intervals),  # divisor length(intervals)-1 = n-2
    min_interval_us = min(intervals),
    max_interval_us = max(intervals),
    median_interval_us = median_interval,
    achieved_rate_hz = if (duration_s > 0) (n - 1) / duration_s else NaN,
    period_us = period,
    t0_us = t0,
    residual_rms_us = sqrt(mean(residuals^2)),
    residual_max_abs_us = max(abs(residuals)),
    n_long_intervals = as.integer(sum(intervals > LONG_INTERVAL_FACTOR * median_interval))
  )
  list(stats = stats, intervals_us = intervals, residuals_us = residuals)
}

#' Volts per ADC code for the board that produced a run.
#'
#' The driver's own conversion: full scale is `2^adc_bits - 1`.
#' @param meta Sidecar metadata (list).
#' @return Volts per code (numeric).
lsb_volts <- function(meta) {
  board <- meta$board
  bits <- if (is.null(board)) NULL else board$adc_bits
  vref_mv <- if (is.null(board)) NULL else board$vref_mV
  if (is.null(bits) || is.null(vref_mv)) {
    stop("sidecar has no board.adc_bits / board.vref_mV")
  }
  as.numeric(vref_mv) / 1000 / (2^as.integer(bits) - 1)
}

#' Standard uncertainty of a value rounded to a grid of `step`.
#'
#' A rectangular distribution of half-width `step/2`, so `step/sqrt(12)`.
#' Applies equally to an ADC code and to an integer microsecond timestamp.
#' @param step The grid spacing.
#' @return Standard uncertainty (numeric).
u_quantization <- function(step) {
  as.numeric(step) / sqrt(12.0)
}

#' Effective number of bits from the residual standard deviation.
#'
#' `log2(FSR / (sqrt(12) * sigma_r))`, the IEEE Std 1241 / 1057 definition
#' with `sigma_r` the RMS residual of a fitted sine.
#' @param fsr Full-scale range.
#' @param sigma_r RMS residual (must be positive).
#' @return ENOB in bits (numeric).
enob <- function(fsr, sigma_r) {
  if (sigma_r <= 0) {
    stop("sigma_r must be positive")
  }
  log2(as.numeric(fsr) / (sqrt(12.0) * as.numeric(sigma_r)))
}

# Standard uncertainty is `half_width / DIVISORS[[distribution]]`, except for
# "normal", whose divisor is the component's own coverage factor.
DIVISORS <- list(
  rectangular = sqrt(3.0),
  triangular = sqrt(6.0),
  `u-shaped` = sqrt(2.0),
  arcsine = sqrt(2.0)
)

DEFAULT_NORMAL_COVERAGE <- 2.0

#' Standard uncertainty of one budget component.
#' @param component A list with `distribution` and the parameter it needs.
#' @return Standard uncertainty (numeric).
.standard_uncertainty <- function(component) {
  distribution <- tolower(trimws(
    if (is.null(component$distribution)) "type-a" else as.character(component$distribution)
  ))
  quantity <- if (is.null(component$quantity)) "None" else component$quantity

  if (distribution %in% c("type-a", "type a", "normal-std", "std")) {
    std <- component$std
    if (is.null(std)) {
      stop(sprintf("component '%s': a type-a component needs 'std'", quantity))
    }
    return(abs(as.numeric(std)))
  }

  half_width <- component$half_width
  if (is.null(half_width)) {
    std <- component$std
    if (!is.null(std)) return(abs(as.numeric(std)))
    stop(sprintf(
      "component '%s': needs 'half_width' for a %s distribution", quantity, distribution
    ))
  }
  half_width <- abs(as.numeric(half_width))

  if (distribution == "normal") {
    coverage <- if (is.null(component$coverage)) DEFAULT_NORMAL_COVERAGE else as.numeric(component$coverage)
    if (coverage <= 0) stop("coverage must be positive")
    return(half_width / coverage)
  }
  if (distribution %in% names(DIVISORS)) {
    return(half_width / DIVISORS[[distribution]])
  }
  stop(sprintf(
    "unknown distribution '%s'; use one of: normal, rectangular, triangular, u-shaped, type-a",
    distribution
  ))
}

#' Builds a GUM uncertainty budget from a list of components.
#'
#' Each component is a list with `quantity`, optionally `value` and `unit`, a
#' `distribution`, the parameter that distribution needs (`half_width`, or
#' `std` for `type-a`), and a `sensitivity` coefficient `c_i` that defaults
#' to 1.
#'
#' Inputs are assumed uncorrelated and the model linear at the operating
#' point; both assumptions belong in the text of any handbook that uses this.
#' @param components A list of component lists.
#' @param k Coverage factor for the expanded uncertainty (default 2).
#' @param unit Fallback unit when components disagree or omit it.
#' @return A list with `$table`, `$u_c`, `$k`, `$U`, `$unit`.
budget_table <- function(components, k = 2.0, unit = NULL) {
  if (length(components) == 0) {
    stop("a budget needs at least one component")
  }

  rows <- vector("list", length(components))
  for (i in seq_along(components)) {
    component <- components[[i]]
    u_i <- .standard_uncertainty(component)
    c_i <- if (is.null(component$sensitivity)) 1.0 else as.numeric(component$sensitivity)
    rows[[i]] <- list(
      quantity = as.character(if (is.null(component$quantity)) "" else component$quantity),
      value = as.numeric(if (is.null(component$value)) 0.0 else component$value),
      unit = as.character(if (is.null(component$unit)) (if (is.null(unit)) "" else unit) else component$unit),
      distribution = as.character(if (is.null(component$distribution)) "type-a" else component$distribution),
      u = u_i,
      c = c_i,
      cu = abs(c_i * u_i)
    )
  }

  u_c <- sqrt(sum(sapply(rows, function(r) r$cu^2)))
  for (i in seq_along(rows)) {
    rows[[i]]$index_pct <- if (u_c > 0) 100.0 * (rows[[i]]$cu^2) / (u_c^2) else 0.0
  }

  table <- data.frame(
    quantity = vapply(rows, function(r) r$quantity, character(1)),
    value = vapply(rows, function(r) r$value, numeric(1)),
    unit = vapply(rows, function(r) r$unit, character(1)),
    distribution = vapply(rows, function(r) r$distribution, character(1)),
    u = vapply(rows, function(r) r$u, numeric(1)),
    c = vapply(rows, function(r) r$c, numeric(1)),
    cu = vapply(rows, function(r) r$cu, numeric(1)),
    index_pct = vapply(rows, function(r) r$index_pct, numeric(1)),
    stringsAsFactors = FALSE
  )

  if (is.null(unit)) {
    units <- unique(table$unit)
    units <- units[nzchar(units)]
    unit <- if (length(units) == 1) units[1] else ""
  }

  list(table = table, u_c = u_c, k = as.numeric(k), U = as.numeric(k) * u_c, unit = unit)
}

#' Formats a number the way Python's `f"{v:.{digits}g}"` does.
#'
#' `formatC(..., format = "g")` matches Python's general format once its
#' fixed-width padding is trimmed; verified against the Python output for
#' the budget numbers this module produces.
#' @param v A numeric value.
#' @param digits Number of significant digits.
#' @return A character string.
.format_g <- function(v, digits) {
  trimws(formatC(v, digits = digits, format = "g"))
}

#' A markdown table plus the two total lines, for `#| output: asis`.
#'
#' Written out by hand, in lockstep with Python's `Budget.to_markdown()`, so
#' both languages produce the same string with no extra dependency.
#' @param budget A budget as returned by `budget_table()`.
#' @param digits Significant digits for the numeric columns (default 4).
#' @param caption Optional table caption.
#' @param label Optional caption label (used only when `caption` is given).
#' @return A markdown string.
budget_to_markdown <- function(budget, digits = 4, caption = NULL, label = NULL) {
  header <- c("Quantity", "Value", "Unit", "Distribution",
              "$u(x_i)$", "$c_i$", "$\\lvert c_i\\rvert u(x_i)$", "$h_i$ [%]")
  lines <- character(0)
  if (!is.null(caption)) {
    cap_line <- paste0(": ", caption, if (!is.null(label)) paste0(" {#", label, "}") else "")
    lines <- c(lines, cap_line, "")
  }
  lines <- c(lines, paste0("| ", paste(header, collapse = " | "), " |"))
  lines <- c(lines, paste0("|", paste(rep("---|", length(header)), collapse = "")))

  table <- budget$table
  for (i in seq_len(nrow(table))) {
    row <- table[i, ]
    lines <- c(lines, paste0("| ", paste(c(
      as.character(row$quantity),
      .format_g(row$value, digits),
      as.character(row$unit),
      as.character(row$distribution),
      .format_g(row$u, digits),
      .format_g(row$c, digits),
      .format_g(row$cu, digits),
      sprintf("%.1f", row$index_pct)
    ), collapse = " | "), " |"))
  }

  unit_tex <- if (nzchar(budget$unit)) paste0("\\ \\mathrm{", budget$unit, "}") else ""
  lines <- c(lines, "", sprintf(
    "Combined standard uncertainty $u_c = %s%s$. Expanded uncertainty $U = k\\,u_c = %s%s$ with $k = %s$.",
    .format_g(budget$u_c, digits), unit_tex,
    .format_g(budget$U, digits), unit_tex,
    .format_g(budget$k, 6)
  ), "")

  paste(lines, collapse = "\n")
}
