#!/usr/bin/env python
"""Granularity benchmark: method-level vs file-level selection, same commits.

For each sampled commit touching ``--pathspec`` (merges skipped) we record
the map once on the commit's parent, then select twice:

* **method** — tia as shipped (a test runs if it executed a changed function).
* **file**   — every test that executed *any* line of a changed file runs
  (Ekstazi-style class/file-level).

Both keep the same safety rules (module-level edits and reflection-flagged
files already widen to file-level), so file-level is always a superset.

    PYTHONPATH=/path/to/tia python benchmark/granularity.py \
        --repo /path/to/flask --n 15 --pathspec src/flask
"""

import argparse
import os
import re
import statistics
import subprocess
import sys

SEL = re.compile(r"tests in suite: (\d+) \| selected: (\d+)")


def _as_tia(argv):
    """Run tia's CLI, forcing file-level selection when TIA_GRAN=file."""
    from tia import select
    if os.environ.get("TIA_GRAN") == "file":
        orig = select.select_tests

        def file_level(map_tests, func_changes, module_files, *a, **k):
            return orig(map_tests, {}, set(module_files) | set(func_changes),
                        *a, **k)

        select.select_tests = file_level
    from tia.cli import main as tia_main
    sys.argv = ["tia", *argv]
    return tia_main()


def run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def git(args, cwd):
    return run(["git", *args], cwd).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--pathspec", default=".")
    ap.add_argument("--testpath", default="tests")
    a = ap.parse_args()
    repo, me = a.repo, os.path.abspath(__file__)

    start = git(["rev-parse", "HEAD"], repo)
    commits = git(["log", "--no-merges", "--format=%H", "-n", str(a.n),
                   "--", a.pathspec], repo).splitlines()
    print(f"{'commit':9} {'suite':>5} {'method':>6} {'file':>5} "
          f"{'skipM':>6} {'skipF':>6}")
    print("-" * 44)

    rows = []
    try:
        for c in commits:
            parent = git(["rev-parse", f"{c}^"], repo)
            if not parent:
                continue
            run(["git", "checkout", "-q", parent], repo)
            rec = run([sys.executable, "-m", "tia", "record", a.testpath], repo)
            run(["git", "checkout", "-q", c], repo)
            if "recorded" not in rec.stdout:
                continue
            got = {}
            for gran in ("method", "file"):
                env = dict(os.environ, TIA_GRAN=gran)
                res = run([sys.executable, me, "--as-tia", "run", "--since",
                           parent, "--list", a.testpath], repo, env)
                m = SEL.search(res.stdout)
                got[gran] = m and (int(m.group(1)), int(m.group(2)))
            if not got["method"] or not got["file"] or got["method"][0] == 0:
                continue  # collection failed at this checkout
            total, sm, sf = got["method"][0], got["method"][1], got["file"][1]
            rows.append((total, sm, sf))
            print(f"{c[:8]:9} {total:>5} {sm:>6} {sf:>5} "
                  f"{100 * (total - sm) / total:>5.1f}% "
                  f"{100 * (total - sf) / total:>5.1f}%", flush=True)
    finally:
        run(["git", "checkout", "-q", start], repo)

    if rows:
        km = [100 * (t - m) / t for t, m, _ in rows]
        kf = [100 * (t - f) / t for t, _, f in rows]
        rm, rf = sum(r[1] for r in rows), sum(r[2] for r in rows)
        print("-" * 44)
        print(f"commits measured     : {len(rows)}")
        print(f"file-level ran more  : {sum(f > m for _, m, f in rows)} commits")
        print(f"tests run (total)    : method {rm} | file {rf} "
              f"({100 * (rf - rm) / rf:.0f}% fewer at method-level)")
        print(f"mean skip            : method {statistics.mean(km):.1f}% | "
              f"file {statistics.mean(kf):.1f}%")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--as-tia":
        sys.exit(_as_tia(sys.argv[2:]))
    main()
