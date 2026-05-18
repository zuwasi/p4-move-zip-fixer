"""Command-line interface."""
from __future__ import annotations

import sys

import click

from . import __version__
from .scan import scan_depot
from .spec import build_remote_spec, expand_spec_with_changelists
from .store import open_store
from .zipper import run_zip


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
        n = build_remote_spec(store, remote_name=remote_name, remote_root=remote_root)
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
    added, total = expand_spec_with_changelists(
        remote_name=remote_name,
        changelists=list(changelists),
        remote_root=remote_root,
    )
    click.echo(f"Added {added} new paths. Spec now has {total} view lines.")


@main.command("zip")
@click.option("--remote", "remote_name", required=True)
@click.option("--output", "output_path", required=True, type=click.Path())
@click.option("--depot", "depot_path", default="//depot/...@1,#head", show_default=True)
@click.option("--auto-retry", default=20, show_default=True, type=int,
              help="On failure, auto-expand the spec from each failed changelist and retry up to N times. Set 0 to disable.")
@click.option("--remote-root", default="//remote", show_default=True,
              help="Used only with --auto-retry when expanding the spec.")
def zip_cmd(remote_name: str, output_path: str, depot_path: str,
            auto_retry: int, remote_root: str) -> None:
    """Run p4 zip against the generated remote spec.

    With `--auto-retry N`, on failure we parse the failing changelist numbers
    out of Perforce's error messages, run `p4 describe -s` on each to harvest
    every path they touch, add those paths to the remote spec, and retry —
    up to N times. This is the documented automated mitigation for
    branch-of-move / move-chain / obliterated-half edge cases.
    """
    attempt = 0
    last_failed: list[int] = []
    while True:
        attempt += 1
        click.echo(f"--- p4 zip attempt {attempt} ---", err=True)
        result = run_zip(remote_name=remote_name, output_path=output_path, depot_path=depot_path)
        if result.ok:
            click.echo(f"p4 zip succeeded on attempt {attempt}: {output_path}")
            return
        click.echo("p4 zip FAILED", err=True)
        for e in result.errors:
            click.echo(f"  ERROR: {e}", err=True)
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
            sys.exit(2)
        last_failed = result.failed_changes
        click.echo(
            f"Auto-expanding spec from {len(result.failed_changes)} failed changelist(s) "
            f"({result.failed_changes}) and retrying...", err=True,
        )
        added, total = expand_spec_with_changelists(
            remote_name=remote_name,
            changelists=result.failed_changes,
            remote_root=remote_root,
        )
        click.echo(f"  expanded: +{added} paths (total view lines = {total})", err=True)


if __name__ == "__main__":
    main()
