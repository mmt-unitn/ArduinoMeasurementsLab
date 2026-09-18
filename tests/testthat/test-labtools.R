# Tests for the R labtools.
#
# The expected numbers here are the same constants tests/test_labtools.py
# checks. They are written out rather than recomputed from the
# implementation, which is the only way the two implementations can be shown
# to agree rather than merely to agree with themselves. Working directory
# during a testthat run is this file's own directory (tests/testthat/), so
# paths below are relative to it.

source("../../shared/R/labtools.R")

FIXTURES <- "../fixtures"

# --- shared constants, checked identically by the Python suite -------------

LSB <- 5.035477225909819e-05          # 3.3 V / (2**16 - 1)
VOLTS_FIRST <- 0.40787365529869535    # raw 8100 through eq-to-volts

T_N <- 12
T_DURATION_S <- 0.012502
T_MEAN_INTERVAL <- 1136.5454545454545
T_SD_INTERVAL <- 451.8788252698645
T_MEDIAN_INTERVAL <- 1001.0
T_MIN_INTERVAL <- 998.0
T_MAX_INTERVAL <- 2499.0
T_ACHIEVED_RATE <- 879.8592225243962
T_PERIOD <- 1188.8181818181818
T_T0 <- 711.833333333333
T_RESID_RMS <- 370.6675216635007
T_RESID_MAX <- 655.2575757575769
T_N_LONG <- 1

B_U1 <- 0.0002
B_U2 <- 2.8867513459481293e-05
B_U3 <- 0.00012
B_UC <- 0.00023501772982763094
B_U_EXPANDED <- 0.0004700354596552619
B_INDEX <- c(72.42003621001811, 1.5087507543753778, 26.071213035606522)

REL <- 1e-9

# --- read_run ----------------------------------------------------------

test_that("read_run reads a run from its CSV path", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  expect_equal(names(run$data), c("t_us", "pin", "raw", "volts"))
  expect_equal(nrow(run$data), 24)
  expect_equal(run$meta$experiment, "TEST")
  expect_true(isTRUE(run$meta$synthetic))
})

test_that("read_run accepts the yaml path and the bare stem", {
  from_csv <- read_run(file.path(FIXTURES, "run-ok.csv"))
  from_yaml <- read_run(file.path(FIXTURES, "run-ok.yaml"))
  from_stem <- read_run(file.path(FIXTURES, "run-ok"))
  expect_equal(from_csv$data, from_yaml$data)
  expect_equal(from_csv$data, from_stem$data)
})

test_that("read_run derives volts with the driver conversion", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  expect_equal(run$data$raw[1], 8100)
  expect_equal(run$data$volts[1], VOLTS_FIRST, tolerance = REL)
})

test_that("read_run without a sidecar is an error mentioning 'sidecar'", {
  tmp <- tempfile()
  dir.create(tmp)
  writeLines(c("t_us,pin,raw", "1000,15,10"), file.path(tmp, "orphan.csv"))
  expect_error(read_run(file.path(tmp, "orphan.csv")), regexp = "sidecar")
})

test_that("read_run rejects a csv without 'raw' or 'volts'", {
  tmp <- tempfile()
  dir.create(tmp)
  writeLines(c("t_us,pin", "1000,15"), file.path(tmp, "x.csv"))
  writeLines("experiment: X", file.path(tmp, "x.yaml"))
  expect_error(read_run(file.path(tmp, "x.csv")), regexp = "'raw' or a 'volts'")
})

# --- write_run -----------------------------------------------------------

test_that("write_run round-trips the raw columns and the metadata", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  tmp <- tempfile()
  dir.create(tmp)
  write_run(file.path(tmp, "copy.csv"), run$data, run$meta)
  back <- read_run(file.path(tmp, "copy.csv"))
  expect_equal(back$data[, c("t_us", "pin", "raw")], run$data[, c("t_us", "pin", "raw")])
  expect_equal(back$meta, run$meta)
})

test_that("write_run does not write a volts column derived from raw", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  tmp <- tempfile()
  dir.create(tmp)
  write_run(file.path(tmp, "copy.csv"), run$data, run$meta)
  header <- readLines(file.path(tmp, "copy.csv"), n = 1)
  expect_equal(header, "t_us,pin,raw")
})

test_that("write_run puts sidecar keys in canonical order", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  scrambled <- run$meta[rev(names(run$meta))]
  tmp <- tempfile()
  dir.create(tmp)
  write_run(file.path(tmp, "copy.csv"), run$data, scrambled)
  text <- paste(readLines(file.path(tmp, "copy.yaml")), collapse = "\n")
  expect_true(regexpr("experiment:", text) < regexpr("synthetic:", text))
  expect_true(regexpr("synthetic:", text) < regexpr("board:", text))
})

# --- validate_meta -----------------------------------------------------

test_that("validate_meta accepts a complete sidecar", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  expect_equal(validate_meta(run$meta), character(0))
})

