#!/usr/bin/env python
"""Skip-rate benchmark: how few tests tia runs per real commit.

For each sampled commit that touches ``--pathspec`` (merges skipped) we
record the map on the commit's **parent** — so the map's ref matches the
diff base, which tia's coordinate system requires — then measure the
selection for that single commit. Re-recording per commit is the price of
measuring it *correctly* rather than against a drifting fixed base.

    PYTHONPATH=/path/to/tia python benchmark/skiprate.py \
        --repo /path/to/flask --n 15 --pathspec src/flask
"""

import argparse
import re
import statistics
import subprocess
import sys

SEL = re.compile(r"tests in suite: (\d+) \| selected: (\d+)")


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def git(args, cwd):
    return run(["git", *args], cwd).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--pathspec", default=".")
    ap.add_argument("--testpath", default="tests")
    a = ap.parse_args()
    repo = a.repo

    start = git(["rev-parse", "HEAD"], repo)
    commits = git(["log", "--no-merges", "--format=%H", "-n", str(a.n),
                   "--", a.pathspec], repo).splitlines()
    print(f"measuring {len(commits)} commits in {repo}\n")
    print(f"{'commit':10} {'files':>5} {'total':>6} {'select':>7} {'skip':>7}")
    print("-" * 40)

    rows = []
    try:
        for c in commits:
            parent = git(["rev-parse", f"{c}^"], repo)
            if not parent:
                continue
            nfiles = len(git(["diff", "--name-only", parent, c], repo).splitlines())

            run(["git", "checkout", "-q", parent], repo)
            rec = run([sys.executable, "-m", "tia", "record", a.testpath], repo)
            run(["git", "checkout", "-q", c], repo)
            if "recorded" not in rec.stdout:
                continue
            res = run([sys.executable, "-m", "tia", "run", "--since", parent,
                       "--list", a.testpath], repo)
            m = SEL.search(res.stdout)
            if not m:
                continue
            total, selected = int(m.group(1)), int(m.group(2))
            if total == 0:
                continue  # collection failed at this checkout — not measurable
            skip = 100 * (total - selected) / total
            rows.append((selected, skip))
            print(f"{c[:8]:10} {nfiles:>5} {total:>6} {selected:>7} {skip:>6.1f}%")
    finally:
        run(["git", "checkout", "-q", start], repo)

    if rows:
        skips = [r[1] for r in rows]
        sels = [r[0] for r in rows]
        print("-" * 40)
        print(f"commits measured : {len(rows)}")
        print(f"median skip rate : {statistics.median(skips):.1f}%")
        print(f"mean skip rate   : {statistics.mean(skips):.1f}%")
        print(f"median selected  : {statistics.median(sels):.0f} tests")


if __name__ == "__main__":
    main()
