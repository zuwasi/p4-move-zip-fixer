#!/usr/bin/env bash
# Option A pre-flight checks.
# Run with both source and destination P4PORT set as env vars.
#   SRC=source:1666 DST=destination:1666 ./preflight-checks.sh
set -euo pipefail

: "${SRC:?set SRC=host:port for source}"
: "${DST:?set DST=host:port for destination}"

fail=0
say()  { printf '  [%s] %s\n' "$1" "$2"; }
ok()   { say  OK "$1"; }
bad()  { say BAD "$1"; fail=1; }
warn() { say WARN "$1"; }

echo "=== Source  ($SRC)"
src_ver=$(p4 -p "$SRC" info | awk -F': ' '/Server version/ {print $2}')
src_case=$(p4 -p "$SRC" info | awk -F': ' '/Server case-handling/ {print $2}')
src_root=$(p4 -p "$SRC" info | awk -F': ' '/Server root/ {print $2}')
echo "  version: $src_ver"
echo "  case   : $src_case"
echo "  root   : $src_root"

echo
echo "=== Destination ($DST)"
dst_ver=$(p4 -p "$DST" info | awk -F': ' '/Server version/ {print $2}')
dst_case=$(p4 -p "$DST" info | awk -F': ' '/Server case-handling/ {print $2}')
dst_root=$(p4 -p "$DST" info | awk -F': ' '/Server root/ {print $2}')
echo "  version: $dst_ver"
echo "  case   : $dst_case"
echo "  root   : $dst_root"

echo
echo "=== Compatibility"
[[ "$src_case" == "$dst_case" ]] && ok "case-handling matches" || bad "case-handling mismatch — restore will FAIL"
[[ "$dst_ver" == "$src_ver" ]] && ok "exact version match" || warn "versions differ — restore works only if dest ≥ src"

echo
echo "=== Destination root state"
dst_db_count=$(ssh "$(echo "$DST" | cut -d: -f1)" "ls $dst_root/db.* 2>/dev/null | wc -l" || echo "?")
[[ "$dst_db_count" == "0" ]] && ok "destination /root has no db.* files (clean)" || bad "destination has $dst_db_count db.* files — refusing"

echo
echo "=== Disk on destination"
ssh "$(echo "$DST" | cut -d: -f1)" "df -h $dst_root" || warn "couldn't check df remotely"

echo
[[ "$fail" == "0" ]] && echo "PRE-FLIGHT: OK ✓" || { echo "PRE-FLIGHT: FAILED — fix above before proceeding"; exit 1; }
