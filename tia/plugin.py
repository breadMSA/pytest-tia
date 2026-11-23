"""Pytest plugin that records a per-test line-coverage map.

We wrap each test's setup/call/teardown and switch coverage.py's dynamic
context to the test's nodeid. After the session we read the coverage
data back and invert it into ``{nodeid: {file: {lines...}}}``.

In the same window we also watch every file the test *opens* via a
``sys.addaudithook`` on the ``open`` event, so non-``.py`` dependencies
(config, fixtures, templates) that coverage can't see are recorded too.
That's what stops a change to ``config.yaml`` from silently skipping the
one test that actually reads it.

Under pytest-xdist the tests run in worker processes, which never see the
``RecordPlugin`` object handed to ``pytest.main``. So the controller passes
its settings to each worker through ``workerinput``; ``tia record`` loads
``-p tia._xdist``, whose ``pytest_configure`` (``worker_configure`` here)
starts a ``_WorkerRecorder`` in every worker. Each worker writes its own
suffixed coverage file and ships its file reads and durations back through
``workeroutput``; the controller combines everything after the session.
"""

import glob
import os
import sys

# coverage.py's sysmon core (the default on Python 3.12+) records only the
# FIRST dynamic context to hit each line and silently drops every later
# test that reuses the same code. For a shared helper that means only one
# of its callers is mapped — a false negative, the one thing tia must not
# do. The C tracer records all contexts correctly, so force it (unless the
# user has deliberately chosen a core). xdist workers inherit the env.
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
# Key under which the controller hands its settings to xdist workers.
_WORKER_KEY = "tia_record"


def nodeid_of(item) -> str:
    """The item's nodeid without the ``@group`` suffix xdist's loadgroup adds.

    ``--dist loadgroup`` rewrites worker nodeids to ``path::test@group``;
    ``tia run`` collects without xdist, so the map must use the plain id.
    """
    nodeid = item.nodeid
    names = set()
    for mark in item.iter_markers("xdist_group"):
        names.add(str(mark.args[0] if mark.args
                      else mark.kwargs.get("name", "default")))
    suffix = "@" + "_".join(sorted(names))
    if names and nodeid.endswith(suffix):
        nodeid = nodeid[: -len(suffix)]
    return nodeid


class _Tracer:
    """Attributes executed lines, opened files and durations to each test."""

    def __init__(self, root: str, data_file: str, source: str, suffix: bool):
        self.root = root
        # Don't measure tia's own code — our wrapper frame is live during
        # each test's context and would otherwise leak into the map.
        tia_glob = os.path.join(os.path.dirname(tia.__file__), "*")
        self.cov = coverage.Coverage(
            data_file=data_file,
            data_suffix=suffix or None,
            branch=False,
            source=[source],
            omit=[tia_glob],
            config_file=False,
        )
        self.cov.start()
        # nodeid -> set(relpath) of non-.py files the test read
        self.reads: dict[str, set[str]] = {}
        # nodeid -> seconds spent in setup + call + teardown
        self.durations: dict[str, float] = {}
        # raw (possibly @group-suffixed) nodeid -> plain nodeid
        self._ids: dict[str, str] = {}
        # The test whose phases are currently executing (None otherwise),
        # read by the audit hook to attribute opens.
        self._current: str | None = None
        self._tracing = True
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

    def _enter(self, item) -> None:
        nodeid = nodeid_of(item)
        self._ids[item.nodeid] = nodeid
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
        self._enter(item)
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        self._enter(item)
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item):
        try:
            return (yield)
        finally:
            self._leave()

    def pytest_runtest_logreport(self, report):
        if not self._tracing:
            return  # xdist controller: durations arrive via workeroutput
        nodeid = self._ids.get(report.nodeid, report.nodeid)
        self.durations[nodeid] = self.durations.get(nodeid, 0.0) + report.duration

    def _stop(self) -> None:
        self.cov.stop()
        self.cov.save()


class _WorkerRecorder(_Tracer):
    """Runs inside each xdist worker; started by ``pytest_configure`` below."""

    def __init__(self, config, root: str, data_file: str, source: str):
        super().__init__(root, data_file, source, suffix=True)
        self.config = config

    def pytest_sessionfinish(self, session, exitstatus):
        self._stop()
        # xdist sends workeroutput after every sessionfinish impl has run.
        out = self.config.workeroutput
        out["tia_reads"] = {k: sorted(v) for k, v in self.reads.items()}
        out["tia_durations"] = self.durations


def worker_configure(config):
    """Start recording in an xdist worker the ``tia record`` controller set up."""
    settings = getattr(config, "workerinput", {}).get(_WORKER_KEY)
    if settings and not config.pluginmanager.has_plugin("tia-worker-recorder"):
        config.pluginmanager.register(
            _WorkerRecorder(config, **settings), "tia-worker-recorder")


class RecordPlugin(_Tracer):
    """The ``tia record`` plugin: records in-process, or drives xdist workers.

    After the session, ``result`` holds ``{nodeid: {relpath: {lines}}}``,
    ``reads`` the non-``.py`` files each test opened and ``durations`` the
    seconds each test took (setup + call + teardown, under coverage).
    """

    def __init__(self, root: str, data_file: str, source: str):
        # Workers append suffixes to the same base name; clear stale ones so
        # a crashed earlier run can't leak old contexts into this map.
        for stale in [data_file, *glob.glob(glob.escape(data_file) + ".*")]:
            try:
                os.remove(stale)
            except OSError:
                pass
        super().__init__(root, data_file, source, suffix=False)
        self.data_file = data_file
        self.source = source
        self.workers = 0
        self.result: dict[str, dict[str, set[int]]] = {}
        # relpath -> lines that ran outside any test (import time, hooks)
        self.outside: dict[str, set[int]] = {}

    def pytest_sessionstart(self, session):
        # xdist registers its DSession in a trylast pytest_configure, so by
        # now we know whether tests will run here or in workers.
        if session.config.pluginmanager.has_plugin("dsession"):
            self._tracing = False
            self.cov.set_option("run:disable_warnings", ["no-data-collected"])
            self.cov.stop()  # the controller runs no tests; nothing to save

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node):
        node.workerinput[_WORKER_KEY] = {
            "root": self.root, "data_file": self.data_file, "source": self.source,
        }

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        out = getattr(node, "workeroutput", None) or {}
        if "tia_reads" not in out:
            return  # crashed worker: its tests stay unmapped, so they always run
        self.workers += 1
        for nodeid, files in out["tia_reads"].items():
            self.reads.setdefault(nodeid, set()).update(files)
        for nodeid, secs in out.get("tia_durations", {}).items():
            self.durations[nodeid] = self.durations.get(nodeid, 0.0) + secs

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        if self._tracing:
            self._stop()
            data = self.cov.get_data()
        else:
            combiner = coverage.Coverage(data_file=self.data_file, config_file=False)
            combiner.set_option("run:disable_warnings", ["no-data-collected"])
            combiner.combine(strict=False)  # no worker files = nothing recorded
            data = combiner.get_data()
        result: dict[str, dict[str, set[int]]] = {}
        outside: dict[str, set[int]] = {}
        for abs_path in data.measured_files():
            rel = os.path.relpath(abs_path, self.root).replace(os.sep, "/")
            for lineno, contexts in data.contexts_by_lineno(abs_path).items():
                for ctx in contexts:
                    if not ctx:  # empty context = import time / pytest hooks
                        outside.setdefault(rel, set()).add(lineno)
                        continue
                    result.setdefault(ctx, {}).setdefault(rel, set()).add(lineno)
        self.result = result
        self.outside = outside
