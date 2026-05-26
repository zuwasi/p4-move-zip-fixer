# `p4-push-fixer` — spec

A thin wrapper around Robert Cowham's `do_push.sh` that adds the auto-exclude-orphan logic we already proved with `auto-skip-zip.py`. Single command. Resumable. No clone, no obliterate, no 100k-DepotMap pressure.

## Why this exists

`p4 push` per-CL solves every problem we had with `p4 zip` **except one**: when a single CL contains a `move/add` or `move/delete` whose counterpart was obliterated, that individual push still fails with *"Change N performs a move/X on //depot/...#rev, but the parameters … include only part of the full action."*

The fix is the same one we landed for `p4 zip`: append a `-//depot/path //remote/path` exclusion line to the remote spec's `DepotMap`, then retry the same `p4 push -s <CL>`. The orphan path is no longer in the view, so `p4 push` skips it cleanly and the rest of the CL goes through.

## Topology assumed

```diagram
╭─────────────────╮         p4 push -s <CL>          ╭──────────────────╮
│ source p4d      │ ─────────────────────────────────▶ │ destination p4d │
│ illin2343:1666  │   (per-CL, resumable, ordered)    │  newhost:1666    │
│ read-only       │                                    │  receives CLs    │
╰─────────────────╯                                    ╰──────────────────╯
        │
        │ remote spec `migration-remote`
        │   DepotMap (catch-all + auto-exclusions)
        ▼
╭─────────────────────────────────────────────╮
│ p4-push-fixer Python wrapper                │
│  loop:                                      │
│   p4 push -r migration-remote -s <CL>       │
│   on failure with orphan-move error:        │
│     parse depot path → add exclusion line → │
│     update remote spec → retry SAME CL      │
│   on success: update LastPush counter,      │
│                advance to next CL           │
╰─────────────────────────────────────────────╯
```

## Inputs

- A working remote spec (`migration-remote`) with `DepotMap` covering `//depot/...` and an `Address:` pointing at the destination `p4d`.
- The destination `p4d` reachable on the network and accepting pushes from the source user.
- An ordered changelist file (produced once, like Robert's `changes.sor`).

## What the wrapper does, per CL

1. `p4 -Ztrack push -v -Oc -r migration-remote //depot/...@=<CHG>`
2. On success:
   - `p4 --field "LastPush=<CHG>" remote -o migration-remote | p4 remote -i`
   - `p4 counter remote_push_counter <CHG>`
3. On failure with `Change <CHG> performs a move/X on //depot/...#rev`:
   - Parse the offending depot path.
   - If already excluded → bail out with diagnostic (means the server didn't honour the previous exclusion).
   - Else: append `-"//depot/..." "//remote/..."` to `migration-remote` DepotMap.
   - **Retry the same CL** from step 1.
4. On any other failure (network, lock, etc.):
   - Retry once with `-I -v -Oc` (Robert's fallback flag — ignore lock).
   - If still failing → log CL to `failed-cls.json` and either stop or skip (configurable).

## Outputs

- The destination p4d, populated CL-by-CL, in submission order.
- `migration-remote` accumulates the same `excluded-paths.json` audit we already produce.
- `failed-cls.json` for non-orphan failures (should be empty in the happy path).
- `LastPush` counter and `remote_push_counter` track progress; killing the script and restarting resumes from the last successfully pushed CL.

## CLI sketch

```bash
p4-push-fixer push \
    --remote migration-remote \
    --depot //depot/... \
    --changes-file /home/perforce/work/changes.sor \
    --counter remote_push_counter \
    --stop-file /home/perforce/work/do_push.stop \
    --max-exclusions 5000
```

## Resilience guarantees

| Failure mode | What the wrapper does |
|---|---|
| Network blip mid-push | Retry once with `-I`, then back-off |
| Source lock conflict | Retry once with `-I` |
| Orphan move (counterpart obliterated) | Add exclusion to spec, retry same CL |
| Destination disk full | Stop cleanly, leave counter intact |
| Operator kills it | Resume from `LastPush` counter on restart |
| Stop-file appears | Stop cleanly before the next CL |

## What we re-use from this repo

- The regex + spec-mutation logic from `auto-skip-zip.py` is lifted verbatim (parse `Change N performs a move/X on //depot/...#rev`, build `-"//depot/..." "//remote/..."`, save remote).
- The `excluded-paths.json` audit format stays identical so operators see the same artifacts.

## What we do NOT need from this repo

- `scan` and `build-spec` are still useful for the initial spec, but the runtime loop no longer needs SQLite — `p4 push` decides per-CL whether the spec is sufficient.

## Implementation effort

Single Python file (~250 lines). Same dependency set as `auto-skip-zip.py` (`p4python`, `click`). 1–2 hours to write + 1 hour to smoke-test against a small repo.

## When this is **not** the right tool

- Destination p4d is not reachable from source → use `p4 zip` path (current Option B).
- Customer wants a portable archive on disk for offline ingestion → `p4 zip` path.
- Customer wants continuous live mirroring (not a one-shot migration) → use Option C filtered replication.
