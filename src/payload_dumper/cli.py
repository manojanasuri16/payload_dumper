"""Command-line interface for payload-dumper."""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from . import __version__
from .core import (
    PayloadError,
    UnsupportedPayloadError,
    extract_partition,
    parse_payload,
    partition_op_types,
    validate_operations,
)
from .source import ByteSource, SourceError, describe, open_source

console = Console()


def _list_partitions_table(manifest) -> None:
    table = Table(
        title=f"{len(manifest.partitions)} partitions",
        title_justify="left",
        header_style="bold",
    )
    table.add_column("Partition", style="cyan", no_wrap=True)
    table.add_column("Ops", justify="right", style="magenta")
    table.add_column("Size", justify="right", style="yellow")
    table.add_column("Types", style="green")
    for part in manifest.partitions:
        size = part.new_partition_info.size if part.new_partition_info else 0
        table.add_row(
            part.partition_name,
            str(len(part.operations)),
            _humansize(size),
            ", ".join(partition_op_types(part)),
        )
    console.print(table)


def _list_partitions_json(manifest) -> None:
    payload = {
        "minor_version": manifest.minor_version,
        "block_size": manifest.block_size,
        "partitions": [
            {
                "name": part.partition_name,
                "operations": len(part.operations),
                "size": part.new_partition_info.size if part.new_partition_info else 0,
                "types": partition_op_types(part),
            }
            for part in manifest.partitions
        ],
    }
    print(json.dumps(payload, indent=2))


def _humansize(n: int) -> str:
    if n <= 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


def _select_partitions(manifest, requested: Optional[List[str]]):
    if not requested:
        return list(manifest.partitions)
    wanted = {p.lower() for p in requested}
    selected = [p for p in manifest.partitions if p.partition_name.lower() in wanted]
    missing = wanted - {p.partition_name.lower() for p in selected}
    if missing:
        console.print(
            f"[yellow]warning:[/yellow] partitions not found: "
            f"{', '.join(sorted(missing))}"
        )
    return selected


def _default_workers() -> int:
    return min(8, (os.cpu_count() or 4))


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="payload-dumper",
        description=(
            "Extract partition images from Android OTA payload.bin files. "
            "Accepts a local .bin, a local OTA .zip, or an http(s):// URL "
            "pointing at either."
        ),
    )
    parser.add_argument(
        "payload",
        help="path or URL to payload.bin or to an OTA .zip containing it",
    )
    parser.add_argument(
        "-o", "--output", default="output",
        help="output directory (default: output/)",
    )
    parser.add_argument(
        "-p", "--partitions", nargs="+", metavar="NAME",
        help="extract only these partitions (e.g. boot system vendor)",
    )
    parser.add_argument(
        "-l", "--list", action="store_true",
        help="list partitions and exit",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="with --list, emit machine-readable JSON to stdout",
    )
    parser.add_argument(
        "-j", "--workers", type=int, default=_default_workers(),
        help=f"parallel extraction workers (default: {_default_workers()})",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"payload-dumper {__version__}",
    )
    return parser.parse_args(argv)


def _extract_all(source, data_offset, partitions, output_dir, workers) -> List[tuple]:
    total_ops = sum(len(p.operations) for p in partitions)
    failures: List[tuple] = []

    progress = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("ops"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )

    with progress:
        overall = progress.add_task("[bold]total", total=total_ops)
        tasks = {
            part.partition_name: progress.add_task(
                part.partition_name, total=len(part.operations)
            )
            for part in partitions
        }

        def advance(name: str, n: int) -> None:
            progress.advance(tasks[name], n)
            progress.advance(overall, n)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    extract_partition,
                    source,
                    data_offset,
                    part,
                    output_dir,
                    lambda n, name=part.partition_name: advance(name, n),
                ): part.partition_name
                for part in partitions
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    fut.result()
                except PayloadError as e:
                    failures.append((name, str(e)))
                    progress.console.print(f"[red]FAIL[/red] {name}: {e}")
                except Exception as e:
                    failures.append((name, repr(e)))
                    progress.console.print(f"[red]FAIL[/red] {name}: {e!r}")

    return failures


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        source: ByteSource = open_source(args.payload)
    except (SourceError, FileNotFoundError) as e:
        console.print(f"[red]error:[/red] {e}")
        return 1

    try:
        try:
            payload = parse_payload(source)
        except PayloadError as e:
            console.print(f"[red]error:[/red] {e}")
            return 1

        manifest = payload.manifest

        if args.list and args.json:
            _list_partitions_json(manifest)
            return 0

        console.print(
            f"[bold]source[/bold]  {describe(args.payload)}  "
            f"size={_humansize(source.size())}"
        )
        console.print(
            f"[bold]payload[/bold]  minor_version={manifest.minor_version}  "
            f"block_size={manifest.block_size}  partitions={len(manifest.partitions)}"
        )

        if args.list:
            _list_partitions_table(manifest)
            return 0

        partitions = _select_partitions(manifest, args.partitions)
        if not partitions:
            console.print("no partitions to extract")
            return 0

        try:
            validate_operations(partitions)
        except UnsupportedPayloadError as e:
            console.print(f"[red]error:[/red] {e}")
            return 1

        workers = max(1, min(args.workers, len(partitions)))
        os.makedirs(args.output, exist_ok=True)
        console.print(
            f"extracting {len(partitions)} partition(s) to "
            f"[bold]{args.output}/[/bold]  workers={workers}\n"
        )

        try:
            failures = _extract_all(
                source, payload.data_offset, partitions, args.output, workers
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/yellow]")
            return 130

        if failures:
            console.print(f"\n[red]{len(failures)} partition(s) failed[/red]")
            return 1

        console.print(
            f"\n[green]done[/green]  extracted {len(partitions)} partition(s) "
            f"to {args.output}/"
        )
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    sys.exit(main())
