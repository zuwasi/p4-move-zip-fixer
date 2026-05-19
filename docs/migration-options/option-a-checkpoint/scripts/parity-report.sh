#!/usr/bin/env bash
# Option A — post-restore parity report.
# Compares source and destination on counters, depots, change count, and
# a random sample of file SHA256s. Emits a single PASS/FAIL line at the end.
#
#   SRC=source:1666 DST=destination:1666 SAMPLE=50 ./parity-report.sh
set -euo pipefail

: "${SRC:?set SRC=host:port for source}"
: "${DST:?set DST=host:port for destination}"
SAMPLE="${SAMPLE:-50}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fail=0
say()  { printf '  [%s] %s\n' "$1" "$2"; }
ok()   { say  OK "$1"; }
bad()  { say BAD "$1"; fail=1; }

echo "=== 1. Counter parity"
p4 -p "$SRC" counter change > "$TMP/src.change"
p4 -p "$DST" counter change > "$TMP/dst.change"
if diff -q "$TMP/src.change" "$TMP/dst.change" > /dev/null; then ok "change counter matches ($(cat "$TMP/src.change"))"; else bad "change counter mismatch — src=$(cat "$TMP/src.change") dst=$(cat "$TMP/dst.change")"; fi

echo
echo "=== 2. Depot listing"
p4 -p "$SRC" depots | awk '{print $2}' | sort > "$TMP/src.depots"
p4 -p "$DST" depots | awk '{print $2}' | sort > "$TMP/dst.depots"
if diff -q "$TMP/src.depots" "$TMP/dst.depots" > /dev/null; then ok "depot lists identical"; else bad "depot list differs — see $TMP/{src,dst}.depots"; diff "$TMP/src.depots" "$TMP/dst.depots" || true; fi

echo
echo "=== 3. Changelist count per depot"
for d in $(cat "$TMP/src.depots"); do
    sc=$(p4 -p "$SRC" changes "${d}/..." | wc -l)
    dc=$(p4 -p "$DST" changes "${d}/..." | wc -l)
    if [[ "$sc" == "$dc" ]]; then ok "$d : $sc changes (match)"; else bad "$d : src=$sc dst=$dc"; fi
done

echo
echo "=== 4. SHA256 sample diff ($SAMPLE files)"
p4 -p "$SRC" files //... | head -100000 | shuf -n "$SAMPLE" | awk '{print $1}' > "$TMP/sample.txt"
mis=0
while IFS= read -r f; do
    sh_src=$(p4 -p "$SRC" print -q "$f" 2>/dev/null | sha256sum | awk '{print $1}')
    sh_dst=$(p4 -p "$DST" print -q "$f" 2>/dev/null | sha256sum | awk '{print $1}')
    if [[ "$sh_src" != "$sh_dst" ]]; then bad "SHA mismatch: $f"; mis=$((mis+1)); fi
done < "$TMP/sample.txt"
[[ "$mis" == "0" ]] && ok "$SAMPLE/$SAMPLE files SHA256-match"

echo
[[ "$fail" == "0" ]] && echo "PARITY: PASS ✓" || { echo "PARITY: FAIL — investigate before promoting"; exit 1; }
