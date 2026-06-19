# tia — Test Impact Analysis for pytest

Run only the tests your changes actually affect. Big suites spend most
of their CI time re-running tests that couldn't possibly have broken;
`tia` builds a per-test coverage map once, then uses your `git diff` to
select the minimal set of tests to run.

This is the same idea Google/Meta run internally (Test Impact Analysis).

## How it works

1. **`tia record`** runs the full suite once with a pytest plugin that
   switches coverage.py's *dynamic context* to each test's nodeid, then
   maps every executed line to its enclosing function via the AST. The
   result is a method-level map `{test -> {file -> {qualnames}}}` saved
   to `.tia/map.json`, stamped with the git ref it was recorded at.
   Because coverage is dynamic, the whole call chain a test exercises
   (controller → service → repo → utils) is captured for free.
2. **`tia run`** diffs your tree against that ref, reading the **old
   side** of each hunk (same coordinate system as the map), then parses
   each file *as it existed at that ref* (`git show`) to resolve changed
   lines to changed functions.
3. It selects tests by three rules (see `tia/select.py`) and runs only
   those via pytest.

## Selection rules

1. **Function hit** — a test executed a function whose body changed.
   Immune to line shifts elsewhere in the file (the whole point of
   going method-level).
2. **Module-level fallback** — a file had a module-level *modification*
   (constant, import, class body). Runs every test touching that file.
   Module-level *insertions* (a new function/test) are ignored so they
   don't drag the whole file in.
3. **Data dependency** — a non-`.py` file changed (config, fixture,
   template). Runs every test that *opened* that file while recording.
   These reads are captured with a `sys.addaudithook` on the `open`
   event, so dependencies coverage can't see don't become silent
   false negatives.
4. **New test** — any collected test not in the map has never been
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

- **Insertion anchoring.** Appending a function/test right after an
  existing one anchors on that one's last line, pulling it in as one
  extra test. A bounded false positive, never a false negative.
- **Coordinate sync.** The map is stamped with the ref it was recorded
  at and `run` diffs against it automatically. Re-run `tia record`
  after you commit so the map stays fresh.
- **Read deps are call-phase only.** A file opened during fixture
  setup/teardown (not the test body) isn't attributed to the test, the
  same boundary coverage uses. Files read in a subprocess or another
  thread can also be missed.
- **Dynamic dispatch / reflection / subprocesses** aren't traced by
  coverage and can hide a real dependency. Re-record periodically and
  run the full suite on a cadence as a safety net.

## Roadmap

- [x] **Method-level analysis** (AST) — done.
- [x] **Silent dependencies** — track non-`.py` files each test reads.
- [ ] **CI mode** — remote map storage + shallow-clone-safe diffing.
- [ ] **Static fallback** for dynamic dispatch / DI frameworks.

## Demo

`examples/calc/` is a tiny suite that proves the behavior end to end.
See the scenarios in the project history.
