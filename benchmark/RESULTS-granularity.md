# Benchmark: method-level vs file-level selection

Is tia's method-level precision worth it over the cheaper file/class-level
granularity that Ekstazi (and [Blastradius](https://github.com/baokhang83/blastradius)
on the JVM) use? Same commits, same map, selected both ways.

* **method** — tia as shipped: a test runs if it executed a changed function.
* **file** — a test runs if it executed *any* line of a changed file.

Both keep the same safety rules (module-level edits and reflection-flagged
files already widen to file-level), so file-level always selects a
superset of method-level: it can only remove misses, never add them.

Reproduce:

```sh
PYTHONPATH=/path/to/tia python /path/to/tia/benchmark/granularity.py \
    --repo /path/to/flask --n 15 --pathspec src/flask
PYTHONPATH=/path/to/tia python /path/to/tia/benchmark/granularity.py \
    --repo /path/to/boltons --n 20 --pathspec boltons
```

Measured 2026-11-20, Python 3.12, pytest 8.4. Flask `main` @ `49943b5`
(~486 tests), boltons `master` @ `d31d821` (~621 tests). These are newer
heads than [`RESULTS.md`](RESULTS.md) / [`RESULTS-boltons.md`](RESULTS-boltons.md),
so the commit windows differ from those files.

## Summary

| codebase | file-level ran more | tests run (method vs file) | mean skip (method vs file) |
|---|---|---|---|
| Flask | 3 of 14 commits | 2,840 vs 3,833 (**−26%**) | 58.2% vs 43.6% |
| boltons | 5 of 20 commits | 699 vs 750 (−7%) | 94.2% vs 93.7% |

Medians are left out on purpose: Flask's results split into two clusters
(near 0% and near 99%), so its median swings from 83.8% to 11.7% on just
three commits, which overstates the difference.

## Flask

| commit | suite | method | file | skip M | skip F |
|---|---|---|---|---|---|
| bfc8f983 | 486 | 429 | 429 | 11.7% | 11.7% |
| f2ef7a55 | 486 | 430 | 430 | 11.5% | 11.5% |
| d086db85 | 486 | **3** | 429 | 99.4% | 11.7% |
| 329c32b9 | 486 | 429 | 429 | 11.7% | 11.7% |
| 7689f984 | 486 | 455 | 455 | 6.4% | 6.4% |
| d73fa1cd | 486 | 3 | 3 | 99.4% | 99.4% |
| d318b683 | 486 | 3 | 3 | 99.4% | 99.4% |
| 2a8a38b0 | 486 | 19 | 19 | 96.1% | 96.1% |
| 89992954 | 486 | **138** | 430 | 71.6% | 11.5% |
| 7203feab | 485 | 429 | 429 | 11.5% | 11.5% |
| de8429ff | 484 | **9** | 284 | 98.1% | 41.3% |
| 81798b40 | 479 | 5 | 5 | 99.0% | 99.0% |
| 8285adf5 | 479 | 5 | 5 | 99.0% | 99.0% |
| 36e4a824 | 483 | 483 | 483 | 0.0% | 0.0% |

## boltons

| commit | suite | method | file | skip M | skip F |
|---|---|---|---|---|---|
| bd46e5ab | 621 | 1 | 1 | 99.8% | 99.8% |
| 73838fc2 | 621 | 97 | 97 | 84.4% | 84.4% |
| 8a49b74e | 620 | 142 | 143 | 77.1% | 76.9% |
| 1c2db466 | 618 | 14 | 14 | 97.7% | 97.7% |
| 922e7b2b | 617 | 25 | 29 | 95.9% | 95.3% |
| 2ecf547b | 610 | 1 | 1 | 99.8% | 99.8% |
| 69a12148 | 609 | 8 | 8 | 98.7% | 98.7% |
| 76e92098 | 608 | 10 | 10 | 98.4% | 98.4% |
| 2873144d | 605 | 10 | 10 | 98.3% | 98.3% |
| 6270b5e3 | 602 | 58 | 58 | 90.4% | 90.4% |
| 37ba6b2a | 594 | 45 | 45 | 92.4% | 92.4% |
| 95119027 | 590 | 100 | 132 | 83.1% | 77.6% |
| 1e6c7950 | 585 | 17 | 17 | 97.1% | 97.1% |
| 59bd0fbc | 581 | 6 | 16 | 99.0% | 97.2% |
| f387b64c | 579 | 34 | 34 | 94.1% | 94.1% |
| 6e0ab7b0 | 573 | 9 | 13 | 98.4% | 97.7% |
| e604f5a3 | 571 | 12 | 12 | 97.9% | 97.9% |
| d885f05e | 568 | 95 | 95 | 83.3% | 83.3% |
| f898d8f8 | 561 | 15 | 15 | 97.3% | 97.3% |
| 20619069 | 557 | 0 | 0 | 100.0% | 100.0% |

## Reading it

Method-level only pays off when a function-level edit lands in a **large,
central file** that most tests execute. In Flask that's `app.py` and friends,
where a one-function change drops the selection from ~430 tests to 3–138.
In boltons each module has its own test file, so the file already
approximates the function, and the extra precision buys ~7%.

The extra precision isn't free on safety: a finer map has more ways to miss
a test. The next step is a mutation pass at both granularities to check
that method-level adds no misses on these windows.
