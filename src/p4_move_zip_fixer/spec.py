"""Build a Perforce remote spec that covers both sides of every recorded move."""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .p4client import P4Like, make_p4
from .store import MoveStore


def build_view_lines(paths: Iterable[str], remote_root: str = "//remote") -> list[str]:
    """Translate //depot/foo/bar -> '//depot/foo/bar //remote/foo/bar'.

    The remote-side path strips the leading '//<depot-name>' segment so the
    remote depot mirrors the local layout.
    """
    lines: list[str] = []
    for p in sorted(set(paths)):
        if not p.startswith("//"):
            continue
        # strip leading '//<depot>' (e.g. '//depot') -> '/foo/bar'
        tail = "/" + p.lstrip("/").split("/", 1)[1] if "/" in p.lstrip("/") else ""
        lines.append(f'"{p}" "{remote_root}{tail}"')
    return lines


def build_remote_spec(
    store: MoveStore,
    remote_name: str,
    p4_factory: Callable[[], P4Like] = make_p4,
    remote_root: str = "//remote",
    depot_map: str = "//depot/... //remote/...",
) -> int:
    """Generate and save a remote spec containing every path involved in a move.

    Path mappings go into the spec's **DepotMap** field — remote specs do not
    have a 'View' field; that was a bug in earlier versions. The catch-all
    `//depot/... //remote/...` line is included first so any path not
    explicitly listed still has a default mapping; per-move file mappings
    follow it so the source and target sides of every move are covered.

    Returns the number of DepotMap lines written (including the catch-all).
    """
    paths = store.all_paths()
    per_file = build_view_lines(paths, remote_root=remote_root)
    depot_map_lines = [depot_map, *per_file]

    p4 = p4_factory()
    p4.connect()
    try:
        spec = p4.fetch_remote(remote_name)
        spec["DepotMap"] = depot_map_lines
        p4.save_remote(spec)
    finally:
        p4.disconnect()
    return len(depot_map_lines)


def _describe_paths(records: Iterable[dict[str, Any]]) -> set[str]:
    """Pull every depot path out of `p4 describe -s` records."""
    paths: set[str] = set()
    for rec in records:
        files = rec.get("depotFile") or []
        if isinstance(files, str):
            files = [files]
        for f in files:
            if f and f.startswith("//"):
                paths.add(f)
    return paths


def expand_spec_with_changelists(
    remote_name: str,
    changelists: Iterable[int],
    p4_factory: Callable[[], P4Like] = make_p4,
    remote_root: str = "//remote",
) -> tuple[int, int]:
    """Add every path touched by the given changelists into the remote spec.

    This is the documented "branch-of-move / iterate on failed changelists"
    mitigation: when `p4 zip` complains about a specific changelist, we run
    `p4 describe -s <n>` for it and add every referenced path to the spec,
    so the next zip attempt covers both sides of whatever caused the failure
    (move chain, branch-of-move, lazy copy, obliterated counterpart, etc.).

    Returns (paths_added, total_view_lines_after).
    """
    p4 = p4_factory()
    p4.connect()
    try:
        spec = p4.fetch_remote(remote_name)
        existing_lines = list(spec.get("DepotMap") or [])

        # Existing depot paths = first whitespace-or-quoted token of each line.
        existing_paths: set[str] = set()
        for line in existing_lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith('"'):
                end = line.find('"', 1)
                if end > 0:
                    existing_paths.add(line[1:end])
            else:
                tok = line.split()[0]
                existing_paths.add(tok)

        # Collect every path touched by every failing changelist.
        new_paths: set[str] = set()
        for chg in changelists:
            records = p4.run("describe", "-s", str(chg))
            new_paths |= _describe_paths(records)

        added = sorted(new_paths - existing_paths)
        if added:
            extra_lines = build_view_lines(added, remote_root=remote_root)
            spec["DepotMap"] = existing_lines + extra_lines
            p4.save_remote(spec)
        return len(added), len(spec.get("DepotMap") or [])
    finally:
        p4.disconnect()
