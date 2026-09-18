# Experiment template

Copy this folder to start a new experiment, or run `tools/new-experiment.sh`,
which copies it and substitutes the identifiers for you:

```bash
tools/new-experiment.sh S4 "Something new" signal-processing
```

The folder is excluded from the site render by `_quarto.yml`, so the
placeholders never appear on the site.

## Files

| File | Purpose |
|---|---|
| `index.qmd` | Overview page. Its front matter feeds the listing on `experiments/index.qmd` |
| `_theory.qmd` | Theory, written once, included by both the slides and the handbook |
| `_procedure.qmd` | Bench procedure, included by the handbook |
| `_instructor.qmd` | Instructor-only notes, included by the handbook inside a profile div |
| `slides.qmd` | RevealJS introduction |
| `handbook.qmd` | The handbook, rendered to HTML and to PDF via Typst |
| `analysis-py.qmd` | Python analysis template (Jupyter engine) |
| `analysis-r.qmd` | R analysis template (knitr engine) |
| `data/` | Reference data: one CSV plus one YAML sidecar per run |

The generator that writes `data/` lives in `tools/synth/`, with its parameters
in `tools/synth/params/<id>.yaml`, and is registered in
`tools/synth/generate.py`.

## Conventions that are not obvious

These were all learned the hard way while building S1. Follow them.

**Theory is written once.** `_theory.qmd` uses `###` headings and is included
by both `slides.qmd` and `handbook.qmd`. Material too detailed for slides goes
inside `::: {.content-hidden when-format="revealjs"} ... :::` rather than into
a second file. `slides.qmd` sets `slide-level: 3` so those `###` headings
become slides.

**Cross-references do not cross documents.** An analysis template cannot write
`@eq-xx-fit` for an equation defined in `_theory.qmd`, because the template
does not include it. Link instead:
`[calibration model](handbook.qmd#eq-xx-fit)`. References *within* one
document (`@tbl-`, `@fig-` defined in the same file) work normally.

**Never label an `output: asis` cell `tbl-...`.** Quarto then treats the cell
as a table float and swallows the next paragraph as its caption. Give the cell
a neutral label (`out-cal`) and put the caption in the emitted markdown:

    : Calibration points and fit residuals {#tbl-s1-cal}

**Start every emitted markdown block with a newline.** Output that begins
directly with `|` on the line after a code block is not recognised as a table.
The first character of the printed string should be `\n`.

**Hide the formatting cells, show the computing cells.** `#| echo: false` on
anything whose only job is to print a table; code visible everywhere else. The
templates are meant to be edited by students.

**Both languages, same output.** The Python and R templates must have the same
section headings, the same figure labels and the same table captions, and must
produce the same numbers. The check is mechanical: render both, extract the
tables, and diff them.

**All mathematics in LaTeX**, `$...$` and `$$...$$` with `{#eq-...}` labels.
Never Unicode glyphs, in any format.

**No invented numbers.** Anything not yet measured is
`[TODO (Paolo)]{.todo}`, which renders highlighted.
