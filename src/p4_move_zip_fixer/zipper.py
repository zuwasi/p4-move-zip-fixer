"""Run `p4 zip` against a generated remote spec and surface structured errors."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .p4client import P4Like, make_p4

# Perforce error format we care about, e.g.:
#   "Partial action in change 12345 for //depot/foo/bar"
_CHANGE_RE = re.compile(r"change\s+(\d+)", re.IGNORECASE)


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
) -> ZipResult:
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
