from p4_move_zip_fixer.zipper import run_zip


def test_run_zip_success(mock_p4):
    factory, p4 = mock_p4()
    result = run_zip("migration", "out.zip", p4_factory=factory)
    assert result.ok is True
    assert result.errors == []
    assert result.failed_changes == []
    assert ("zip", "-o", "out.zip", "-r", "migration", "//depot/...@1,#head") in p4.run_calls


def test_run_zip_failure_extracts_changelists(mock_p4):
    factory, _ = mock_p4(fail_zip_with=[
        "Partial action in change 12345 for //depot/foo/bar",
        "Partial action in change 99999 for //depot/x",
        "Some other error with no number",
    ])
    result = run_zip("migration", "out.zip", p4_factory=factory)
    assert result.ok is False
    assert result.failed_changes == [12345, 99999]
    assert len(result.errors) == 3
