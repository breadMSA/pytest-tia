"""`tia record` under pytest-xdist records in parallel, in the workers.

Big suites often set ``addopts = -n auto``. xdist runs every test in a
worker process, which never sees the RecordPlugin handed to pytest.main.
1.1.2 forced ``-n 0``; now each worker records its own coverage file and
the controller combines them, so the map matches a serial recording.
"""

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("xdist")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _project(tmp_path, addopts):
    (tmp_path / "m.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def mul(a, b):\n    return a * b\n\n"
        "def load():\n    with open('conf.txt') as fh:\n        return fh.read()\n"
    )
    (tmp_path / "conf.txt").write_text("x\n")
    (tmp_path / "test_m.py").write_text(
        "import pytest\nfrom m import add, mul, load\n\n"
        "def test_a():\n    assert add(1, 2) == 3\n\n"
        "def test_b():\n    assert add(0, 0) == 0\n\n"
        "@pytest.mark.xdist_group('g1')\n"
        "def test_c():\n    assert mul(2, 3) == 6\n\n"
        "def test_d():\n    assert load().strip() == 'x'\n"
    )
    (tmp_path / "pytest.ini").write_text(f"[pytest]\naddopts = {addopts}\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init")


def _record(tmp_path):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    proc = subprocess.run([sys.executable, "-m", "tia", "record"],
                          cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    with open(tmp_path / ".tia" / "map.json", encoding="utf-8") as fh:
        return proc.stdout, json.load(fh)


@pytest.mark.parametrize("addopts", ["-n 2", "-n 2 --dist loadgroup"])
def test_record_in_parallel_matches_serial(tmp_path, addopts):
    par = tmp_path / "par"
    ser = tmp_path / "ser"
    par.mkdir()
    ser.mkdir()
    _project(par, addopts)
    _project(ser, "-p no:xdist")

    out, pmap = _record(par)
    assert "recorded 4 tests" in out and "2 xdist workers" in out, out
    _, smap = _record(ser)

    # Same tests, same functions — and loadgroup's "@g1" suffix stripped.
    assert pmap["tests"] == smap["tests"]
    assert pmap["tests"]["test_m.py::test_c"] == {"m.py": ["mul"], "test_m.py": ["test_c"]}
    # File reads made in workers reach the map.
    assert pmap["reads"] == {"test_m.py::test_d": ["conf.txt"]}
    assert set(pmap["durations"]) == set(smap["durations"]) == set(pmap["tests"])
    # Worker coverage files are combined away.
    assert not [f for f in os.listdir(par / ".tia") if f.startswith("coverage.")]
