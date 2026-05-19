# Option C — Filtered Replication

**Branch:** `option-c-filtered-replication`
**Risk to data:** None
**Best when:** you want a continuously-syncing destination with minimal cutover downtime and per-depot selectivity
**Time to first byte:** minutes (continuous stream, not batch)

## What this is

Set up the destination server as a **filtered read-only replica** of the source. Perforce replicates metadata in real-time over the journal stream, and the replica pulls archive content on demand (or proactively). When you're ready, **promote** the replica to standalone and decommission the source.

This is Perforce's recommended primitive for live, per-depot, no-downtime migrations.

```diagram
╭───────────╮   ╭──────────────────╮   ╭───────────────╮   ╭──────────────╮
│ Source    │──▶│ p4 pull (journal)│──▶│ Destination   │──▶│ Promote to   │
│ master    │   │ + p4 pull -u     │   │ as filtered   │   │ standalone   │
│ p4d       │   │ (archives)       │   │ replica       │   │ (Server.id)  │
│           │   │                  │   │ ServerType=   │   │              │
│           │   │                  │   │ forwarding-   │   │ Source goes  │
│           │   │                  │   │ replica       │   │ read-only    │
╰───────────╯   ╰──────────────────╯   ╰───────┬───────╯   ╰──────────────╯
                                                ▼
                                  ╭──────────────────────────╮
                                  │ ClientDataFilter         │
                                  │ ArchiveDataFilter        │
                                  │ -T  //depot/...          │
                                  │ Only the depot you want  │
                                  ╰──────────────────────────╯
```

## Pros

- **Zero data loss by construction.** Replication preserves every database table as the source emits it — obliterates, integrations, moves, the lot. The 100k DepotMap cap is irrelevant because no remote spec is involved.
- **Continuous sync = minimal cutover window.** Replica is always within seconds of the source. Cutover = "stop writes to source, wait for replica to catch up, promote." Typically minutes of downtime, not hours.
- **Per-depot selectivity via filters.** `ClientDataFilter`, `RevisionDataFilter`, `ArchiveDataFilter` let you replicate only what matches `//depot/...`. The other depots simply never come across.
- **Perforce-canonical.** This is how Perforce themselves recommend migrating to a new host. Full support, well-documented, used in production at scale.
- **Fully reversible until promotion.** A replica is read-only; you cannot accidentally damage it from clients. Destroy the replica directory and re-set-up at any time.
- **Source server is untouched.** Replication uses normal `p4 pull` from the source — no writes, no admin-level operations.
- **Detectable parity.** `p4 dbstat` and `p4 verify -t` can be run against source and replica continuously to detect divergence.

## Cons

- **Setup is configuration-heavy.** Server specs (`p4 server`), service users (`p4 user`), tickets, `p4d.cfg` settings, `serverid` files, started replica daemons. Each piece must be correct.
- **Server-to-server trust required.** Service user with a long-lived ticket; firewall rules to allow `p4 pull` traffic; potentially TLS configuration.
- **Promotion is one-way.** Once you flip the replica's `Services` field from `forwarding-replica` to `standard` and update `serverid`, going back to "replica of source" requires re-syncing from scratch.
- **All-or-nothing per top-level path.** Filters apply to map specs; you cannot replicate "this depot but skip files matching this pattern" cleanly without juggling multiple filters.
- **No control over historical edge cases.** Replica mirrors source state exactly — including any obliterated history (which is fine) and any data state that bothers `p4 zip` (which doesn't matter here because we don't use `p4 zip`).
- **Initial seeding can be large.** First replica startup pulls all selected metadata + archives; for a 12-year, 700k-path depot this can be hours-to-days depending on bandwidth.
- **Operationally ongoing until promotion.** You're running an additional service (the replica daemon) and have to monitor it.

## Detailed step-by-step

### Pre-flight

- [ ] **Network**: replica host can reach source on `P4PORT`; source firewall allows it
- [ ] **Versions**: replica `p4d` ≥ source `p4d` (newer-or-equal — never older)
- [ ] **Same case-handling** on both
- [ ] **Disk** on replica: enough for filtered depot size + metadata + journal queue
- [ ] **Decision** on `ServerType`: usually `forwarding-replica` (clients can write through it) or `replica` (read-only)
- [ ] **Service user account** name agreed on (e.g. `svc_replica_to_dest`)

### Step 1 — Create the service user on source

```bash
# on source — as super user
p4 user -o -f svc_replica_to_dest | sed \
    -e 's/^Type:.*/Type:     service/' \
    -e 's/^FullName:.*/FullName: Replica service for destination/' | p4 user -i -f

# grant a long ticket
p4 -u svc_replica_to_dest login -a < /tmp/passwd_for_service_user
```

Grant service-level read access via `p4 protect`:
```
super  user  svc_replica_to_dest    *   //...
super  user  svc_replica_to_dest    *   //spec/...
```

### Step 2 — Define the replica server spec on source

