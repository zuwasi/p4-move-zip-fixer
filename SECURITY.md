# Security Policy

## Scope

`p4-move-zip-fixer` is a read-mostly automation tool that:

- **Reads** Perforce depot metadata via `p4 filelog -ztag` (read-only).
- **Writes** a single Perforce *remote spec* via `p4 save_remote` (no depot
  contents are modified).
- **Invokes** `p4 zip` against that remote spec to produce a single output
  archive on local disk.

It does **not** modify file contents in the depot, does **not** mutate
existing changelists, and does **not** require admin privileges beyond what
`p4 zip` itself requires.

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| `0.1.x` | ✅ active           |

## Reporting a vulnerability

Please report suspected security issues **privately** to the maintainer:

- Email: `[your-email-here]`
- Expected response: within 5 business days
- Coordinated disclosure: please withhold public disclosure until a fix is
  released or 90 days have passed, whichever is sooner.

Do **not** open a public GitHub issue for security reports.

## Scope of concern

Things we treat as security-relevant:

- Path traversal or injection via crafted depot paths feeding into the
  generated remote spec.
- SQL injection into the local SQLite cache (we use parameterised queries
  throughout — please report any deviation).
- Credential leakage via logs, error messages, or the SQLite cache file.
- Denial-of-service against a Perforce server through unbounded parallel
  `filelog` calls (the `--workers` flag exists to cap this).

Things we treat as **out of scope** (please don't report):

- Behaviour when run against a Perforce server you do not own or are not
  authorised to query.
- Misconfiguration of `p4 protect` on the target server.
- Issues in `p4python` or the Perforce server itself — please report those
  to Perforce directly.

## Operational guidance for reviewers

- The tool reads `P4PORT`, `P4USER`, and `P4PASSWD`/`P4TICKETS` from the
  environment via P4Python's standard mechanisms. No credentials are
  written to disk by this tool.
- The SQLite cache (`moves.sqlite` by default) contains only depot **path
  strings** and **changelist numbers** — no file contents, no user data.
- All Perforce commands are invoked through P4Python, not via shell
  string-formatting, so depot path values cannot inject shell commands.
