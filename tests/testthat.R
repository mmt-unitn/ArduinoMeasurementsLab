# Entry point for the R suite, run from the repository root:
#
#   cd AMLAB
#   Rscript -e 'testthat::test_dir("tests/testthat")'
#   # or, equivalently:
#   Rscript tests/testthat.R
#
# tests/testthat/test-labtools.R sources shared/R/labtools.R and checks it
# against tests/fixtures/, using paths relative to this root -- mirroring
# tests/test_labtools.py, which resolves the same root from its own file
# location.

library(testthat)

test_dir("tests/testthat", reporter = "summary")
