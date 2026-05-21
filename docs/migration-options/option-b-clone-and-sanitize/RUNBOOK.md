# Option B — Clone, Sanitize, Then Zip

**Branch:** `option-b-clone-and-sanitize`
**Risk to data:** None (all destructive work happens on a disposable clone)
**Best when:** you want to keep `p4-move-zip-fixer` in the loop, your depot has obliterated history, and per-depot transfer is preferable to whole-server
**Time to first byte:** hours (clone + sanitize + zip)

## ⚡ Fast path — no clone, no obliterate (new in v0.1.4)

If you cannot spend hours building a clone, use `scripts/auto-skip-zip.py`.
It runs `p4-move-zip-fixer zip` in a loop against the **source** (read-only —
no writes are ever sent to the source server) and **automatically splits the
output into multiple chunk-NNN.zip files around every changelist that is
unrecoverable by spec widening** (the "Expand added 0 paths" case — typically
an orphan `move/add` / `move/delete` whose counterpart was obliterated long
ago).

```bash
# on the source host (e.g. illin2343), read-only against p4d
python scripts/auto-skip-zip.py \
    --p4port illin2343:1666 \
    --remote migration-remote \
    --depot //depot/... \
    --out-dir /p4data/export/chunks \
    --head 817597
```

Outputs:
```
/p4data/export/chunks/chunk-0001.zip          # CLs 1..781421
/p4data/export/chunks/chunk-0002.zip          # CLs 781423..<next_bad - 1>
/p4data/export/chunks/chunk-0003.zip          # ...
/p4data/export/chunks/manifest.json           # chunks + slices view
/p4data/export/chunks/skipped-cls.json        # full audit of skipped CLs
```

Replay on destination, in CL order (uses the same `sliced-unzip.py`):
```bash
python scripts/sliced-unzip.py \
    --p4port destination:1666 \
    --in-dir /import/chunks/
```

**What the fast path costs you:** every changelist listed in
`skipped-cls.json` is not transferred. Each one has the form *"move whose
counterpart is already obliterated"*, so the destination would have nothing
to materialise from them anyway — the move target is gone on the source. You
keep the audit so the customer can later restore individual paths from a
backup if any are still important.

**When NOT to use the fast path:**
* If policy requires every CL to be present in destination history (use the
  full clone+obliterate flow below instead, which preserves CL continuity).
* If you have *hundreds* of orphans — at that scale the cumulative skipped
  history may be significant; clone + sanitize is cleaner.

The full **clone + sanitize** flow remains below as the zero-history-loss
option.

## What this is

Spin up a **disposable clone** of the source server from a checkpoint, surgically remove the small number of orphan-move records that break `p4 zip`, then run `p4-move-zip-fixer` against the clone. The destination receives a clean zip with no holes.

```diagram
╭───────────╮   ╭───────────────╮   ╭────────────────╮   ╭─────────────╮   ╭──────────────╮
│ Source    │──▶│ Clone host    │──▶│ Sanitize:      │──▶│ Run tool on │──▶│ Destination  │
│ p4d       │   │ p4d -r -jr    │   │ obliterate the │   │ clone:      │   │ p4 unzip     │
│ untouched │   │ from checkpt  │   │ orphan move    │   │ scan/build/ │   │              │
│           │   │ + rsync       │   │ halves only    │   │ zip         │   │              │
╰───────────╯   ╰───────────────╯   ╰────────────────╯   ╰─────────────╯   ╰──────────────╯
```

## Pros

- **Source server is read-only throughout.** Every destructive operation happens on the clone. Nothing on production source can be damaged.
- **Per-depot precision.** Run the migration for just `//depot/...` if that's all you want — no need to obliterate the rest.
- **Reuses `p4-move-zip-fixer`.** The work you've already done (the tool, the `moves.sqlite`, the operational familiarity) is not wasted.
- **Solves the 100k DepotMap cap by slicing.** The runbook explicitly splits the zip into ≤100k-line chunks so we never trip the server-side limit.
- **Solves obliterated counterparts by either:** obliterating the matching half on the clone (clean fix), *or* splitting the zip around the bad CL (skip fix). Both are documented below.
- **Disposable clone = unlimited do-overs.** Mistake during sanitize? Destroy the clone, restore from checkpoint, try again. The "you-only-get-one-shot" pressure that comes with destructive ops on production simply isn't here.
- **Audit trail.** Every obliterate is logged. Every sliced zip is named. Reviewing what happened post-migration is trivial.

