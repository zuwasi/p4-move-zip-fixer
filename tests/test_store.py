from p4_move_zip_fixer.store import MoveRow, open_store


def test_insert_and_paths_roundtrip(tmp_path):
    db = tmp_path / "moves.sqlite"
    with open_store(db) as store:
        store.insert_moves([
            MoveRow("//depot/old/a", "//depot/new/a", 1, "move/add"),
            MoveRow("//depot/old/a", "//depot/new/a", 1, "move/delete"),
        ])
        assert store.count() == 2
        assert store.all_paths() == {"//depot/old/a", "//depot/new/a"}


def test_unique_constraint_dedups(tmp_path):
    db = tmp_path / "moves.sqlite"
    with open_store(db) as store:
        row = MoveRow("//depot/x", "//depot/y", 5, "move/add")
        store.insert_moves([row])
        store.insert_moves([row])  # duplicate
        assert store.count() == 1


def test_scanned_range_tracking(tmp_path):
    db = tmp_path / "moves.sqlite"
    with open_store(db) as store:
        assert not store.is_range_scanned(1, 100)
        store.mark_range_scanned(1, 100)
        assert store.is_range_scanned(1, 100)
        assert not store.is_range_scanned(101, 200)
