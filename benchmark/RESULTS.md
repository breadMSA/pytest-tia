# Benchmark: tia on Flask (the worst case)

A real third-party suite, not a toy. Target: [pallets/flask](https://github.com/pallets/flask)
`main` (HEAD `36e4a82`), 483 tests, pytest 8, Python 3.14.

> This is the **floor**: Flask is small and tightly-coupled, near the
> worst case for test selection. For the other end of the honest range —
> a modular library where tia skips a ~96% median on real logic changes —
> see [`RESULTS-boltons.md`](RESULTS-boltons.md). The point of publishing
> both is that TIA's payoff is a *range* set by how modular your code is.

Reproduce:

```sh
git clone https://github.com/pallets/flask && cd flask
pip install -e . "pytest>=8,<9"
PYTHONPATH=/path/to/tia python /path/to/tia/benchmark/skiprate.py \
    --repo . --n 15 --pathspec src/flask
```

## Methodology

For each of the last 15 non-merge commits touching `src/flask`, we record
the map on the commit's **parent** and measure the selection for that one
commit (`tia run --since <parent>`). Recording per-commit keeps the map's
ref equal to the diff base — tia's coordinate system requires it. This is
the honest "one PR against its base" measure.

## Skip rate (how much of the suite tia skips per commit)

| commit | files | suite | selected | skip |
|--------|------:|------:|---------:|-----:|
| a29f88ce | 4 | 482 | 2 | **99.6%** |
| 3709c4a9 | 3 | 482 | 2 | **99.6%** |
| da6d075d | 6 | 482 | 2 | **99.6%** |
| 9368fb3f | 2 | 483 | 38 | 92.1% |
| 06ea505c | 4 | 483 | 303 | 37.3% |
| a411a243 | 1 | 486 | 305 | 37.2% |
| c17f3793 | 6 | 482 | 342 | 29.0% |
| 7b008869 | 1 | 486 | 426 | 12.3% |
| c77a5203 | 3 | 482 | 422 | 12.4% |
| 6a649690 | 6 | 482 | 422 | 12.4% |
| e82db2ca | 7 | 486 | 429 | 11.7% |
| eca5fd1d | 4 | 482 | 434 | 10.0% |
| fbb6f0bc | 10 | 487 | 440 | 9.7% |
| 36e4a824 | 1 | 483 | 483 | 0.0% |

```
commits measured : 14
median skip rate : 20.7%
mean skip rate   : 40.2%
```

### Honest reading

Flask is a **small, tightly-coupled** library — close to the worst case
for test selection. The result is **bimodal**: an isolated or cosmetic
change skips 90%+, while a change to core request handling correctly
pulls in most of the suite, because most tests really do exercise that
path. `36e4a82` selects everything: it edits a module imported
everywhere, so a module-level change fans out — correct, not a miss. The
payoff of *any* TIA scales with how modular your code is; on a large app
with independent feature areas it is far higher than on Flask.

### Effect of the cosmetic-change filter (乙)

These numbers have the filter **on** (the default). With `--all-changes`
the same 14 commits give a median of **12.4%** / mean **27.3%**. The gap
is three commits whose `src/flask` edits were purely docstrings/formatting:
they go from selecting ~430 tests to **2** (99.6% skip). That's not
spin — those commits change nothing a test can observe, so running ~430
of them was wasted. A real-world history (full of "fix typo" / "run
black" commits) shifts further in the filter's favour.

## The bug this benchmark caught

The first run of this exact benchmark reported a **median skip of 73%** —
which looked great and was wrong. coverage.py's default core on Python
3.12+ (`sysmon`) records only the *first* test to hit each line and drops
the rest, so 90 of 483 tests were never mapped. The high "skip rate" was
those tests being silently **mis-selected** — i.e. false negatives, the
one failure mode tia exists to prevent.

Fix: force `COVERAGE_CORE=ctrace` during recording (the C tracer records
all per-test contexts). After the fix, **483/483 tests map** and the
numbers above are real. The toy `examples/` demo could never have shown
this — each of its tests hits a unique function, so no line is shared.

## Silent dependency (②) — control

Change only `tests/templates/template_test.html` (zero lines of Python):

```
tests in suite: 483 | selected: 9
  ~ tests/templates/template_test.html: data dep (9 readers)
```

tia selects exactly the 9 tests that render that template (98% skipped).
A coverage-of-`.py` tool (testmon) or an import-graph tool (Jest-style)
sees no Python change and selects **0** — a silent miss of a real
dependency. This is the wall ② is built to clear.
