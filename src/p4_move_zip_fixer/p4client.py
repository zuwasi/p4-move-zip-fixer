"""Thin wrapper around P4Python so the rest of the code can be unit-tested
against a mock that implements the same tiny interface.
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol


class P4Like(Protocol):
    """Minimal subset of P4Python we depend on."""

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def run(self, *args: str) -> list[dict[str, Any]]: ...
    def fetch_remote(self, name: str) -> dict[str, Any]: ...
    def save_remote(self, spec: dict[str, Any]) -> None: ...

    @property
    def errors(self) -> Iterable[str]: ...


def make_p4() -> P4Like:
    """Construct a real P4Python client. Imported lazily so unit tests don't
    need p4python installed."""
    try:
        from P4 import P4  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "p4python is not installed. Install with: pip install 'p4-move-zip-fixer[p4]'"
        ) from e
    return P4()
