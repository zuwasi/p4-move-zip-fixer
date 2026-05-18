"""Command-line interface."""
from __future__ import annotations

import sys

import click

from . import __version__
from .scan import scan_depot
from .spec import SpecCapReached, build_remote_spec, expand_spec_with_changelists
from .store import open_store
from .zipper import is_zip_exists_error, run_zip


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Automate the Perforce p4-zip move/add+move/delete workaround."""


@main.command()
@click.option("--depot", default="//depot/...", show_default=True, help="Depot path glob to scan.")
@click.option("--db", "db_path", default="moves.sqlite", show_default=True, type=click.Path())
@click.option("--head", "head_change", type=int, required=True, help="Highest changelist to scan.")
@click.option("--chunk", "chunk_size", default=5000, show_default=True, type=int)
@click.option("--workers", default=8, show_default=True, type=int)
def scan(depot: str, db_path: str, head_change: int, chunk_size: int, workers: int) -> None:
    """Scan the depot for move/add and move/delete pairs."""
    with open_store(db_path) as store:
        def _progress(done: int, total: int, inserted: int) -> None:
            click.echo(f"  [{done}/{total}] inserted={inserted}", err=True)

        n = scan_depot(
            depot_path=depot,
            store=store,
            head_change=head_change,
            chunk_size=chunk_size,
            workers=workers,
            progress=_progress,
        )
        click.echo(f"Scan complete. New rows: {n}. Total move rows: {store.count()}")


@main.command("build-spec")
@click.option("--db", "db_path", default="moves.sqlite", show_default=True, type=click.Path(exists=True))
@click.option("--remote", "remote_name", required=True, help="Remote spec name to write.")
@click.option("--remote-root", default="//remote", show_default=True)
def build_spec(db_path: str, remote_name: str, remote_root: str) -> None:
    """Generate and save a remote spec covering every recorded move."""
    with open_store(db_path) as store:
        try:
            n = build_remote_spec(store, remote_name=remote_name, remote_root=remote_root)
        except SpecCapReached as e:
            click.echo(f"ERROR: {e}", err=True)
            click.echo(
                "The depot has more unique move paths than Perforce will accept in a "
                "single remote spec. Split the migration into multiple changelist ranges "
                "(each with its own remote spec) and run `p4 zip` once per range.",
                err=True,
            )
            sys.exit(3)
        click.echo(f"Wrote remote spec '{remote_name}' with {n} view lines.")


@main.command("expand")
@click.option("--remote", "remote_name", required=True)
@click.option("--changelist", "-c", "changelists", multiple=True, required=True, type=int,
              help="Changelist number(s) whose paths should be added to the spec. Repeatable.")
@click.option("--remote-root", default="//remote", show_default=True)
def expand_cmd(remote_name: str, changelists: tuple[int, ...], remote_root: str) -> None:
    """Add every path touched by the given changelists into an existing remote spec.

    Use this when `p4 zip` fails on a specific changelist that involves a
    branch-of-move, lazy copy, obliterated half, or move chain that the
    filelog-based scan didn't catch on its own.
    """
    try:
        added, total = expand_spec_with_changelists(
            remote_name=remote_name,
            changelists=list(changelists),
            remote_root=remote_root,
        )
    except SpecCapReached as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(3)
    click.echo(f"Added {added} new paths. Spec now has {total} view lines.")


@main.command("zip")
@click.option("--remote", "remote_name", required=True)
@click.option("--output", "output_path", required=True, type=click.Path())
@click.option("--depot", "depot_path", default="//depot/...@1,#head", show_default=True)
@click.option("--auto-retry", default=20, show_default=True, type=int,
              help="On failure, auto-expand the spec from each failed changelist and retry up to N times. Set 0 to disable.")
@click.option("--remote-root", default="//remote", show_default=True,
              help="Used only with --auto-retry when expanding the spec.")
@click.option("--keep-failed-output/--clobber-failed-output", default=False, show_default=True,
              help="Keep the partial .zip on disk after a failed attempt. Default clobbers it before retrying.")
def zip_cmd(remote_name: str, output_path: str, depot_path: str,
            auto_retry: int, remote_root: str, keep_failed_output: bool) -> None:
    """Run p4 zip against the generated remote spec.

    With `--auto-retry N`, on failure we parse the failing changelist numbers
    out of Perforce's error messages, run `p4 describe -s` on each to harvest
    every path they touch, add those paths to the remote spec, and retry —
    up to N times. This is the documented automated mitigation for
    branch-of-move / move-chain / obliterated-half edge cases.

    Between attempts, the partial output zip left by a failed `p4 zip` is
    deleted so the retry doesn't fail with "Output zip file already exists".
    Use `--keep-failed-output` to preserve it (e.g. for forensic inspection).
    """
    attempt = 0
    last_failed: list[int] = []
    while True:
        attempt += 1
        click.echo(f"--- p4 zip attempt {attempt} ---", err=True)
        # Clobber stale/partial output so p4 zip never fails with the
        # "already exists" error on a retry (or on a re-run after a crash).
        clobber = not keep_failed_output
        result = run_zip(
            remote_name=remote_name,
            output_path=output_path,
            depot_path=depot_path,
            clobber=clobber,
        )
        if result.ok:
            click.echo(f"p4 zip succeeded on attempt {attempt}: {output_path}")
            return
        click.echo("p4 zip FAILED", err=True)
        for e in result.errors:
            click.echo(f"  ERROR: {e}", err=True)

        # An "Output zip file already exists" error means our pre-run clobber
        # was skipped (--keep-failed-output) or couldn't delete the file.
        # Don't auto-retry — there's nothing to widen, just file-system state.
        if any(is_zip_exists_error(e) for e in result.errors):
            click.echo(
                f"Output file {output_path} already exists. "
                "Remove it manually or re-run without --keep-failed-output.",
                err=True,
            )
            sys.exit(2)

        if not result.failed_changes:
            click.echo("No changelist numbers parsed from errors — cannot auto-retry.", err=True)
            sys.exit(2)
        if auto_retry <= 0 or attempt > auto_retry:
            click.echo(
                f"Failed changelists: {result.failed_changes}. "
                f"Re-run: p4-move-zip-fixer expand --remote {remote_name} "
                + " ".join(f"-c {c}" for c in result.failed_changes),
                err=True,
            )
            sys.exit(2)
        # Avoid infinite loop on the same set of failures
        if result.failed_changes == last_failed:
            click.echo("Same failed changelists as previous attempt — aborting to avoid loop.", err=True)
            _print_unrecoverable_guidance(remote_name, result.failed_changes, depot_path)
            sys.exit(2)
        last_failed = result.failed_changes
        click.echo(
            f"Auto-expanding spec from {len(result.failed_changes)} failed changelist(s) "
            f"({result.failed_changes}) and retrying...", err=True,
        )
        try:
            added, total = expand_spec_with_changelists(
                remote_name=remote_name,
                changelists=result.failed_changes,
                remote_root=remote_root,
            )
        except SpecCapReached as e:
            click.echo(f"ERROR: {e}", err=True)
            _print_unrecoverable_guidance(remote_name, result.failed_changes, depot_path)
            sys.exit(3)

        click.echo(f"  expanded: +{added} paths (total view lines = {total})", err=True)

        # If expand contributed nothing, the next p4 zip attempt is guaranteed
        # to fail on the same changelist (Perforce's view is identical). The
        # cause is usually an obliterated move counterpart that p4 describe -s
        # cannot see. Abort immediately with actionable guidance.
        if added == 0:
            click.echo(
                "Expand added 0 paths — the move counterpart for these changelists "
                "is not visible to `p4 describe -s` (likely obliterated or in a "
                "different depot). Auto-retry cannot recover; aborting.",
                err=True,
            )
            _print_unrecoverable_guidance(remote_name, result.failed_changes, depot_path)
            sys.exit(2)


def _print_unrecoverable_guidance(remote_name: str, failed: list[int], depot_path: str) -> None:
    """Emit a copy-pasteable runbook for unrecoverable per-CL failures."""
    cl_list = ",".join(str(c) for c in failed)
    click.echo("", err=True)
    click.echo("Recovery options:", err=True)
    click.echo(
        f"  1. Inspect the offending changelist(s) to confirm the move counterpart "
        f"is missing or obliterated:\n"
        f"     p4 describe -s {failed[0] if failed else '<CL>'}\n"
        f"     p4 filelog -m1 <one of the depot paths in that CL>",
        err=True,
    )
    click.echo(
        f"  2. Split the zip around the bad changelist(s) {cl_list}:\n"
        f"     p4-move-zip-fixer zip --remote {remote_name} "
        f"--output before.zip --depot '//depot/...@1,#{(min(failed) - 1) if failed else '<CL-1>'}'\n"
        f"     p4-move-zip-fixer zip --remote {remote_name} "
        f"--output after.zip  --depot '//depot/...@{(max(failed) + 1) if failed else '<CL+1>'},#head'",
        err=True,
    )
    click.echo(
        "  3. Or add explicit exclusion lines for the orphan paths to the "
        f"remote spec ({remote_name}) so they're excluded from view entirely.",
        err=True,
    )


if __name__ == "__main__":
    main()
