# Option A — Full Server Checkpoint Migration

**Branch:** `option-a-checkpoint`
**Risk to data:** None
**Best when:** you can accept transferring the whole server and pruning unwanted depots afterwards
**Time to first byte:** hours (checkpoint + transfer + restore)

## What this is

The textbook Perforce server migration. Take a complete metadata snapshot (`p4d -jc`) and the depot archive files (`rsync`) from the source, restore on the destination, then delete the depots you didn't want. The destination ends up byte-identical to the source.

```diagram
╭───────────╮   ╭────────────╮   ╭─────────────╮   ╭───────────────╮
│ Source    │──▶│ p4d -jc -Z │──▶│ rsync ckp + │──▶│ Destination   │
│ p4d       │   │ checkpoint │   │ depot files │   │ p4d -r -jr    │
│ (live OK) │   │ + journal  │   │             │   │ restore       │
╰───────────╯   ╰────────────╯   ╰─────────────╯   ╰───────┬───────╯
                                                            ▼
                                                  ╭───────────────────╮
                                                  │ p4 obliterate -y  │
                                                  │ depots you don't  │
                                                  │ want at dest      │
                                                  ╰───────────────────╯
```

## Pros

- **Bullet-proof correctness.** Every database table, every revision, every move history, every obliterate record comes across exactly as-is. Perforce engineers use this method to migrate their own production servers.
- **No Perforce-side limits.** No 100k DepotMap cap. No remote-spec hand-rolling. No "partial action" errors.
- **Obliterated history preserved correctly.** Because we're moving raw `db.*` tables, the destination has the same notion of "obliterated" as the source — no skipped changelists, no gaps.
- **Source server completely safe.** `p4d -jc` works against a live server; clients can still commit during the checkpoint. Worst case the journal copy captures committed work; checkpoint + journal replay gives a consistent state.
- **Single-command restore.** `p4d -r /destroot -jr checkpoint.ckp.gz` is one command, idempotent, and tells you immediately if anything is wrong.
- **Resumable.** Checkpoint failure mid-stream → re-run. Rsync interrupted → re-run. Restore failure → delete `/destroot/db.*` and re-run.

## Cons

- **Brings everything across the wire.** If the source has 5 TB and you only want one 800 GB depot, you still ship 5 TB. Mitigated by post-restore `obliterate -y`, but the bandwidth and disk are spent.
- **Requires destination to be a fresh Perforce server** (or willing to be overwritten). You cannot restore a checkpoint into an existing server with other depots — you must restore standalone, then either consolidate later (via Option C or another method) or designate this server as the final home.
- **Server-level user/group/protect tables come across too.** If the destination has its own user accounts, you'll need to reconcile after restore. Acceptable when destination is fresh.
- **One-shot.** Unlike replication, this doesn't keep the destination in sync after the checkpoint — any commits to source after the checkpoint window are lost unless you also ship a follow-up journal.
- **Disk-heavy.** Need ≈ 1× source size for the checkpoint file + ≈ 1× source size for the unpacked destination root.
- **Requires `p4d` admin shell access on both ends.** Not just `p4` client — actual server-process operations.

## Detailed step-by-step

### Pre-flight

- [ ] **Disk space check** on destination: at least 2× source depot size free
- [ ] **Same major `p4d` version** on both ends (or destination ≥ source — never older)
- [ ] **Same case-sensitivity** setting (`p4 info` → "Server case-handling"). If different, you cannot restore — full stop.
- [ ] **SSH / file-transfer path** between source and destination identified (rsync over SSH preferred for resumability)
- [ ] **Network bandwidth** estimated. For 800 GB of depot files at 100 Mbit/s, plan ~24 hours.
- [ ] **Maintenance window** scheduled if you want a "no commits during" guarantee (otherwise live checkpoint is fine but be aware of journal tail)

### Step 1 — Live checkpoint on source

```bash
# As perforce user on source server
cd /p4/2/root
p4d -r /p4/2/root -jc -Z   # -Z = gzip the checkpoint
# Produces: checkpoint.NNN.gz + journal.NNN-1.gz  (NNN = checkpoint number)
```

The `-jc` operation rotates the journal — the active journal becomes `journal.NNN-1` and a new empty journal starts. Atomic against the live server.

**Verify integrity:**
```bash
p4d -r /tmp/verify-test -jr checkpoint.NNN.gz
# Should complete without errors. Delete /tmp/verify-test after.
```

### Step 2 — Identify what to transfer

