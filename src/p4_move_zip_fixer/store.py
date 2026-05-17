"""Resumable SQLite cache of discovered move pairs."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class MoveRow:
    src: str | None
    tgt: str | None
    change: int
    action: str  # "move/add" or "move/delete"


SCHEMA = """
CREATE TABLE IF NOT EXISTS moves (
    src    TEXT,
    tgt    TEXT,
    change INTEGER NOT NULL,
    action TEXT NOT NULL,
    UNIQUE(src, tgt, change, action)
);
CREATE INDEX IF NOT EXISTS idx_moves_change ON moves(change);

CREATE TABLE IF NOT EXISTS scanned_ranges (
    start_change INTEGER NOT NULL,
    end_change   INTEGER NOT NULL,
    PRIMARY KEY (start_change, end_change)
);
"""


class MoveStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def insert_moves(self, rows: Iterable[MoveRow]) -> int:
        cur = self._conn.executemany(
            "INSERT OR IGNORE INTO moves(src,tgt,change,action) VALUES(?,?,?,?)",
            [(r.src, r.tgt, r.change, r.action) for r in rows],
        )
        self._conn.commit()
        return cur.rowcount

    def mark_range_scanned(self, start: int, end: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO scanned_ranges(start_change,end_change) VALUES(?,?)",
            (start, end),
        )
        self._conn.commit()

    def is_range_scanned(self, start: int, end: int) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM scanned_ranges WHERE start_change=? AND end_change=?",
            (start, end),
        )
        return cur.fetchone() is not None

    def all_paths(self) -> set[str]:
        cur = self._conn.execute("SELECT src, tgt FROM moves")
        out: set[str] = set()
        for src, tgt in cur:
            if src:
                out.add(src)
            if tgt:
                out.add(tgt)
        return out

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM moves")
        return int(cur.fetchone()[0])

    def close(self) -> None:
        self._conn.close()


@contextmanager
def open_store(path: str | Path) -> Iterator[MoveStore]:
    s = MoveStore(path)
    try:
        yield s
    finally:
        s.close()
