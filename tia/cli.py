"""tia command line: record | run | status."""

import argparse
import os
import subprocess
import sys

import pytest

from tia import (astmap, diff, dynscan, imports, pytestcfg, remotestore,
                 report, resolve, select, semantic, server, store)
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
    """Convert {test -> {file -> lines}} into {test -> {file -> qualnames}}.

    Returns the converted map plus the per-file line->qualname tables we
    built along the way, so they can be baked into the stored map. With
    those tables, `run` resolves a diff without ever calling ``git show``
    — which is what makes it safe under CI shallow clones.
    """
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
    return result, cache


def _cosmetic_files(changed: dict, ref: str, root: str) -> set[str]:
    """.py files in the diff whose change is non-semantic.

    Comments/whitespace/docstrings, plus the provably-dead type-only edits
    (``if TYPE_CHECKING:`` bodies, function-local annotations). See
    ``semantic.is_semantic_change``.
    """
    out: set[str] = set()
    for path in changed:
        if not path.endswith(".py"):
            continue
        old = resolve.git_show(ref, path, root)
        if old is None:
            continue  # new/unavailable at ref — let normal selection handle it
        try:
            with open(os.path.join(root, path), encoding="utf-8") as fh:
                new = fh.read()
        except OSError:
            continue  # deleted in the worktree — a real change
        if not semantic.is_semantic_change(old, new):
            out.add(path)
    return out


def cmd_record(args) -> int:
    root = os.getcwd()
    source = args.source or root
    data_file = os.path.join(store.tia_dir(root), "coverage")
    os.makedirs(store.tia_dir(root), exist_ok=True)

    plugin = RecordPlugin(root, data_file, source)
    pytest_args = ["-q", "-p", "no:cacheprovider"] + _xdist_args()
    if args.path:
        pytest_args.insert(0, args.path)
    code = pytest.main(pytest_args, plugins=[plugin])

    quals, funcmaps = _lines_to_quals(root, plugin.result)
    dynamic = _scan_dynamic(root, funcmaps)
    import_time = _import_time_quals(root, plugin.outside)
    path = store.save_map(root, quals, ref=_head_sha(root),
                          reads=plugin.reads, funcmaps=funcmaps, dynamic=dynamic,
                          durations=plugin.durations, import_time=import_time)
    n_reads = sum(len(v) for v in plugin.reads.values())
    where = f", {plugin.workers} xdist workers" if plugin.workers else ""
    print(f"\n[tia] recorded {len(quals)} tests "
          f"({n_reads} non-py read deps, {len(dynamic)} dynamic files{where}) -> {path}")
    if not quals and code == 0:
        # Tests ran but none were attributed: the recorder never saw them
        # (e.g. another plugin ran them out-of-process). Every test would
        # then be "new" and always run — safe, but tia would do nothing.
        print("[tia] WARNING: tests ran but none were recorded. Is a plugin "
              "running them in subprocesses?",
              file=sys.stderr)
    return int(code) if code not in (0, 5) else 0


def _xdist_args() -> list[str]:
    """Let xdist workers record too, when pytest-xdist is installed.

    The ``RecordPlugin`` object handed to ``pytest.main`` lives only in this
    process. xdist workers rebuild their config from the invocation args, so
    ``-p tia._xdist`` makes each worker load the module whose
    ``pytest_configure`` starts a recorder there. Parallel recording then
    follows the suite's own ``-n``/``--dist`` settings (e.g. addopts).
    """
    import importlib.util
    return ["-p", "tia._xdist"] if importlib.util.find_spec("xdist") else []


def _import_time_quals(root: str, outside: dict[str, set[int]]) -> dict[str, set[str]]:
    """Functions whose body ran outside any test (import time, pytest hooks)."""
    out: dict[str, set[str]] = {}
    for rel, lines in outside.items():
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                body = astmap.body_line_to_qualname(fh.read())
        except (OSError, SyntaxError, ValueError):
            continue
        quals = {body[ln] for ln in lines if ln in body}
        if quals:
            out[rel] = quals
    return out


def _scan_dynamic(root: str, funcmaps: dict[str, dict[int, str]]) -> dict[str, list[str]]:
    """Flag measured files that use reflection coverage can't trace fully."""
    out: dict[str, list[str]] = {}
    for rel in funcmaps:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                markers = dynscan.find_markers(fh.read())
        except OSError:
            continue
        if markers:
            out[rel] = markers
    return out


