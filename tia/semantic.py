"""Tell a real code change apart from a cosmetic one.

A huge fraction of real-world commits are "fix a typo", "add a docstring",
or "reformat with black". Those touch a file's lines but change nothing
the tests can observe — yet line-diffing still selects every test on the
file (or, for a core file, half the suite). That's correct-but-annoying
over-selection.

The clean test for "did anything semantic change?" is the AST itself:
comments and whitespace are never in the AST, so a change confined to them
leaves the tree identical. Docstrings *are* in the AST (as bare string
expressions), so we strip those before comparing. Crucially we compare the
**old and new** trees, not just classify lines — uncommenting `# x = 1`
flips a comment into code, and only an old-vs-new comparison catches it.

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


def is_semantic_change(old_src: str, new_src: str) -> bool:
    """True if old -> new changes anything beyond comments/whitespace/docstrings.

    Returns True (keep the change) if either side can't be parsed.
    """
    try:
        old = _strip_docstrings(ast.parse(old_src))
        new = _strip_docstrings(ast.parse(new_src))
    except SyntaxError:
        return True
    return ast.dump(old) != ast.dump(new)
