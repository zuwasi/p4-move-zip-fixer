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


def test_build_exclusion_line_puts_dash_inside_quotes(auto_skip_zip):
    """The '-' must be INSIDE the leading double-quote, not outside.

    When the '-' is outside ('-"//depot/..."'), Perforce parses the line
    in unquoted mode, reads the '"' as a literal character of the depot
    path, and rejects the spec with:
        Error in remote specification.
        Null directory (//) not allowed in '"//depot/.../file.jar"'.

    The canonical Perforce form is '"-//depot/path" "//remote/path"'.
    """
    line = auto_skip_zip.build_exclusion_line(
        "//depot/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/"
        "AmdocsCRM-BM-Collection__V8_1_2_5_1.jar"
    )
    assert line == (
        '"-//depot/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/'
        'AmdocsCRM-BM-Collection__V8_1_2_5_1.jar" '
        '"//remote/Advocacy/HF/FoundationJarsForSigning-SHA2/700001-070417/'
        'AmdocsCRM-BM-Collection__V8_1_2_5_1.jar"'
    )
    # The leading character of the entire line must be a double-quote, not
    # a dash — that's the whole point.
    assert line.startswith('"-')
    assert not line.startswith('-"')


def test_build_exclusion_line_uses_custom_remote_root(auto_skip_zip):
    line = auto_skip_zip.build_exclusion_line(
        "//depot/foo/bar.txt", remote_root="//mirror"
    )
    assert line == '"-//depot/foo/bar.txt" "//mirror/foo/bar.txt"'


def test_build_exclusion_line_rejects_non_depot_paths(auto_skip_zip):
    with pytest.raises(ValueError):
        auto_skip_zip.build_exclusion_line("relative/path")


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
