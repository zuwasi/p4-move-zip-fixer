# Migration Options — Pick Your Path

The `p4-move-zip-fixer` tool on `main` works correctly for depots whose history fits inside Perforce's hard limits and contains no obliterated move counterparts. For large depots (>100k unique paths in a single `DepotMap`) or depots with obliterated history, `p4 zip` is the wrong primitive.

This repository now offers **three independent, self-contained migration approaches** as separate git branches. Each branch contains:

- A step-by-step **runbook** (Markdown + PDF)
- A **detailed pros / cons** analysis
- Any **supporting scripts** needed for that approach
- A **rollback / safety** section

Pick the branch that matches your constraints, follow its runbook, and decide. Branches are independent — you can try one, abandon it, and try another without affecting the others.

---

## Quick comparison

| Aspect | A · Checkpoint | B · Clone + Sanitize | C · Filtered Replica |
|---|---|---|---|
| **Branch** | `option-a-checkpoint` | `option-b-clone-and-sanitize` | `option-c-filtered-replication` |
| **Data-loss risk** | None | None | None |
| **Source server touched?** | Read-only (live checkpoint) | Read-only (checkpoint only) | Read-only (journal stream) |
| **Handles obliterated history?** | Yes — preserves as-is | Yes — sanitize on clone | Yes — preserves as-is |
| **Handles >100k paths?** | Yes — no spec involved | Yes — bypasses 100k cap via slicing | Yes — no spec involved |
| **Tool used** | `p4d -jc`, `p4d -jr`, rsync | `p4d -jc` + this tool on clone | `p4d -p` / `p4 pull` (replica) |
| **Selectivity** | Whole server (then prune) | Per-depot, per-CL-range | Per-depot via filter |
| **Bandwidth** | Largest (entire server) | Medium (clone host, then zip) | Medium (filtered stream) |
| **Operational complexity** | Low conceptually | Medium (clone + sanitize) | Medium (replica config) |
| **Time to first byte on destination** | Hours (transfer + restore) | Hours (clone + sanitize + zip) | Minutes (continuous stream) |
| **Reversibility** | Full — source untouched | Full — clone is disposable | Full — replica is detachable |
| **Perforce-supported** | Yes (canonical) | Partially (zip is supported; sanitize is ours) | Yes (canonical) |
| **Best fit** | Whole-server consolidation | Per-depot consolidation with dirty history | Live cut-over with minimal downtime |

---

## How to evaluate

We recommend trying them in **this order**, stopping at the first one that succeeds end-to-end:

1. **C (filtered replica)** first — least bandwidth, most natural for a "move one depot" objective, fully Perforce-supported
2. **A (checkpoint)** second — bullet-proof, but moves more than you need and requires a prune step
3. **B (clone + sanitize)** last — keeps `p4-move-zip-fixer` in the loop and gives surgical control over orphan changelists, at the cost of more steps

If you have a time pressure and want the highest-confidence path with the fewest moving parts: **C**. If you want to minimise wire bandwidth and you already have a place to land the clone: **B**. If your destination needs to mirror the source exactly: **A**.

> **⚡ Fast path inside Option B (added in v0.1.4).** Branch `option-b-clone-and-sanitize` now includes [`scripts/auto-skip-zip.py`](https://github.com/zuwasi/p4-move-zip-fixer/blob/option-b-clone-and-sanitize/docs/migration-options/option-b-clone-and-sanitize/scripts/auto-skip-zip.py), a **read-only-against-source** loop that produces N chunk zips automatically split around every changelist whose move counterpart was obliterated. **No clone, no obliterate, no DepotMap > 100k risk.** The trade-off: each skipped CL is recorded in `skipped-cls.json` and is not transferred — those CLs have no recoverable content on source anyway (move target is gone). Recommended when an hours-long clone is not affordable. See the *Fast path* section at the top of the [Option B RUNBOOK](https://github.com/zuwasi/p4-move-zip-fixer/blob/option-b-clone-and-sanitize/docs/migration-options/option-b-clone-and-sanitize/RUNBOOK.md).

## How to use the branches

```bash
git fetch --all
git checkout option-a-checkpoint               # or option-b-..., option-c-...
cat docs/migration-options/<option>/RUNBOOK.md
```

Or browse on GitHub:

- https://github.com/zuwasi/p4-move-zip-fixer/tree/option-a-checkpoint
- https://github.com/zuwasi/p4-move-zip-fixer/tree/option-b-clone-and-sanitize
- https://github.com/zuwasi/p4-move-zip-fixer/tree/option-c-filtered-replication

## Common pre-flight (applies to all three)

Regardless of which option you choose, do these first:

1. **Take a storage-level snapshot** of `/p4/2/root` on the source server. Cheap insurance.
2. **Record HEAD changelist:** `p4 counter change` — for parity checks afterwards.
3. **Identify the obliterated changelists**, if any, by running the report on `main`:
   ```bash
   p4-move-zip-fixer scan --depot //depot/... --head <HEAD> --workers 4 --db moves.sqlite
   p4-move-zip-fixer zip  --remote dummy --output /tmp/probe.zip --auto-retry 5
   ```
   The probe's failure output tells you which CLs (if any) have obliterated counterparts. Options A and C handle these automatically; option B's sanitize step targets them explicitly.
4. **Confirm destination disk** has at least 2× source depot size free (zip + unzip both need room).

## What stays on main

`main` will continue to host the `p4-move-zip-fixer` tool itself. The migration-options branches build on top of it; none of them require code changes to the tool except branch B (which adds a small `sanitize-clone` subcommand).
