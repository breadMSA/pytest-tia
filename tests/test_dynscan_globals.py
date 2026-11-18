"""Coverage for the globals()/locals() dynamic markers."""

from tia import dynscan


def test_dynscan_flags_globals():
    src = "def f():\n    return globals()['x']\n"
    assert any("globals" in m for m in dynscan.find_markers(src))


def test_dynscan_flags_locals():
    src = "def f():\n    return locals()\n"
    assert any("locals" in m for m in dynscan.find_markers(src))


def test_dynscan_static_file_still_clean():
    src = "def add(a, b):\n    return a + b\n"
    assert dynscan.find_markers(src) == []