```bash
ls -la /p4/2/root/db.* /p4/2/checkpoints/checkpoint.NNN.gz
du -sh /p4/2/depots/   # depot archive files (the actual file contents)
```

You will ship:
- The checkpoint file: `/p4/2/checkpoints/checkpoint.NNN.gz`
- The depot archive directory: `/p4/2/depots/`
- Server config: `/p4/2/root/server.id` (optional, recommended)

You will **not** ship the `db.*` files directly — the destination rebuilds them from the checkpoint.

### Step 3 — Transfer to destination

```bash
# Recommended: rsync with --partial for interruption recovery
rsync -avh --progress --partial --partial-dir=.rsync-partial \
  /p4/2/checkpoints/checkpoint.NNN.gz \
  perforce@destination:/p4/1/incoming/

rsync -avh --progress --partial --partial-dir=.rsync-partial \
  /p4/2/depots/ \
  perforce@destination:/p4/1/depots/
```

For large depot directories, consider:
- **Splitting by top-level depot:** rsync each `/p4/2/depots/<name>/` separately for finer-grained restart
- **Bandwidth limiting:** `--bwlimit=50M` to keep production network responsive
- **Compression:** `-z` if CPU is plentiful and link is slow

### Step 4 — Restore on destination

```bash
# As perforce user on destination
# IMPORTANT: destination /p4/1/root MUST be empty of db.* files
cd /p4/1/root
ls db.* 2>/dev/null && echo "REFUSING — root not empty" && exit 1

p4d -r /p4/1/root -jr /p4/1/incoming/checkpoint.NNN.gz
# Watch for errors. Successful output ends with: "Recovery complete."
```

If you also need to apply post-checkpoint journal entries (to capture commits that happened during transfer):
```bash
p4d -r /p4/1/root -jr /p4/1/incoming/journal.NNN-1.gz
```

### Step 5 — Bring the destination server up

```bash
# Edit /p4/1/etc/p4d.conf or your systemd unit to point at /p4/1/root
sudo systemctl start p4d
p4 -p destination:1666 info   # confirm running
```

### Step 6 — Validation

```bash
# Counter parity
p4 -p source:1666      counter change > /tmp/src.change
p4 -p destination:1666 counter change > /tmp/dst.change
diff /tmp/src.change /tmp/dst.change

# Depot listing
p4 -p source:1666      depots > /tmp/src.depots
p4 -p destination:1666 depots > /tmp/dst.depots
diff /tmp/src.depots /tmp/dst.depots

# Server-side verify against archive files
p4 -p destination:1666 verify -q //...   # zero BAD, zero MISSING
```

### Step 7 — Prune depots you didn't want

If the migration's goal was "only the Default depot", obliterate the others:
```bash
p4 -p destination:1666 obliterate -y //CRM_depot/...
p4 -p destination:1666 obliterate -y //other_depot/...
# repeat per unwanted depot
```

This is destructive **on the destination only**. Source is untouched.

### Step 8 — Decommission window

Source server remains read-only (set `db.config` `monitor=1` and revoke write permissions via `p4 protect`) until:
- All 3 depots' migrations have passed validation
- A second independent diff has been run (sample-file SHA256)
- A grace period has elapsed (recommend ≥ 1 week)

Only then schedule decommission.

## Rollback

At any point before Step 8:
- Destination is fresh → nuke `/p4/1/root` and re-run from Step 4 (or earlier)
- Source is untouched throughout — there is nothing to roll back on source

After Step 8, rollback means re-enabling write on source and pointing clients back. Plan this in advance.

## Time / bandwidth estimates (rough)

| Source size | Checkpoint time | Transfer @ 1 Gbps | Restore time | Total |
|---|---|---|---|---|
| 100 GB | ~5 min | ~15 min | ~15 min | ~35 min |
| 1 TB | ~30 min | ~2.5 hr | ~2 hr | ~5 hr |
| 5 TB | ~2 hr | ~12 hr | ~8 hr | ~22 hr |

Add 20-50% for safety. CPU during restore is the long pole on large depots.

## Helper scripts

This branch ships:

- [`scripts/preflight-checks.sh`](scripts/preflight-checks.sh) — runs Steps from "Pre-flight" against a live source/dest pair
- [`scripts/parity-report.sh`](scripts/parity-report.sh) — Step 6 validations in one command

## When to abandon this option

If destination already has live data you cannot lose → use Option B or C.
If you want a per-depot transfer without bringing everything → use Option B or C.
If you want continuous sync rather than one-shot → use Option C.
