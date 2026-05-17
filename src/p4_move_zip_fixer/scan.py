"""Discover move/add ↔ move/delete pairs in a Perforce depot."""
from __future__ import annotations

import concurrent.futures as cf
from typing import Any, Callable, Iterable

from .p4client import P4Like, make_p4
from .store import MoveRow, MoveStore

MOVE_ACTIONS = {"move/add", "move/delete"}


def _normalise(value: Any, index: int) -> Any:
    """p4python returns scalars or lists depending on revision count.
    Normalise indexed access so callers don't have to special-case."""
    if isinstance(value, list):
        return value[index] if index < len(value) else None
    return value if index == 0 else None


def extract_moves_from_filelog(records: Iterable[dict[str, Any]]) -> list[MoveRow]:
    """Pure function: turn raw `p4 filelog -ztag` records into MoveRows.

    A filelog record per file contains parallel arrays for each revision:
    action[i], change[i], movedFile[i]. We emit one row per move-related rev.
    """
    rows: list[MoveRow] = []
    for rec in records:
        depot_file = rec.get("depotFile")
        actions = rec.get("action") or []
        if isinstance(actions, str):
            actions = [actions]
        for i, action in enumerate(actions):
            if action not in MOVE_ACTIONS:
                continue
            change = int(_normalise(rec.get("change"), i) or 0)
            moved = _normalise(rec.get("movedFile"), i)
            if action == "move/add":
                rows.append(MoveRow(src=moved, tgt=depot_file, change=change, action=action))
            else:  # move/delete
                rows.append(MoveRow(src=depot_file, tgt=moved, change=change, action=action))
    return rows


def scan_range(
    depot_path: str,
    start: int,
    end: int,
    p4_factory: Callable[[], P4Like] = make_p4,
) -> list[MoveRow]:
    """Scan a single changelist range and return discovered move rows.

    `p4 filelog -c <n>` only accepts a single changelist number, so we
    pass the range as a revision specifier on the path itself:
    `//depot/...@<start>,@<end>`.
    """
    p4 = p4_factory()
    p4.connect()
    try:
        path_with_range = f"{depot_path}@{start},@{end}"
        records = p4.run("filelog", path_with_range)
    finally:
        p4.disconnect()
    return extract_moves_from_filelog(records)


def scan_depot(
    depot_path: str,
    store: MoveStore,
    head_change: int,
    chunk_size: int = 5000,
    workers: int = 8,
    p4_factory: Callable[[], P4Like] = make_p4,
    progress: Callable[[int, int, int], None] | None = None,
) -> int:
    """Scan the entire depot in parallel chunks, persisting incrementally.

    Returns the total number of new move rows inserted.
    """
    ranges = [(s, min(s + chunk_size - 1, head_change)) for s in range(1, head_change + 1, chunk_size)]
    pending = [(s, e) for s, e in ranges if not store.is_range_scanned(s, e)]
    inserted = 0

    def _work(rng: tuple[int, int]) -> tuple[tuple[int, int], list[MoveRow]]:
        s, e = rng
        return rng, scan_range(depot_path, s, e, p4_factory)

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for done, (rng, rows) in enumerate(ex.map(_work, pending), 1):
            inserted += store.insert_moves(rows)
            store.mark_range_scanned(*rng)
            if progress:
                progress(done, len(pending), inserted)
    return inserted
