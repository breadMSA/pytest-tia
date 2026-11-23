"""Map source lines to the qualified name of their enclosing function.

`line_to_qualname("...source...")` returns ``{lineno: "Class.method"}``.
Lines that belong to no function (module-level statements, imports,
class-body lines between methods) are simply absent from the dict — the
caller treats those as "module-level".

Decorator lines count as part of the function they decorate: changing a
decorator changes the function's behaviour.

Only *functions* own lines. A class body (its attributes, dataclass field
defaults, its decorators) runs once at import time, never inside a test, so
no test can ever be credited with it. Those lines are left out, which makes
the caller treat a change to them as module-level. Methods still get the
``Class.method`` qualname.
"""

import ast


def line_to_qualname(source: str) -> dict[int, str]:
    tree = ast.parse(source)
    mapping: dict[int, str] = {}

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, stack + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = ".".join(stack + [child.name])
                start = min(
                    [child.lineno] + [d.lineno for d in child.decorator_list]
                )
                end = child.end_lineno or child.lineno
                for ln in range(start, end + 1):
                    mapping[ln] = qual  # outer assigned first...
                visit(child, stack + [child.name])  # ...inner overwrites
            else:
                visit(child, stack)

    visit(tree, [])
    return mapping


def body_line_to_qualname(source: str) -> dict[int, str]:
    """Like ``line_to_qualname`` but only for lines inside function *bodies*.

    The ``def`` line, decorators and default arguments run when the module
    is imported; body lines run only when the function is called. So a
    body line that executed outside any test means the function was called
    at import time (or by a pytest hook), and a change to it reaches every
    test that imports the module.
    """
    tree = ast.parse(source)
    mapping: dict[int, str] = {}

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, stack + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = ".".join(stack + [child.name])
                first = child.body[0].lineno
                if first > child.lineno:  # one-line defs share the def line
                    for ln in range(first, (child.end_lineno or first) + 1):
                        mapping[ln] = qual
                visit(child, stack + [child.name])
            else:
                visit(child, stack)

    visit(tree, [])
    return mapping


def line_to_qualname_from_file(path: str) -> dict[int, str]:
    with open(path, encoding="utf-8") as fh:
        return line_to_qualname(fh.read())
