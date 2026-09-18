# AMLab — Arduino Measurements Lab

A [Quarto](https://quarto.org) website with laboratory activities for courses
in measurement science and technology, built on the
[ArduinoDriver](https://github.com/pbosetti/ArduinoDriver) platform: a
supported Arduino board running the `UsbIo` firmware becomes a USB
vendor-class I/O device, driven from the host by a C++20 library, the
`arduino-io` CLI, or the Python and R bindings.

Each of the nine experiments ships an introductory RevealJS presentation, a
handbook (HTML and PDF), two analysis templates (Python and R, producing the
same figures and numbers), and seeded synthetic reference data so everything
renders — and is checked in CI — with no hardware attached.

## Building the site

```bash
quarto render                                   # student site   -> _site/
quarto render --profile instructor              # instructor site -> _site-instructor/
```

The instructor build shows blocks wrapped in
`::: {.content-visible when-profile="instructor"} ... :::`. It is produced as
a CI artifact and is **never** deployed to the public site.

### Toolchain

| Tool | Version used | Notes |
|---|---|---|
| Quarto | 1.10.18 | Typst 0.15.1 is bundled; no LaTeX needed for the PDF handbooks |
| Python | 3.14 | numpy, scipy, pandas, matplotlib, pyyaml, jupyter |
| R | 4.6.1 | tidyverse, yaml, gsignal, broom, patchwork, knitr, rmarkdown, testthat |

The Jupyter engine needs to find the right interpreter. On the development
machine the environment is `~/.venv`:

```bash
export QUARTO_PYTHON="$HOME/.venv/bin/python"
quarto render
```

`pyproject.toml` pins the same dependency set for CI, which creates the
environment with [`uv`](https://docs.astral.sh/uv/); `renv.lock` does the same
for R.

### Tests

```bash
QUARTO_PYTHON="$HOME/.venv/bin/python" "$HOME/.venv/bin/python" -m pytest tests
Rscript -e 'testthat::test_dir("tests/testthat")'
```

`labtools` (Python) and `labtools.R` are behaviourally equivalent readers and
GUM helpers; the test suites check them against the same fixtures.

## Layout

```
_quarto.yml              website config; the student profile is the default
_quarto-instructor.yml   instructor profile (separate output directory)
setup/                   boards, firmware, host OS, bindings, verification
hardware/                conditioning, filtering, thermal rig, beam rig, BOM
data-format/             the CSV + YAML sidecar specification
experiments/             one folder per experiment, plus _template/
shared/                  labtools (Python and R), shared includes, bibliography
tools/synth/             seeded generators of synthetic reference data
assets/                  SCSS themes, RevealJS theme, listing template
```

## Open decisions

These follow the defaults recorded during the initial build; change them here
first, then in `_quarto.yml`.

| Decision | Current default | Note |
|---|---|---|
| Repository | `pbosetti/ArduinoMeasurementsLab` | The original plan named it `ArduinoDriver-labs`; the working directory settled it |
| Hosting | GitHub Pages via GitHub Actions | Student site only |
| Language | English only | Nothing in the layout prevents adding Italian later as a Quarto profile or a language subdirectory |
| License | Text and figures CC BY-SA 4.0; code Apache-2.0 | Same code licence as ArduinoDriver |
| PDF engine | Typst | Chosen for CI speed; switch to LaTeX only if a feature requires it |
| Python environment | `pyproject.toml`, `uv` in CI, `~/.venv` on the development machine | |
| R environment | `renv` | |
| Citation DOI | not assigned | See `about.qmd` |

## Conventions

- All mathematics is LaTeX (`$...$`, `$$...$$` with `{#eq-...}` labels), in
  every output format. Never Unicode glyph mathematics.
- Cross-references use Quarto prefixes: `fig-`, `tbl-`, `eq-`, `sec-`.
- Theory is written once, in `_theory.qmd`, and pulled into both the slides
  and the handbook with `{{< include >}}`.
- Values that depend on hardware not yet measured are marked `TODO (Paolo)`
  and rendered with a visible highlight; no number in this repository is
  invented.
- Synthetic reference data is labelled on every page that uses it, by
  `synthetic_banner()`.

## License

Text and figures: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
Code: [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0).
