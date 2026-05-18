from p4_move_zip_fixer.spec import (
    build_remote_spec,
    build_view_lines,
    expand_spec_with_changelists,
)
from p4_move_zip_fixer.store import MoveRow, open_store


def test_build_view_lines_translates_paths():
    lines = build_view_lines(
        ["//depot/old/foo.c", "//depot/new/foo.c"], remote_root="//remote"
    )
    assert lines == [
        '"//depot/new/foo.c" "//remote/new/foo.c"',
        '"//depot/old/foo.c" "//remote/old/foo.c"',
    ]


def test_build_view_lines_skips_non_depot_paths():
    assert build_view_lines(["not-a-depot-path", "//depot/x"]) == [
        '"//depot/x" "//remote/x"'
    ]


def test_build_remote_spec_writes_depotmap(tmp_path, mock_p4):
    factory, p4 = mock_p4()
    db = tmp_path / "moves.sqlite"
    with open_store(db) as store:
        store.insert_moves([
            MoveRow("//depot/old/a.c", "//depot/new/a.c", 10, "move/add"),
            MoveRow("//depot/old/a.c", "//depot/new/a.c", 10, "move/delete"),
            MoveRow("//depot/old/b.c", "//depot/new/b.c", 11, "move/add"),
        ])
        n = build_remote_spec(store, "migration", p4_factory=factory)

    # 1 catch-all + 4 per-file lines (2 distinct moves × 2 sides)
    assert n == 5
    spec = p4.saved_remotes["migration"]
    assert "View" not in spec  # remote specs have no View field
    assert spec["DepotMap"][0] == "//depot/... //remote/..."
    assert any("//depot/old/a.c" in line for line in spec["DepotMap"])
    assert any("//depot/new/b.c" in line for line in spec["DepotMap"])


def test_expand_spec_adds_changelist_paths(mock_p4):
    # Existing spec already contains /old/a.c. Changelist 781422 touches
    # /old/a.c and /new/a.c — we expect only /new/a.c to be added.
    preset = {
        "migration": {
            "RemoteID": "migration",
            "DepotMap": [
                "//depot/... //remote/...",
                '"//depot/old/a.c" "//remote/old/a.c"',
            ],
        }
    }
    describe = {
        781422: [{"depotFile": ["//depot/old/a.c", "//depot/new/a.c"]}]
    }
    factory, p4 = mock_p4(preset_remotes=preset, describe_records=describe)

    added, total = expand_spec_with_changelists(
        remote_name="migration", changelists=[781422], p4_factory=factory
    )
    assert added == 1
    assert total == 3  # catch-all + old + new
    spec = p4.saved_remotes["migration"]
    assert any("//depot/new/a.c" in line for line in spec["DepotMap"])


def test_expand_spec_no_new_paths(mock_p4):
    preset = {
        "migration": {
            "RemoteID": "migration",
            "DepotMap": ['"//depot/x" "//remote/x"'],
        }
    }
    describe = {1: [{"depotFile": ["//depot/x"]}]}
    factory, p4 = mock_p4(preset_remotes=preset, describe_records=describe)
    added, total = expand_spec_with_changelists(
        remote_name="migration", changelists=[1], p4_factory=factory
    )
    assert added == 0
    assert total == 1
