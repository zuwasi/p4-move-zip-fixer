#!/usr/bin/env python3
"""Find move/add and move/delete records on a Perforce server (the clone)
whose counterpart is missing from the depot (typically: obliterated).

This is the input to scripts/sanitize-clone.py.

    python find-orphan-moves.py --p4port clone:1700 --depot //depot/... --output orphans.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from P4 import P4, P4Exception
except ImportError:
    sys.stderr.write("p4python is required: pip install p4python\n")
    raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--p4port", required=True, help="Clone server P4PORT (host:port).")
    ap.add_argument("--depot", default="//depot/...", help="Depot path to scan.")
    ap.add_argument("--output", required=True, type=Path, help="JSON output file.")
    ap.add_argument("--user", default=None, help="Override p4 user (else env).")
    args = ap.parse_args()

    p4 = P4()
    p4.port = args.p4port
    if args.user:
        p4.user = args.user
    p4.connect()

    orphans: list[dict] = []
    scanned = 0
    try:
        # Get every move-related revision via -ztag filelog.
        recs = p4.run("filelog", args.depot)
        for rec in recs:
            scanned += 1
            depot_file = rec.get("depotFile")
            actions = rec.get("action") or []
            moved_files = rec.get("movedFile") or []
            changes = rec.get("change") or []
            if isinstance(actions, str):
                actions = [actions]
                moved_files = [moved_files]
                changes = [changes]
            for i, action in enumerate(actions):
                if action not in ("move/add", "move/delete"):
                    continue
                moved = moved_files[i] if i < len(moved_files) else None
                change = changes[i] if i < len(changes) else None
                # An orphan = no moved-file, OR moved-file does not currently exist.
                if not moved:
                    orphans.append({
                        "change": int(change) if change else None,
                        "depotFile": f"{depot_file}#{rec.get('rev', [None])[i] if isinstance(rec.get('rev'), list) else '?'}",
                        "action": action,
                        "movedFile": None,
                        "reason": "filelog returned no movedFile reference",
                    })
                    continue
                # Cheap existence probe — p4 fstat returns nothing for missing
                try:
                    fstat = p4.run("fstat", moved)
                    if not fstat:
                        orphans.append({
                            "change": int(change) if change else None,
                            "depotFile": depot_file,
                            "action": action,
                            "movedFile": moved,
                            "reason": "movedFile not present in depot — counterpart obliterated",
                        })
                except P4Exception:
                    orphans.append({
                        "change": int(change) if change else None,
                        "depotFile": depot_file,
                        "action": action,
                        "movedFile": moved,
                        "reason": "fstat raised — counterpart inaccessible",
                    })
    finally:
        p4.disconnect()

    report = {
        "p4port": args.p4port,
        "depot": args.depot,
        "scanned_files": scanned,
        "orphan_count": len(orphans),
        "orphan_pairs": orphans,
    }
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"scanned {scanned} files; orphans={len(orphans)} → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
