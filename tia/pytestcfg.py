"""Detect a change to pytest's own configuration.

pytest reads its config file once at startup, before any test runs, so the
recorder never sees a test open it. But the settings in it (``addopts``,
``filterwarnings``, ``markers``, ``xfail_strict``, ...) can change the
outcome of any test. A real change to the pytest section therefore selects
the whole suite.

Only the pytest section is compared, so bumping a dependency in
``pyproject.toml`` or editing ``[flake8]`` in ``setup.cfg`` doesn't trigger
a full run. If a file can't be parsed, the change counts (conservative).
"""

import configparser
import os

# basename -> how to pull the pytest section out of it
CONFIG_FILES = {
    "pytest.ini": "ini:pytest",
    ".pytest.ini": "ini:pytest",
    "tox.ini": "ini:pytest",
    "setup.cfg": "ini:tool:pytest",
    "pyproject.toml": "toml",
}


def is_config_file(path: str) -> bool:
    return os.path.basename(path) in CONFIG_FILES


def _section(text: str | None, kind: str):
    """A comparable value for the pytest section of ``text`` (None = absent)."""
    if text is None:
        return None
    if kind == "toml":
        try:
            import tomllib
        except ImportError:  # Python 3.10: compare the whole file
            return text
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return text
        return repr(data.get("tool", {}).get("pytest"))
    section = kind.split(":", 1)[1]
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return text
    if not parser.has_section(section):
        return None
    return sorted(parser.items(section))


def pytest_section_changed(path: str, old: str | None, new: str | None) -> bool:
    kind = CONFIG_FILES[os.path.basename(path)]
    return _section(old, kind) != _section(new, kind)
