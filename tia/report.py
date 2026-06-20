"""Human-facing explanations of a `tia run` selection.

The whole pitch of tia is "you can see *why* every test was picked". On the
terminal that's the ``~``/``->`` lines; in CI it should be a table on the PR
itself. Both come from here so they never drift apart.

``impact_tag`` is the single source of truth for "what did this file do to
the selection"; ``render_markdown`` arranges those tags (plus the ignored
and selected lists) into a GitHub Step Summary.
"""


def impact_tag(
    path: str,
    func_changes: dict[str, set[str]],
    module_files: set[str],
    escalated: dict[str, list[str]],
    data_changes: set[str],
    reads: dict[str, set[str]],
) -> str:
    """One-line reason a changed file contributed to the selection."""
    funcs = func_changes.get(path)
    if path in escalated:
        return f"{', '.join(sorted(funcs or []))} -> file-level (dynamic)"
    if funcs:
        return ", ".join(sorted(funcs))
    if path in module_files:
        return "module-level"
    if path in data_changes:
        n = sum(1 for f in reads.values() if path in f)
        return f"data dep ({n} reader{'' if n == 1 else 's'})"
    return "no covered funcs"


def _cell(text: str) -> str:
    """Escape a value for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(
    ref: str | None,
    changed: list[str],
    func_changes: dict[str, set[str]],
    module_files: set[str],
    escalated: dict[str, list[str]],
    data_changes: set[str],
    reads: dict[str, set[str]],
    cosmetic: set[str],
    selected: dict[str, str],
    total: int,
) -> str:
    """A GitHub-Step-Summary report for one ``tia run``."""
    n = len(selected)
    short = (ref or "?")[:8]
    pct = f"{100 * (total - n) // total}% skipped" if total else "n/a"

    out: list[str] = ["## 🎯 tia — Test Impact Analysis", ""]
    if not selected:
        saved = "100% saved" if total else "n/a"
        out.append(f"**No affected tests — skipping all {total} tests ({saved}).** "
                   f"_(ref `{short}`)_")
    else:
        out.append(f"**Selected {n} / {total} tests — {pct}.** _(ref `{short}`)_")
    out.append("")

    if changed:
        out += ["### Changed files", "", "| File | Impact |", "|------|--------|"]
        for path in sorted(changed):
            tag = impact_tag(path, func_changes, module_files, escalated,
                             data_changes, reads)
            out.append(f"| `{_cell(path)}` | {_cell(tag)} |")
        out.append("")

    if cosmetic:
        out += ["### Ignored (non-semantic)", "",
                "Comments, formatting, docstrings, or provably-dead type hints "
                "— no test could observe these.", ""]
        for path in sorted(cosmetic):
            out.append(f"- `{_cell(path)}`")
        out.append("")

    if escalated:
        out += ["### ⚠️ Widened to file-level", "",
                "These files use reflection coverage can't trace; tia ran "
                "*every* test touching them. Run the full suite periodically.", ""]
        for path, markers in sorted(escalated.items()):
            out.append(f"- `{_cell(path)}` — {_cell(', '.join(markers))}")
        out.append("")

    if selected:
        out += ["### Selected tests", "", "| Test | Why |", "|------|-----|"]
        for nodeid, reason in sorted(selected.items()):
            out.append(f"| `{_cell(nodeid)}` | {_cell(reason)} |")
        out.append("")

    return "\n".join(out).rstrip() + "\n"
