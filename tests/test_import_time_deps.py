"""End-to-end regressions for changes whose effect lands outside any test.

Each case records a real repo, commits one change, and checks that
``tia run`` would select the tests the full suite shows are affected
(issue #7):

* a dataclass field default (class body, runs at import time),
* a module renamed, with the importer updated (100%-similar rename),
* pytest's own config (read at startup, never opened by a test),
* a function called at module scope (its body runs at import time).
"""

import os
import subprocess
import sys

import pytest

from tia import cli, imports, pytestcfg, store

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t",
       "-c", "commit.gpgSign=false"]


def _git(root, *args):
    subprocess.run([*GIT, *args], cwd=root, check=True,
                   capture_output=True, text=True)


def _write(root, files):
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


def _record(root, files):
    _write(root, files)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    env = dict(os.environ, PYTHONPATH=root, PYTHONDONTWRITEBYTECODE="1")
    env.pop("GITHUB_STEP_SUMMARY", None)
    rec = subprocess.run([sys.executable, "-m", "tia", "record"], cwd=root,
                         env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    assert os.path.exists(store.map_path(root)), rec.stdout + rec.stderr
    return store.load_map(root)


def _select(root, tia_map):
    plan = cli.plan_selection(root, tia_map, tia_map["ref"], set(tia_map["tests"]))
    return plan["selected"]


def test_dataclass_default_change_selects_constructing_test(tmp_path):
    root = str(tmp_path)
    tia_map = _record(root, {
        "model.py": "from dataclasses import dataclass\n\n"
                    "@dataclass\nclass Item:\n    price: int = 3\n",
        "test_model.py": "from model import Item\n\n"
                         "def test_default():\n    assert Item().price == 3\n",
    })
    _write(root, {"model.py": "from dataclasses import dataclass\n\n"
                              "@dataclass\nclass Item:\n    price: int = 4\n"})
    assert set(_select(root, tia_map)) == {"test_model.py::test_default"}


def test_rename_selects_tests_using_the_moved_function(tmp_path):
    root = str(tmp_path)
    tia_map = _record(root, {
        "impl.py": 'def label():\n    return "ok"\n',
        "api.py": "from impl import label\n",
        "test_api.py": "from api import label\n\n"
                       'def test_label():\n    assert label() == "ok"\n\n'
                       "def test_length():\n    assert len(label()) == 2\n",
    })
    _git(root, "mv", "impl.py", "labels.py")
    _write(root, {"api.py": "from labels import label\n"})
    _git(root, "commit", "-qam", "rename")
    assert set(_select(root, tia_map)) == {
        "test_api.py::test_label", "test_api.py::test_length"}


def test_pytest_ini_change_selects_everything(tmp_path):
    root = str(tmp_path)
    tia_map = _record(root, {
        "pytest.ini": "[pytest]\nfilterwarnings = default::UserWarning\n",
        "test_warning.py": "import warnings\n\n"
                           "def test_warning():\n"
                           '    warnings.warn("example", UserWarning)\n',
    })
    _write(root, {"pytest.ini": "[pytest]\nfilterwarnings = error::UserWarning\n"})
    sel = _select(root, tia_map)
    assert set(sel) == {"test_warning.py::test_warning"}
    assert "pytest config" in sel["test_warning.py::test_warning"]


def test_function_called_at_import_time_selects_importers(tmp_path):
    root = str(tmp_path)
    tia_map = _record(root, {
        "cfg.py": "def load():\n    return 3\n\nLIMIT = load()\n",
        "test_cfg.py": "from cfg import LIMIT\n\n"
                       "def test_limit():\n    assert LIMIT == 3\n",
    })
    assert tia_map["import_time"] == {"cfg.py": {"load"}}
    _write(root, {"cfg.py": "def load():\n    return 4\n\nLIMIT = load()\n"})
    assert set(_select(root, tia_map)) == {"test_cfg.py::test_limit"}


def test_function_called_by_tests_only_stays_method_level(tmp_path):
    """Import-time tracking must not widen ordinary function changes."""
    root = str(tmp_path)
    tia_map = _record(root, {
        "calc.py": "def add(a, b):\n    return a + b\n\n"
                   "def mul(a, b):\n    return a * b\n",
        "test_calc.py": "from calc import add, mul\n\n"
                        "def test_add():\n    assert add(1, 2) == 3\n\n"
                        "def test_mul():\n    assert mul(2, 2) == 4\n",
    })
    assert tia_map["import_time"] == {}
    _write(root, {"calc.py": "def add(a, b):\n    return a + b\n\n"
                             "def mul(a, b):\n    return b * a\n"})
    assert set(_select(root, tia_map)) == {"test_calc.py::test_mul"}


# --- pure helpers -----------------------------------------------------------

def test_import_graph_follows_packages_relative_imports_and_conftest(tmp_path):
    root = str(tmp_path)
    _write(root, {
        "src/pkg/__init__.py": "from .core import run\n",
        "src/pkg/core.py": "from . import util\n",
        "src/pkg/util.py": "",
        "src/pkg/other.py": "",
        "tests/conftest.py": "import helpers\n",
        "tests/helpers.py": "",
        "tests/test_a.py": "from pkg import run\n",
    })
    reach = imports.reachable(root, {"tests/test_a.py"})["tests/test_a.py"]
    assert {"src/pkg/__init__.py", "src/pkg/core.py", "src/pkg/util.py",
            "tests/conftest.py", "tests/helpers.py"} <= reach
    assert "src/pkg/other.py" not in reach


@pytest.mark.parametrize("path, old, new, changed", [
    ("pytest.ini", "[pytest]\na = 1\n", "[pytest]\na = 2\n", True),
    ("pytest.ini", "[pytest]\na = 1\n", "[pytest]\na = 1\n# hi\n", False),
    ("setup.cfg", "[flake8]\nx = 1\n", "[flake8]\nx = 2\n", False),
    ("setup.cfg", "[tool:pytest]\nx = 1\n", "[tool:pytest]\nx = 2\n", True),
    ("tox.ini", "[tox]\n", "[tox]\n[pytest]\naddopts = -x\n", True),
    ("pytest.ini", "[pytest]\n", None, True),
])
def test_pytest_ini_style_sections(path, old, new, changed):
    assert pytestcfg.pytest_section_changed(path, old, new) is changed


@pytest.mark.skipif(sys.version_info < (3, 11), reason="needs tomllib")
def test_pyproject_only_pytest_table_counts():
    base = '[project]\nversion = "1"\n[tool.pytest.ini_options]\naddopts = "-q"\n'
    bump = base.replace('"1"', '"2"')
    opts = base.replace('"-q"', '"-x"')
    assert not pytestcfg.pytest_section_changed("pyproject.toml", base, bump)
    assert pytestcfg.pytest_section_changed("pyproject.toml", base, opts)


@pytest.mark.parametrize("mode, code", [("skip", 0), ("fail", 5), ("all", 0)])
def test_on_empty(tmp_path, monkeypatch, capsys, mode, code):
    root = str(tmp_path)
    _record(root, {"calc.py": "def add(a, b):\n    return a + b\n",
                   "test_calc.py": "from calc import add\n\n"
                                   "def test_add():\n    assert add(1, 2) == 3\n"})
    monkeypatch.chdir(root)
    monkeypatch.setenv("PYTHONPATH", root)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert cli.main(["run", "--on-empty", mode]) == code
    out = capsys.readouterr().out
    assert ("running all" in out) is (mode == "all")


def test_dynamic_widening_does_not_reach_importers(tmp_path):
    """Reflection widens to tests touching the file, not to all importers."""
    root = str(tmp_path)
    tia_map = _record(root, {
        "dyn.py": "def f(name):\n    return getattr(str, name)\n\n"
                  "def g():\n    return 1\n",
        "test_f.py": "from dyn import f\n\n"
                     "def test_f():\n    assert f('upper')\n",
        "test_other.py": "import dyn\n\n"
                         "def test_other():\n    assert True\n",
    })
    _write(root, {"dyn.py": "def f(name):\n    return getattr(str, name)\n\n"
                            "def g():\n    return 2\n"})
    # g ran in no test, but dyn.py is dynamic: widened to the tests touching
    # it (test_f), not to test_other, which only imports it.
    assert set(_select(root, tia_map)) == {"test_f.py::test_f"}
