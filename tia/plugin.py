"""Pytest plugin that records a per-test line-coverage map.

We wrap each test's *call* phase and switch coverage.py's dynamic
context to the test's nodeid. After the session we read the coverage
data back and invert it into ``{nodeid: {file: {lines...}}}``.

In the same window we also watch every file the test *opens* via a
``sys.addaudithook`` on the ``open`` event, so non-``.py`` dependencies
(config, fixtures, templates) that coverage can't see are recorded too.
That's what stops a change to ``config.yaml`` from silently skipping the
one test that actually reads it.
"""

import os
import sys

# coverage.py's sysmon core (the default on Python 3.12+) records only the
# FIRST dynamic context to hit each line and silently drops every later
# test that reuses the same code. For a shared helper that means only one
# of its callers is mapped — a false negative, the one thing tia must not
# do. The C tracer records all contexts correctly, so force it (unless the
# user has deliberately chosen a core).
os.environ.setdefault("COVERAGE_CORE", "ctrace")

import coverage
import pytest

import tia

# Extensions coverage already accounts for, plus compiled noise.
_IGNORED_EXT = {".py", ".pyc", ".pyo", ".pyd"}
# Directory names anywhere in the path that are never a real data dep.
_IGNORED_DIRS = {
    ".git", ".tia", "__pycache__", ".pytest_cache",
    "node_modules", ".venv", "venv", "env", "site-packages",
}


class RecordPlugin:
    def __init__(self, root: str, data_file: str, source: str):
        self.root = root
        # Don't measure tia's own code — our wrapper frame is live during
        # each test's context and would otherwise leak into the map.
        tia_glob = os.path.join(os.path.dirname(tia.__file__), "*")
        self.cov = coverage.Coverage(
            data_file=data_file,
            branch=False,
            source=[source],
            omit=[tia_glob],
            config_file=False,
        )
        self.cov.erase()
        self.cov.start()
        # nodeid -> {relpath -> set(line numbers)}
        self.result: dict[str, dict[str, set[int]]] = {}
        # nodeid -> set(relpath) of non-.py files the test read
        self.reads: dict[str, set[str]] = {}
        # The test whose call phase is currently executing (None otherwise),
        # read by the audit hook to attribute opens.
        self._current: str | None = None
        sys.addaudithook(self._audit)

    def _audit(self, event: str, args) -> None:
        # Must be fast and must never raise — it runs on every open in the
        # process. Bail out before doing any real work in the common case.
        if event != "open" or self._current is None:
            return
        try:
            path, mode = args[0], (args[1] if len(args) > 1 else None)
            if not isinstance(path, str):
                return
            # builtins.open passes a mode string; skip pure writes. os.open
            # passes mode=None (flags carry intent) — keep those.
            if isinstance(mode, str) and not ("r" in mode or "+" in mode):
                return
            self._record_read(self._current, path)
        except Exception:
            return

    def _record_read(self, nodeid: str, path: str) -> None:
        ab = os.path.abspath(path)
        rel = os.path.relpath(ab, self.root)
        if rel.startswith("..") or os.path.isabs(rel):
            return  # outside the project root
        rel = rel.replace(os.sep, "/")
        if os.path.splitext(rel)[1].lower() in _IGNORED_EXT:
            return
        parts = rel.split("/")
        if any(p in _IGNORED_DIRS or p.endswith((".egg-info", ".dist-info"))
               for p in parts):
            return
        if not os.path.isfile(ab):
            return
        self.reads.setdefault(nodeid, set()).add(rel)

    def _enter(self, nodeid: str) -> None:
        self.cov.switch_context(nodeid)
        self._current = nodeid

    def _leave(self) -> None:
        self._current = None
        self.cov.switch_context("")

    # Attribute setup + call (+ teardown) to the test. Fixture-heavy suites
    # (e.g. flask builds the app/client in fixtures) exercise most of their
    # code during setup; attributing only the call phase under-records those
    # tests, which then fall through to the always-run "new test" rule.
    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item):
        self._enter(item.nodeid)
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        self._enter(item.nodeid)
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item):
        try:
            return (yield)
        finally:
            self._leave()

    def pytest_sessionfinish(self, session, exitstatus):
        self.cov.stop()
        self.cov.save()
        data = self.cov.get_data()
        result: dict[str, dict[str, set[int]]] = {}
        for abs_path in data.measured_files():
            rel = os.path.relpath(abs_path, self.root).replace(os.sep, "/")
            for lineno, contexts in data.contexts_by_lineno(abs_path).items():
                for ctx in contexts:
                    if not ctx:  # empty context = import-time / setup
                        continue
                    result.setdefault(ctx, {}).setdefault(rel, set()).add(lineno)
        self.result = result
