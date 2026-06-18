"""Decide which tests to run given a coverage map and a set of changes.

Three rules, in order of precision:

1. Line-level hit: a changed line is one a test actually executed.
   This is the precise, ideal case.
2. Module-level fallback: a changed line *inside a file's covered span*
   that no test executed (a module-level constant, a `def` signature,
   an import). We can't pin it to one test, so we conservatively select
   every test that touches that file. Bounded by the covered span so a
   trailing insertion (new code/new test appended at EOF) does not drag
   the whole file's tests in.
3. New tests: any currently-collected test not in the map has never
   been measured, so we always run it.
"""


def select_tests(
    tia_map: dict,
    changed: dict[str, set[int]],
    all_nodeids: set[str],
) -> dict[str, str]:
    """Return ``{nodeid: human-readable reason}`` for tests to run."""
    map_tests: dict[str, dict[str, set[int]]] = tia_map["tests"]

    # Index once: file -> {line -> [tests]}, and file -> highest covered line.
    line_index: dict[str, dict[int, list[str]]] = {}
    file_max: dict[str, int] = {}
    for nodeid, files in map_tests.items():
        for path, lines in files.items():
            idx = line_index.setdefault(path, {})
            for ln in lines:
                idx.setdefault(ln, []).append(nodeid)
            if lines:
                file_max[path] = max(file_max.get(path, 0), max(lines))

    selected: dict[str, str] = {}
    for path, lines in changed.items():
        idx = line_index.get(path)
        if not idx:
            continue  # no test touches this file -> nothing to select for it
        tests_touching = {t for tests in idx.values() for t in tests}
        for ln in sorted(lines):
            if ln in idx:
                for t in idx[ln]:
                    selected.setdefault(t, f"executes {path}:{ln}")
            elif ln <= file_max.get(path, 0):
                for t in tests_touching:
                    selected.setdefault(
                        t, f"module-level change in {path} near line {ln}"
                    )
            # else: change is past the covered region -> new code / new test

    known = set(map_tests)
    for nodeid in all_nodeids:
        if nodeid not in known:
            selected.setdefault(nodeid, "new test (never measured)")

    return selected
