# `p4-move-zip-fixer` — 1-page brief for Perforce engineering review

**Audience:** Perforce support + engineering
**Status:** Prototype, MIT-licensed, seeking technical review before customer delivery
**Author:** [Your name], [Reseller company] — Perforce reseller for Amdocs
**Related case:** [#######]

---

## Problem (as we understand it)

`p4 zip` aborts when a changelist contains a `move/add` or `move/delete`
whose counterpart sits outside the command's view, with errors of the form
"the parameters of this fetch, push, or zip command include only part of
the full action."

The supported workaround — widening the remote spec to cover both sides of
every move — is correct, but at customer-scale (depot ≈ 705k unique
paths, decades of history) manual enumeration is not tractable.

## Approach

A three-step pipeline driven by the existing `p4` command set; **no server
modification, no protocol changes, no privileged access required.**

```diagram
╭──────────────╮   ╭────────────────────╮   ╭──────────────────╮   ╭──────────╮
│ Customer     │──▶│ scan: p4 filelog   │──▶│ build-spec:      │──▶│ p4 zip   │
│ depot        │   │ -ztag (parallel,   │   │ generate remote  │   │ succeeds │
│              │   │ resumable)         │   │ spec covering    │   │          │
╰──────────────╯   ╰─────────┬──────────╯   │ both sides of    │   ╰──────────╯
                             │              │ every move       │
                             ▼              ╰──────────────────╯
                    ╭────────────────╮
                    │ moves.sqlite   │  ← resumable cache
                    ╰────────────────╯
```

### How discovery works

For each filelog record, we read the `action`, `change`, and `movedFile`
parallel arrays:

- `action[i] == "move/add"`   → emit `(src=movedFile[i], tgt=depotFile, change[i])`
- `action[i] == "move/delete"`→ emit `(src=depotFile, tgt=movedFile[i], change[i])`

Both endpoints land in SQLite under a UNIQUE index, then `build-spec`
emits one view line per distinct path. The remote spec is therefore
*provably* a superset of the paths Perforce's error checker requires.

## Why this is conservative

- **Read-only against the live depot** (`p4 filelog`)
- **No depot mutation** — only writes a remote spec and runs `p4 zip`
- **Idempotent & resumable** — Ctrl-C and re-run; SQLite tracks scanned ranges
- **Fail-fast** — on `p4 zip` failure, the tool parses error messages for
  changelist numbers and prints them, narrowing the next iteration

## Open questions for Perforce engineering

We'd genuinely like guidance on these — see [`C_questions_for_perforce.md`](./C_questions_for_perforce.md):

1. Does `filelog -ztag` reliably surface **both halves** of a move when only
   one half is inside the path filter? (We currently scan `//depot/...` to
   sidestep this.)
2. Are there server flags (`filesys.depot.move`, branch views, etc.) that
   change how `movedFile` is reported on older revisions?
3. How are **chained moves** (A→B→C across changelists) represented — do we
   need a transitive-closure pass on `movedFile`?
4. For **branched-then-moved** files, is there an integration record we
   should follow as well?
5. Is there a more efficient bulk query than `filelog -c <range> //depot/...`
   for very large depots? `p4 changes` + `p4 describe -s` was an alternative
   we considered.

## What we are *not* claiming

- Not a replacement for the enhancement Perforce has scheduled
- Not officially supported software
- Not bundling or redistributing any Perforce binary
- Not tested against every server version — currently exercised only against
  a mocked P4Python interface; needs a Perforce-side validation pass

## Repository contents

```
p4-move-zip-fixer/
├── pyproject.toml              # MIT, p4python optional extra
├── src/p4_move_zip_fixer/
│   ├── scan.py                 # parallel filelog scanner + pure extractor
│   ├── store.py                # SQLite cache w/ scanned-range tracking
│   ├── spec.py                 # remote-spec generator
│   ├── zipper.py               # runs p4 zip, parses failed changelists
│   ├── cli.py                  # `scan` / `build-spec` / `zip`
│   └── p4client.py             # Protocol-typed P4Python wrapper
└── tests/                      # 16 tests, MockP4, no live server needed
```

≈ 400 lines of Python. Tests pass under `pytest`; `ruff` lint clean.

## Asks of Perforce

1. A **20-minute review call** with whoever owns `p4 zip` / DVCS commands.
2. Confirmation (or correction) of the five open questions above.
3. Permission to share the tool with Amdocs, **with or without** Perforce
   endorsement — your call. We will respect either answer.
4. If useful: we can contribute the tool to a Perforce-owned location
   (KB script, `p4-contrib`, partner repo) under whatever terms work for you.

---

*Contact:* [Your name] · [Email] · [Phone] · [Reseller company]
