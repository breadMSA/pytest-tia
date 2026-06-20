# tia — Test Impact Analysis for pytest

Run only the tests your changes actually affect. Big suites spend most
of their CI time re-running tests that couldn't possibly have broken;
`tia` builds a per-test coverage map once, then uses your `git diff` to
select the minimal set of tests to run.

This is the same idea Google/Meta run internally (Test Impact Analysis).

## Honest disclosure (read this first)

Test selection has exactly one cardinal sin: skipping a test that would
have failed. A tool you can't trust on that is worse than no tool. So
before any feature list, here is where this project earned — or failed to
earn — that trust.

While benchmarking on Flask, an early run reported a **median skip rate of
73%**. It looked great. It was a lie. coverage.py's default measurement
core on Python 3.12+ (`sysmon`) records only the *first* test to hit each
line and silently drops the rest, so any test that reused a shared helper
was never mapped — and changing that helper would have skipped them. The
high number was false negatives wearing a costume. We forced the C tracer
(`COVERAGE_CORE=ctrace`), every test mapped, and the number fell to the
truth: **median ~21% skip on Flask** (≈40% mean; cosmetic commits 99%+).
The toy demo could never have shown this — only a real codebase did.

So the Flask number is deliberately the *lower bound*: Flask is small and
tightly coupled, near the worst case for test selection. We'd rather
publish the honest floor than a cherry-picked ceiling. But a floor alone
is misleading too, so we measured the other end: on
[boltons](benchmark/RESULTS-boltons.md), a modular utility library, the
honest median skip on real logic changes is **~96%**. Same tool, same
rules — the variable is how decoupled your tests are. The truth is a
**range, ~21% (worst case) ↔ ~96% (modular)**, and both ends are measured
on real third-party suites, not toys. Per-commit tables and the "I
distrusted this number first" audits:
[Flask](benchmark/RESULTS.md) · [boltons](benchmark/RESULTS-boltons.md).

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

A **dynamic-safety modifier** sits on top of rules 1–2: a file flagged
at record time as using reflection (`getattr` by computed name, `eval`,
`importlib`, `__getattr__`) is widened from method-level to file-level
when it changes — coverage can't be trusted to have captured every edge
in/out of it. `--trust-dynamic` opts out. This is mitigation, not a
solution; nothing resolves dynamic dispatch precisely (it's undecidable
in general), so still run the full suite on a cadence.

A **cosmetic-change filter** sits *underneath* everything: before
selecting, a changed `.py` file whose edit is only comments, whitespace,
or docstrings is dropped entirely. The test is the AST — comments and
formatting aren't in it, docstrings are stripped before comparing — and
it compares the **old and new** trees, so uncommenting a line still
counts as real. So a "fix typo" / "add docstring" / "run black" commit on
a core file selects *nothing* instead of half the suite. `--all-changes`
opts out. Safe by construction: it only removes false positives.

The same filter also drops **type-only** edits — but only the kind that
is *provably* dead at runtime, because the common belief that "type hints
don't run" is false: `dataclasses`, `pydantic` and `attrs` all read
`__annotations__`. So it strips exactly two things: the body of an
`if TYPE_CHECKING:` block (never executed at runtime — where most hint
churn lives) and **function-local** annotations (PEP 526: never evaluated
nor stored). It deliberately *keeps* a change to a function signature or a
class/module-level annotation as real, because a dataclass field's type
genuinely changes behavior. Stripping those would be a false negative —
the one sin this tool won't commit. So "added the type hints" stops
triggering tests, without ever hiding a `dataclass` field-type change.

## Usage

```sh
pip install -e .

tia record [PATH]          # build the map (run from the repo root)
tia run [PATH]             # run only affected tests
tia run --since main       # diff against another ref
tia run --list             # show the selection, don't run
tia run --report -         # also print a Markdown summary (see CI mode)
tia status                 # summarize the recorded map

tia serve --dir ./maps     # run the bundled map store (zero deps)
tia push --to  <dir|url>   # publish the local map (for CI)
tia pull --from <dir|url>  # fetch a published map
```

Remotes for `push`/`pull`/`--remote` can be a directory, an `http(s)://`
URL (the bundled store), or native object storage: `s3://bucket/prefix`
(`pip install pytest-tia[s3]`) or `gs://bucket/prefix`
(`pytest-tia[gcs]`). The cloud SDKs are imported lazily, so the core
install stays dependency-light unless you actually use those URLs.

Run from the repository root (where `pyproject.toml` / `.git` live) so
nodeids and file paths stay consistent.

### CI mode