test_that("validate_meta reports every problem, with the same wording as Python", {
  meta <- yaml::yaml.load_file(file.path(FIXTURES, "run-bad.yaml"))
  problems <- validate_meta(meta)
  joined <- paste(problems, collapse = "\n")
  expect_true(grepl("board.adc_bits", joined, fixed = TRUE))
  expect_true(grepl("board.vref_mV", joined, fixed = TRUE))
  expect_true(grepl("date is not an ISO date", joined, fixed = TRUE))
  expect_true(grepl("synthetic must be true or false", joined, fixed = TRUE))
  expect_true(grepl("channels[0].unit", joined, fixed = TRUE))
  expect_true(grepl("pin 16 with no channels entry", joined, fixed = TRUE))
})

# --- synthetic_banner ----------------------------------------------------

test_that("synthetic_banner warns for generated data", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  banner <- synthetic_banner(run$meta)
  expect_true(startsWith(banner, "::: {.callout-warning}"))
  expect_true(grepl("Synthetic data", banner, fixed = TRUE))
  expect_true(grepl("TEST/run-ok", banner, fixed = TRUE))
  expect_true(endsWith(trimws(banner), ":::"))
})

test_that("synthetic_banner is empty for real data", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  meta <- run$meta
  meta$synthetic <- FALSE
  expect_equal(synthetic_banner(meta), "")
})

# --- pivot_channels --------------------------------------------------------

test_that("pivot_channels gives one column per pin", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  wide <- pivot_channels(run$data, "raw")
  expect_equal(colnames(wide), c("15", "16"))
  expect_equal(nrow(wide), T_N)
  expect_equal(unname(wide[1, "15"]), 8100)
  expect_equal(unname(wide[1, "16"]), 19800)
})

# --- timing_summary --------------------------------------------------------

test_that("timing_summary computes interval statistics", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  s <- timing_summary(run$data)$stats
  expect_equal(s$n, T_N)
  expect_equal(s$duration_s, T_DURATION_S, tolerance = REL)
  expect_equal(s$mean_interval_us, T_MEAN_INTERVAL, tolerance = REL)
  expect_equal(s$sd_interval_us, T_SD_INTERVAL, tolerance = REL)
  expect_equal(s$median_interval_us, T_MEDIAN_INTERVAL, tolerance = REL)
  expect_equal(s$min_interval_us, T_MIN_INTERVAL, tolerance = REL)
  expect_equal(s$max_interval_us, T_MAX_INTERVAL, tolerance = REL)
  expect_equal(s$achieved_rate_hz, T_ACHIEVED_RATE, tolerance = REL)
})

test_that("timing_summary fits the grid", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  s <- timing_summary(run$data)$stats
  expect_equal(s$period_us, T_PERIOD, tolerance = REL)
  expect_equal(s$t0_us, T_T0, tolerance = REL)
  expect_equal(s$residual_rms_us, T_RESID_RMS, tolerance = REL)
  expect_equal(s$residual_max_abs_us, T_RESID_MAX, tolerance = REL)
  expect_equal(s$n_long_intervals, T_N_LONG)
})

test_that("timing_summary uses distinct timestamps only", {
  # Two pins share every timestamp: the grid has 12 points, not 24.
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  both <- timing_summary(run$data)
  one <- timing_summary(run$data, pin = 15)
  expect_equal(both$stats$n, T_N)
  expect_equal(one$stats$n, T_N)
  expect_equal(length(both$intervals_us), T_N - 1)
  expect_equal(length(both$residuals_us), T_N)
})

test_that("timing_summary residuals are a perfect grid when the grid is perfect", {
  t <- seq(0, 9999, by = 500)
  frame <- data.frame(t_us = t, pin = 15, raw = 0)
  s <- timing_summary(frame)$stats
  expect_equal(s$period_us, 500.0, tolerance = REL)
  expect_equal(s$residual_rms_us, 0.0, tolerance = 1e-9)
  expect_equal(s$n_long_intervals, 0)
})

test_that("timing_summary needs at least three timestamps", {
  frame <- data.frame(t_us = c(0, 1000), pin = 15, raw = 0)
  expect_error(timing_summary(frame), regexp = "at least 3")
})

# --- small quantities --------------------------------------------------

test_that("lsb_volts is the driver conversion", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  expect_equal(lsb_volts(run$meta), LSB, tolerance = REL)
})

test_that("u_quantization", {
  expect_equal(u_quantization(1.0), 1.0 / sqrt(12.0), tolerance = REL)
  expect_equal(u_quantization(LSB), LSB / sqrt(12.0), tolerance = REL)
})

test_that("enob", {
  expect_equal(enob(3.3, 1e-3), 9.8957690587726, tolerance = REL)
})

test_that("enob rejects a non-positive sigma", {
  expect_error(enob(3.3, 0.0))
})

# --- budget_table --------------------------------------------------------

.components <- function() {
  list(
    list(quantity = "Reference voltage", value = 2.5, unit = "V",
         distribution = "normal", half_width = 0.0004, coverage = 2,
         sensitivity = 1.0),
    list(quantity = "Quantization", value = 0.0, unit = "V",
         distribution = "rectangular", half_width = 0.00005, sensitivity = 1.0),
    list(quantity = "Repeatability", value = 0.0, unit = "V",
         distribution = "type-a", std = 0.00012, sensitivity = 1.0)
  )
}

