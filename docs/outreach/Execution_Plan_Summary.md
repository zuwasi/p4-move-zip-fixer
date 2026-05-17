# Default Depot Migration — Execution Plan Summary

**Case 01565143 · Tool: `p4-move-zip-fixer` v0.1.0 · Target: today (one working day)**

## The Decision

We do **not** wait for Perforce. The manual workaround does not scale to 705k paths and the official `p4 zip` enhancement has no committed date. We execute today with our own tool, validate independently, and reuse the same playbook for the remaining two depots.

## What & Where

| | Source | Destination |
|---|---|---|
| Server | `illin2343` (P4D 2025.1/2831954) | `p4jirastg22` (P4D 2025.1/2831954) |
| Root | `/p4/2/root` | `/p4/1/root` |
| Depot | `/p4/2/depots/depot/...` (since 2013-12-21) | new |
| TZ | −0500 CDT | +0300 IDT |

## Run-book (10 steps)

1. Storage-level snapshot of `/p4/2/root`
2. Capture HEAD: `p4 -p illin2343:1666 counter change`
3. Install: `pip install -e "p4-move-zip-fixer[p4]"`
4. Authenticate: `P4PORT=illin2343:1666 p4 login`
5. **Scan:** `p4-move-zip-fixer scan --depot //depot/... --head $HEAD --workers 4 --db moves.sqlite`
6. **Build spec:** `p4-move-zip-fixer build-spec --db moves.sqlite --remote migration-remote`
7. Gate 1 — inspect spec (`p4 remote -o migration-remote`, sample 100 pairs)
8. **Zip:** `p4-move-zip-fixer zip --remote migration-remote --output default-depot.zip` *(loop on failed changelists)*
9. Transfer & unzip on destination
10. Gate 2 + Gate 3 — `p4 verify -q`, changelist parity, 50-file SHA256 sample diff

## Timeline (one day)

`T+00:00` snapshot → `T+00:30` scan → `T+03:30` build spec → `T+03:35` Gate 1 → `T+04:00` zip → `T+06:00` transfer + unzip → `T+08:00` Gates 2 & 3 → **GO/NO-GO**

## Validation gates (no vendor sign-off needed)

- **Gate 1 — pre-zip:** spec coverage proof, random 100-pair inspection, view-line count vs. `p4 files` count
- **Gate 2 — post-unzip:** `p4 verify -q //depot/...` zero BAD/MISSING + changelist + revision parity
- **Gate 3 — end-to-end:** 50-file random `p4 print` SHA256 diff between source and destination

## Top risks → mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Move chains (A→B→C) | Medium | Transitive-closure pass over `moves.sqlite`; re-zip if needed |
| Branch-of-move / integrate-then-move | Medium | Iterative loop on failed changelists (converges in 2–3 retries) |
| Lazy copies | Low | Fallback to broad spec `//depot/...` if iterative loop doesn't converge |
| Server load from parallel scan | Medium | `--workers 4` default, off-peak window, throttle on `p4 monitor` spikes |
| **Data loss in zip→unzip cycle** | **High** | **Source stays read-only — not decommissioned — until all 3 depots pass Gate 3** |
| No Perforce sign-off | Medium | Archive spec + full logs; output is identical to a hand-built spec run |

## Rollback guarantees

- **Source server is read-only throughout** — every operation is reversible until Gate 3
- **Destination depot is disposable** — failure → drop the depot directory and restart
- **Decommission is a separate decision** taken only after all three depots pass Gate 3

## Posture toward Perforce

Respectful, transparent, independent. We continue case 01565143, share the resulting spec + logs after the run, track the official enhancement, and switch to it for depot #3 if it ships in time.

## GO / NO-GO checklist

- [x] Tool built, tested 16/16, MIT-licensed
- [x] Source & destination versions match
- [x] Validation gates defined
- [x] Rollback plan & reversibility guarantees in place
- [x] Risks identified with concrete mitigations
- [ ] Storage snapshot taken
- [ ] Off-peak window confirmed with Amdocs ops
- [ ] Sign-off from Amdocs migration lead

**Once the bottom three boxes are checked, we run.**
