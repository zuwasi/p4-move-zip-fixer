# p4-move-zip-fixer

Automates the workaround Perforce support proposed for the `p4 zip` failure on
`move/add` / `move/delete` pairs whose source and target are not both inside
the command's view.

Instead of hand-curating hundreds of paths in a remote spec on a depot with
700K+ files, this tool:

1. **Scans** the depot in parallel via `p4 filelog -ztag` and records every
   move pair into a resumable SQLite cache.
2. **Builds** a remote spec containing both sides of every move so the view
   covers the full action.
3. **Runs** `p4 zip` against the generated spec, with structured error capture
   so a failure points at the exact changelist range to rescan.

## Install

```bash
pip install -e ".[p4,dev]"
```

`p4python` is an optional dependency so the unit tests can run against a mock.

## Usage

```bash
# 1. discover all move pairs (resumable; safe to Ctrl-C and re-run)
p4-move-zip-fixer scan --depot //depot/... --db moves.sqlite --workers 8

# 2. emit a remote spec covering both sides of every move
p4-move-zip-fixer build-spec --db moves.sqlite --remote migration-remote

# 3. run the zip and capture structured errors
p4-move-zip-fixer zip --remote migration-remote --output migration.zip
```

### Failure handling (v0.1.3+)

The `zip` subcommand now handles three edge cases that previously caused the
auto-retry loop to fail silently or loop forever:

- **Stale output file** — `p4 zip` writes a partial archive when it errors
  mid-stream and then refuses to overwrite it on the next attempt
  (*"Output zip file ... already exists"*). The tool now clobbers the
  partial file before every attempt. Use `--keep-failed-output` to opt out
  (e.g. for forensic inspection of the partial archive).
- **Spec at the Perforce DepotMap cap (100,000 lines)** — Perforce silently
  drops further entries once a remote spec hits the cap, so additional
  expansion would be a no-op. The tool now detects this and exits with a
  clear `SpecCapReached` error and a runbook of recovery options (split
  zip by changelist range, or add exclusions).
- **Obliterated move counterpart** — when `p4 describe -s <CL>` returns no
  new paths for a failing changelist (typically because the matching
  `move/add` was obliterated), the loop aborts immediately instead of
  retrying the same failure indefinitely, and prints copy-pasteable
  commands to split the zip around the bad CL.

## Why this beats the manual workaround

| Aspect                | Manual spec              | Wait for enhancement | This tool                |
| --------------------- | ------------------------ | -------------------- | ------------------------ |
| Time to deliver       | weeks–months             | unknown              | hours–days               |
| Risk of missing paths | high                     | n/a                  | none (depot is truth)    |
| Repeatable            | no                       | n/a                  | yes (re-run script)      |
| Auditable             | spreadsheet              | n/a                  | SQLite + git-tracked code|

## Development

```bash
pytest          # run unit tests against the mocked P4 server
ruff check src tests
```
