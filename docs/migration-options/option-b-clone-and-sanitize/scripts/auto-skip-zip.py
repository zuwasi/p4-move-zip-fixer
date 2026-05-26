#!/usr/bin/env python3
"""Auto-exclude-then-zip — produce a single `p4 zip` archive by
automatically adding DepotMap exclusion lines for every orphan path that
p4 zip refuses to process (because the move counterpart was obliterated).

Why exclusion (not chunking)
----------------------------
`p4 zip` walks each file's full revision history regardless of the
requested CL range. Narrowing `--depot //depot/...@1,#781421` does NOT
exclude the file revision at CL 781422 if that file is in the DepotMap.
So splitting the zip around a bad CL cannot work. The only thing that
actually stops `p4 zip` from inspecting the orphan file is to remove it
from the view — which is recovery option #3 in p4-move-zip-fixer's own
guidance:

    "add explicit exclusion lines for the orphan paths to the remote
     spec so they're excluded from view entirely."

This script automates that. For every "Change N performs a move/X on
//depot/...#rev" error it parses, it appends an exclusion line of the
form ``-//depot/<path> //remote/<path>`` to the remote spec's DepotMap
and retries `p4 zip`. It loops until p4 zip succeeds or until the same
path would be excluded twice (which means exclusion didn't help and the
error is something else).

Read-only against source data: the only write is to the *remote spec*
itself, which is metadata you already built with build-spec. No depot
content is touched.

USAGE
-----

    python auto-skip-zip.py \\
        --remote migration-remote \\
        --depot //depot/... \\
        --output /p4data/export/default-depot.zip \\
        --p4port illin2343:1666

Outputs:
    /p4data/export/default-depot.zip
    /p4data/export/excluded-paths.json   (audit)

REPLAY on destination (just one zip, ordinary p4 unzip):

    p4 -p destination:1666 unzip -i /import/default-depot.zip
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
# Raw p4 zip error with file path. Captures both CL and depot path.
#   "Change 781422 performs a move/delete on //depot/.../file.jar#2,"
_RAW_MOVE_RE = re.compile(
    r"Change\s+(\d+)\s+performs a move/\w+\s+on\s+(//[^#\s,]+)",
    re.IGNORECASE,
)
# Sentinel emitted by the tool when expand contributed 0 paths.
_UNRECOVERABLE_RE = re.compile(r"Expand added 0 paths", re.IGNORECASE)


def _p4():
    """Lazy-import P4 only when actually needed (so --help works without it)."""
    try:
        from P4 import P4
    except ImportError:
        sys.stderr.write("p4python is required: pip install p4python\n")
        sys.exit(2)
    return P4


def parse_orphan_paths(stderr: str) -> list[tuple[int, str]]:
    """Return [(changelist, depot_path), ...] from p4 zip error output.

    Depot paths come with the `#rev` suffix already stripped by the regex.
    """
    pairs = []
    seen = set()
    for m in _RAW_MOVE_RE.finditer(stderr):
        cl = int(m.group(1))
        path = m.group(2)
        if path not in seen:
            seen.add(path)
            pairs.append((cl, path))
    return pairs


def is_unrecoverable(stderr: str) -> bool:
    return bool(_UNRECOVERABLE_RE.search(stderr))


def build_exclusion_line(depot_path: str, remote_root: str = "//remote") -> str:
    """Translate //depot/foo/bar -> '"-//depot/foo/bar" "//remote/foo/bar"'.

    The depot-side has a leading '-' to mark the line as exclusion. The '-'
    MUST go INSIDE the quotes — Perforce's spec parser, when a line starts
    with '-', stays in unquoted-token mode and reads the following '"' as a
    literal character of the depot path, producing errors like:

        Error in remote specification.
        Null directory (//) not allowed in '"//depot/.../file.jar"'.

    Putting the '-' inside the quotes (`"-//depot/path"`) is the canonical
    Perforce form and tokenises correctly regardless of whether the path
    needs quoting. Quoting handles paths with spaces or other special
    characters. The remote side mirrors the depot layout (we strip the
    '//depot' prefix and graft it onto remote_root).
    """
    if not depot_path.startswith("//"):
        raise ValueError(f"bad depot path: {depot_path!r}")
    parts = depot_path.lstrip("/").split("/", 1)
    tail = "/" + parts[1] if len(parts) > 1 else ""
    return f'"-{depot_path}" "{remote_root}{tail}"'


def _is_exclusion_line(line: str) -> bool:
    """True if this DepotMap line is an exclusion (depot-side starts with '-').

    Recognises both the canonical quoted form `"-//depot/..."` and the
    unquoted form `-//depot/...`. Defensive against leading whitespace.
    """
    s = (line or "").lstrip()
    return s.startswith('"-') or s.startswith("-//")


def _split_catchall_and_exclusions(lines: list[str]) -> tuple[str | None, list[str]]:
    """Return (catchall_line, [exclusion_lines]) from an existing DepotMap.

    The catch-all is the first non-exclusion line (typically
    ``//depot/... //remote/...``). All per-file inclusion lines that aren't
    the catch-all are dropped during a compact rebuild — they're redundant
    given the catch-all already covers every depot path. Exclusion lines
    are preserved because they actively remove paths from view.
    """
    catchall: str | None = None
    exclusions: list[str] = []
    for line in lines:
        stripped = line.strip() if line else ""
        if not stripped:
            continue
        if _is_exclusion_line(stripped):
            exclusions.append(stripped)
        elif catchall is None:
            catchall = stripped
        # else: redundant per-file inclusion — drop it on compact rebuild.
    return catchall, exclusions


def add_exclusions_to_remote(
    remote_name: str,
    depot_paths: list[str],
    remote_root: str = "//remote",
) -> tuple[int, int]:
    """Append exclusion lines to the remote spec's DepotMap.

    Returns ``(actually_added, total_after)`` — note that ``actually_added``
    is the **delta in persisted DepotMap length**, not the intended add
    count. When the spec is at Perforce's 100k DepotMap cap the server
    silently drops new entries; the additive write reports zero growth and
    we transparently switch to a **compact rebuild**: replace the spec
    with ``[catch-all] + [existing exclusions] + [new exclusions]``,
    dropping all redundant per-file inclusion lines that the catch-all
    already covers. This frees the room needed for the new exclusions
    without changing what the spec includes in view.
    """
    if not depot_paths:
        return 0, 0
    P4 = _p4()
    p4 = P4()
    p4.connect()
    try:
        spec = p4.fetch_remote(remote_name)
        existing = list(spec.get("DepotMap") or [])
        existing_set = set(existing)
        new_lines = [build_exclusion_line(p, remote_root) for p in depot_paths]
        to_add = [ln for ln in new_lines if ln not in existing_set]
        if not to_add:
            return 0, len(existing)

        # First try: simple append.
        spec["DepotMap"] = existing + to_add
        p4.save_remote(spec)
        fresh = p4.fetch_remote(remote_name)
        persisted = list(fresh.get("DepotMap") or [])
        actual_delta = len(persisted) - len(existing)

        if actual_delta >= len(to_add):
            return actual_delta, len(persisted)

        # Server silently truncated (DepotMap is at the 100k cap). Rebuild
        # compactly: catch-all + every exclusion (existing + new), dropping
        # the redundant per-file inclusion lines.
        catchall, prior_excl = _split_catchall_and_exclusions(persisted)
        if catchall is None:
            # No catch-all found — fall back to the conventional default
            # so the spec still covers the depot.
            catchall = f"//depot/... {remote_root}/..."

        prior_excl_set = set(prior_excl)
        compact = [catchall] + prior_excl + [ln for ln in to_add if ln not in prior_excl_set]

        spec = p4.fetch_remote(remote_name)
        spec["DepotMap"] = compact
        p4.save_remote(spec)
        fresh2 = p4.fetch_remote(remote_name)
        persisted2 = list(fresh2.get("DepotMap") or [])

        # Real growth is the count of new exclusions now in the spec versus
        # the *original* existing set (so the caller can see whether the
        # compact rebuild actually delivered the requested additions).
        truly_added = sum(1 for ln in to_add if ln in set(persisted2))
        return truly_added, len(persisted2)
    finally:
        p4.disconnect()


def try_zip(remote: str, output_path: Path, depot: str,
            auto_retry: int) -> subprocess.CompletedProcess:
    cmd = [
        "p4-move-zip-fixer", "zip",
        "--remote", remote,
        "--output", str(output_path),
        "--depot", depot,
        "--auto-retry", str(auto_retry),
    ]
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--remote", required=True,
                    help="Remote spec name (already built by p4-move-zip-fixer build-spec).")
    ap.add_argument("--depot", default="//depot/...@1,#head",
                    help='Depot path with range, e.g. "//depot/...@1,#head".')
    ap.add_argument("--output", required=True, type=Path,
                    help="Output zip path.")
    ap.add_argument("--remote-root", default="//remote",
                    help="Remote side prefix used in exclusion lines.")
    ap.add_argument("--auto-retry", type=int, default=5,
                    help="Per-attempt p4-move-zip-fixer auto-retry (default 5).")
    ap.add_argument("--max-exclusions", type=int, default=5000,
                    help="Safety cap on how many paths to auto-exclude (default 5000).")
    ap.add_argument("--audit", type=Path, default=None,
                    help="Where to write the JSON audit of excluded paths. "
                         "Defaults to <output>.excluded-paths.json")
    args = ap.parse_args()

    audit_path = args.audit or args.output.with_suffix(args.output.suffix + ".excluded-paths.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    excluded: list[dict] = []
    excluded_paths_set: set[str] = set()
    attempt = 0
    print(f"remote   = {args.remote}")
    print(f"depot    = {args.depot}")
    print(f"output   = {args.output}")
    print(f"audit    = {audit_path}")
    print()

    while True:
        attempt += 1
        print(f"=== attempt {attempt}  (excluded so far: {len(excluded)})")
        proc = try_zip(args.remote, args.output, args.depot, args.auto_retry)
        if proc.stdout:
            print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
        if proc.stderr:
            sys.stderr.write(proc.stderr)
            if not proc.stderr.endswith("\n"):
                sys.stderr.write("\n")

        if proc.returncode == 0:
            print(f"\nSUCCESS on attempt {attempt}.")
            print(f"  zip      -> {args.output}")
            print(f"  excluded -> {len(excluded)} path(s)")
            break

        pairs = parse_orphan_paths(proc.stderr)
        if not pairs:
            sys.stderr.write(
                "FATAL: zip failed but no orphan-move path could be parsed "
                "from the error. Inspect stderr above and resolve manually.\n"
            )
            return 2
        # Only act on paths we haven't already excluded; if all of them are
        # already excluded the exclusion didn't help and we must stop.
        fresh_pairs = [(cl, p) for cl, p in pairs if p not in excluded_paths_set]
        if not fresh_pairs:
            sys.stderr.write(
                "FATAL: every offending path is already excluded but p4 zip "
                "still failed for the same path(s). The exclusion was not "
                "honoured by the server (DepotMap truncated? wrong remote spec?). "
                "Inspect with: p4 remote -o %s\n" % args.remote
            )
            return 2

        if len(excluded) + len(fresh_pairs) > args.max_exclusions:
            sys.stderr.write(
                f"FATAL: would exceed --max-exclusions={args.max_exclusions}. "
                "Re-run with a larger cap or investigate why so many orphans.\n"
            )
            return 2

        for cl, path in fresh_pairs:
            print(f"  --> excluding orphan path from CL {cl}: {path}")
            excluded.append({"cl": cl, "depot_path": path,
                             "reason": "orphan move counterpart obliterated"})
            excluded_paths_set.add(path)

        added, total = add_exclusions_to_remote(
            args.remote, [p for _, p in fresh_pairs], remote_root=args.remote_root,
        )
        print(f"  remote spec '{args.remote}': +{added} exclusion lines "
              f"(DepotMap total = {total})")

        if added == 0:
            sys.stderr.write(
                "FATAL: spec update reported +0 lines persisted — the server "
                "may have refused or truncated the update.\n"
            )
            return 2

        # Persist audit on every iteration so a crash still leaves a trail.
        audit_path.write_text(
            json.dumps({"excluded_count": len(excluded), "excluded": excluded}, indent=2),
            encoding="utf-8",
        )

    # Final audit write.
    audit_path.write_text(
        json.dumps({"excluded_count": len(excluded), "excluded": excluded}, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
