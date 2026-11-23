"""Static import graph: which project files a test module pulls in.

Coverage only credits a test with lines that ran *during* that test. Code
that runs at import time (constants, class bodies, dataclass defaults,
decorators, a function called from module scope) runs once, before any
test, so no test is credited with it. A module-level change to such a file
then selected only the tests that happened to call a function in it, and
none at all when a test only read a constant or built a dataclass.

So a module-level change selects every test whose module imports the
changed file, directly or through other project files. The edges come from
the ``import`` statements of the current tree, parsed with ``ast`` (no code
is run). Every import counts, including ones inside functions and ``try``
blocks, and a module name that matches several files links to all of them.
Both of those over-approximate, which can only add tests, never drop one.

A test module also depends on the ``conftest.py`` files above it, which
pytest imports implicitly.
"""

import ast
import os

# Directory names that never hold project source.
_SKIP_DIRS = {
    ".git", ".tia", "__pycache__", ".pytest_cache", ".mypy_cache", ".tox",
    ".nox", "node_modules", ".venv", "venv", "env", "site-packages", "build",
    "dist",
}


def _py_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS
                       and not d.endswith((".egg-info", ".dist-info"))]
        for name in filenames:
            if name.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                out.append(rel.replace(os.sep, "/"))
    return out


def _module_names(rel: str, files: set[str]) -> set[str]:
    """Dotted names ``rel`` can be imported as.

    Two candidates: the name relative to the first ancestor directory that
    isn't a package (how pytest's rootdir insertion and ``src`` layouts see
    it), and the name relative to the repo root (``PYTHONPATH=.`` with
    namespace packages).
    """
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    names = {".".join(parts)} if parts else set()
    # Walk up while the parent directory is a regular package.
    dirs = rel.split("/")[:-1]
    start = len(dirs)
    while start > 0 and "/".join(dirs[:start] + ["__init__.py"]) in files:
        start -= 1
    pkg_parts = parts[start:]
    if pkg_parts:
        names.add(".".join(pkg_parts))
    return names


def _package_of(rel: str, files: set[str]) -> list[str]:
    """The dotted package parts ``rel`` lives in, for relative imports."""
    dirs = rel.split("/")[:-1]
    start = len(dirs)
    while start > 0 and "/".join(dirs[:start] + ["__init__.py"]) in files:
        start -= 1
    return dirs[start:]


def _imported_names(tree: ast.AST, rel: str, files: set[str]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                pkg = _package_of(rel, files)  # same for a/__init__.py and a/m.py
                up = node.level - 1
                if up > len(pkg):
                    continue
                base = pkg[: len(pkg) - up] if up else pkg
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            if mod:
                names.add(mod)
            for alias in node.names:
                if alias.name != "*":
                    names.add(f"{mod}.{alias.name}" if mod else alias.name)
    # Importing a.b.c runs a/__init__ and a/b/__init__ too.
    for name in list(names):
        parts = name.split(".")
        for i in range(1, len(parts)):
            names.add(".".join(parts[:i]))
    return names


def import_graph(root: str) -> dict[str, set[str]]:
    """``{file: {project files it imports directly}}`` for the current tree."""
    files = _py_files(root)
    fileset = set(files)
    by_name: dict[str, set[str]] = {}
    for rel in files:
        for name in _module_names(rel, fileset):
            by_name.setdefault(name, set()).add(rel)

    graph: dict[str, set[str]] = {}
    for rel in files:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError, ValueError):
            graph[rel] = set()
            continue
        deps: set[str] = set()
        for name in _imported_names(tree, rel, fileset):
            deps |= by_name.get(name, set())
        deps.discard(rel)
        graph[rel] = deps
    return graph


def _conftests_above(rel: str, files: set[str]) -> list[str]:
    dirs = rel.split("/")[:-1]
    out = []
    for i in range(len(dirs) + 1):
        cand = "/".join(dirs[:i] + ["conftest.py"])
        if cand in files and cand != rel:
            out.append(cand)
    return out


def reachable(root: str, test_files: set[str]) -> dict[str, set[str]]:
    """``{test_file: every project file it imports, transitively}``.

    Includes the test file itself and the conftests pytest loads for it.
    """
    graph = import_graph(root)
    files = set(graph)
    out: dict[str, set[str]] = {}
    for test_file in test_files:
        seen: set[str] = set()
        stack = [test_file, *_conftests_above(test_file, files)]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(graph.get(cur, ()))
        out[test_file] = seen
    return out
