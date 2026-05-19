#!/usr/bin/env python3
"""Replay sliced zips on the destination server in CL order.

Reads manifest.json produced by sliced-zip.py, then runs `p4 unzip -i` for
each slice. Stops at the first failure so you can investigate before
ordering goes wrong.

    python sliced-unzip.py --p4port destination:1666 --in-dir /p4/1/incoming/migration/
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--p4port", required=True)
    ap.add_argument("--in-dir", required=True, type=Path)
    ap.add_argument("--p4user", default=None)
    ap.add_argument("--continue-on-error", action="store_true",
                    help="Do not stop at the first failed unzip. Use only with eyes on output.")
    args = ap.parse_args()

    manifest_path = args.in_dir / "manifest.json"
    if not manifest_path.exists():
        sys.stderr.write(f"missing manifest.json in {args.in_dir}\n")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    slices = sorted(manifest["slices"], key=lambda s: s["slice"])

    base = ["p4", "-p", args.p4port]
    if args.p4user:
        base += ["-u", args.p4user]

    failed = 0
    for s in slices:
        zip_path = args.in_dir / s["zip"]
        if not zip_path.exists():
            print(f"  SKIP slice {s['slice']}: missing {zip_path}")
            failed += 1
            if not args.continue_on_error:
                return 2
            continue
        cmd = base + ["unzip", "-i", str(zip_path)]
        print(f"\n=== slice {s['slice']}  CLs {s['cl_start']}..{s['cl_end']}  ({s['paths']} paths)")
        print("  $", " ".join(cmd))
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"  FAILED with rc={rc}")
            failed += 1
            if not args.continue_on_error:
                return rc
        else:
            print(f"  OK")

    print(f"\nDone. ok={len(slices) - failed}  failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
