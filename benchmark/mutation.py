#!/usr/bin/env python
"""Mutation benchmark: does a selection miss a test that would have failed?

For each sampled commit touching ``--pathspec`` (merges skipped) we record
the map on the commit's parent, check out the commit, and then plant small
bugs (mutants) on the lines that commit added. For every mutant:

* the **full suite** runs; the tests that fail (minus any that already fail
  on the unmutated commit) are the ones that *should* have been selected;
* tia selects tests twice, against the parent, with the mutant in the
  working tree: **method** (as shipped) and **file** (any line of a changed
  file, see ``granularity.py``).

A mutant the full suite kills is a **miss** for a granularity when none of
its failing tests were selected: the developer would have pushed the bug
with a green tia run. ``tests missed`` counts failing tests left out even
when another selected test caught the mutant.

Mutants are planted only on lines the commit added, so the selection sees
the same changed functions the real commit had. Operators: comparison
flip, and/or swap, ``not`` removal, True/False flip, int +1, +/- swap,
``return x`` -> ``return None``.

    PYTHONPATH=/path/to/tia python benchmark/mutation.py \
        --repo /path/to/flask --n 15 --pathspec src/flask --per-commit 6
"""

import argparse
import ast
import copy
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GRAN = os.path.join(HERE, "granularity.py")
SEL = re.compile(r"tests in suite: (\d+) \| selected: (\d+)")
PICK = re.compile(r"^\s+-> (\S+)", re.M)
FAIL = re.compile(r"^(?:FAILED|ERROR) (\S+?)(?: - .*)?$", re.M)

FLIP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE,
        ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt,
        ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn,
        ast.NotIn: ast.In}


def run(cmd, cwd, env=None, timeout=None):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def git(args, cwd):
    return run(["git", *args], cwd).stdout.strip()


def added_lines(repo, parent, commit, pathspec):
    """{path: {line numbers added by the commit}} for .py files."""
    out = git(["diff", "--unified=0", "--no-color", "--no-renames",
               parent, commit, "--", pathspec], repo)
    added, path = {}, None
    for line in out.splitlines():
        if line.startswith("+++ "):
            p = line[4:]
            path = p[2:] if p.startswith("b/") and p.endswith(".py") else None
        elif line.startswith("@@") and path:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, n = int(m.group(1)), int(m.group(2) or 1)
            added.setdefault(path, set()).update(range(start, start + n))
    return added


def _annotation_ids(tree):
    skip = set()
    for node in ast.walk(tree):
        anns = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anns.append(node.returns)
        elif isinstance(node, ast.arg):
            anns.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            anns.append(node.annotation)
        for a in anns:
            if a is not None:
                skip.update(id(n) for n in ast.walk(a))
    return skip


def _mutate(node):
    """Return (mutated replacement source, label) or None."""
    new = copy.deepcopy(node)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 \
            and type(node.ops[0]) in FLIP:
        new.ops = [FLIP[type(node.ops[0])]()]
        label = "cmp"
    elif isinstance(node, ast.BoolOp):
        new.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        label = "boolop"
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        new = node.operand
        label = "not"
    elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
        new.value = not node.value
        label = "bool"
    elif isinstance(node, ast.Constant) and type(node.value) is int:
        new.value = node.value + 1
        label = "int"
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        new.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
        label = "arith"
    elif isinstance(node, ast.Return) and node.value is not None and not (
            isinstance(node.value, ast.Constant) and node.value.value is None):
        return "return None", "return"
    else:
        return None
    return ast.unparse(new), label


def mutants_for(repo, path, lines):
    """Single-line mutants on the given lines, as (path, line, col, end, src, label)."""
    src = open(os.path.join(repo, path), "rb").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    skip = _annotation_ids(tree)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if id(node) in skip or id(node) in docstrings:
            continue
        ln = getattr(node, "lineno", None)
        if ln is None or ln != node.end_lineno or ln not in lines:
            continue
        m = _mutate(node)
        if m:
            out.append((path, ln, node.col_offset, node.end_col_offset, *m))
    return sorted(out)


def apply(repo, mutant):
    """Write the mutant into the working tree; return the original bytes."""
    path, ln, col, end, new, _ = mutant
    full = os.path.join(repo, path)
    orig = open(full, "rb").read()
    lines = orig.splitlines(keepends=True)
    b = lines[ln - 1]  # col offsets are UTF-8 byte offsets
    lines[ln - 1] = b[:col] + new.encode() + b[end:]
    open(full, "wb").write(b"".join(lines))
    return orig