def cmd_run(args) -> int:
    root = os.getcwd()
    # In CI the map is built elsewhere; pull it by base ref if we have none.
    if getattr(args, "remote", None) and not os.path.exists(store.map_path(root)):
        os.makedirs(store.tia_dir(root), exist_ok=True)
        want = args.since or _head_sha(root)
        got = remotestore.pull(args.remote, want, store.map_path(root))
        print(f"[tia] pulled map from {got}" if got
              else f"[tia] no map in remote {args.remote}", file=sys.stderr)
    if not os.path.exists(store.map_path(root)):
        print("[tia] no map found. Run `tia record` first.", file=sys.stderr)
        return 2

    tia_map = store.load_map(root)
    # Default to the ref the map was recorded at, so line numbers line up.
    ref = args.since or tia_map.get("ref") or "HEAD"

    all_nodeids = _collect_nodeids(args.path)
    plan = plan_selection(root, tia_map, ref, all_nodeids,
                          all_changes=args.all_changes,
                          trust_dynamic=args.trust_dynamic)
    changed, cosmetic = plan["changed"], plan["cosmetic"]
    func_changes, module_files = plan["func_changes"], plan["module_files"]
    data_changes, reads = plan["data_changes"], plan["reads"]
    escalated, selected = plan["escalated"], plan["selected"]

    total = len(all_nodeids)
    n = len(selected)
    print(f"[tia] ref {ref[:8] if ref else '?'} | changed files: {len(changed)} | "
          f"tests in suite: {total} | selected: {n}")
    for path in sorted(changed):
        tag = report.impact_tag(path, func_changes, module_files, escalated,
                                data_changes, reads, plan["config_changes"])
        print(f"       ~ {path}: {tag}")
    for path in sorted(cosmetic):
        print(f"       ~ {path}: comments/docstring/format/type-only - ignored")
    for nodeid, reason in sorted(selected.items()):
        print(f"       -> {nodeid}   ({reason})")

    for path, markers in sorted(escalated.items()):
        print(f"[tia] WARNING: {path} uses reflection ({', '.join(markers)}); "
              f"widened to file-level - coverage can't trace these edges. "
              f"Run the full suite periodically as a safety net.",
              file=sys.stderr)

    # Explainability in CI: drop a Markdown summary onto the PR check page.
    # Auto-on when GitHub hands us $GITHUB_STEP_SUMMARY; --report forces it.
    dest = args.report or os.environ.get("GITHUB_STEP_SUMMARY")
    if dest:
        md = report.render_markdown(
            ref, list(changed), func_changes, module_files, escalated,
            data_changes, reads, cosmetic, selected, total,
            plan["config_changes"])
        if dest == "-":
            # The report is UTF-8 (it has emoji). Write raw bytes through the
            # buffer so a legacy-codepage console (e.g. Windows cp950) can't
            # raise UnicodeEncodeError mid-run; fall back if there's no buffer
            # (e.g. stdout was replaced under capture).
            buf = getattr(sys.stdout, "buffer", None)
            if buf is not None:
                sys.stdout.flush()
                buf.write(md.encode("utf-8"))
                buf.flush()
            else:
                sys.stdout.write(md)
        else:
            try:
                with open(dest, "a", encoding="utf-8") as fh:
                    fh.write(md)
            except OSError as e:
                print(f"[tia] could not write report to {dest}: {e}", file=sys.stderr)

    if not selected:
        if args.on_empty == "fail":
            print("[tia] no affected tests - exiting 5 (--on-empty fail).")
            return 5
        if args.on_empty == "all":
            print(f"[tia] no affected tests - running all {total} tests "
                  f"(--on-empty all).")
            if args.list:
                return 0
            cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]
            if args.path:
                cmd.append(args.path)
            return subprocess.run(cmd).returncode
        saved = "100%" if total else "n/a"
        print(f"[tia] no affected tests - skipping {total} tests ({saved} saved).")
        return 0
    if total:
        est = select.recorded_seconds(tia_map.get("durations", {}), selected)
        est = f", ~{est:.1f}s recorded" if est is not None else ""
        print(f"[tia] running {n}/{total} tests "
              f"({100 * (total - n) // total}% skipped{est}).")

    if args.list:
        return 0

    cmd = [sys.executable, "-m", "pytest", *sorted(selected),
           "-p", "no:cacheprovider"]
    return subprocess.run(cmd).returncode


def _config_changes(changed, ref: str, root: str) -> set[str]:
    """pytest config files in the diff whose pytest section really changed."""
    out: set[str] = set()
    for path in changed:
        if not pytestcfg.is_config_file(path):
            continue
        old = resolve.git_show(ref, path, root)
        try:
            with open(os.path.join(root, path), encoding="utf-8") as fh:
                new = fh.read()
        except OSError:
            new = None
        if pytestcfg.pytest_section_changed(path, old, new):
            out.add(path)
    return out


