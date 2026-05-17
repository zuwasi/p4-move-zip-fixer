from unittest.mock import patch

from click.testing import CliRunner

from p4_move_zip_fixer import cli
from p4_move_zip_fixer.store import MoveRow, open_store


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.output
    assert "build-spec" in result.output
    assert "zip" in result.output


def test_cli_scan_invokes_scan_depot(tmp_path):
    db = tmp_path / "m.sqlite"
    runner = CliRunner()
    with patch("p4_move_zip_fixer.cli.scan_depot", return_value=3) as mocked:
        result = runner.invoke(
            cli.main,
            ["scan", "--db", str(db), "--head", "10", "--workers", "1", "--chunk", "5"],
        )
    assert result.exit_code == 0, result.output
    assert "New rows: 3" in result.output
    assert mocked.called


def test_cli_build_spec_uses_store(tmp_path):
    db = tmp_path / "m.sqlite"
    with open_store(db) as s:
        s.insert_moves([MoveRow("//depot/a", "//depot/b", 1, "move/add")])

    runner = CliRunner()
    with patch("p4_move_zip_fixer.cli.build_remote_spec", return_value=2) as mocked:
        result = runner.invoke(
            cli.main, ["build-spec", "--db", str(db), "--remote", "migration"]
        )
    assert result.exit_code == 0, result.output
    assert "2 view lines" in result.output
    mocked.assert_called_once()


def test_cli_zip_failure_exits_nonzero():
    runner = CliRunner()
    fake = type("R", (), {"ok": False, "errors": ["Partial action in change 5"], "failed_changes": [5]})()
    with patch("p4_move_zip_fixer.cli.run_zip", return_value=fake):
        result = runner.invoke(
            cli.main, ["zip", "--remote", "migration", "--output", "out.zip"]
        )
    assert result.exit_code == 2
    assert "FAILED" in result.output
    assert "[5]" in result.output
