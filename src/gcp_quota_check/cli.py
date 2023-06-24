"""Command-line entry point: gcp-quota-check."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

from . import __version__
from .quotas import (
    CRITICAL,
    GLOBAL,
    OK,
    OVER,
    WARNING,
    ZERO_LIMIT,
    Dataset,
    InputError,
    Quota,
    evaluate,
    exit_code,
    read_dataset,
    select,
    sort_quotas,
)

USAGE_ERROR = 3

EPILOG = """\
inputs (one or more files, or - for stdin):
  gcloud compute regions list --format=json > regions.json
  gcloud compute regions describe us-central1 --format=json > us-central1.json
  gcloud compute project-info describe --format=json > project.json

exit codes: 0 all below --warn, 1 a quota at or above --warn,
            2 a quota at or above --critical or over its limit, 3 bad input or arguments
"""


class Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse uses exit 2, which means "critical" here
        self.print_usage(sys.stderr)
        self.exit(USAGE_ERROR, f"{self.prog}: error: {message}\n")


def build_parser() -> Parser:
    p = Parser(
        prog="gcp-quota-check",
        description="Show Compute Engine quotas close to their limits, from gcloud JSON output.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("files", nargs="+", type=Path, metavar="FILE",
                   help="gcloud regions list/describe or project-info describe JSON")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--warn", type=float, default=80.0, help="warning threshold in percent (default 80)")
    p.add_argument("--critical", type=float, default=95.0, help="critical threshold in percent (default 95)")
    p.add_argument("--metric", action="append", default=[], metavar="PATTERN",
                   help="only these metrics; shell-style wildcards, repeatable (e.g. 'CPUS', '*GPUS')")
    p.add_argument("--exclude-metric", action="append", default=[], metavar="PATTERN",
                   help="skip these metrics; wildcards, repeatable")
    p.add_argument("--region", action="append", default=[], metavar="PATTERN",
                   help="only these regions; 'global' selects project-wide quotas; wildcards, repeatable")
    p.add_argument("--all", action="store_true", help="list every quota, not only those at or above --warn")
    p.add_argument("--include-zero", action="store_true",
                   help="also list quotas whose limit is 0 (hidden by default)")
    p.add_argument("--sort", choices=("percent", "metric", "scope"), default="percent")
    p.add_argument("--top", type=int, metavar="N", help="show at most N rows")
    p.add_argument("--format", choices=("table", "json", "csv"), default="table")
    return p


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


def _pct(q: Quota) -> str:
    return "-" if q.percent is None else f"{q.percent:.1f}%"


def visible(quotas: list[Quota], show_all: bool, include_zero: bool) -> list[Quota]:
    out = []
    for q in quotas:
        if q.status == ZERO_LIMIT and not include_zero:
            continue
        if not show_all and q.status not in (WARNING, CRITICAL, OVER, ZERO_LIMIT):
            continue
        out.append(q)
    return out


def summary(ds: Dataset, checked: list[Quota]) -> dict[str, int]:
    counts = {s: 0 for s in (OVER, CRITICAL, WARNING, OK, ZERO_LIMIT)}
    for q in checked:
        if q.status in counts:
            counts[q.status] += 1
    counts["checked"] = len(checked)
    return counts


def render_table(rows: list[Quota], checked: list[Quota], ds: Dataset, counts: dict[str, int], args) -> str:
    scopes = {q.scope for q in checked}
    where = []
    if scopes - {GLOBAL}:
        where.append(f"{len(scopes - {GLOBAL})} region(s)")
    if GLOBAL in scopes:
        where.append("project-wide")
    project = f" for {', '.join(ds.projects)}" if ds.projects else ""
    lines = [f"Checked {counts['checked']} quota(s) in {' + '.join(where) or 'no scopes'}{project}; "
             f"warn at {_num(args.warn)}%, critical at {_num(args.critical)}%", ""]
    if rows:
        table = [["SCOPE", "METRIC", "USAGE", "LIMIT", "USED", "STATUS"]]
        table += [[q.scope, q.metric, _num(q.usage), _num(q.limit), _pct(q), q.status.upper()] for q in rows]
        widths = [max(len(r[i]) for r in table) for i in range(len(table[0]))]
        right = {2, 3, 4}
        for r in table:
            cells = [c.rjust(widths[i]) if i in right else c.ljust(widths[i]) for i, c in enumerate(r)]
            lines.append("  ".join(cells).rstrip())
    else:
        lines.append("No quota at or above the warning threshold.")
    lines.append("")
    parts = [f"{counts[OVER]} over limit", f"{counts[CRITICAL]} critical", f"{counts[WARNING]} warning",
             f"{counts[OK]} ok"]
    zero = f"{counts[ZERO_LIMIT]} with limit 0"
    if counts[ZERO_LIMIT] and not args.include_zero:
        zero += " (hidden; --include-zero to list)"
    lines.append("Summary: " + ", ".join(parts) + ", " + zero)
    return "\n".join(lines)


def render_json(rows: list[Quota], ds: Dataset, counts: dict[str, int], args) -> str:
    return json.dumps({
        "projects": ds.projects,
        "regions": ds.regions,
        "thresholds": {"warn": args.warn, "critical": args.critical},
        "summary": counts,
        "warnings": ds.warnings,
        "quotas": [q.to_dict() for q in rows],
    }, indent=2)


def render_csv(rows: list[Quota]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["project", "scope", "metric", "usage", "limit", "percent", "status"])
    for q in rows:
        w.writerow([q.project, q.scope, q.metric, _num(q.usage), _num(q.limit),
                    "" if q.percent is None else q.percent, q.status])
    return buf.getvalue().rstrip("\n")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 < args.warn <= args.critical:
        parser.error("thresholds must satisfy 0 < --warn <= --critical")
    if args.top is not None and args.top < 1:
        parser.error("--top must be at least 1")

    try:
        ds = read_dataset(args.files)
    except InputError as exc:
        print(f"gcp-quota-check: error: {exc}", file=sys.stderr)
        return USAGE_ERROR

    for w in ds.warnings:
        print(f"gcp-quota-check: warning: {w}", file=sys.stderr)

    checked = [evaluate(q, args.warn, args.critical)
               for q in select(ds.quotas, args.metric, args.exclude_metric, args.region)]
    if not checked and (args.metric or args.region):
        print("gcp-quota-check: warning: no quota matched the --metric/--region filters", file=sys.stderr)
    counts = summary(ds, checked)
    rows = sort_quotas(visible(checked, args.all, args.include_zero), args.sort)
    if args.top:
        rows = rows[: args.top]

    if args.format == "json":
        print(render_json(rows, ds, counts, args))
    elif args.format == "csv":
        print(render_csv(rows))
    else:
        print(render_table(rows, checked, ds, counts, args))
    return exit_code(checked)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
