"""tia command line: record | run | status."""

import argparse
import os
import subprocess
import sys

import pytest

from tia import astmap, diff, resolve, select, store
from tia.plugin import RecordPlugin


def _collect_nodeids(path: str | None) -> set[str]:
    """Ask pytest which tests currently exist (no execution)."""
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q",
           "-p", "no:cacheprovider"]
    if path:
        cmd.append(path)
    out = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return {ln.strip() for ln in out.stdout.splitlines() if "::" in ln}


def _head_sha(cwd: str) -> str | None:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return out.stdout.strip() if out.returncode == 0 else None


def _lines_to_quals(root: str, line_map: dict[str, dict[str, set[int]]]):
    """Convert {test -> {file -> lines}} into {test -> {file -> qualnames}}."""
    cache: dict[str, dict[int, str]] = {}
    result: dict[str, dict[str, set[str]]] = {}
    for nodeid, files in line_map.items():
        quals: dict[str, set[str]] = {}
        for rel, lines in files.items():
            l2q = cache.get(rel)
            if l2q is None:
                try:
                    l2q = astmap.line_to_qualname_from_file(os.path.join(root, rel))
                except (SyntaxError, OSError):
                    l2q = {}
                cache[rel] = l2q
            quals[rel] = {l2q[ln] for ln in lines if ln in l2q}
        result[nodeid] = quals
    return result


def cmd_record(args) -> int:
    root = os.getcwd()
    source = args.source or root
    data_file = os.path.join(store.tia_dir(root), "coverage")
    os.makedirs(store.tia_dir(root), exist_ok=True)

    plugin = RecordPlugin(root, data_file, source)
    pytest_args = ["-q", "-p", "no:cacheprovider"]
    if args.path:
        pytest_args.insert(0, args.path)
    code = pytest.main(pytest_args, plugins=[plugin])

    quals = _lines_to_quals(root, plugin.result)
    path = store.save_map(root, quals, ref=_head_sha(root), reads=plugin.reads)
    n_reads = sum(len(v) for v in plugin.reads.values())
    print(f"\n[tia] recorded {len(quals)} tests "
          f"({n_reads} non-py read deps) -> {path}")
    return int(code) if code not in (0, 5) else 0


def cmd_run(args) -> int:
    root = os.getcwd()
    if not os.path.exists(store.map_path(root)):
        print("[tia] no map found. Run `tia record` first.", file=sys.stderr)
        return 2

    tia_map = store.load_map(root)
    # Default to the ref the map was recorded at, so line numbers line up.
    ref = args.since or tia_map.get("ref") or "HEAD"

    changed = diff.changed_lines(ref, cwd=root)
    func_changes, module_files = resolve.changed_functions(changed, ref, root)
    data_changes = {p for p in changed if not p.endswith(".py")}
    reads = tia_map.get("reads", {})
    all_nodeids = _collect_nodeids(args.path)
    selected = select.select_tests(
        tia_map["tests"], func_changes, module_files, all_nodeids,
        data_changes, reads,
    )

    total = len(all_nodeids)
    n = len(selected)
    print(f"[tia] ref {ref[:8] if ref else '?'} | changed files: {len(changed)} | "
          f"tests in suite: {total} | selected: {n}")
    for path in sorted(changed):
        funcs = func_changes.get(path)
        if funcs:
            tag = ", ".join(sorted(funcs))
        elif path in module_files:
            tag = "module-level"
        elif path in data_changes:
            n_readers = sum(1 for f in reads.values() if path in f)
            tag = f"data dep ({n_readers} reader{'' if n_readers == 1 else 's'})"
        else:
            tag = "no covered funcs"
        print(f"       ~ {path}: {tag}")
    for nodeid, reason in sorted(selected.items()):
        print(f"       -> {nodeid}   ({reason})")

    if not selected:
        saved = "100%" if total else "n/a"
        print(f"[tia] no affected tests — skipping {total} tests ({saved} saved).")
        return 0
    if total:
        print(f"[tia] running {n}/{total} tests "
              f"({100 * (total - n) // total}% skipped).")

    if args.list:
        return 0

    cmd = [sys.executable, "-m", "pytest", *sorted(selected),
           "-p", "no:cacheprovider"]
    return subprocess.run(cmd).returncode


def cmd_status(args) -> int:
    root = os.getcwd()
    if not os.path.exists(store.map_path(root)):
        print("[tia] no map recorded yet.")
        return 0
    m = store.load_map(root)
    files = {f for t in m["tests"].values() for f in t}
    print(f"[tia] map: {len(m['tests'])} tests covering {len(files)} files "
          f"(recorded {m['created']}).")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tia", description="Test Impact Analysis for pytest")
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="run the full suite and build the impact map")
    rec.add_argument("path", nargs="?", help="test path (default: auto-discover)")
    rec.add_argument("--source", help="source root to measure (default: cwd)")
    rec.set_defaults(func=cmd_record)

    run = sub.add_parser("run", help="run only the tests affected by current changes")
    run.add_argument("path", nargs="?", help="restrict collection to this path")
    run.add_argument("--since", default=None,
                     help="git ref to diff against (default: the ref the map was recorded at)")
    run.add_argument("--list", action="store_true", help="list selected tests, don't run")
    run.set_defaults(func=cmd_run)

    st = sub.add_parser("status", help="show map summary")
    st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)
