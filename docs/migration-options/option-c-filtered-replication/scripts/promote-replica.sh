#!/usr/bin/env bash
# Option C — promote a replica to standalone master.
# DESTRUCTIVE: changes the replica's ServerID and Services to standalone.
# Refuses to run without explicit confirmation. Also runs the health check
# first and refuses if the replica is not caught up.
#
#   SRC=source:1666 REPLICA=destination:1666 \
#     NEW_SERVERID=destination-master \
#     ./promote-replica.sh --i-understand-this-is-one-way
set -euo pipefail

: "${SRC:?set SRC=host:port (source/master)}"
: "${REPLICA:?set REPLICA=host:port (replica)}"
: "${NEW_SERVERID:?set NEW_SERVERID=new-server-id}"
CONFIRM="${1:-}"

if [[ "$CONFIRM" != "--i-understand-this-is-one-way" ]]; then
    cat <<EOF
REFUSING: promotion is one-way.

To promote, re-invoke with:
  $0 --i-understand-this-is-one-way

This will:
  1. Verify the replica is fully caught up (calls check-replica-health.sh)
  2. Set source to read-only (revoke writes via p4 protect)
  3. Update replica's serverid file to '$NEW_SERVERID'
  4. Edit the replica's server spec: Services = standard
  5. Restart the replica's p4d process

After this you cannot rejoin the replica to the master without re-seeding.
EOF
    exit 2
fi

here="$(cd "$(dirname "$0")" && pwd)"

echo "STEP 1 — health check"
if ! "$here/check-replica-health.sh"; then
    echo "Health check failed. Refusing to promote."
    exit 1
fi

echo
echo "STEP 2 — confirm with user"
read -r -p "Promote $REPLICA (new serverid='$NEW_SERVERID') and revoke writes on $SRC? [type: PROMOTE] " ans
[[ "$ans" == "PROMOTE" ]] || { echo "Aborted."; exit 1; }

echo
echo "STEP 3 — revoke writes on source"
echo "  (manual step — edit 'p4 protect' to remove write/super for all users)"
p4 -p "$SRC" protect
echo "  source set to read-only. Continue? [type: CONTINUE] "
read -r ans
[[ "$ans" == "CONTINUE" ]] || { echo "Aborted."; exit 1; }

echo
echo "STEP 4 — wait for replica to drain residual journal"
sleep 3
"$here/check-replica-health.sh" || true

echo
echo "STEP 5 — flip replica serverid + Services"
old_id=$(p4 -p "$REPLICA" info | awk -F': ' '/ServerID/ {print $2}')
echo "  current serverid on replica: '$old_id'"
echo "  setting new serverid on disk → '$NEW_SERVERID'"
ssh "$(echo "$REPLICA" | cut -d: -f1)" "echo '$NEW_SERVERID' | sudo -u perforce tee /p4/r/root/server.id"

echo "  editing server spec: Services → standard"
p4 -p "$REPLICA" server -o "$NEW_SERVERID" \
  | sed 's/^Services:.*/Services: standard/' \
  | p4 -p "$REPLICA" server -i

echo
echo "STEP 6 — restart replica p4d"
ssh "$(echo "$REPLICA" | cut -d: -f1)" "sudo systemctl restart p4d-replica-dest"
sleep 5
p4 -p "$REPLICA" info

echo
echo "PROMOTED. Re-point clients to $REPLICA."
echo "Source $SRC remains read-only — decommission only after grace period."
