"""Unit tests for tia's pure logic (no git / no pytest subprocess)."""

import os
import threading
from http.server import ThreadingHTTPServer

from tia import astmap, dynscan, remotestore, resolve, select, semantic, server

SOURCE = '''\
import os


CONST = 1


def top():
    return CONST


class Calc:
    def add(self, a, b):
        return a + b

    @staticmethod
    def helper():
        x = 1
        return x
'''


def test_qualname_module_level_lines_absent():
    m = astmap.line_to_qualname(SOURCE)
    assert 1 not in m          # import
    assert 4 not in m          # CONST assignment


def test_qualname_function_and_method():
    m = astmap.line_to_qualname(SOURCE)
    assert m[8] == "top"
    assert m[13] == "Calc.add"          # return a + b
    assert m[17] == "Calc.helper"       # x = 1
    assert m[18] == "Calc.helper"       # return x


def test_qualname_decorator_belongs_to_function():
    m = astmap.line_to_qualname(SOURCE)
    assert m[16] == "Calc.helper"       # the @staticmethod line


MAP = {
    "t_add": {"calc.py": {"add"}, "test_calc.py": {"t_add"}},
    "t_mul": {"calc.py": {"mul"}, "test_calc.py": {"t_mul"}},
}


def test_select_function_hit():
    sel = select.select_tests(MAP, {"calc.py": {"mul"}}, set(), {"t_add", "t_mul"})
    assert set(sel) == {"t_mul"}


def test_select_module_fallback_runs_all_touching_file():
    sel = select.select_tests(MAP, {}, {"calc.py"}, {"t_add", "t_mul"})
    assert set(sel) == {"t_add", "t_mul"}


def test_select_new_test_always_runs():
    sel = select.select_tests(MAP, {}, set(), {"t_add", "t_mul", "t_brand_new"})
    assert set(sel) == {"t_brand_new"}


def test_select_unrelated_change_runs_nothing():
    sel = select.select_tests(MAP, {"calc.py": {"sub"}}, set(), {"t_add", "t_mul"})
    assert sel == {}


READS = {
    "t_tax": {"tax.json"},
    "t_other": {"other.yaml"},
}


def test_select_data_dep_picks_only_readers():
    sel = select.select_tests(
        {"t_tax": {}, "t_other": {}}, {}, set(), {"t_tax", "t_other"},
        data_changes={"tax.json"}, reads=READS,
    )
    assert set(sel) == {"t_tax"}
    assert sel["t_tax"] == "reads tax.json"


def test_select_unread_data_file_runs_nothing():
    sel = select.select_tests(
        {"t_tax": {}, "t_other": {}}, {}, set(), {"t_tax", "t_other"},
        data_changes={"nobody_reads_this.json"}, reads=READS,
    )
    assert sel == {}


# --- ③ shallow-clone-safe resolution: baked funcmaps, no git needed -------

FUNCMAPS = {"calc.py": {12: "mul", 13: "mul"}}


def test_resolve_uses_baked_funcmaps_without_git():
    # cwd points nowhere: if this needed `git show`, it would fail.
    changes = {"calc.py": {"mod": {13}, "ins": set()}}
    fc, mf = resolve.changed_functions(
        changes, ref="deadbeef", cwd="/no/such/dir", funcmaps=FUNCMAPS)
    assert fc == {"calc.py": {"mul"}}
    assert mf == set()


def test_resolve_baked_module_level_mod_falls_back_to_file():
    changes = {"calc.py": {"mod": {1}, "ins": set()}}  # line 1 not in any func
    fc, mf = resolve.changed_functions(
        changes, ref="deadbeef", cwd="/no/such/dir", funcmaps=FUNCMAPS)
    assert fc == {}
    assert mf == {"calc.py"}


# --- ③ remote store roundtrip --------------------------------------------

def test_remote_push_pull_by_ref(tmp_path):
    local = tmp_path / "map.json"
    local.write_text('{"ref":"abc123"}', encoding="utf-8")
    remote = str(tmp_path / "remote")
    remotestore.push(str(local), remote, "abc123")

    dest = tmp_path / "pulled.json"
    got = remotestore.pull(remote, "abc123", str(dest))
    assert got is not None
    assert dest.read_text(encoding="utf-8") == '{"ref":"abc123"}'


def test_remote_pull_falls_back_to_latest(tmp_path):
    local = tmp_path / "map.json"
    local.write_text('{"ref":"abc123"}', encoding="utf-8")
    remote = str(tmp_path / "remote")
    remotestore.push(str(local), remote, "abc123")

    dest = tmp_path / "out.json"
    got = remotestore.pull(remote, "a-ref-nobody-recorded", str(dest))
    assert got is not None  # fell back to latest.json
    assert dest.read_text(encoding="utf-8") == '{"ref":"abc123"}'


# --- ④ dynamic-dispatch scan + escalation --------------------------------

def test_dynscan_flags_getattr_by_computed_name():
    src = "import sys\ndef d(a):\n    return getattr(sys, 'x' + a)()\n"
    markers = dynscan.find_markers(src)
    assert any(m.startswith("getattr()") for m in markers)


def test_dynscan_ignores_literal_getattr_and_static_code():
    static = "def f(x):\n    return getattr(x, 'attr')\n"  # literal name = safe
    assert dynscan.find_markers(static) == []
    assert dynscan.find_markers("def g():\n    return 1 + 1\n") == []


