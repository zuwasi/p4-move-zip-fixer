from p4_move_zip_fixer.scan import extract_moves_from_filelog, scan_depot, scan_range
from p4_move_zip_fixer.store import open_store


def test_extract_moves_handles_parallel_arrays():
    records = [
        {
            "depotFile": "//depot/new/foo.c",
            "action": ["move/add", "edit"],
            "change": ["1002", "1010"],
            "movedFile": ["//depot/old/foo.c", None],
        },
        {
            "depotFile": "//depot/old/foo.c",
            "action": ["move/delete"],
            "change": ["1002"],
            "movedFile": ["//depot/new/foo.c"],
        },
        {
            "depotFile": "//depot/unrelated.c",
            "action": ["add"],
            "change": ["1001"],
        },
    ]
    rows = extract_moves_from_filelog(records)

    assert len(rows) == 2
    add = next(r for r in rows if r.action == "move/add")
    delete = next(r for r in rows if r.action == "move/delete")
    assert add.src == "//depot/old/foo.c"
    assert add.tgt == "//depot/new/foo.c"
    assert add.change == 1002
    assert delete.src == "//depot/old/foo.c"
    assert delete.tgt == "//depot/new/foo.c"


def test_extract_moves_handles_scalar_action():
    records = [
        {
            "depotFile": "//depot/x.c",
            "action": "move/add",
            "change": "42",
            "movedFile": "//depot/y.c",
        }
    ]
    rows = extract_moves_from_filelog(records)
    assert len(rows) == 1
    assert rows[0].change == 42


def test_scan_range_uses_factory(mock_p4):
    factory, p4 = mock_p4(records=[
        {"depotFile": "//depot/a", "action": ["move/add"], "change": ["7"], "movedFile": ["//depot/b"]}
    ])
    rows = scan_range("//depot/...", 1, 100, p4_factory=factory)
    assert rows[0].change == 7
    assert ("filelog", "//depot/...@1,@100") in p4.run_calls


def test_scan_depot_is_resumable(tmp_path, mock_p4):
    records = [
        {"depotFile": "//depot/a", "action": ["move/add"], "change": ["3"], "movedFile": ["//depot/b"]}
    ]
    factory, p4 = mock_p4(records=records)
    db = tmp_path / "moves.sqlite"

    with open_store(db) as store:
        first = scan_depot("//depot/...", store, head_change=10, chunk_size=5,
                           workers=1, p4_factory=factory)
        # Run again — every range is now marked scanned, so no new inserts.
        second = scan_depot("//depot/...", store, head_change=10, chunk_size=5,
                            workers=1, p4_factory=factory)

    assert first > 0
    assert second == 0
