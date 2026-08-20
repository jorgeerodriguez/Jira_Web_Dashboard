"""No module may reference a name it does not define.

Two slice-based edits in one session deleted neighbouring module constants along with the block they
were meant to remove. The first took six out of slas.py and 46 tests caught it immediately. The second
took four out of ingest.py — `_WATERMARK_MARGIN`, `_FULL_JQL`, `_PAGE_SIZE`, `_RETRY_ATTEMPTS` and
`_RETRY_BACKOFF_SECONDS` — and **reached production**, where the Jira poller crashed on every cycle
with NameError before issuing a single query.

The reason it merged green is that unit tests only reach the code paths they exercise, and nothing
called `build_incremental_jql` or `get_jira_timezone`. This checks every module wholesale instead, so
a deletion cannot hide in an untested path.
"""
import ast
import builtins
import pathlib

import pytest

_PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "darkstar"


def _module_paths():
    return sorted(p for p in _PACKAGE.glob("*.py") if p.name != "__init__.py")


def _defined_at_module_level(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    names |= {(a.asname or a.name).split(".")[0] for a in sub.names}
    return names


def _bound_anywhere(tree):
    """Every name bound inside a function: parameters, assignments, comprehensions, with, for."""
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Global):
            bound |= set(node.names)
    return bound


@pytest.mark.parametrize("path", _module_paths(), ids=lambda p: p.name)
def test_module_defines_every_private_name_it_uses(path):
    """A module-private name (leading underscore) must be defined in the module that uses it."""
    tree = ast.parse(path.read_text())
    known = _defined_at_module_level(tree) | _bound_anywhere(tree)
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    # Module dunders are supplied by the interpreter, not by the source.
    supplied = {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__"}
    missing = sorted(n for n in used - known - supplied
                     if n.startswith("_") and not hasattr(builtins, n))
    assert not missing, f"{path.name} references undefined {missing}"


@pytest.mark.parametrize("path", _module_paths(), ids=lambda p: p.name)
def test_module_imports_cleanly(path):
    """Import the module for real: a missing name in a decorator or default blows up at import."""
    import importlib
    importlib.import_module(f"darkstar.{path.stem}")
