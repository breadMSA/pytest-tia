"""Detect reflection / dynamic-dispatch markers coverage can't trace.

Coverage records the call edges that *actually executed* during `record`.
When a file leans on reflection — ``getattr`` by a computed name,
``importlib``, ``eval``/``exec``, a ``__getattr__`` hook — the edges
coverage saw may be incomplete or unstable across recordings, so
method-level selection on that file can't be fully trusted.

We do **not** try to resolve these statically; which method a
``getattr(obj, name)`` lands on is undecidable in general. We only *flag*
the file, so `run` can degrade from method-level to safe file-level
selection there and say why. Pair it with a periodic full run — that's
the honest safety net, not a precise solution.
"""

import ast

# Builtins whose target/effect isn't statically knowable.
# ``globals()``/``locals()`` expose the namespace for arbitrary runtime
# name lookup or mutation, so coverage can't trust the edges either.
_DYNAMIC_BUILTINS = {"eval", "exec", "__import__", "globals", "locals"}
# Attribute access by name; safe only when the name is a literal.
_DYNAMIC_ATTR = {"getattr", "setattr", "delattr"}
# Dynamic-attribute hooks: defining one makes a class's attributes runtime.
_DYNAMIC_DEFS = {"__getattr__", "__getattribute__"}


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def find_markers(source: str) -> list[str]:
    """Return sorted ``"marker @Lnn"`` strings, empty if the file is static."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    markers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _DYNAMIC_ATTR:
                # getattr(obj, "literal") is statically resolvable — ignore.
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    continue
                markers.add(f"{name}() @L{node.lineno}")
            elif name in _DYNAMIC_BUILTINS:
                markers.add(f"{name}() @L{node.lineno}")
            elif name == "import_module":  # importlib.import_module(...)
                markers.add(f"import_module() @L{node.lineno}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in _DYNAMIC_DEFS:
                markers.add(f"{node.name} @L{node.lineno}")

    return sorted(markers)