## Cons

- **Needs a clone host.** A VM with disk for source size + free space for the checkpoint extraction.
- **More moving parts than Options A or C.** Checkpoint → transfer → restore → sanitize → scan → build-spec → zip → ship → unzip. Each step needs its own validation.
- **Sanitize is a judgement call.** "Obliterate the orphan half" preserves continuity but removes a (broken) record from history. "Split around the bad CL" preserves the record but loses everything from that CL. The runbook recommends but doesn't decide for you.
- **Slicing complicates `p4 unzip` on the destination.** Multiple zips must be unzipped in CL order, which is mechanical but easy to get wrong if scripted poorly. The runbook ships a helper.
- **Time-bound clone freshness.** Anything committed to source after the checkpoint is not in the clone. Either accept a small data loss window, or follow up with a journal-tail ship and replay (extra step).
- **Still depends on `p4 zip` ultimately.** If Perforce adds new restrictions in a future release, this path may need re-validation.

## Detailed step-by-step

### Pre-flight

- [ ] **Clone host provisioned** with disk ≥ 2× source size, same OS/arch family as source
- [ ] **Same `p4d` version** as source (or newer) installed on clone host
- [ ] **Network path** source → clone (rsync) and clone → destination (scp / rsync) tested
- [ ] **`p4-move-zip-fixer` v0.1.3+** installed on the clone (`pip install -e ".[p4]"`)
- [ ] **Backup snapshot** of source server filesystem (defence in depth)

### Step 1 — Checkpoint source (live, non-disruptive)

```bash
# on source
cd /p4/2/root
p4d -r /p4/2/root -jc -Z
# yields: checkpoint.NNN.gz + journal.NNN-1.gz
```

### Step 2 — Provision the clone

```bash
# on clone host (call it cloneN)
sudo -u perforce mkdir -p /clone/root /clone/depots /clone/incoming
sudo chown -R perforce: /clone

# transfer checkpoint + depot files
rsync -avh --progress source:/p4/2/checkpoints/checkpoint.NNN.gz /clone/incoming/
rsync -avh --progress source:/p4/2/depots/                       /clone/depots/

# restore on the clone
sudo -u perforce p4d -r /clone/root -jr /clone/incoming/checkpoint.NNN.gz
# expect: "Recovery complete."
```

Bring the clone server up on a non-conflicting port:
```bash
sudo -u perforce p4d -r /clone/root -p clonehost:1700 -d   # daemonise
p4 -p clonehost:1700 info   # confirm
```

### Step 3 — Discover orphan moves on the clone

```bash
# run the helper script shipped on this branch
python scripts/find-orphan-moves.py \
    --p4port clonehost:1700 \
    --depot //depot/... \
    --output /clone/orphan-moves.json
```

Output looks like:
```json
{
  "scanned_files": 705610,
  "orphan_pairs": [
    {"change": 781422, "depotFile": "//depot/.../AmdocsCRM-BM-Collection__V8_1_2_5_1.jar#2",
     "action": "move/delete", "movedFile": "//depot/.../target.jar",
     "reason": "movedFile not present in depot — counterpart obliterated"}
  ]
}
```

Review this list. It is the **only** set of changes the sanitize step will touch.

### Step 4 — Sanitize (only on the clone)

Two strategies — pick one per orphan based on policy:

**4a · OBLITERATE the orphan half** — preserves CL continuity, removes the broken record:
```bash
python scripts/sanitize-clone.py \
    --p4port clonehost:1700 \
    --orphans /clone/orphan-moves.json \
    --strategy obliterate \
    --dry-run            # always run dry first
# review output, then:
python scripts/sanitize-clone.py ... --execute --i-really-mean-it
```

