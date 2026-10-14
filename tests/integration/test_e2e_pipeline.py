"""Final end-to-end test: init → demo → post list → export → backup."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from autoposter.cli.main import app

runner = CliRunner()


def test_full_pipeline(tmp_path: Path, monkeypatch) -> None:
    """Exercise the entire CLI pipeline end to end."""
    monkeypatch.chdir(tmp_path)

    # 1. Init.
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / "config.yaml").exists()
    assert (tmp_path / ".env").exists()

    # 2. Demo (creates posts and runs a tick).
    result = runner.invoke(app, ["demo", "--posts", "3"])
    assert result.exit_code == 0
    assert "Demo complete" in result.output

    # 3. Post list.
    result = runner.invoke(app, ["post", "list"])
    assert result.exit_code == 0
    assert "ID" in result.output

    # 4. Export.
    result = runner.invoke(app, ["export", "--format", "json"])
    assert result.exit_code == 0
    assert (tmp_path / "autoposter-export.json").exists()

    # 5. Backup.
    backup_file = tmp_path / "backup.tar.gz"
    result = runner.invoke(app, ["backup", "--out", str(backup_file)])
    assert result.exit_code == 0
    assert backup_file.exists()

    # 6. Doctor (will warn about no real accounts, but should not crash).
    result = runner.invoke(app, ["doctor"])
    assert "Doctor" in result.output
