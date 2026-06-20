"""Adversarial proof of the one guarantee: never skip a test that a change
could break.

The other tests check tia's logic in pieces. This one attacks the promise
end to end: build a real git repo, record the map, then **mutate every
function the map says is covered** and assert tia re-selects *every* test
that exercises it. If a single mutation leaves a covering test unselected,
that's a false negative — the cardinal sin — and this test fails loudly.

The synthetic repo is small but deliberately covers the ways a naive
selector leaks:

* ``helper`` is called transitively by ``alpha`` and ``beta`` and is hit
  by *both* their tests — the exact shape the ``sysmon`` recorder bug
  dropped (it kept only the first test to touch a shared line). Mutating
  ``helper`` must pull in both callers' tests.
* class methods (``Calc.inc`` / ``Calc.dec`` / ``Calc.__init__``).
* ``unused`` is covered by no test, so changing it must select nothing —
  the matching guard that we don't over-claim either.

Each function carries a unique sentinel ``_m_<qual> = 0`` as its first body
statement; the mutation flips it to ``= 1`` in place (same line count, a
real semantic change), which the diff sees as a modification of a line the
funcmap resolves straight back to that function.
"""

import os
import subprocess
import sys
from collections import defaultdict

import pytest

from tia import cli, diff, resolve, select, store

PKG_INIT = ""

CORE = '''\
def helper(x):
    _m_helper = 0
    return x + 1 + _m_helper


def alpha(x):
    _m_alpha = 0
    return helper(x) * 2 + _m_alpha


def beta(x):
    _m_beta = 0
    return helper(x) + 3 + _m_beta


class Calc:
    def __init__(self):
        _m_Calc___init__ = 0
        self.base = _m_Calc___init__

    def inc(self, n):
        _m_Calc_inc = 0
        return n + 1 + _m_Calc_inc

    def dec(self, n):
        _m_Calc_dec = 0
        return n - 1 + _m_Calc_dec
'''

EXTRA = '''\
def gamma(x):
    _m_gamma = 0
    return x * x + _m_gamma


def unused(x):
    _m_unused = 0
    return x + _m_unused
'''

TEST_CORE = '''\
from pkg.core import alpha, beta, Calc


def test_alpha():
    assert alpha(1) == 4


def test_beta():
    assert beta(1) == 5


def test_calc_inc():
    assert Calc().inc(1) == 2


def test_calc_dec():
    assert Calc().dec(1) == 0
'''

TEST_EXTRA = '''\
from pkg.extra import gamma


def test_gamma():
    assert gamma(3) == 9
'''

FILES = {
    "conftest.py": "",  # makes the repo root the rootdir (pkg importable)
    "pkg/__init__.py": PKG_INIT,
    "pkg/core.py": CORE,
    "pkg/extra.py": EXTRA,
    "tests/test_core.py": TEST_CORE,
    "tests/test_extra.py": TEST_EXTRA,
}

# The source functions we expect to actually mutate (guards against the
# whole loop silently no-op'ing if sentinel naming ever breaks).
EXPECTED_FUNCS = {"helper", "alpha", "beta", "gamma", "Calc.inc",
                  "Calc.dec", "Calc.__init__"}


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _disk_path(root, relkey):
    return os.path.join(root, *relkey.replace("\\", "/").split("/"))


def _sentinel(qual):
    return "_m_" + qual.replace(".", "_")


