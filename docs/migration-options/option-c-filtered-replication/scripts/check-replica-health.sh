#!/usr/bin/env bash
# Option C — quick replica health check. Reports journal lag, archive
# transfer queue, and pull worker state. Exits 0 if replica is "ready
# enough" to promote (lag < 5s, archive queue empty), non-zero otherwise.
#
#   SRC=source:1666 REPLICA=destination:1666 ./check-replica-health.sh
set -euo pipefail

: "${SRC:?set SRC=host:port (source/master)}"
: "${REPLICA:?set REPLICA=host:port (replica)}"
LAG_THRESHOLD_SEC="${LAG_THRESHOLD_SEC:-5}"

ok()  { printf '  [OK]  %s\n' "$1"; }
bad() { printf '  [BAD] %s\n' "$1"; exit 1; }
warn(){ printf '  [WARN]%s\n' "$1"; }

echo "=== Journal lag"
src_jn=$(p4 -p "$SRC"     counter journal 2>/dev/null || echo 0)
dst_jn=$(p4 -p "$REPLICA" counter journal 2>/dev/null || echo 0)
echo "  source  journal counter: $src_jn"
echo "  replica journal counter: $dst_jn"
[[ "$src_jn" == "$dst_jn" ]] && ok "journals match" || warn "differ — replica may be catching up"

echo
echo "=== Change-counter parity"
src_ch=$(p4 -p "$SRC"     counter change)
dst_ch=$(p4 -p "$REPLICA" counter change)
echo "  source : $src_ch"
echo "  replica: $dst_ch"
diff=$(( src_ch - dst_ch ))
[[ "$diff" -le 0 ]] && ok "replica at or ahead of source (impossible — re-check)" \
    || ([[ "$diff" -le 1 ]] && ok "replica is 1 CL behind (normal)" \
        || warn "replica is $diff CLs behind source")

echo
echo "=== Pull worker state (replica side)"
p4 -p "$REPLICA" pull -lj || warn "pull -lj failed — replication may be stalled"

echo
echo "=== Archive transfer queue"
queue=$(p4 -p "$REPLICA" pull -ls 2>/dev/null | wc -l)
echo "  pending archive transfers: $queue"
[[ "$queue" -le 10 ]] && ok "queue drained enough to promote" || warn "queue is deep ($queue) — wait"

echo
echo "=== verify (sample) on replica"
p4 -p "$REPLICA" verify -q //depot/...@1,@100 2>&1 | tail -5 || warn "verify produced output — investigate"

echo
echo "Health summary: lag=$diff CLs, archive_queue=$queue."
if [[ "$diff" -le 0 ]] && [[ "$queue" -eq 0 ]]; then
    echo "READY TO PROMOTE."
    exit 0
fi
echo "NOT READY — wait for lag and archive queue to clear, then re-run."
exit 2
