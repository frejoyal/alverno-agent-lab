"""Layout guards of module 5, set before its code exists (ADR 0017, ADR 0018; step I1).

- Only ``mcp_server/`` and its evaluation (``evals/mcp_eval.py``) import the ``mcp`` SDK,
  and no other package module imports ``mcp_server`` (the server sits at the top, next to
  the CLI).
- The MCP server imports no write or approval code and no web server.
- The management orchestrator never imports the MCP SDK or the server: it is not
  exposed over MCP in v1.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import REPO_ROOT
from .test_tools import _imports, _within

SRC = REPO_ROOT / "src" / "alverno_lab"
MCP_SERVER = SRC / "mcp_server"
DIRECTION = SRC / "agents" / "direction"
# The evaluation of the server drives it with the SDK's in-memory client (ADR 0020).
MCP_EVAL = SRC / "evals" / "mcp_eval.py"
# Write paths of module 3 (ADR 0013) and web servers the SDK installs (ADR 0017).
MCP_FORBIDDEN = (
    "alverno_lab.agents.ops.approval",
    "alverno_lab.tools.authority",
    "alverno_lab.tools.executor",
    "uvicorn",
    "starlette",
    "sse_starlette",
)


def _sources(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py")) if root.is_dir() else []


def test_only_the_mcp_server_imports_the_sdk() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _sources(SRC)
        if not path.is_relative_to(MCP_SERVER)
        and path != MCP_EVAL
        and _within_any(_imports(path), ("mcp",))
    ]
    assert not offenders, offenders


def test_no_package_module_imports_the_mcp_server() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _sources(SRC)
        if not path.is_relative_to(MCP_SERVER)
        and path not in (SRC / "cli.py", MCP_EVAL)
        and _within_any(_imports(path), ("alverno_lab.mcp_server",))
    ]
    assert not offenders, offenders


def test_the_mcp_server_imports_no_write_path_and_no_web_server() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(
            m for m in _imports(path) if _within(m, MCP_FORBIDDEN)
        )
        for path in _sources(MCP_SERVER)
    }
    assert not {k: v for k, v in offenders.items() if v}


def test_the_orchestrator_is_not_exposed_over_mcp() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _sources(DIRECTION)
        if _within_any(_imports(path), ("mcp", "alverno_lab.mcp_server"))
    ]
    assert not offenders, offenders


def _within_any(modules: set[str], packages: tuple[str, ...]) -> bool:
    return any(_within(m, packages) for m in modules)
