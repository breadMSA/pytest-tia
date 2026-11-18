"""The Python API that downstream tools build on must not drift silently.

Projects drive their own recorders and selectors from these functions (and
write v5 maps by hand), pinned to a release. Renaming a parameter or a
function here breaks them on upgrade, so changes must be deliberate.
"""

import inspect

from tia import astmap, diff, dynscan, resolve, select, semantic, store

STABLE = {
    astmap.line_to_qualname: ["source"],
    astmap.line_to_qualname_from_file: ["path"],
    dynscan.find_markers: ["source"],
    diff.changed_lines: ["ref", "cwd"],
    resolve.git_show: ["ref", "path", "cwd"],
    resolve.changed_functions: ["changes", "ref", "cwd", "funcmaps"],
    select.select_tests: ["map_tests", "func_changes", "module_files",
                          "all_nodeids", "data_changes", "reads"],
    select.escalate_dynamic: ["func_changes", "module_files", "dynamic"],
    select.recorded_seconds: ["durations", "nodeids"],
    semantic.is_semantic_change: ["old_src", "new_src"],
}


def test_stable_signatures():
    for fn, params in STABLE.items():
        got = list(inspect.signature(fn).parameters)
        assert got[: len(params)] == params, f"{fn.__module__}.{fn.__name__}: {got}"


def test_git_show_old_name_still_works():
    assert resolve._git_show is resolve.git_show


def test_map_v5_loads_without_durations(tmp_path):
    (tmp_path / ".tia").mkdir()
    (tmp_path / ".tia" / "map.json").write_text(
        '{"version": 5, "ref": null, "created": "x", "tests": {}}')
    m = store.load_map(str(tmp_path))
    assert m["durations"] == {} and m["dynamic"] == {} and m["funcmaps"] == {}


def test_recorded_seconds():
    assert select.recorded_seconds({"a": 1.0, "b": 0.5}, ["a", "b"]) == 1.5
    assert select.recorded_seconds({"a": 1.0}, ["a", "c"]) is None
