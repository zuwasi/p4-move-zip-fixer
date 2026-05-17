"""Mock P4 server used by all unit tests."""
from __future__ import annotations

from typing import Any

import pytest


class MockP4:
    """Implements the tiny subset of the P4Python API our code uses."""

    def __init__(
        self,
        filelog_records: list[dict[str, Any]] | None = None,
        fail_zip_with: list[str] | None = None,
        describe_records: dict[int, list[dict[str, Any]]] | None = None,
        preset_remotes: dict[str, dict[str, Any]] | None = None,
    ):
        self.filelog_records = filelog_records or []
        self.fail_zip_with = fail_zip_with
        self.describe_records = describe_records or {}
        self.connected = False
        self.saved_remotes: dict[str, dict[str, Any]] = dict(preset_remotes or {})
        self.errors: list[str] = []
        self.run_calls: list[tuple[str, ...]] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def run(self, *args: str) -> list[dict[str, Any]]:
        self.run_calls.append(args)
        if args[0] == "filelog":
            return list(self.filelog_records)
        if args[0] == "describe":
            # args = ("describe", "-s", "<n>")
            try:
                chg = int(args[-1])
            except ValueError:
                return []
            return list(self.describe_records.get(chg, []))
        if args[0] == "zip":
            if self.fail_zip_with:
                self.errors = list(self.fail_zip_with)
                raise RuntimeError("p4 zip failed")
            return [{"status": "ok", "output": args}]
        return []

    def fetch_remote(self, name: str) -> dict[str, Any]:
        if name in self.saved_remotes:
            return dict(self.saved_remotes[name])
        return {"RemoteID": name, "View": [], "DepotMap": []}

    def save_remote(self, spec: dict[str, Any]) -> None:
        self.saved_remotes[spec["RemoteID"]] = spec


@pytest.fixture
def mock_p4():
    """Build a MockP4 and return a (factory, instance) tuple.

    `factory` is what `scan_depot` / `build_remote_spec` / `run_zip` accept.
    `instance` is the same MockP4 object so tests can assert on it.
    """

    def _build(records=None, fail_zip_with=None, describe_records=None, preset_remotes=None):
        m = MockP4(
            filelog_records=records,
            fail_zip_with=fail_zip_with,
            describe_records=describe_records,
            preset_remotes=preset_remotes,
        )
        return (lambda: m), m

    return _build
