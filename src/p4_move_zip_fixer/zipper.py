"""Run `p4 zip` against a generated remote spec and surface structured errors."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable

from .p4client import P4Like, make_p4

# Perforce error format we care about, e.g.:
#   "Partial action in change 12345 for //depot/foo/bar"
_CHANGE_RE = re.compile(r"change\s+(\d+)", re.IGNORECASE)

# Perforce refuses to overwrite an existing zip file and emits this exact text.
# When the auto-retry loop runs after a failed first attempt, p4 zip leaves a
# partial archive on disk; the next attempt then fails for the wrong reason.
_ZIP_EXISTS_RE = re.compile(r"zip file\b.*\balready exists", re.IGNORECASE)


@dataclass
class ZipResult:
    ok: bool
    output: list
    errors: list[str]
    failed_changes: list[int]


def run_zip(
    remote_name: str,
    output_path: str,
    depot_path: str = "//depot/...@1,#head",
    p4_factory: Callable[[], P4Like] = make_p4,
    clobber: bool = True,
) -> ZipResult:
    """Invoke `p4 zip` and return a structured result.

    When ``clobber`` is True (the default) any pre-existing file at
    ``output_path`` is removed before running. ``p4 zip`` writes a partial
    archive to disk when it errors mid-stream and then refuses to overwrite
    it on the next call ("Output zip file ... already exists"), which would
    cause the auto-retry loop to fail for the wrong reason.
    """
    if clobber and output_path and os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            # Fall through; p4 zip will surface a clearer error than we can.
            pass

    p4 = p4_factory()
    p4.connect()
    try:
        try:
            out = p4.run("zip", "-o", output_path, "-r", remote_name, depot_path)
            return ZipResult(ok=True, output=list(out), errors=[], failed_changes=[])
        except Exception:
            errs = list(p4.errors)
            failed = sorted({int(m.group(1)) for e in errs if (m := _CHANGE_RE.search(e))})
            return ZipResult(ok=False, output=[], errors=errs, failed_changes=failed)
    finally:
        p4.disconnect()


def is_zip_exists_error(error: str) -> bool:
    """True if the given p4 error indicates an existing-output-zip collision."""
    return bool(_ZIP_EXISTS_RE.search(error or ""))
