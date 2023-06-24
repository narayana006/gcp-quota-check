"""Read Compute Engine quota data from gcloud JSON and classify each quota.

Accepted inputs (the ``quotas`` array is the same shape in all of them:
``{"metric": ..., "limit": number, "usage": number}``):

* ``gcloud compute regions list --format=json``        -> list of Region objects
* ``gcloud compute regions describe REGION --format=json`` -> one Region object
* ``gcloud compute project-info describe --format=json``  -> one Project object
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

GLOBAL = "global"

# statuses from worst to best; the order drives sorting and exit codes
OVER = "over"
CRITICAL = "critical"
WARNING = "warning"
OK = "ok"
ZERO_LIMIT = "zero-limit"
NO_LIMIT = "no-limit"
SEVERITY = {OVER: 4, CRITICAL: 3, WARNING: 2, OK: 1, ZERO_LIMIT: 0, NO_LIMIT: 0}


class InputError(Exception):
    """Raised for unreadable or unrecognised input files."""


@dataclass
class Quota:
    scope: str  # region name, or "global" for project-wide quotas
    project: str
    metric: str
    limit: float
    usage: float
    percent: float | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Dataset:
    quotas: list[Quota] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ reading


def _project_from_link(link: str) -> str:
    parts = (link or "").split("/")
    if "projects" in parts:
        i = parts.index("projects")
        if i + 1 < len(parts):
            return parts[i + 1]
    return ""


def classify_object(obj: dict[str, Any]) -> str:
    """Return 'region' or 'project' for a gcloud resource with a quotas array."""
    kind = obj.get("kind", "")
    if kind == "compute#region":
        return "region"
    if kind == "compute#project":
        return "project"
    link = obj.get("selfLink", "")
    if "/regions/" in link or "zones" in obj:
        return "region"
    if "commonInstanceMetadata" in obj or "defaultServiceAccount" in obj or (
        link and link.rstrip("/").split("/")[-2:-1] == ["projects"]
    ):
        return "project"
    raise InputError(f"cannot tell whether object {obj.get('name', '?')!r} is a region or a project")


def _number(value: Any, what: str, where: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise InputError(f"{where}: quota {what} {value!r} is not a number") from None


def load_objects(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8") if str(path) != "-" else _stdin()
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON ({exc.msg} at line {exc.lineno})") from exc
    if isinstance(data, dict):
        data = data["items"] if isinstance(data.get("items"), list) else [data]
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise InputError(f"{path} must be a gcloud region or project-info JSON object, or a list of them")
    return data


def _stdin() -> str:
    import sys

    return sys.stdin.read()


def read_dataset(paths: Iterable[Path]) -> Dataset:
    ds = Dataset()
    for path in paths:
        for obj in load_objects(path):
            if "quotas" not in obj:
                raise InputError(f"{path}: object {obj.get('name', '?')!r} has no 'quotas' field; "
                                 "use regions list/describe or project-info describe output")
            kind = classify_object(obj)
            name = obj.get("name", "?")
            if kind == "region":
                scope = name
                project = _project_from_link(obj.get("selfLink", ""))
                if scope not in ds.regions:
                    ds.regions.append(scope)
            else:
                scope = GLOBAL
                project = name
            if project and project not in ds.projects:
                ds.projects.append(project)
            warn = obj.get("quotaStatusWarning")
            if warn:
                ds.warnings.append(f"{scope}: quotas may be incomplete ({warn.get('code', '')}: "
                                   f"{warn.get('message', '').strip()})")
            for q in obj.get("quotas") or []:
                metric = str(q.get("metric", "?"))
                where = f"{path} {scope} {metric}"
                limit = _number(q.get("limit", 0), "limit", where)
                usage = _number(q.get("usage", 0), "usage", where)
                ds.quotas.append(Quota(scope, project, metric, limit, usage, None, ""))
    return ds


# --------------------------------------------------------------- evaluating


def evaluate(q: Quota, warn_pct: float, crit_pct: float) -> Quota:
    """Fill in percent and status.

    A limit of 0 means the project may not use that resource in that scope at
    all; many projects have limit 0 for GPU and other opt-in quotas. Usage
    under a zero limit cannot normally happen, but if it does it is reported
    as over the limit. A negative limit is treated as no limit.
    """
    if q.limit < 0:
        q.percent, q.status = None, NO_LIMIT
    elif q.limit == 0:
        q.percent = None
        q.status = OVER if q.usage > 0 else ZERO_LIMIT
    else:
        q.percent = round(q.usage / q.limit * 100, 1)
        if q.usage > q.limit:
            q.status = OVER
        elif q.percent >= crit_pct:
            q.status = CRITICAL
        elif q.percent >= warn_pct:
            q.status = WARNING
        else:
            q.status = OK
    return q


def _match(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value.upper(), p.upper()) for p in patterns)


def select(
    quotas: list[Quota],
    metrics: list[str] | None = None,
    exclude_metrics: list[str] | None = None,
    regions: list[str] | None = None,
) -> list[Quota]:
    out = []
    for q in quotas:
        if metrics and not _match(q.metric, metrics):
            continue
        if exclude_metrics and _match(q.metric, exclude_metrics):
            continue
        if regions and not _match(q.scope, regions):
            continue
        out.append(q)
    return out


def sort_quotas(quotas: list[Quota], key: str) -> list[Quota]:
    if key == "metric":
        return sorted(quotas, key=lambda q: (q.metric, q.scope))
    if key == "scope":
        return sorted(quotas, key=lambda q: (q.scope != GLOBAL, q.scope, q.metric))
    # "percent": worst first, then highest percentage
    return sorted(quotas, key=lambda q: (-SEVERITY[q.status], -(q.percent if q.percent is not None else -1),
                                         q.scope, q.metric))


def exit_code(quotas: list[Quota]) -> int:
    worst = max((SEVERITY[q.status] for q in quotas), default=0)
    if worst >= SEVERITY[CRITICAL]:
        return 2
    if worst == SEVERITY[WARNING]:
        return 1
    return 0
