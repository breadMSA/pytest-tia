"""Turn a git diff into changed line numbers per file.

We diff against a ref (default HEAD, which includes both staged and
unstaged work) using ``--unified=0`` so each hunk header pins down the
exact line range that changed, and we read the **old side** (``-a,b``)
so the numbers live in the same coordinate system the impact map was
recorded in (the map was recorded at that ref).

Per file we separate two kinds of change:

* ``mod`` — existing old lines that were modified or deleted.
* ``ins`` — anchor lines straddling a pure insertion (no old lines).

Renames are turned off (``--no-renames``): a renamed file shows up as the
old path deleted plus a new path added. Otherwise a 100%-similar rename has
no hunks at all and the moved code looks unchanged. A deleted file is keyed
by its old path, with every old line marked ``mod``.

They are handled differently downstream: a module-level *modification*
(e.g. changing a constant) is a real impact, but a module-level
*insertion* (a brand-new function or test) is not.
"""

import subprocess
from collections import defaultdict


def changed_lines(ref: str = "HEAD", cwd: str = ".") -> dict[str, dict[str, set[int]]]:
    out = subprocess.run(
        ["git", "diff", "--unified=0", "--no-color", "--no-renames", "--relative", ref],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    result: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: {"mod": set(), "ins": set()}
    )
    current: str | None = None
    old_path: str | None = None
    for line in out.stdout.splitlines():
        if line.startswith("--- "):
            path = line[4:].strip()
            old_path = None if path == "/dev/null" else (
                path[2:] if path.startswith("a/") else path
            )
        elif line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                current = old_path  # deleted: keep the old path
            else:
                current = path[2:] if path.startswith("b/") else path
        elif line.startswith("@@") and current:
            try:
                minus = line.split(" ")[1].lstrip("-")
            except IndexError:
                continue
            if "," in minus:
                start_s, count_s = minus.split(",")
                start, count = int(start_s), int(count_s)
            else:
                start, count = int(minus), 1
            if count == 0:
                result[current]["ins"].update({start, start + 1})
            else:
                result[current]["mod"].update(range(start, start + count))
    return dict(result)