@pytest.fixture(scope="module")
def recorded_repo(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("mutrepo"))
    for rel, content in FILES.items():
        p = _disk_path(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    _git(["init", "-q"], root)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], root)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], root)

    rec = subprocess.run([sys.executable, "-m", "tia", "record", "tests"],
                         cwd=root, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    assert os.path.exists(store.map_path(root)), \
        f"record produced no map.\nstdout:\n{rec.stdout}\nstderr:\n{rec.stderr}"
    tia_map = store.load_map(root)
    sources = {_disk_path(root, rel): content
               for rel, content in FILES.items()}
    return root, tia_map, sources


def _run_selection(root, tia_map):
    """Mirror cli.cmd_run's selection pipeline (no pytest run)."""
    ref = tia_map.get("ref") or "HEAD"
    changed = diff.changed_lines(ref, cwd=root)
    cosmetic = cli._cosmetic_files(changed, ref, root)
    for p in cosmetic:
        changed.pop(p, None)
    func_changes, module_files = resolve.changed_functions(
        changed, ref, root, tia_map.get("funcmaps"))
    data_changes = {p for p in changed if not p.endswith(".py")}
    module_files, _esc = select.escalate_dynamic(
        func_changes, module_files, tia_map.get("dynamic", {}))
    all_nodeids = set(tia_map["tests"])
    selected = select.select_tests(
        tia_map["tests"], func_changes, module_files, all_nodeids,
        data_changes, tia_map.get("reads", {}))
    return set(selected), cosmetic


def _coverage(tia_map):
    """(file, qual) -> set of tests whose recorded map runs that function."""
    cover = defaultdict(set)
    for nodeid, files in tia_map["tests"].items():
        for f, quals in files.items():
            for q in quals:
                cover[(f, q)].add(nodeid)
    return cover


def test_shared_helper_mapped_to_all_callers(recorded_repo):
    """sysmon-bug guard: a line shared by two tests records both."""
    _, tia_map, _ = recorded_repo
    cover = _coverage(tia_map)
    core_key = next(f for (f, q) in cover if f.endswith("core.py") and q == "helper")
    assert cover[(core_key, "helper")] == {
        "tests/test_core.py::test_alpha",
        "tests/test_core.py::test_beta",
    }


def test_mutating_each_covered_function_selects_all_its_tests(recorded_repo):
    root, tia_map, sources = recorded_repo
    cover = _coverage(tia_map)
    assert cover, "map recorded no coverage"

    mutated_quals = set()
    for (f, q), covering in sorted(cover.items()):
        disk = _disk_path(root, f)
        src = sources.get(disk)
        if src is None:
            continue
        token = f"{_sentinel(q)} = 0"
        if token not in src:
            continue  # e.g. a test-file's own function — no sentinel seeded
        mutated_quals.add(q)

        with open(disk, "w", encoding="utf-8") as fh:
            fh.write(src.replace(token, f"{_sentinel(q)} = 1", 1))
        try:
            selected, cosmetic = _run_selection(root, tia_map)
        finally:
            with open(disk, "w", encoding="utf-8") as fh:
                fh.write(src)

        assert f not in cosmetic, \
            f"a value change to {f}:{q} was wrongly judged cosmetic"
        missing = covering - selected
        assert not missing, (
            f"FALSE NEGATIVE: mutating {f}:{q} left these covering tests "
            f"unselected: {sorted(missing)}")

    # The loop must have actually exercised the real source functions, or it
    # proved nothing.
    assert EXPECTED_FUNCS <= mutated_quals, \
        f"expected to mutate {EXPECTED_FUNCS}, only hit {mutated_quals}"


def test_changing_an_uncovered_function_selects_nothing(recorded_repo):
    """The matching guard: no test covers ``unused``, so a real change to it
    must select nothing — we don't run the world for untested code."""
    root, tia_map, sources = recorded_repo
    extra_key = next(f for f in {ff for ff, _ in _coverage(tia_map)}
                     if f.endswith("extra.py"))
    disk = _disk_path(root, extra_key)
    src = sources[disk]
    with open(disk, "w", encoding="utf-8") as fh:
        fh.write(src.replace("_m_unused = 0", "_m_unused = 1", 1))
    try:
        selected, _ = _run_selection(root, tia_map)
    finally:
        with open(disk, "w", encoding="utf-8") as fh:
            fh.write(src)
    assert selected == set(), \
        f"changing the uncovered `unused` selected {selected}"