test_that("budget_table computes standard uncertainties", {
  budget <- budget_table(.components())
  u <- budget$table$u
  expect_equal(u[1], B_U1, tolerance = REL)
  expect_equal(u[2], B_U2, tolerance = REL)
  expect_equal(u[3], B_U3, tolerance = REL)
})

test_that("budget_table combines the components", {
  budget <- budget_table(.components(), k = 2)
  expect_equal(budget$u_c, B_UC, tolerance = REL)
  expect_equal(budget$U, B_U_EXPANDED, tolerance = REL)
  expect_equal(budget$k, 2)
  expect_equal(budget$unit, "V")
})

test_that("budget_table indices sum to one hundred", {
  budget <- budget_table(.components())
  indices <- budget$table$index_pct
  for (i in seq_along(indices)) {
    expect_equal(indices[i], B_INDEX[i], tolerance = REL)
  }
  expect_equal(sum(indices), 100.0, tolerance = 1e-12)
})

test_that("budget_table sensitivity scales the contribution", {
  components <- .components()
  components[[1]]$sensitivity <- 2.0
  budget <- budget_table(components)
  expect_equal(budget$table$cu[1], 2.0 * B_U1, tolerance = REL)
})

test_that("budget_table divisors match the GUM table", {
  budget <- budget_table(list(
    list(quantity = "tri", distribution = "triangular", half_width = 0.006),
    list(quantity = "u", distribution = "u-shaped", half_width = 0.006)
  ))
  u <- budget$table$u
  expect_equal(u[1], 0.006 / sqrt(6.0), tolerance = REL)
  expect_equal(u[2], 0.006 / sqrt(2.0), tolerance = REL)
})

test_that("budget_table rejects an unknown distribution", {
  expect_error(
    budget_table(list(list(quantity = "x", distribution = "lognormal", half_width = 1.0))),
    regexp = "unknown distribution"
  )
})

test_that("budget_table rejects a type-a component without std", {
  expect_error(
    budget_table(list(list(quantity = "x", distribution = "type-a"))),
    regexp = "needs 'std'"
  )
})

test_that("budget_to_markdown carries the totals", {
  text <- budget_to_markdown(budget_table(.components()))
  expect_true(grepl("$h_i$ [%]", text, fixed = TRUE))
  expect_true(grepl("Combined standard uncertainty", text, fixed = TRUE))
  expect_true(grepl("Expanded uncertainty", text, fixed = TRUE))
})

# --- equivalence with the Python suite --------------------------------
#
# The real deliverable: read the shared fixture with the R implementation
# and check every number against the same hard-coded constants the Python
# suite checks (tests/test_labtools.py), not against a value recomputed by
# this same implementation.

test_that("timing_summary on the shared fixture matches the Python constants", {
  run <- read_run(file.path(FIXTURES, "run-ok.csv"))
  s <- timing_summary(run$data)$stats

  expect_equal(s$n, T_N)
  expect_equal(s$duration_s, T_DURATION_S, tolerance = REL)
  expect_equal(s$mean_interval_us, T_MEAN_INTERVAL, tolerance = REL)
  expect_equal(s$sd_interval_us, T_SD_INTERVAL, tolerance = REL)
  expect_equal(s$min_interval_us, T_MIN_INTERVAL, tolerance = REL)
  expect_equal(s$max_interval_us, T_MAX_INTERVAL, tolerance = REL)
  expect_equal(s$median_interval_us, T_MEDIAN_INTERVAL, tolerance = REL)
  expect_equal(s$achieved_rate_hz, T_ACHIEVED_RATE, tolerance = REL)
  expect_equal(s$period_us, T_PERIOD, tolerance = REL)
  expect_equal(s$t0_us, T_T0, tolerance = REL)
  expect_equal(s$residual_rms_us, T_RESID_RMS, tolerance = REL)
  expect_equal(s$residual_max_abs_us, T_RESID_MAX, tolerance = REL)
  expect_equal(s$n_long_intervals, T_N_LONG)

  expect_equal(lsb_volts(run$meta), LSB, tolerance = REL)
  expect_equal(run$data$volts[1], VOLTS_FIRST, tolerance = REL)
})

test_that("budget_table on the reference components matches the Python constants", {
  budget <- budget_table(.components(), k = 2)
  u <- budget$table$u
  expect_equal(u[1], B_U1, tolerance = REL)
  expect_equal(u[2], B_U2, tolerance = REL)
  expect_equal(u[3], B_U3, tolerance = REL)
  expect_equal(budget$u_c, B_UC, tolerance = REL)
  expect_equal(budget$U, B_U_EXPANDED, tolerance = REL)

  indices <- budget$table$index_pct
  for (i in seq_along(indices)) {
    expect_equal(indices[i], B_INDEX[i], tolerance = REL)
  }
})
