from p4_move_zip_fixer.zipper import is_zip_exists_error, run_zip


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


def test_run_zip_clobbers_existing_output_by_default(tmp_path, mock_p4):
    """A partial .zip left behind by a previous failed attempt must be
    removed before the next p4 zip call, or Perforce will refuse with
    'Output zip file ... already exists' on the retry."""
    factory, _ = mock_p4()
    out = tmp_path / "out.zip"
    out.write_bytes(b"partial garbage")
    assert out.exists()

    result = run_zip("migration", str(out), p4_factory=factory)
    assert result.ok is True
    # Tool deleted the stale file before invoking p4 zip.
    # The mock doesn't recreate it, so it should be gone.
    assert not out.exists()


def test_run_zip_keeps_existing_output_when_clobber_false(tmp_path, mock_p4):
    factory, _ = mock_p4()
    out = tmp_path / "out.zip"
    out.write_bytes(b"partial garbage")
    run_zip("migration", str(out), p4_factory=factory, clobber=False)
    assert out.exists()


def test_is_zip_exists_error():
    assert is_zip_exists_error("Output zip file /tmp/x.zip already exists.")
    assert is_zip_exists_error("output ZIP file already exists")
    assert not is_zip_exists_error("Partial action in change 12345")
    assert not is_zip_exists_error("")
