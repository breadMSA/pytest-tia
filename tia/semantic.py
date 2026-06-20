"""Tell a real code change apart from a cosmetic one.

A huge fraction of real-world commits are "fix a typo", "add a docstring",
"reformat with black", or "tidy up the type hints". Those touch a file's
lines but change nothing the tests can observe — yet line-diffing still
selects every test on the file (or, for a core file, half the suite).
That's correct-but-annoying over-selection.

The clean test for "did anything semantic change?" is the AST itself:
comments and whitespace are never in the AST, so a change confined to them
leaves the tree identical. Docstrings *are* in the AST (as bare string
expressions), so we strip those before comparing. Crucially we compare the
**old and new** trees, not just classify lines — uncommenting `# x = 1`
flips a comment into code, and only an old-vs-new comparison catches it.

Type hints (v1.1)
------------------
We *also* strip the type-only constructs that are provably dead at run
time, so "added the type hints" stops triggering tests. But only the ones
that are genuinely inert — the naive belief that "annotations have no
runtime effect" is **false** in modern Python: ``dataclasses``, ``pydantic``,
``attrs`` and ``typing.get_type_hints`` all read ``__annotations__``. So:

* ``if TYPE_CHECKING:`` bodies — stripped. At run time ``TYPE_CHECKING``
  is ``False``, so the block never executes (this is where most type-only
  churn lives: imports added purely for hints).
* **function-local** annotations — stripped. PEP 526: local variable
  annotations are never evaluated and never stored anywhere.

Deliberately **kept** (a change to them is semantic): function argument /
return annotations and class- or module-level annotations, because those
land in ``__annotations__`` and a framework can read them. Stripping those
would hide a real dataclass/pydantic field-type change — a false negative,
the one sin this tool exists to avoid.

Conservative by construction: anything we can't parse, or any genuine
token change, counts as semantic. This only ever *removes* false
positives; it can never hide a real change.
"""

import ast

_SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in [n for n in ast.walk(tree) if isinstance(n, _SCOPES)]:
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return tree


def _is_type_checking_test(test: ast.expr) -> bool:
    """True for ``TYPE_CHECKING`` / ``typing.TYPE_CHECKING`` (the positive form).

    ``if not TYPE_CHECKING:`` is a ``UnaryOp`` and won't match — its body
    *does* run at runtime, so we leave it alone (conservative).
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


class _TypeStripper(ast.NodeTransformer):
    """Normalise away the provably-inert type-only constructs.

    ``func_depth`` tracks whether we're inside a function body, where local
    annotations are inert. It resets across a ``ClassDef`` boundary because
    a class body's annotations feed ``__annotations__`` even when the class
    is nested in a function.
    """

    def __init__(self) -> None:
        self.func_depth = 0

    def visit_If(self, node: ast.If) -> ast.AST:
        if _is_type_checking_test(node.test):
            # Dead body; keep `orelse` (it runs) and keep recursing into it.
            node.body = [ast.Pass()]
            node.orelse = [self.visit(n) for n in node.orelse]
            return node
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.AST) -> ast.AST:
        self.func_depth += 1
        self.generic_visit(node)
        self.func_depth -= 1
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        saved, self.func_depth = self.func_depth, 0
        self.generic_visit(node)
        self.func_depth = saved
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if self.func_depth > 0:
            if node.value is None:
                return None  # bare `x: int` in a function — pure no-op
            # `x: int = v` -> `x = v`; the value runs, the annotation doesn't.
            return ast.Assign(targets=[node.target], value=node.value)
        return node  # class/module level: kept, it reaches __annotations__


def _strip_types(tree: ast.AST) -> ast.AST:
    return _TypeStripper().visit(tree)


def is_semantic_change(old_src: str, new_src: str) -> bool:
    """True if old -> new changes anything a running test could observe.

    Strips comments/whitespace/docstrings and the provably-dead type-only
    constructs before comparing. Returns True (keep the change) if either
    side can't be parsed.
    """
    try:
        old = _strip_types(_strip_docstrings(ast.parse(old_src)))
        new = _strip_types(_strip_docstrings(ast.parse(new_src)))
    except SyntaxError:
        return True
    return ast.dump(old) != ast.dump(new)
