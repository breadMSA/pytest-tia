"""`tia record` must not be silently emptied by pytest-xdist.

Big suites often set ``addopts = -n auto``. xdist then runs every test in
a worker process, where the recorder's coverage contexts never fire, so
the map came out empty: every test looked "new" and always ran. Safe, but
tia did nothing, and said nothing. Record now forces ``-n 0``.
"""

import os
import subprocess
import sys

import pytest

pytest.importorskip("xdist")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_record_maps_tests_despite_xdist_addopts(tmp_path):
    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_m.py").write_text(
        "from m import add\n\n"
        "def test_a():\n    assert add(1, 2) == 3\n\n"
        "def test_b():\n    assert add(0, 0) == 0\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -n 2\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init")

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    proc = subprocess.run([sys.executable, "-m", "tia", "record"],
                          cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "recorded 2 tests" in proc.stdout, proc.stdout + proc.stderr
