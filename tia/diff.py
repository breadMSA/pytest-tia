"""Turn a git diff into a set of changed line numbers per file.

We diff against a ref (default HEAD, which includes both staged and
unstaged work) using ``--unified=0`` so each hunk header pins down the
exact line range that changed.

Crucially we read the **old side** of each hunk (``-a,b``). The impact
map was recorded at that same ref, so its line numbers live in the old
coordinate system. Using the new side would misattribute every edit
that shifts line numbers (insert one line and everything below "moved").
"""

import subprocess
from collections import defaultdict


def changed_lines(ref: str = "HEAD", cwd: str = ".") -> dict[str, set[int]]:
    out = subprocess.run(
        ["git", "diff", "--unified=0", "--no-color", "--relative", ref],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    result: dict[str, set[int]] = defaultdict(set)
    current: str | None = None
    for line in out.stdout.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                current = None
            else:
                current = path[2:] if path.startswith("b/") else path
        elif line.startswith("@@") and current:
            # Format: @@ -a,b +c,d @@   (the "-a,b" is the old side)
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
                # Pure insertion: no old lines were touched, but code was
                # inserted just after old line `start`, so flag the lines
                # straddling that point (whoever ran them is affected).
                result[current].update({start, start + 1})
            else:
                result[current].update(range(start, start + count))
    return dict(result)
