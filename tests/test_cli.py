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
        # disable auto-retry so we test the plain "fail fast" path
        result = runner.invoke(
            cli.main,
            ["zip", "--remote", "migration", "--output", "out.zip", "--auto-retry", "0"],
        )
    assert result.exit_code == 2
    assert "FAILED" in result.output
    assert "[5]" in result.output


def test_cli_zip_auto_retry_calls_expand_then_succeeds():
    """Verify --auto-retry default invokes expand_spec_with_changelists on
    failure and stops once run_zip reports success."""
    runner = CliRunner()
    failing = type("R", (), {"ok": False, "errors": ["change 7 partial"], "failed_changes": [7]})()
    ok = type("R", (), {"ok": True, "errors": [], "failed_changes": []})()

    with patch("p4_move_zip_fixer.cli.run_zip", side_effect=[failing, ok]), \
         patch("p4_move_zip_fixer.cli.expand_spec_with_changelists", return_value=(3, 100)) as expand:
        result = runner.invoke(
            cli.main, ["zip", "--remote", "migration", "--output", "out.zip"]
        )
    assert result.exit_code == 0, result.output
    assert "succeeded on attempt 2" in result.output
    expand.assert_called_once()


def test_cli_zip_aborts_when_expand_adds_zero_paths():
    """If expand adds 0 new paths, the next p4 zip attempt will fail on the
    same CL again — caused by an obliterated move counterpart that
    `p4 describe -s` cannot see. The CLI must short-circuit immediately
    rather than wasting another zip attempt and looping."""
    runner = CliRunner()
    failing = type("R", (), {"ok": False, "errors": ["change 781422 partial"],
                             "failed_changes": [781422]})()

    with patch("p4_move_zip_fixer.cli.run_zip", return_value=failing) as zipper, \
         patch("p4_move_zip_fixer.cli.expand_spec_with_changelists",
               return_value=(0, 100_000)) as expand:
        result = runner.invoke(
            cli.main,
            ["zip", "--remote", "migration", "--output", "out.zip", "--auto-retry", "5"],
        )
    assert result.exit_code == 2
    assert "Expand added 0 paths" in result.output
    assert "Recovery options" in result.output
    expand.assert_called_once()
    # Only one zip attempt — no wasted second invocation.
    assert zipper.call_count == 1


def test_cli_zip_aborts_on_spec_cap_reached():
    """SpecCapReached from expand must abort with a clear error and runbook."""
    from p4_move_zip_fixer.spec import SpecCapReached
    runner = CliRunner()
    failing = type("R", (), {"ok": False, "errors": ["change 999 partial"],
                             "failed_changes": [999]})()

    with patch("p4_move_zip_fixer.cli.run_zip", return_value=failing), \
         patch("p4_move_zip_fixer.cli.expand_spec_with_changelists",
               side_effect=SpecCapReached(current=100_000, attempted=100_005)):
        result = runner.invoke(
            cli.main, ["zip", "--remote", "migration", "--output", "out.zip"]
        )
    assert result.exit_code == 3
    assert "cap" in result.output.lower()
    assert "Recovery options" in result.output


def test_cli_zip_aborts_when_output_already_exists():
    """If p4 zip reports the output already exists (e.g. user passed
    --keep-failed-output and the prior attempt wrote a partial), we must
    not auto-retry — file-system state, not view widening, is the issue."""
    runner = CliRunner()
    failing = type("R", (), {
        "ok": False,
        "errors": ["Output zip file /tmp/x.zip already exists."],
        "failed_changes": [],
    })()
    with patch("p4_move_zip_fixer.cli.run_zip", return_value=failing):
        result = runner.invoke(
            cli.main,
            ["zip", "--remote", "migration", "--output", "/tmp/x.zip",
             "--keep-failed-output"],
        )
    assert result.exit_code == 2
    assert "already exists" in result.output


def test_cli_zip_passes_clobber_flag_to_run_zip():
    """Default invocation should clobber stale output; --keep-failed-output
    must propagate clobber=False."""
    runner = CliRunner()
    ok = type("R", (), {"ok": True, "errors": [], "failed_changes": []})()

    with patch("p4_move_zip_fixer.cli.run_zip", return_value=ok) as zipper:
        runner.invoke(cli.main, ["zip", "--remote", "m", "--output", "out.zip"])
    assert zipper.call_args.kwargs["clobber"] is True

    with patch("p4_move_zip_fixer.cli.run_zip", return_value=ok) as zipper:
        runner.invoke(cli.main, ["zip", "--remote", "m", "--output", "out.zip",
                                 "--keep-failed-output"])
    assert zipper.call_args.kwargs["clobber"] is False