```bash
p4 -p source server -o replica-dest | tee /tmp/replica-dest.spec
# Edit to set:
#   ServerID:       replica-dest
#   Type:           server
#   Services:       forwarding-replica   (or 'replica' for read-only)
#   Name:           replica-dest
#   Address:        destination:1666
#   ReplicatingFrom: source:1666
#   ClientDataFilter:    //depot/...
#   RevisionDataFilter:  //depot/...
#   ArchiveDataFilter:   //depot/...
#   Options:        nomandatory
p4 -p source server -i < /tmp/replica-dest.spec
```

The three filters restrict replication to `//depot/...` only — other depots will not be pulled.

### Step 3 — Take a seed checkpoint from source

The replica cannot start without a seed; replication only forwards *new* journal events.

```bash
# on source
p4d -r /p4/2/root -jc -Z
# transfer checkpoint + filtered depot files to replica host
rsync -avh /p4/2/checkpoints/checkpoint.NNN.gz   replica:/p4/r/incoming/
rsync -avh /p4/2/depots/depot/                   replica:/p4/r/depots/depot/
# (note: only the //depot/ subdir, not the whole /p4/2/depots/, because the
#  filter restricts replication to //depot/...)
```

### Step 4 — Restore the seed on the replica

```bash
# on replica host as perforce user
echo "replica-dest" > /p4/r/root/server.id
p4d -r /p4/r/root -jr /p4/r/incoming/checkpoint.NNN.gz
```

Configure the replica's startup with the source as its master:
```ini
# /p4/r/etc/p4d.cfg or systemd EnvironmentFile
P4PORT=destination:1666
P4ROOT=/p4/r/root
P4SERVICE=svc_replica_to_dest
P4TICKETS=/p4/r/.p4tickets
```

Set `db.config` values:
```bash
p4 -p source configure set replica-dest#monitor=1
p4 -p source configure set replica-dest#auth.id=source
p4 -p source configure set replica-dest#serviceUser=svc_replica_to_dest
p4 -p source configure set replica-dest#startup.1="pull -i 1"        # metadata
p4 -p source configure set replica-dest#startup.2="pull -u -i 1"     # archives
p4 -p source configure set replica-dest#startup.3="pull -u -i 1"     # second archive puller
```

### Step 5 — Start the replica

```bash
sudo systemctl start p4d-replica-dest
journalctl -u p4d-replica-dest -f
```

You should see `p4 pull` workers connect to the source and begin streaming.

### Step 6 — Monitor replication health

```bash
# on replica
p4 -p destination:1666 pull -lj         # journal status
p4 -p destination:1666 pull -ls         # transfer queue (archives)
p4 -p destination:1666 monitor show

# on source (lag detection)
p4 -p source:1666 servers -J            # who is replicating, journal positions
```

Wait until **journal lag is < 5 seconds** and **archive queue is empty** before proceeding.

### Step 7 — Pre-cutover validation (replica is read-only)

```bash
SRC=source:1666 DST=destination:1666 SAMPLE=50 \
    ../option-a-checkpoint/scripts/parity-report.sh
```

(Re-uses Option A's parity report — same checks apply.)

### Step 8 — Cutover

```bash
# 1. announce read-only window on source
p4 -p source configure set monitor=1
p4 -p source protect    # revoke write for everyone
# 2. wait for replica to catch up to current journal head
p4 -p source counter change
p4 -p destination counter change
# 3. when equal, promote the replica
echo "destination-master" > /p4/r/root/server.id
p4 -p destination server -o destination-master | sed 's/^Services:.*/Services: standard/' | p4 -p destination server -i
sudo systemctl restart p4d-replica-dest
# 4. clients re-point to destination
```

### Step 9 — Post-cutover

```bash
# verify clients can write
p4 -p destination changes -m1
# verify integrity end-to-end
p4 -p destination verify -q //depot/...
# leave source read-only for a grace period before decommission
```

## Rollback

| Failure at step | Response |
|---|---|
| 1-2 (config on source) | Destructive only to specs; reset/re-edit |
| 3 (seed checkpoint) | Source untouched; re-run |
| 4 (restore) | Wipe `/p4/r/root/db.*`, re-restore |
| 5 (replica start) | Logs in `/p4/r/logs/log` show error; fix config, restart |
| 6 (replication lag) | Increase puller count, fix network, restart pullers |
| 7 (parity fails) | Investigate divergence; destroy replica, re-seed |
| 8 (cutover) | Before promotion: just back out — restart writes on source. After promotion: source is still intact read-only — point clients back to source and treat the destination as a write-off |

## Helper scripts (shipped on this branch)

- [`scripts/check-replica-health.sh`](scripts/check-replica-health.sh) — single-glance replication status
- [`scripts/promote-replica.sh`](scripts/promote-replica.sh) — performs Step 8 with safety prompts

## When to abandon this option

- If you cannot grant a service-user account on the source → use Option A or B
- If the network between source and replica is unreliable or slow → use Option A (offline ship)
- If you want a one-shot migration with no ongoing replica daemon → use Option A
- If your destination needs to keep its existing data (replica destination must be a fresh server) → use Option B
