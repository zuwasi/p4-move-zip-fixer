"""Regression tests for docs/migration-options/option-b-clone-and-sanitize/scripts/auto-skip-zip.py

The script lives outside the package because it's a stand-alone runbook
helper, so we load it via importlib by file path.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "docs" / "migration-options"
    / "option-b-clone-and-sanitize" / "scripts" / "auto-skip-zip.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("auto_skip_zip", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def auto_skip_zip():
    return _load()


def test_build_exclusion_line_emits_canonical_unquoted_form(auto_skip_zip):
    """Emit the documented Helix Core canonical form: '-<depot> <remote>'.

    Reference:
      https://help.perforce.com/helix-core/server-apps/p4sag/current/Content/DVCS/remotes.mappings.exclude.html
      "To exclude a file or directory, precede the mapping with a minus
      sign (-). Whitespace is not allowed between the minus sign and the
      mapping."

    A previous iteration of this script wrapped the depot side in double
    quotes (`"-//depot/..."`). That parsed but the server was observed
    to silently drop the quoted exclusion when persisting the remote
    spec, leaving `p4 zip` to fail on the same path. Mirroring the doc
    example exactly avoids that ambiguity.
    """
    line = auto_skip_zip.build_exclusion_line(
        "//depot/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/"
        "AmdocsCRM-BM-Collection__V8_1_2_5_1.jar"
    )
    assert line == (
        "-//depot/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/"
        "AmdocsCRM-BM-Collection__V8_1_2_5_1.jar "
        "//remote/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/"
        "AmdocsCRM-BM-Collection__V8_1_2_5_1.jar"
    )
    # Leading char must be '-' immediately followed by '//' (no quote, no
    # whitespace) — that's the documented rule.
    assert line.startswith("-//")
    assert '"' not in line


def test_build_exclusion_line_uses_custom_remote_root(auto_skip_zip):
    line = auto_skip_zip.build_exclusion_line(
        "//depot/foo/bar.txt", remote_root="//mirror"
    )
    assert line == "-//depot/foo/bar.txt //mirror/foo/bar.txt"


def test_build_exclusion_line_rejects_non_depot_paths(auto_skip_zip):
    with pytest.raises(ValueError):
        auto_skip_zip.build_exclusion_line("relative/path")


def test_build_exclusion_line_rejects_paths_with_whitespace(auto_skip_zip):
    """Unquoted form cannot safely represent paths with whitespace."""
    with pytest.raises(ValueError):
        auto_skip_zip.build_exclusion_line("//depot/foo bar/baz.jar")


def test_parse_orphan_paths_extracts_cl_and_path(auto_skip_zip):
    err = (
        "p4 zip FAILED\n"
        "  ERROR: Change 781422 performs a move/delete on "
        "//depot/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/"
        "AmdocsCRM-BM-Collection__V8_1_2_5_1.jar#2, but the parameters of "
        "this fetch, push, or zip command include only part of the full action.\n"
    )
    pairs = auto_skip_zip.parse_orphan_paths(err)
    assert pairs == [(
        781422,
        "//depot/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/"
        "AmdocsCRM-BM-Collection__V8_1_2_5_1.jar",
    )]


# --- compact-rebuild helpers ---------------------------------------------

def test_is_exclusion_line_recognises_both_forms(auto_skip_zip):
    f = auto_skip_zip._is_exclusion_line
    assert f('"-//depot/foo" "//remote/foo"')
    assert f('-//depot/foo //remote/foo')
    assert f('   "-//depot/foo" "//remote/foo"')  # leading whitespace
    assert not f('"//depot/foo" "//remote/foo"')
    assert not f('//depot/... //remote/...')
    assert not f('')
    assert not f(None)


def test_split_catchall_drops_redundant_per_file_inclusions(auto_skip_zip):
    """The compact-rebuild path must keep the catch-all and exclusions but
    drop all the redundant per-file inclusion lines that the catch-all
    already covers — that's how we free space under Perforce's 100k cap."""
    lines = [
        "//depot/... //remote/...",                            # catch-all (keep)
        '"//depot/foo/a.c" "//remote/foo/a.c"',                # per-file (drop)
        '"//depot/foo/b.c" "//remote/foo/b.c"',                # per-file (drop)
        "-//depot/bad/orphan.jar //remote/bad/orphan.jar",     # exclusion (keep)
        '"//depot/foo/c.c" "//remote/foo/c.c"',                # per-file (drop)
    ]
    catchall, exclusions = auto_skip_zip._split_catchall_and_exclusions(lines)
    assert catchall == "//depot/... //remote/..."
    assert exclusions == ["-//depot/bad/orphan.jar //remote/bad/orphan.jar"]


def test_split_catchall_handles_missing_catchall(auto_skip_zip):
    catchall, exclusions = auto_skip_zip._split_catchall_and_exclusions([
        "-//depot/a //remote/a",
        "-//depot/b //remote/b",
    ])
    assert catchall is None
    assert exclusions == ["-//depot/a //remote/a",
                         "-//depot/b //remote/b"]


def test_split_catchall_ignores_blank_lines(auto_skip_zip):
    catchall, _ = auto_skip_zip._split_catchall_and_exclusions(
        ["", "   ", "//depot/... //remote/..."]
    )
    assert catchall == "//depot/... //remote/..."


# --- cap-truncation integration ------------------------------------------

class _CapMockP4:
    """Mocks just enough of the P4Python API to exercise the cap-rebuild
    path in add_exclusions_to_remote without a live Perforce server."""

    def __init__(self, initial_map, cap):
        self._spec = {"RemoteID": "migration-remote", "DepotMap": list(initial_map)}
        self._cap = cap
        self.save_calls = 0

    def connect(self): pass
    def disconnect(self): pass

    def fetch_remote(self, name):
        return {"RemoteID": self._spec["RemoteID"],
                "DepotMap": list(self._spec["DepotMap"])}

    def save_remote(self, spec):
        self.save_calls += 1
        # Perforce silently truncates writes that exceed the cap.
        self._spec["DepotMap"] = list(spec.get("DepotMap") or [])[: self._cap]


def test_add_exclusions_triggers_compact_rebuild_on_cap(monkeypatch, auto_skip_zip):
    """Reproduces the field failure: DepotMap at the 100k cap, the append
    is silently dropped, and the script must transparently rebuild the
    spec compactly so the new exclusion actually persists."""
    cap = 100
    initial = ["//depot/... //remote/..."] + [
        f'"//depot/old/f{i}.c" "//remote/old/f{i}.c"' for i in range(cap - 1)
    ]
    mock = _CapMockP4(initial_map=initial, cap=cap)
    monkeypatch.setattr(auto_skip_zip, "_p4", lambda: (lambda: mock))

    orphan = ("//depot/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/"
              "AmdocsCRM-BM-Collection__V8_1_2_5_1.jar")
    added, total = auto_skip_zip.add_exclusions_to_remote(
        "migration-remote", [orphan]
    )

    # Two saves: the failed additive attempt, then the compact rebuild.
    assert mock.save_calls == 2
    # The compact spec is catch-all + the one new exclusion (no per-file lines).
    assert mock._spec["DepotMap"] == [
        "//depot/... //remote/...",
        auto_skip_zip.build_exclusion_line(orphan),
    ]
    assert total == 2
    # And we report a real, non-zero delta so the caller does NOT abort.
    assert added == 1
