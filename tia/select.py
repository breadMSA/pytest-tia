"""Decide which tests to run, at method-level granularity.

Four rules:

1. Function hit — a test executed a function whose body changed.
   Immune to line shifts elsewhere in the file.
2. Module-level fallback — a file had a module-level *modification*
   (constant, import, class body). Run every test touching that file.
3. Data dependency — a non-``.py`` file changed; run every test that
   opened it during recording (the silent-dependency rule).
4. New test — any collected test not in the map has never been measured.
"""


def select_tests(
    map_tests: dict[str, dict[str, set[str]]],
    func_changes: dict[str, set[str]],
    module_files: set[str],
    all_nodeids: set[str],
    data_changes: set[str] | None = None,
    reads: dict[str, set[str]] | None = None,
) -> dict[str, str]:
    """Return ``{nodeid: human-readable reason}`` for tests to run."""
    selected: dict[str, str] = {}

    for path, changed_funcs in func_changes.items():
        for nodeid, files in map_tests.items():
            executed = files.get(path)
            if executed:
                hit = changed_funcs & executed
                if hit:
                    selected.setdefault(nodeid, f"{path}: {', '.join(sorted(hit))}")

    for path in module_files:
        for nodeid, files in map_tests.items():
            if path in files:
                selected.setdefault(nodeid, f"module-level change in {path}")

    for path in data_changes or set():
        for nodeid, files in (reads or {}).items():
            if path in files:
                selected.setdefault(nodeid, f"reads {path}")

    known = set(map_tests)
    for nodeid in all_nodeids:
        if nodeid not in known:
            selected.setdefault(nodeid, "new test (never measured)")

    return selected


def escalate_dynamic(
    func_changes: dict[str, set[str]],
    module_files: set[str],
    dynamic: dict[str, list[str]],
) -> tuple[set[str], dict[str, list[str]]]:
    """Widen method-level hits to file-level for reflection-heavy files.

    A function-level change in a file flagged dynamic (``getattr`` by
    computed name, ``eval``, ``importlib``, ...) can't be trusted to have
    captured every edge during recording, so we run *every* test touching
    that file instead of just the ones that hit the changed function — a
    deliberate, bounded loss of precision in exchange for not missing a
    test. Module-level changes already select file-level, so they need no
    escalation.

    Returns ``(module_files, escalated)`` where ``escalated`` maps each
    widened path to the markers that triggered it (for reporting).
    """
    module_files = set(module_files)
    escalated: dict[str, list[str]] = {}
    for path in func_changes:
        if path in dynamic:
            module_files.add(path)
            escalated[path] = dynamic[path]
    return module_files, escalated


def recorded_seconds(durations: dict[str, float], nodeids) -> float | None:
    """Total recorded time of ``nodeids``; None if any of them is unmeasured.

    Recording runs under coverage, so this over-estimates a plain run; it is
    meant for relative decisions (e.g. whether xdist startup is worth it).
    """
    total = 0.0
    for nodeid in nodeids:
        if nodeid not in durations:
            return None
        total += durations[nodeid]
    return total