def spread(items, k):
    """k items evenly spaced across the list (deterministic)."""
    if len(items) <= k:
        return items
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)] \
        if k > 1 else items[:1]


def failing(repo, testpath, timeout):
    res = run([sys.executable, "-m", "pytest", testpath, "-q", "--tb=no",
               "-rfE", "-p", "no:cacheprovider", "-p", "no:randomly"],
              repo, timeout=timeout)
    if res is None:
        return None  # hung: an infinite loop counts as killed, not as a miss
    return set(FAIL.findall(res.stdout))


def select(repo, parent, testpath, gran):
    env = dict(os.environ, TIA_GRAN=gran)
    res = run([sys.executable, GRAN, "--as-tia", "run", "--since", parent,
               "--list", testpath], repo, env)
    if not SEL.search(res.stdout):
        return None
    return set(PICK.findall(res.stdout))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--pathspec", default=".")
    ap.add_argument("--testpath", default="tests")
    ap.add_argument("--per-commit", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    repo = a.repo

    start = git(["rev-parse", "HEAD"], repo)
    commits = git(["log", "--no-merges", "--format=%H", "-n", str(a.n),
                   "--", a.pathspec], repo).splitlines()
    print(f"{'commit':9} {'mutant':34} {'fail':>4} {'selM':>4} {'selF':>4} "
          f"{'missM':>5} {'missF':>5}")
    print("-" * 72)

    tot = dict(mutants=0, killed=0, hung=0, missM=0, missF=0,
               testsM=0, testsF=0, failing=0)
    misses = []
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
            base = failing(repo, a.testpath, a.timeout)
            if base is None:
                continue
            cands = []
            for path, lines in sorted(added_lines(repo, parent, c,
                                                  a.pathspec).items()):
                if os.path.exists(os.path.join(repo, path)):
                    cands += mutants_for(repo, path, lines)
            for mut in spread(cands, a.per_commit):
                path, ln, _, _, new, label = mut
                orig = apply(repo, mut)
                try:
                    fails = failing(repo, a.testpath, a.timeout)
                    selM = select(repo, parent, a.testpath, "method")
                    selF = select(repo, parent, a.testpath, "file")
                finally:
                    open(os.path.join(repo, path), "wb").write(orig)
                if selM is None or selF is None:
                    continue
                tot["mutants"] += 1
                name = f"{os.path.basename(path)}:{ln} {label}"
                if fails is None:
                    tot["hung"] += 1
                    print(f"{c[:8]:9} {name:34} hung {len(selM):>4} "
                          f"{len(selF):>4}", flush=True)
                    continue
                fails -= base
                if not fails:
                    print(f"{c[:8]:9} {name:34} {0:>4} {len(selM):>4} "
                          f"{len(selF):>4}    survived", flush=True)
                    continue
                tot["killed"] += 1
                tot["failing"] += len(fails)
                mM, mF = not (fails & selM), not (fails & selF)
                tot["missM"] += mM
                tot["missF"] += mF
                tot["testsM"] += len(fails - selM)
                tot["testsF"] += len(fails - selF)
                if mM or mF:
                    misses.append((c[:8], name, sorted(fails - selM)[:3]))
                print(f"{c[:8]:9} {name:34} {len(fails):>4} {len(selM):>4} "
                      f"{len(selF):>4} {'MISS' if mM else '-':>5} "
                      f"{'MISS' if mF else '-':>5}", flush=True)
    finally:
        run(["git", "checkout", "-q", "--", "."], repo)
        run(["git", "checkout", "-q", start], repo)

    t = tot
    print("-" * 72)
    print(f"mutants              : {t['mutants']} "
          f"(killed by full suite {t['killed']}, hung {t['hung']}, "
          f"survived {t['mutants'] - t['killed'] - t['hung']})")
    print(f"killed mutants missed: method {t['missM']} | file {t['missF']}")
    print(f"failing tests left out: method {t['testsM']} | file {t['testsF']} "
          f"(of {t['failing']})")
    for c, name, left in misses:
        print(f"  miss {c} {name}  e.g. {', '.join(left)}")


if __name__ == "__main__":
    main()
