"""Resolve changed lines to changed qualnames, at the recorded ref.

The impact map stores qualnames captured against a git ref. To turn the
diff's old-side line numbers into the *same* qualnames, we look up the
enclosing function of every changed line in the line->qualname table for
that file *as it existed at the ref*.

That table is supplied two ways, in priority order:

1. ``funcmaps`` — baked into the map at record time. Needs no git access,
   so it survives CI shallow clones (the recorded blob may not be there).
2. ``git show ref:path`` — fallback for files not in the baked map (e.g.
   a changed file that no recorded test ever touched).

Returns:
    func_changes: {path: {qualname, ...}}  — functions whose body changed
    module_files: {path, ...}              — files with a module-level
                                             *modification* (fallback)
"""

import subprocess

from tia import astmap


def git_show(ref: str, path: str, cwd: str) -> str | None:
    """The file's source at ``ref``, or None if it doesn't exist there."""
    out = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return out.stdout if out.returncode == 0 else None


# Pre-1.2 name, kept so callers that imported it keep working.
_git_show = git_show


def changed_functions(
    changes: dict[str, dict[str, set[int]]],
    ref: str,
    cwd: str,
    funcmaps: dict[str, dict[int, str]] | None = None,
) -> tuple[dict[str, set[str]], set[str]]:
    funcmaps = funcmaps or {}
    func_changes: dict[str, set[str]] = {}
    module_files: set[str] = set()

    for path, kinds in changes.items():
        if not path.endswith(".py"):
            continue  # non-Python deps are handled separately (silent deps)

        l2q = funcmaps.get(path)
        if l2q is None:
            # Not in the baked map — fall back to reading the blob at the ref.
            src = git_show(ref, path, cwd)
            if src is None:
                continue  # new at this ref, or not fetched — new tests run anyway
            try:
                l2q = astmap.line_to_qualname(src)
            except SyntaxError:
                module_files.add(path)  # can't parse: be conservative
                continue

        funcs: set[str] = set()
        for ln in kinds["mod"]:
            qual = l2q.get(ln)
            if qual is None:
                module_files.add(path)  # module-level modification
            else:
                funcs.add(qual)
        for ln in kinds["ins"]:
            qual = l2q.get(ln)
            if qual is not None:
                funcs.add(qual)  # insertion inside an existing function
            # module-level insertion (new func/test) deliberately ignored

        if funcs:
            func_changes[path] = funcs

    return func_changes, module_files
