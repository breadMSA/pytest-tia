"""Load/save the test->qualnames impact map as JSON under .tia/map.json.

v2 stores qualnames (functions/methods) instead of raw line numbers, and
records the git ref the map was captured at so `run` can diff against the
same coordinate system automatically.

v3 adds ``reads``: the non-``.py`` files each test opened, so a change to
a config/fixture selects the tests that actually read it.
"""

import datetime
import json
import os

TIA_DIR = ".tia"
MAP_NAME = "map.json"


def tia_dir(root: str) -> str:
    return os.path.join(root, TIA_DIR)


def map_path(root: str) -> str:
    return os.path.join(tia_dir(root), MAP_NAME)


def save_map(
    root: str,
    result: dict[str, dict[str, set[str]]],
    ref: str | None,
    reads: dict[str, set[str]] | None = None,
) -> str:
    os.makedirs(tia_dir(root), exist_ok=True)
    reads = reads or {}
    data = {
        "version": 3,
        "ref": ref,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "tests": {
            nodeid: {f: sorted(quals) for f, quals in files.items()}
            for nodeid, files in sorted(result.items())
        },
        "reads": {
            nodeid: sorted(files)
            for nodeid, files in sorted(reads.items())
        },
    }
    path = map_path(root)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    return path


def load_map(root: str) -> dict:
    with open(map_path(root), encoding="utf-8") as fh:
        data = json.load(fh)
    for nodeid, files in data["tests"].items():
        data["tests"][nodeid] = {f: set(quals) for f, quals in files.items()}
    data["reads"] = {
        nodeid: set(files) for nodeid, files in data.get("reads", {}).items()
    }
    return data
