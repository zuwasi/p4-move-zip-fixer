#!/usr/bin/env python3
"""Bisect a Perforce depot history into ≤N-path slices and run `p4 zip`
on each slice so we never trip the server-side 100k DepotMap cap.

Each slice gets its own remote spec named <remote>-NNN, its own SQLite,
and its own output zip. A manifest.json records slice → CL-range mapping
so the destination can replay them in order.

    python sliced-zip.py --p4port clone:1700 --remote migration-remote \\
        --depot //depot/... --out-dir /clone/slices --max-paths-per-slice 95000
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

try:
    from P4 import P4
except ImportError:
    sys.stderr.write("p4python is required: pip install p4python\n")
    raise


def head_change(p4port: str) -> int:
    p4 = P4(); p4.port = p4port; p4.connect()
    try:
        return int(p4.run("counter", "change")[0]["value"])
    finally:
        p4.disconnect()


def estimate_paths_per_range(p4port: str, depot: str, start: int, end: int) -> int:
    p4 = P4(); p4.port = p4port; p4.connect()
    try:
        recs = p4.run("files", f"{depot}@{start},@{end}")
        return len(recs)
    finally:
        p4.disconnect()


def run(cmd: list[str]) -> None:
    print("  $", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--p4port", required=True)
    ap.add_argument("--remote", required=True, help="Base remote-spec name.")
    ap.add_argument("--depot", default="//depot/...")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--max-paths-per-slice", type=int, default=95000,
                    help="Hard ceiling, well below Perforce's 100k cap.")
    ap.add_argument("--initial-slice-cls", type=int, default=50000,
                    help="Starting CL window size; halved on overflow, doubled on under-fill.")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    head = head_change(args.p4port)
    print(f"head changelist = {head}")

    slices = []
    cur = 1
    window = args.initial_slice_cls
    n = 0
    while cur <= head:
        end = min(cur + window - 1, head)
        paths = estimate_paths_per_range(args.p4port, args.depot, cur, end)
        if paths > args.max_paths_per_slice and window > 1:
            window = max(1, window // 2)
            print(f"  range {cur}..{end} has {paths} paths — shrinking window to {window}")
            continue
        n += 1
        slice_remote = f"{args.remote}-{n:03d}"
        slice_db = args.out_dir / f"slice-{n:03d}.sqlite"
        slice_zip = args.out_dir / f"slice-{n:03d}.zip"
        env = f"--depot '{args.depot}@{cur},#{end}'"
        print(f"\n=== slice {n}: CLs {cur}..{end}  ({paths} paths, window {window})")
        run(["p4-move-zip-fixer", "scan", "--depot", args.depot,
             "--head", str(end), "--db", str(slice_db), "--workers", "4"])
        run(["p4-move-zip-fixer", "build-spec", "--db", str(slice_db),
             "--remote", slice_remote])
        run(["p4-move-zip-fixer", "zip", "--remote", slice_remote,
             "--output", str(slice_zip),
             "--depot", f"{args.depot}@{cur},#{end}"])
        slices.append({"slice": n, "cl_start": cur, "cl_end": end,
                       "paths": paths, "zip": slice_zip.name,
                       "remote": slice_remote})
        cur = end + 1
        # gradually grow the window again if we shrunk it
        window = min(args.initial_slice_cls, window * 2)

    manifest = {"depot": args.depot, "head": head, "slices": slices}
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nDone. {len(slices)} slices → {args.out_dir}/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
