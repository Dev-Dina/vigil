"""Gate 7.1 — the structural isolation proof (import-graph). THE core 7.1 check.

The Guide must import NOTHING from the real app (`vigil.*`) nor from the deny-listed clients
(Vault `hvac`, `redis`, the Arq queue `arq`) — specs/isolation.md §1 import-graph test + the
must-not-import list. Proven two ways: a static AST scan of every guide/ module (no execution),
and a subprocess that imports the guide modules and asserts none of those landed in sys.modules.
This must pass now and stay passing for the life of the Guide.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUIDE_DIR = _REPO_ROOT / "guide"

# Roots the Guide must never import: the whole real app + the Vault/Redis/queue/participant-DB
# clients (psycopg is the app's Postgres driver — the Guide's own SQLite sink needs no driver).
_FORBIDDEN_ROOTS = {"vigil", "hvac", "redis", "arq", "psycopg"}


def _guide_py_files() -> list[Path]:
    return sorted(_GUIDE_DIR.rglob("*.py"))


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if (
                node.level == 0 and node.module
            ):  # ignore relative imports (within guide)
                roots.add(node.module.split(".")[0])
    return roots


def test_guide_files_exist() -> None:
    files = _guide_py_files()
    assert files, "expected guide/ python modules"


def test_static_import_graph_has_zero_forbidden_imports() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _guide_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for root in _import_roots(tree):
            if root in _FORBIDDEN_ROOTS:
                offenders.append((path.relative_to(_REPO_ROOT).as_posix(), root))
    assert not offenders, (
        f"the Guide must import NOTHING from {_FORBIDDEN_ROOTS}; found: {offenders}"
    )


def test_importing_guide_pulls_no_forbidden_module_into_sys_modules() -> None:
    code = (
        "import sys; import guide.app, guide.config, guide.observability, guide.turn; "
        "bad=[m for m in sys.modules if m.split('.')[0] in "
        "{'vigil','hvac','redis','arq','psycopg'}]; "
        "assert not bad, bad; print('ok')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
