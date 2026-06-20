# Benchmark: tia on boltons (the modular case)

The Flask benchmark ([`RESULTS.md`](RESULTS.md)) deliberately measured the
**worst case**: a small, tightly-coupled library where most tests really
do exercise the core request path, so the honest median skip was only
~21%. That's the floor. This is the other end of the honest range.

Target: [mahmoud/boltons](https://github.com/mahmoud/boltons) `main`
(HEAD `979fa9b`), **437 tests**, pytest 8, Python 3.14. boltons is a
collection of **independent utility modules** (`strutils`, `dictutils`,
`cacheutils`, …), each with its own test file — modular at both the file
and the function level. This is where method-level TIA should shine.

Reproduce:

```sh
git clone https://github.com/mahmoud/boltons && cd boltons
PYTHONPATH=/path/to/tia python /path/to/tia/benchmark/skiprate.py \
    --repo . --n 20 --pathspec boltons --testpath tests
```

## Methodology

Identical to the Flask run: for each non-merge commit touching `boltons/`,
record the map on the commit's **parent** (so the map's ref equals the
diff base) and measure the selection for that one commit
(`tia run --since <parent>`). All 437 tests map under the C tracer — no
`sysmon`-style context drop (we checked first; see Flask RESULTS for that
story).

## Skip rate per commit

| commit | files | suite | selected | skip | kind |
|--------|------:|------:|---------:|-----:|------|
| 979fa9b6 | 1 | 437 | 0 | **100.0%** | version bump † |
| fb464991 | 1 | 437 | 0 | **100.0%** | version bump † |
| b34c5342 | 8 | 435 | 0 | **100.0%** | CI + `__version__` † |
| 80f9eb42 | 1 | 431 | 0 | **100.0%** | docstring-example fix (乙) † |
| 25e8d6b1 | 2 | 437 | 4 | 99.1% | logic |
| 471dad1c | 1 | 435 | 3 | 99.3% | logic |
| 766b5547 | 2 | 433 | 2 | 99.5% | logic |
| ce7c7d2b | 1 | 426 | 2 | 99.5% | logic ✓ |
| 8a2a93d8 | 2 | 432 | 6 | 98.6% | logic |
| 42586df3 | 2 | 431 | 8 | 98.1% | logic |
| 207651ee | 1 | 431 | 17 | 96.1% | logic |
| 1b1d3787 | 2 | 426 | 17 | 96.0% | logic |
| ce60604a | 1 | 431 | 34 | 92.1% | logic |
| c0d580df | 1 | 431 | 34 | 92.1% | logic |
| 17374731 | 2 | 426 | 41 | 90.4% | logic |
| 49f381ee | 2 | 428 | 43 | 90.0% | logic |
| 4aa77cdd | 2 | 436 | 44 | 89.9% | logic |
| c463d163 | 4 | 435 | 154 | 64.6% | logic (test file + dynamic) |
| b4717124 | 1 | 424 | 424 | 0.0% | **contaminated** ✗ |
| c8c442b9 | 2 | 404 | 0 | 100.0% | **broken commit** ✗ |

```
all 20 commits        : median 98.4%   mean 90.3%
excl. 2 contaminated  : median 98.4%   mean 94.8%   (n=18)
logic changes only    : median 96.0%   mean 93.2%   (n=14)
```

† Inert by construction — see below.  ✓ Hand-verified correct.
✗ Excluded, see "The two commits I threw out".

## Honest reading

The honest, contamination-free, **non-cosmetic** number is the last row:
on real logic changes, tia skips a **median ~96%** of the suite. A change
to `strutils.human_readable_list` runs the 2 tests for that function, not
the other 424. That is the whole thesis of going method-level, and on a
modular codebase it pays off exactly as designed.

This is *not* in tension with Flask's 21%. Same tool, same rules; the
variable is the **codebase**. Flask's tests fan into a shared request
path, so a core change correctly pulls in most of them. boltons' modules
are independent, so a change stays local. The truth about TIA was always
"it depends how modular your code is" — now both ends are measured, on
real third-party suites: **~21% worst case ↔ ~96% modular case.**

### I distrusted this number first (as I should)

98% median looked exactly as "too good" as Flask's debunked 73% did, so I
treated it as a bug until proven otherwise:

- **All 437 tests map** under `ctrace` — no silently-dropped contexts.
- **Every `0 selected` row is genuinely inert.** Three are `__version__`
  bumps (no test reads `boltons.__version__` — grepped). One
  (`80f9eb42`) fixes a syntax typo *inside a docstring example*, which the
  cosmetic filter (乙) correctly strips. Skipping the suite for these is
  right, not a miss.
- **The small selections are exact.** `ce7c7d2b` changed one function and
  selected precisely the 2 tests that call it — and a grep confirms no
  other test exercises it. No false negative.
- **The big selection is conservative, not lucky.** `c463d163`'s 154 come
  from the commit *also* editing `tests/test_dictutils.py` at module level
  (whole test file runs) and from `urlutils.py` being reflection-flagged
  (widened to file-level). tia erring toward *more* tests, correctly.

### The two commits I threw out

`c8c442b9` committed literal `<<<<<<< Updated upstream` git **conflict
markers** into `strutils.py` — the file doesn't even parse, so pytest
collection drops to 404 tests. Its child `b4717124` is then measured
against a map recorded on that broken tree, so almost every test reads as
"new (never measured)" and all 424 run (its 0% skip). Both are artifacts
of a broken intermediate commit, not real measurements, so they're
excluded from the n=18 / n=14 rows. Leaving them *in* would only drag the
average **down** (mean 90.3%) — i.e. the exclusion is not cherry-picking
in tia's favour; the raw all-commits median is still 98.4%.

## Takeaway

Publish the range, not a single hero number. tia's value scales with how
decoupled your tests are from each other:

| codebase | character | honest median skip |
|----------|-----------|-------------------:|
| Flask | small, tightly-coupled (worst case) | ~21% |
| boltons | modular utility library | ~96% (logic changes) |

Most real applications with independent feature areas sit closer to the
boltons end than the Flask end.