def test_dynscan_flags_eval_and_dunder_getattr():
    assert dynscan.find_markers("y = eval('1')\n")
    assert dynscan.find_markers("class C:\n    def __getattr__(self, n):\n        return n\n")


def test_escalate_widens_dynamic_file_to_file_level():
    func_changes = {"reg.py": {"handle_shout"}}
    dynamic = {"reg.py": ["getattr() @L10"]}
    module_files, escalated = select.escalate_dynamic(func_changes, set(), dynamic)
    assert module_files == {"reg.py"}      # widened
    assert escalated == {"reg.py": ["getattr() @L10"]}


def test_escalate_leaves_static_file_method_level():
    func_changes = {"calc.py": {"mul"}}
    module_files, escalated = select.escalate_dynamic(func_changes, set(), {})
    assert module_files == set()           # not widened
    assert escalated == {}


# --- ⑤ HTTP backend roundtrip (in-process threaded server) ----------------

def _serve(root):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_http_push_pull_by_ref_and_latest_fallback(tmp_path):
    httpd, base = _serve(tmp_path / "srv")
    try:
        local = tmp_path / "map.json"
        local.write_text('{"ref":"abc123"}', encoding="utf-8")
        remotestore.push(str(local), base, "abc123")

        by_ref = tmp_path / "by_ref.json"
        assert remotestore.pull(base, "abc123", str(by_ref)) is not None
        assert by_ref.read_text(encoding="utf-8") == '{"ref":"abc123"}'

        # unknown ref falls back to latest over HTTP
        latest = tmp_path / "latest.json"
        assert remotestore.pull(base, "nobody-recorded-this", str(latest)) is not None
        assert latest.read_text(encoding="utf-8") == '{"ref":"abc123"}'
    finally:
        httpd.shutdown()


def test_http_pull_missing_returns_none(tmp_path):
    httpd, base = _serve(tmp_path / "empty")
    try:
        dest = tmp_path / "out.json"
        assert remotestore.pull(base, "whatever", str(dest)) is None
        assert not dest.exists()
    finally:
        httpd.shutdown()


def test_server_rejects_path_traversal(tmp_path):
    assert server._resolve(str(tmp_path), "/maps/map.json") is not None
    assert server._resolve(str(tmp_path), "/maps/../secret") is None
    assert server._resolve(str(tmp_path), "/etc/passwd") is None
    assert server._resolve(str(tmp_path), "/maps/") is None


# --- 乙 cosmetic vs semantic change detection ----------------------------

def test_semantic_comment_only_change_is_cosmetic():
    old = "def f(x):\n    return x + 1  # add one\n"
    new = "def f(x):\n    return x + 1  # adds one to x\n"
    assert semantic.is_semantic_change(old, new) is False


def test_semantic_docstring_and_format_changes_are_cosmetic():
    old = 'def f(x):\n    """Old doc."""\n    return x+1\n'
    new = 'def f(x):\n    """A much longer, rewritten docstring."""\n    return x + 1\n'
    assert semantic.is_semantic_change(old, new) is False


def test_semantic_blank_line_change_is_cosmetic():
    old = "def f():\n    return 1\ndef g():\n    return 2\n"
    new = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    assert semantic.is_semantic_change(old, new) is False


def test_semantic_real_code_change_is_semantic():
    old = "def f(x):\n    return x + 1\n"
    new = "def f(x):\n    return x + 2\n"
    assert semantic.is_semantic_change(old, new) is True


def test_semantic_uncommenting_is_semantic_not_cosmetic():
    # The trap: old line is a comment, but uncommenting it IS a real change.
    # Only an old-vs-new comparison catches this; classifying lines wouldn't.
    old = "def f():\n    # x = compute()\n    return 0\n"
    new = "def f():\n    x = compute()\n    return 0\n"
    assert semantic.is_semantic_change(old, new) is True


def test_semantic_unparseable_is_conservative():
    assert semantic.is_semantic_change("def f(:\n", "def f():\n    pass\n") is True


# --- recorder: every test that hits a shared line must be mapped ----------

def test_recorder_records_all_contexts_per_shared_line(tmp_path):
    """Guards the sysmon-core bug: the default core on 3.12+ recorded only
    the first test to hit a line, dropping every other caller of a shared
    helper — a false negative. Importing tia.plugin forces COVERAGE_CORE
    to a tracer that records all contexts.
    """
    import sys
    import tia.plugin  # noqa: F401  (import sets COVERAGE_CORE=ctrace)
    import coverage

    assert os.environ.get("COVERAGE_CORE") != "sysmon"

    (tmp_path / "shared_mod.py").write_text(
        "def helper(x):\n    return x + 1\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        cov = coverage.Coverage(data_file=str(tmp_path / "c.db"),
                                source=["shared_mod"], config_file=False)
        cov.erase()
        cov.start()
        import shared_mod
        cov.switch_context("t1"); shared_mod.helper(1)
        cov.switch_context("t2"); shared_mod.helper(2)
        cov.switch_context(""); cov.stop(); cov.save()
        data = cov.get_data()
        fp = next(iter(data.measured_files()))
        ctxs = set(data.contexts_by_lineno(fp).get(2, []))  # the `return` line
        assert {"t1", "t2"} <= ctxs, f"shared line dropped a caller: {ctxs}"
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("shared_mod", None)