**4b · SKIP the changelist entirely** — preserves the broken record, but the zip will exclude the whole CL:
```bash
python scripts/sanitize-clone.py \
    --p4port clonehost:1700 \
    --orphans /clone/orphan-moves.json \
    --strategy skip \
    --emit-skip-list /clone/skip-cls.txt
# produces a CL list to feed --skip-changes to the zip command
```

**Recommendation:** **4a (obliterate)** for clean migration; **4b (skip)** if Amdocs policy forbids any obliterate on a clone.

### Step 5 — Run `p4-move-zip-fixer` on the clone (with 100k-slicing)

The DepotMap 100k cap means we slice the zip into changelist ranges sized to fit. Use the helper:

```bash
python scripts/sliced-zip.py \
    --p4port clonehost:1700 \
    --remote migration-remote \
    --depot //depot/... \
    --out-dir /clone/migration-slices \
    --max-paths-per-slice 95000        # leave 5k headroom below the 100k cap
```

This produces:
```
/clone/migration-slices/slice-001.zip    (CLs 1..50000)
/clone/migration-slices/slice-002.zip    (CLs 50001..120000)
...
/clone/migration-slices/manifest.json    (slice → CL-range mapping)
```

### Step 6 — Ship slices to destination

```bash
rsync -avh /clone/migration-slices/  destination:/p4/1/incoming/migration/
```

### Step 7 — Unzip on destination in CL order

```bash
# on destination
python /path/to/sliced-unzip.py \
    --p4port destination:1666 \
    --in-dir /p4/1/incoming/migration/
# the script reads manifest.json and runs p4 unzip in order
```

### Step 8 — Validate destination vs clone (not vs source)

The clone is your reference truth, because sanitize may have differed from source:
```bash
SRC=clonehost:1700 DST=destination:1666 SAMPLE=50 \
  ../option-a-checkpoint/scripts/parity-report.sh
```

(Re-uses the parity-report from Option A — same checks apply.)

### Step 9 — Decommission the clone, set source to read-only

```bash
# clone is no longer needed
sudo -u perforce p4d -r /clone/root -p clonehost:1700 -j stop
sudo rm -rf /clone

# source goes read-only (not destroyed yet)
p4 -p source:1666 protect    # edit to revoke write
```

Source decommission is a separate decision taken later.

## Rollback

| Failure at step | Response |
|---|---|
| 1 (checkpoint) | Re-run; source untouched |
| 2 (restore on clone) | `rm -rf /clone/root/db.*` and re-run jr |
| 3 (discovery) | Idempotent — re-run anytime |
| 4 (sanitize) | If `--dry-run` was used: review and adjust. If `--execute`: destroy the clone (Step 2), re-restore, re-sanitize differently |
| 5 (zip) | Re-run individual slices; manifest tracks state |
| 6 (transfer) | rsync --partial resumes |
| 7 (unzip) | `p4 obliterate` on destination, re-unzip the affected slices |
| 8 (validation fails) | Investigate; destination is still disposable until promoted |

## Helper scripts (shipped on this branch)

- [`scripts/auto-skip-zip.py`](scripts/auto-skip-zip.py) — **fast path.** Read-only against source. Produces N chunk zips + a JSON audit, automatically stepping over any changelist whose orphan-move counterpart was obliterated. No clone, no obliterate, no DepotMap > 100k risk.
- [`scripts/find-orphan-moves.py`](scripts/find-orphan-moves.py) — discovers every move action whose counterpart is missing
- [`scripts/sanitize-clone.py`](scripts/sanitize-clone.py) — `--dry-run` by default; obliterates orphan halves with `--execute --i-really-mean-it`
- [`scripts/sliced-zip.py`](scripts/sliced-zip.py) — bisects the depot history into ≤95k-path slices and runs `p4-move-zip-fixer zip` on each
- [`scripts/sliced-unzip.py`](scripts/sliced-unzip.py) — replays the slices (or chunks from `auto-skip-zip.py`) on the destination in CL order

## When to abandon this option

- If you don't have a clone host → use Option A (whole-server) or C (replica)
- If the orphan count is in the hundreds → consider Option A; sanitize at that scale is fragile
- If you need continuous sync → use Option C (replica) instead