The map a base-branch job builds has to reach the PR job that consumes
it. Publish it to a shared remote keyed by the git ref it was recorded
at. The remote can be a **directory** (a cache volume / artifact dir), an
**`http(s)://` URL** served by the bundled store, or a native
**`s3://` / `gs://`** bucket:

```sh
# one zero-dependency map store for the team / CI (stdlib only)
python -m tia.server --dir ./tia-maps --port 8000     # or: tia serve ...

# base branch job
tia record && tia push --to s3://my-company-ci/tia-maps

# PR job — no local map needed; pulls by base ref, falls back to latest
tia run --remote s3://my-company-ci/tia-maps --since "$(git merge-base origin/main HEAD)"
```

In a GitHub Actions PR job, `tia run` auto-detects `$GITHUB_STEP_SUMMARY`
and posts a Markdown table to the check page — which files changed, the
impact of each, and exactly which tests were selected and why. No flag
needed; it's the same explanation as the terminal output, rendered for
the PR. (`--report <path>`, or `--report -` for stdout, forces it
anywhere.)

A ready-to-copy GitHub Actions workflow is in
[`examples/ci/github-actions.yml`](examples/ci/github-actions.yml).

`run` resolves the diff against line→function tables **baked into the
map** at record time, so it never needs `git show` on the base blob —
which is what makes it safe under shallow clones (`clone --depth=1`),
where that blob may not be fetched. The diff itself still needs the base
*commit*; in a shallow checkout, fetch just that ref first
(`git fetch --depth=1 origin <base-sha>`).

## Known limitations (honest list)

- **Insertion anchoring.** Appending a function/test right after an
  existing one anchors on that one's last line, pulling it in as one
  extra test. A bounded false positive, never a false negative.
- **Coordinate sync.** The map is stamped with the ref it was recorded
  at and `run` diffs against it automatically. Re-run `tia record`
  after you commit so the map stays fresh.
- **Subprocess / other-thread execution isn't traced.** We attribute a
  test's setup, call, and teardown (so fixture work and fixture file
  reads count), but code that runs in a child process or a non-measured
  thread is invisible to coverage and can hide a dependency.
- **Needs coverage's C tracer.** On Python 3.12+ coverage defaults to the
  `sysmon` core, which records only the *first* test to hit each line and
  silently drops the rest — a false negative for any shared helper. tia
  forces `COVERAGE_CORE=ctrace` during recording to get correct per-test
  contexts; don't override it back to `sysmon`.
- **Dynamic dispatch / reflection / subprocesses** aren't traced by
  coverage and can hide a real dependency. tia *detects* reflection and
  degrades to file-level there (see the dynamic-safety modifier), but a
  `getattr` target in one file pointing at a function in another is still
  beyond it. Re-record periodically and run the full suite on a cadence.

## Roadmap

- [x] **Method-level analysis** (AST) — done.
- [x] **Silent dependencies** — track non-`.py` files each test reads.
- [x] **CI mode** — remote map storage + shallow-clone-safe diffing.
- [x] **Static fallback** for dynamic dispatch / DI frameworks —
  detect-and-degrade (mitigation, not a precise solution).
- [x] **Industrialize** — zero-dep HTTP map store (`tia serve`) + GitHub
  Actions template, so adopting it is a few lines, not a project.
- [x] **Real-repo benchmark, both ends of the range** — Flask (worst
  case, ~21%, [`RESULTS.md`](benchmark/RESULTS.md)) and boltons (modular,
  ~96% on logic changes, [`RESULTS-boltons.md`](benchmark/RESULTS-boltons.md)).
  The Flask run also caught a recorder false-negative bug (the `sysmon`
  core dropping contexts).
- [x] **Cosmetic-change filter** — ignore comment/docstring/format-only
  edits so they select nothing instead of a whole file.
- [x] **Type-only filter (v1.1)** — also ignore `if TYPE_CHECKING:` and
  function-local annotation edits; keep signature/dataclass-field type
  changes (provably-safe subset only — no false negatives).
- [x] **Native S3 / GCS remotes (v1.1)** — `s3://` / `gs://` map stores
  via lazily-imported SDKs; core install stays dependency-light.
- [x] **CI step summary (v1.1)** — auto-post a Markdown impact table to
  the PR via `$GITHUB_STEP_SUMMARY`.
- [ ] **Not planned: precise cross-file dynamic dispatch.** Resolving a
  `getattr`/`eval` target across files is undecidable in general; the
  detect-and-degrade modifier plus periodic full runs is the honest
  answer, not a static call graph that drowns in edge cases.

## Demo

`examples/calc/` is a tiny suite that proves the behavior end to end.
See the scenarios in the project history.
