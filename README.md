# tia — Test Impact Analysis for pytest

Run only the tests your changes actually affect. Big suites spend most
of their CI time re-running tests that couldn't possibly have broken;
`tia` builds a per-test coverage map once, then uses your `git diff` to
select the minimal set of tests to run.

This is the same idea Google/Meta run internally (Test Impact Analysis).

## How it works

1. **`tia record`** runs the full suite once with a pytest plugin that
   switches coverage.py's *dynamic context* to each test's nodeid. The
   result is a map: `{test -> {file -> lines it executed}}`, saved to
   `.tia/map.json`.
2. **`tia run`** diffs your working tree against a git ref (default
   `HEAD`), reading the **old side** of each hunk so the changed line
   numbers live in the same coordinate system the map was recorded in.
3. It selects tests by three rules (see `tia/select.py`) and runs only
   those via pytest.

## Selection rules

1. **Line hit** — a changed line is one the test executed. Precise.
2. **Module-level fallback** — a changed line *inside a file's covered
   span* that no test ran (a module constant, a `def` signature, an
   import). Conservatively runs every test touching that file. Bounded
   by the covered span, so trailing/appended code doesn't drag the
   whole file in.
3. **New test** — any collected test not in the map has never been
   measured, so it always runs.

## Usage

```sh
pip install -e .

tia record [PATH]          # build the map (run from the repo root)
tia run [PATH]             # run only affected tests
tia run --since main       # diff against another ref
tia run --list             # show the selection, don't run
tia status                 # summarize the recorded map
```

Run from the repository root (where `pyproject.toml` / `.git` live) so
nodeids and file paths stay consistent.

## Known limitations (honest list)

- **Insertion anchoring.** Inserting lines is anchored to the
  surrounding old line, which can pull in one extra test that executed
  that line. False positives, never false negatives.
- **Coordinate sync.** The map must be recorded at the ref you diff
  against. Re-run `tia record` after you commit so the line numbers
  re-sync.
- **`def`-line-only edits.** Changing only a function's signature line
  (e.g. a default argument) executes at import time, not under a test,
  so it falls back to file-level selection rather than the one test.
- **Dynamic dispatch / reflection / subprocesses** aren't traced by
  coverage and can hide a real dependency. Re-record periodically and
  run the full suite on a cadence as a safety net.

## Demo

`examples/calc/` is a tiny suite that proves the behavior end to end.
See the scenarios in the project history.
