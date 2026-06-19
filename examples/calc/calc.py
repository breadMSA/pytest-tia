"""A tiny module so each test clearly exercises a different function."""

import json
import os

_TAX_FILE = os.path.join(os.path.dirname(__file__), "tax.json")


def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def mul(a, b):
    return a * b


def div(a, b):
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b


def apply_tax(amount):
    # The tax rate lives in a data file, not in the source — a "silent"
    # dependency that coverage.py can't see. tia tracks it via the open().
    with open(_TAX_FILE, encoding="utf-8") as fh:
        rate = json.load(fh)["rate"]
    return amount * (1 + rate)
