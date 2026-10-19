"""Meta-test: Cypher reaches the driver only as constants of recallops.fraud.cypher (plan WP1 Revision 1).

Every .run( / tx.run( / session.run( / execute_query( call in src/recallops/fraud/ must pass, as its first argument,
a Name imported from recallops.fraud.cypher or a Subscript of one (a per-kind mapping keyed by the GraphKind
literal). No f-string, concatenation, .format, or % ever builds a statement at a call site. Every constant that
graph.py runs binds $ns and $firm_id; graph_admin.py (schema, epoch, purges) is exempt by module.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

from recallops.fraud import cypher

FRAUD = Path(__file__).resolve().parents[2] / "src" / "recallops" / "fraud"
EXEMPT_FROM_SCOPE = {"graph_admin.py"}


def _cypher_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "recallops.fraud.cypher":
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _final_constants() -> set[str]:
    tree = ast.parse((FRAUD / "cypher.py").read_text(encoding="utf-8"))
    out = set()
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            ann = ast.unparse(node.annotation)
            if ann.startswith("Final"):
                out.add(node.target.id)
    return out


def _run_calls(tree: ast.Module) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr in {"run", "execute_query"}) or (
            isinstance(func, ast.Name) and func.id == "execute_query"
        ):
            calls.append(node)
    return calls


def _statement_values(name: str) -> list[str]:
    value = getattr(cypher, name)
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [v for v in value.values() if isinstance(v, str)]
    if isinstance(value, tuple):
        return [v for v in value if isinstance(v, str)]
    raise AssertionError(f"{name} is not a statement constant")


def test_cypher_reaches_the_driver_only_as_module_constants() -> None:
    finals = _final_constants()
    checked = 0
    offenders: list[str] = []
    used_by_module: dict[str, set[str]] = {}
    for path in sorted(FRAUD.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _cypher_imports(tree)
        for call in _run_calls(tree):
            checked += 1
            where = f"{path.name}:{call.lineno}"
            if not call.args:
                offenders.append(f"{where} has no positional statement")
                continue
            first = call.args[0]
            base = first.value if isinstance(first, ast.Subscript) else first
            if not isinstance(base, ast.Name) or base.id not in imported or base.id not in finals:
                offenders.append(f"{where} runs {ast.unparse(first)}")
                continue
            if any(isinstance(kw.arg, str) and kw.arg == "query" for kw in call.keywords):
                offenders.append(f"{where} passes query=")
            used_by_module.setdefault(path.name, set()).add(base.id)
    assert offenders == []
    assert checked >= 15  # the scan really sees the store's calls
    for module, names in used_by_module.items():
        if module in EXEMPT_FROM_SCOPE:
            continue
        for name in sorted(names):
            for stmt in _statement_values(name):
                assert "$ns" in stmt and "$firm_id" in stmt, f"{module} runs {name} without $ns and $firm_id"


def test_no_statement_text_outside_cypher_module() -> None:
    """Belt and braces: no other fraud module contains Cypher text, in a literal or an f-string."""
    tokens = ("MATCH (", "MERGE (", "DETACH DELETE", "UNWIND $", "CREATE CONSTRAINT")
    offenders = []
    for path in sorted(FRAUD.glob("*.py")):
        if path.name == "cypher.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and any(t in node.value for t in tokens):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def imported_modules(source: str) -> set[str]:
    """Every module an import statement names, with `from a import b` counted as both a and a.b."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


def test_api_side_modules_never_import_graph_admin() -> None:
    for name in ("graph.py", "view.py", "cypher.py"):
        assert "recallops.fraud.graph_admin" not in imported_modules((FRAUD / name).read_text(encoding="utf-8")), name