def plan_selection(root: str, tia_map: dict, ref: str, all_nodeids: set[str],
                   all_changes: bool = False, trust_dynamic: bool = False) -> dict:
    """Diff against ``ref`` and decide what to run. No tests are executed.

    Returns a dict with the selection (``selected``) and every intermediate
    the terminal and Markdown reports explain.
    """
    changed = diff.changed_lines(ref, cwd=root)
    # Drop .py files whose change is non-semantic (comments, format,
    # docstrings, or provably-dead type-only edits), unless the user opts
    # out. Best-effort — needs the old blob (skipped if unavailable, e.g. a
    # shallow clone without the base fetched).
    cosmetic: set[str] = set()
    if not all_changes:
        cosmetic = _cosmetic_files(changed, ref, root)
        for path in cosmetic:
            del changed[path]

    import_time = tia_map.get("import_time")
    # Maps from before 1.3 baked funcmaps that credit class-body lines to the
    # class; resolve those through `git show` with the current rules instead.
    funcmaps = tia_map.get("funcmaps") if import_time is not None else None
    func_changes, module_files = resolve.changed_functions(
        changed, ref, root, funcmaps)
    # A function whose body ran outside any test (at import time or from a
    # pytest hook) affects every importer, like a module-level line.
    for path, funcs in func_changes.items():
        if funcs & (import_time or {}).get(path, set()):
            module_files.add(path)
    # A deleted (or renamed-away) module: everything that used it is affected.
    for path in changed:
        if path.endswith(".py") and not os.path.exists(os.path.join(root, path)):
            module_files.add(path)
    data_changes = {p for p in changed if not p.endswith(".py")}
    reads = tia_map.get("reads", {})

    # Files whose change reaches every importer. Taken before the dynamic
    # widening below, which only means "every test touching the file".
    import_level = set(module_files)

    # Degrade to file-level where coverage edges can't be trusted, unless
    # the user explicitly opts out with --trust-dynamic.
    escalated: dict[str, list[str]] = {}
    if not trust_dynamic:
        module_files, escalated = select.escalate_dynamic(
            func_changes, module_files, tia_map.get("dynamic", {}))

    config_changes = _config_changes(changed, ref, root)
    run_all = (f"pytest config changed: {', '.join(sorted(config_changes))}"
               if config_changes else None)

    importers = None
    if import_level and not run_all:
        nodeids = set(all_nodeids) | set(tia_map["tests"])
        reach = imports.reachable(root, {n.split("::", 1)[0] for n in nodeids})
        importers = {t: files & import_level for t, files in reach.items()}

    selected = select.select_tests(
        tia_map["tests"], func_changes, module_files, all_nodeids,
        data_changes, reads, importers=importers, run_all=run_all,
    )
    if all_nodeids:  # a mapped test that no longer exists can't be run
        selected = {n: r for n, r in selected.items() if n in all_nodeids}
    return {
        "changed": changed, "cosmetic": cosmetic, "func_changes": func_changes,
        "module_files": module_files, "data_changes": data_changes,
        "reads": reads, "escalated": escalated, "config_changes": config_changes,
        "selected": selected,
    }


def cmd_push(args) -> int:
    root = os.getcwd()
    if not os.path.exists(store.map_path(root)):
        print("[tia] no local map to push. Run `tia record` first.", file=sys.stderr)
        return 2
    ref = store.load_map(root).get("ref")
    dst = remotestore.push(store.map_path(root), args.to, ref)
    print(f"[tia] pushed map (ref {ref[:8] if ref else '?'}) -> {dst}")
    return 0


def cmd_pull(args) -> int:
    root = os.getcwd()
    os.makedirs(store.tia_dir(root), exist_ok=True)
    want = args.ref or _head_sha(root)
    got = remotestore.pull(args.from_, want, store.map_path(root))
    if not got:
        print(f"[tia] no map found in remote {args.from_}", file=sys.stderr)
        return 2
    print(f"[tia] pulled map from {got} -> {store.map_path(root)}")
    return 0


def cmd_serve(args) -> int:
    server.serve(args.dir, args.host, args.port)
    return 0


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
    run.add_argument("--remote", help="remote dir to pull the map from if absent locally")
    run.add_argument("--trust-dynamic", action="store_true",
                     help="don't widen reflection-heavy files to file-level (less safe)")
    run.add_argument("--all-changes", action="store_true",
                     help="don't ignore comment/docstring/format/type-only edits")
    run.add_argument("--on-empty", choices=["skip", "all", "fail"], default="skip",
                     help="when nothing is selected: skip (exit 0, default), "
                          "run the full suite, or fail with exit code 5")
    run.add_argument("--report", metavar="PATH",
                     help="write a Markdown summary to PATH ('-' for stdout); "
                          "auto-enabled from $GITHUB_STEP_SUMMARY in CI")
    run.set_defaults(func=cmd_run)

    push = sub.add_parser("push", help="publish the local map to a shared remote (for CI)")
    push.add_argument("--to", required=True, help="remote dir or http(s) URL to publish into")
    push.set_defaults(func=cmd_push)

    pull = sub.add_parser("pull", help="fetch a map from a shared remote")
    pull.add_argument("--from", dest="from_", required=True, help="remote dir or http(s) URL")
    pull.add_argument("--ref", help="git ref to fetch (default: HEAD, else latest)")
    pull.set_defaults(func=cmd_pull)

    srv = sub.add_parser("serve", help="run a tiny HTTP store for maps (zero deps)")
    srv.add_argument("--dir", default="./tia-maps", help="directory to store maps in")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.set_defaults(func=cmd_serve)

    st = sub.add_parser("status", help="show map summary")
    st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)
