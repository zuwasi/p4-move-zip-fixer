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
