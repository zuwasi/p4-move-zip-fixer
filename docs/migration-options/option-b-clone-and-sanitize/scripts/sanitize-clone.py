#!/usr/bin/env python3
"""Sanitize a Perforce CLONE server by obliterating orphan move records.

WILL REFUSE TO RUN without --execute --i-really-mean-it.
WILL REFUSE TO RUN against a server whose hostname looks like production.

    # dry-run (always do this first)
    python sanitize-clone.py --p4port clone:1700 --orphans orphans.json --strategy obliterate

    # actually execute
    python sanitize-clone.py --p4port clone:1700 --orphans orphans.json \\
        --strategy obliterate --execute --i-really-mean-it

For --strategy skip, no obliterates are performed; a CL skip-list is emitted.
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

# Sanity-guard: hostnames matching any of these substrings refuse to run.
PROD_GUARD_SUBSTRINGS = ("illin", "prod", "p4jirastg", "live", "main-")


def looks_like_prod(p4port: str) -> bool:
    host = p4port.split(":")[0].lower()
    return any(g in host for g in PROD_GUARD_SUBSTRINGS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--p4port", required=True)
    ap.add_argument("--orphans", required=True, type=Path)
    ap.add_argument("--strategy", choices=["obliterate", "skip"], required=True)
    ap.add_argument("--execute", action="store_true",
                    help="Actually perform obliterates. Without this, dry-run only.")
    ap.add_argument("--i-really-mean-it", action="store_true",
                    help="Required confirmation alongside --execute.")
    ap.add_argument("--force-allow-prod-hostname", action="store_true",
                    help="Skip the production-hostname safety guard. Don't.")
    ap.add_argument("--emit-skip-list", type=Path, default=None,
                    help="For --strategy skip: write CLs to this file.")
    args = ap.parse_args()

    if looks_like_prod(args.p4port) and not args.force_allow_prod_hostname:
        sys.stderr.write(
            f"REFUSING: --p4port='{args.p4port}' looks like a production server "
            f"(matched one of {PROD_GUARD_SUBSTRINGS}).\n"
            "If you really intend to run against this host, pass "
            "--force-allow-prod-hostname.\n"
        )
        return 2

    report = json.loads(args.orphans.read_text(encoding="utf-8"))
    pairs = report.get("orphan_pairs", [])
    if not pairs:
        print("No orphans to act on.")
        return 0

    print(f"Loaded {len(pairs)} orphans from {args.orphans}")
    print(f"Strategy: {args.strategy}")
    print(f"Mode    : {'EXECUTE' if (args.execute and args.i_really_mean_it) else 'DRY-RUN'}")

    if args.strategy == "skip":
        cls = sorted({p["change"] for p in pairs if p.get("change") is not None})
        out = args.emit_skip_list or Path("skip-cls.txt")
        out.write_text("\n".join(str(c) for c in cls) + "\n", encoding="utf-8")
        print(f"Wrote {len(cls)} changelists to {out}")
        return 0

    # strategy == obliterate
    will_execute = args.execute and args.i_really_mean_it
    p4 = P4()
    p4.port = args.p4port
    p4.connect()
    ok = bad = 0
    try:
        for pair in pairs:
            df = pair.get("depotFile")
            if not df:
                continue
            print(f"  obliterate -y {df}")
            if not will_execute:
                ok += 1
                continue
            try:
                p4.run("obliterate", "-y", df)
                ok += 1
            except P4Exception as e:
                bad += 1
                print(f"    FAILED: {e}")
    finally:
        p4.disconnect()

    print(f"\nDone. ok={ok}  failed={bad}  mode={'EXECUTE' if will_execute else 'DRY-RUN'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
