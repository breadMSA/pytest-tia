"""Unit tests for tia's pure logic (no git / no pytest subprocess)."""

from tia import astmap, select

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
