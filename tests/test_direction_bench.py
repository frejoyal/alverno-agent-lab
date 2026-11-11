"""``alverno direction bench`` (ADR 0020 « Répétitions », step I7), in ``--fake`` mode."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alverno_lab.cli import app

from .conftest import REPO_ROOT


def test_the_fake_bench_gates_each_repetition_and_rewrites_only_its_blocks(
    tmp_path: Path,
) -> None:
    readme = tmp_path / "README.md"
    shutil.copyfile(REPO_ROOT / "README.md", readme)
    before = readme.read_text(encoding="utf-8")
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(REPO_ROOT)  # relative settings paths
        result = CliRunner().invoke(
            app,
            [
                "direction",
                "bench",
                "--models",
                "gemma4:e4b,llama3.2:3b",
                "--fake",
                "--repeats",
                "2",
                "--out",
                str(tmp_path / "out"),
                "--readme",
                str(readme),
            ],
        )
    assert result.exit_code == 0, result.output[-2000:]
    after = readme.read_text(encoding="utf-8")
    assert "| `gemma4:e4b` | 2 | ✅ ✅ |" in after
    assert "| `llama3.2:3b` | 1 | ✅ |" in after
    assert "Généré par `alverno direction bench`" in after
    outside = [line for line in after.splitlines() if line not in before.splitlines()]
    assert all(
        line.startswith(("|", "Mesures", "Measured", "- ", "Généré", "Generated"))
        for line in outside
        if line
    )
    (run,) = (tmp_path / "out").iterdir()
    assert (run / "summary.md").is_file() and (run / "summary.fr.md").is_file()
    assert len(list(run.glob("*.json"))) == 3  # one report per repetition
