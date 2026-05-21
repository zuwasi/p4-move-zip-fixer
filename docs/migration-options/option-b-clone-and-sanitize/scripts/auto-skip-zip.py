#!/usr/bin/env python3
"""Auto-skip-zip — produce a sequence of `p4 zip` chunks that together cover
the whole depot history, automatically skipping changelists whose move
counterpart was obliterated (i.e. unrecoverable by spec widening).

When p4-move-zip-fixer's auto-expand cannot widen the remote spec to cover
a failing CL (because `p4 describe -s` does not see the move counterpart),
the only safe options Perforce supports are:

  (a) clone + sanitize + obliterate the orphan path on the clone, then zip,
  (b) split the zip into ranges that do not contain the bad CL.

This script implements option (b) end-to-end. It is the fast / no-clone
path. It is safe — it never writes to the source server. It only invokes
`p4-move-zip-fixer zip` repeatedly with different `--depot` ranges and
records which changelists were skipped.

USAGE
-----

    python auto-skip-zip.py \\
        --p4port illin2343:1666 \\
        --remote migration-remote \\
        --depot //depot/... \\
        --out-dir /p4data/export/chunks \\
        --head 817597

Outputs:
    /p4data/export/chunks/chunk-001.zip
    /p4data/export/chunks/chunk-002.zip
    ...
    /p4data/export/chunks/manifest.json
    /p4data/export/chunks/skipped-cls.json

Replay on the destination, in order:

    for z in /import/chunks/chunk-*.zip; do
        p4 unzip -i "$z"
    done

WHAT GETS SKIPPED
-----------------
Only changelists where:
  * p4 zip reports "performs a move/{add,delete} ... but the parameters
    of this fetch, push, or zip command include only part of the full
    action", AND
  * the tool's auto-expand cannot widen the spec (counterpart obliterated
    or otherwise invisible to `p4 describe -s`).

These changelists have no recoverable content on the source side anyway:
the move target is gone, so there is nothing the destination could
materialise from them. The manifest records every skipped CL so you can
audit them later (and, if needed, restore them by hand from a backup).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# p4-move-zip-fixer emits this when it cannot widen the spec.
_FAILED_CLS_RE = re.compile(r"failed changelist\(s\) \(\[([\d, ]+)\]\)")
# Raw p4 zip error (first attempt, before auto-retry triggers).
_RAW_CHANGE_RE = re.compile(r"Change\s+(\d+)\s+performs a move", re.IGNORECASE)
# Sentinel emitted by the tool when expand contributed 0 paths.
_UNRECOVERABLE_RE = re.compile(r"Expand added 0 paths", re.IGNORECASE)


def head_change(p4port: str) -> int:
    try:
        from P4 import P4
    except ImportError:
        sys.stderr.write("p4python is required to auto-detect head CL: pip install p4python\n")
        sys.stderr.write("Or pass --head <NNN> explicitly.\n")
        sys.exit(2)
    p4 = P4(); p4.port = p4port; p4.connect()
    try:
        return int(p4.run("counter", "change")[0]["value"])
    finally:
        p4.disconnect()


def parse_bad_cls(stderr: str) -> list[int]:
    """Return the changelist numbers that p4 zip refused to process.

    Prefers the structured output emitted by p4-move-zip-fixer, falls
    back to the raw `Change <N> performs a move` lines.
    """
    cls: set[int] = set()
    m = _FAILED_CLS_RE.search(stderr)
    if m:
        cls.update(int(x.strip()) for x in m.group(1).split(",") if x.strip())
    cls.update(int(x) for x in _RAW_CHANGE_RE.findall(stderr))
    return sorted(cls)


def is_unrecoverable(stderr: str) -> bool:
    return bool(_UNRECOVERABLE_RE.search(stderr))


def try_zip_range(
    remote: str,
    output_path: Path,
    depot: str,
    start: int,
    end: int,
    auto_retry: int,
) -> subprocess.CompletedProcess:
    depot_path = f"{depot}@{start},#{end}"
    cmd = [
        "p4-move-zip-fixer", "zip",
        "--remote", remote,
        "--output", str(output_path),
        "--depot", depot_path,
        "--auto-retry", str(auto_retry),
    ]
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p4port", default=None,
                    help="P4PORT of the source server (only used to auto-detect --head). "
                         "Not required if --head is passed explicitly.")
    ap.add_argument("--remote", required=True,
                    help="Remote spec name (already built by p4-move-zip-fixer build-spec).")
    ap.add_argument("--depot", default="//depot/...",
                    help="Depot path glob.")
    ap.add_argument("--out-dir", required=True, type=Path,
                    help="Directory for chunk-NNN.zip outputs and manifest.")
    ap.add_argument("--head", type=int, default=None,
                    help="Highest changelist to include. Default: live head from --p4port.")
    ap.add_argument("--start", type=int, default=1,
                    help="First changelist (default 1).")
    ap.add_argument("--auto-retry", type=int, default=5,
                    help="Per-chunk auto-retry attempts for p4-move-zip-fixer (default 5).")
    ap.add_argument("--max-chunks", type=int, default=1000,
                    help="Safety stop: abort after this many chunks (default 1000).")
    args = ap.parse_args()

    if args.head is not None:
        head = args.head
    elif args.p4port:
        head = head_change(args.p4port)
    else:
        sys.stderr.write("FATAL: pass either --head <NNN> or --p4port <host:port>\n")
        return 2
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "manifest.json"
    skipped_path = args.out_dir / "skipped-cls.json"

    print(f"depot     = {args.depot}")
    print(f"range     = {args.start}..{head}")
    print(f"remote    = {args.remote}")
    print(f"out-dir   = {args.out_dir}")
    print()

    cur = args.start
    chunk_idx = 0
    chunks: list[dict] = []
    skipped: list[dict] = []

    while cur <= head:
        chunk_idx += 1
        if chunk_idx > args.max_chunks:
            sys.stderr.write(f"Aborting: hit --max-chunks={args.max_chunks}\n")
            break

        chunk_path = args.out_dir / f"chunk-{chunk_idx:04d}.zip"
        print(f"=== chunk {chunk_idx}: CLs {cur}..{head}")
        proc = try_zip_range(args.remote, chunk_path, args.depot, cur, head, args.auto_retry)
        # Emit subprocess output for the live log.
        if proc.stdout:
            print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
        if proc.stderr:
            sys.stderr.write(proc.stderr)
            if not proc.stderr.endswith("\n"):
                sys.stderr.write("\n")

        if proc.returncode == 0:
            print(f"  -> wrote {chunk_path}")
            chunks.append({
                "chunk": chunk_idx, "cl_start": cur, "cl_end": head,
                "zip": chunk_path.name, "skipped_cls_in_chunk": [],
            })
            break

        bad_cls = parse_bad_cls(proc.stderr)
        unrecoverable = is_unrecoverable(proc.stderr)
        if not bad_cls or not unrecoverable:
            sys.stderr.write(
                "FATAL: zip failed for a reason this script does not know how to skip.\n"
                "       (unrecoverable=%s, bad_cls=%s)\n"
                "       Inspect the stderr above and resolve before re-running.\n"
                % (unrecoverable, bad_cls)
            )
            return 2

        bad_cl = bad_cls[0]  # The first one is where p4 zip stopped.
        if bad_cl < cur or bad_cl > head:
            sys.stderr.write(f"FATAL: parsed bad CL {bad_cl} is outside range {cur}..{head}\n")
            return 2

        # Salvage everything before the bad CL into its own chunk.
        if bad_cl > cur:
            salvage_end = bad_cl - 1
            salvage_path = args.out_dir / f"chunk-{chunk_idx:04d}.zip"
            print(f"  --> salvaging clean prefix {cur}..{salvage_end} into {salvage_path.name}")
            proc2 = try_zip_range(args.remote, salvage_path, args.depot,
                                  cur, salvage_end, args.auto_retry)
            if proc2.stdout:
                print(proc2.stdout, end="" if proc2.stdout.endswith("\n") else "\n")
            if proc2.stderr:
                sys.stderr.write(proc2.stderr)
                if not proc2.stderr.endswith("\n"):
                    sys.stderr.write("\n")
            if proc2.returncode != 0:
                # A different bad CL inside [cur..salvage_end]. Walk forward.
                inner_bad = parse_bad_cls(proc2.stderr)
                if inner_bad and is_unrecoverable(proc2.stderr) and cur <= inner_bad[0] < salvage_end:
                    bad_cl = inner_bad[0]
                    print(f"  --> nested bad CL {bad_cl} found inside salvage range")
                    salvage_end = bad_cl - 1
                    if salvage_end >= cur:
                        proc3 = try_zip_range(args.remote, salvage_path, args.depot,
                                              cur, salvage_end, args.auto_retry)
                        if proc3.returncode != 0:
                            sys.stderr.write(
                                f"FATAL: cannot salvage even {cur}..{salvage_end}. Aborting.\n"
                            )
                            return 2
                    else:
                        salvage_path.unlink(missing_ok=True)
                else:
                    sys.stderr.write(f"FATAL: cannot salvage {cur}..{salvage_end}.\n")
                    return 2
            if salvage_path.exists():
                chunks.append({
                    "chunk": chunk_idx, "cl_start": cur, "cl_end": salvage_end,
                    "zip": salvage_path.name,
                    "skipped_cls_in_chunk": [],
                })
                print(f"  -> wrote {salvage_path}")
            chunk_idx_for_skip = chunk_idx  # for log
        else:
            chunk_idx -= 1  # we didn't actually create a salvage chunk

        # Record the skipped CL and advance past it.
        print(f"  --> SKIPPING CL {bad_cl} (unrecoverable orphan move)")
        skipped.append({
            "cl": bad_cl,
            "after_chunk": chunks[-1]["chunk"] if chunks else None,
            "reason": "p4 zip auto-expand could not widen spec (counterpart obliterated)",
        })
        cur = bad_cl + 1

    # Persist manifests.
    # We expose the chunks under both keys so sliced-unzip.py works unchanged:
    #   - "chunks" is the native auto-skip shape (includes skipped_cls_in_chunk)
    #   - "slices" mirrors sliced-zip.py's manifest so the same replay tool
    #     (sliced-unzip.py) can drive it on the destination.
    slices_view = [
        {"slice": c["chunk"], "cl_start": c["cl_start"], "cl_end": c["cl_end"],
         "paths": None, "zip": c["zip"], "remote": args.remote}
        for c in chunks
    ]
    manifest = {
        "depot": args.depot,
        "head": head,
        "start": args.start,
        "remote": args.remote,
        "chunk_count": len(chunks),
        "skipped_count": len(skipped),
        "chunks": chunks,
        "slices": slices_view,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    skipped_path.write_text(json.dumps(skipped, indent=2), encoding="utf-8")

    print()
    print(f"Done. chunks={len(chunks)}  skipped_cls={len(skipped)}")
    print(f"  manifest -> {manifest_path}")
    print(f"  skipped  -> {skipped_path}")
    if skipped:
        print()
        print("Skipped changelists (audit these against a backup if you need their content):")
        for s in skipped:
            print(f"  CL {s['cl']}  ({s['reason']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
