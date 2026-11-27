# Benchmark: mutation pass at both granularities

[`RESULTS-granularity.md`](RESULTS-granularity.md) showed method-level runs
fewer tests than file-level. A finer map also has more ways to miss a
test, so this pass checks the safety side: plant a bug, and see whether
the selection would have caught it.

For each commit in the same windows as the granularity benchmark, the map
is recorded on the commit's parent. Then, on the commit, up to 8 single-line
mutants are planted **on lines that commit added**, spread evenly over
them. For each mutant:

* the **full suite** runs; the tests that fail (minus any that already fail
  on the unmutated commit) are the tests that *should* be selected;
* tia selects against the parent with the mutant in the working tree, once
  **method**-level (as shipped) and once **file**-level.

A killed mutant is a **miss** when none of its failing tests were selected:
the developer would push the bug with a green tia run. Operators:
comparison flip, `and`/`or` swap, `not` removal, `True`/`False` flip,
int +1, `+`/`-` swap, `return x` → `return None`.

Reproduce (same heads as the granularity benchmark):

```sh
PYTHONPATH=/path/to/tia python /path/to/tia/benchmark/mutation.py \
    --repo /path/to/flask --n 15 --pathspec src/flask --per-commit 8
PYTHONPATH=/path/to/tia python /path/to/tia/benchmark/mutation.py \
    --repo /path/to/boltons --n 20 --pathspec boltons --per-commit 8
```

Measured 2026-11-27, Python 3.12, pytest 8.4. Flask `main` @ `49943b5`,
boltons `master` @ `d31d821`.

## Summary

| codebase | mutants | killed by full suite | missed (method) | missed (file) | failing tests left out (method / file) |
|---|---|---|---|---|---|
| Flask | 13 | 9 | **0** | 0 | 0 / 0 of 51 |
| boltons | 96 | 82 | **0** | 0 | 0 / 0 of 303 |

Method-level misses nothing that file-level catches. It isn't only
"neither missed": no failing test was left out of either selection, even
where another selected test would have caught the mutant anyway.

The cases that matter are the killed mutants where method-level selected
**fewer** tests than file-level, since only there can the finer map miss
something file-level wouldn't:

| codebase | killed mutants where method < file | tests selected (method vs file) | missed |
|---|---|---|---|
| Flask | 2 | 141 vs 723 | 0 |
| boltons | 23 | 1,305 vs 1,550 | 0 |

The other mutants (14 survived the full suite, 2 hung on an infinite
loop) say nothing about selection and are left out of the miss counts.

## Caveat: Flask is a small sample

Most of the 14 Flask commits in the window change docstrings, typing or
packaging, so they have no added executable line to mutate. Only 5
commits produced mutants, and only 2 killed mutants landed where
method-level selected less than file-level (`scaffold.py`, 135 vs 434
tests; `testing.py`, 6 vs 289). Read the Flask row as "no counterexample
found", not as a rate. boltons, with 23 such mutants across 17 commits,
is the stronger evidence.

## It found a real bug (fixed in 1.3.2)

The first run, on 1.3.1, had exactly **one miss, at both granularities**:
boltons `f387b64c`, `strutils.py:65`, a module-level line:

```python
_punct_re = re.compile('[' - re.escape(_punct_ws_str) + ']+')  # mutant: + -> -
```

That raises `TypeError` at import, so `tests/test_strutils.py` and
`tests/test_fileutils.py` fail **collection**. Their tests then vanish
from what pytest collects (579 → 538 tests), every mapped test the change
selected is filtered out as "no longer exists", and tia printed
`no affected tests - skipping 538 tests` and exited 0. The full suite was
red.

This has nothing to do with granularity: any change that breaks a module
at import time would do it. Since 1.3.2, a file pytest can't collect is
always run (rule 6 in the README), so the import error fails the build.
Rerun on 1.3.2, same mutants: the miss is gone, and the table above is
the 1.3.2 result.

## Raw output

Flask:

```text
commit    mutant                             fail selM selF missM missF
------------------------------------------------------------------------
329c32b9  app.py:208 bool                       3  493  493     -     -
329c32b9  app.py:213 bool                       4  493  493     -     -
329c32b9  app.py:220 bool                       0  493  493    survived
329c32b9  app.py:222 bool                       0  493  493    survived
329c32b9  app.py:226 int                        0  493  493    survived
329c32b9  app.py:230 bool                      25  493  493     -     -
329c32b9  app.py:231 bool                       0  493  493    survived
329c32b9  app.py:235 bool                      10  493  493     -     -
7689f984  sessions.py:230 return                1  493  493     -     -
7689f984  sessions.py:230 arith                 1  493  493     -     -
89992954  scaffold.py:309 return                1  135  434     -     -
7203feab  app.py:737 cmp                        5  433  433     -     -
de8429ff  testing.py:180 boolop                 1    6  289     -     -
```

The boltons per-mutant log has the same columns and is reproducible with
the command above.
