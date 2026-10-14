"""CLI smoke tests using typer's CliRunner."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from autoposter.cli.main import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "setup" in result.output
    assert "doctor" in result.output
    assert "post" in result.output
    assert "run" in result.output
    assert "demo" in result.output


def test_init_creates_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / "config.yaml").exists()
    assert (tmp_path / ".env").exists()


def test_init_existing_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    # Second init should warn but not fail.
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1  # warns and exits


def test_doctor_no_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    # Should fail gracefully (no config).
    assert result.exit_code != 0


def test_demo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["demo", "--posts", "2"])
    assert result.exit_code == 0
    assert "Demo complete" in result.output


def test_post_add_and_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["demo", "--posts", "1"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["post", "list"])
    assert result.exit_code == 0


def test_import_csv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0

    # Create a sample CSV.
    csv_file = tmp_path / "posts.csv"
    csv_file.write_text(
        "text,platforms,datetime,timezone\n"
        "Hello world,fake,2026-10-14 09:00,UTC\n"
    )

    result = runner.invoke(app, ["import", str(csv_file), "--validate-only"])
    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_import_csv_invalid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0

    csv_file = tmp_path / "posts.csv"
    csv_file.write_text("text,platforms,datetime,timezone\n,,,\n")

    result = runner.invoke(app, ["import", str(csv_file), "--validate-only"])
    assert result.exit_code != 0


def test_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["demo", "--posts", "1"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["export", "--format", "json"])
    assert result.exit_code == 0
    assert (tmp_path / "autoposter-export.json").exists()


def test_backup_restore(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["demo", "--posts", "1"])
    assert result.exit_code == 0

    backup_file = tmp_path / "backup.tar.gz"
    result = runner.invoke(app, ["backup", "--out", str(backup_file)])
    assert result.exit_code == 0
    assert backup_file.exists()

    result = runner.invoke(app, ["restore", str(backup_file), "--force"])
    assert result.exit_code == 0
