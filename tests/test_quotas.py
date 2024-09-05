import json

import pytest

from gcp_quota_check.quotas import (
    CRITICAL, NO_LIMIT, OK, OVER, WARNING, ZERO_LIMIT, InputError, Quota, classify_object, evaluate,
    exit_code, read_dataset, select, sort_quotas,
)

B = "https://www.googleapis.com/compute/v1/projects/demo-1"


def q(metric="CPUS", limit=100.0, usage=0.0, scope="us-east1"):
    return evaluate(Quota(scope, "demo-1", metric, limit, usage, None, ""), 80, 95)


@pytest.mark.parametrize("limit,usage,status,pct", [
    (100, 10, OK, 10.0),
    (100, 80, WARNING, 80.0),
    (100, 94.9, WARNING, 94.9),
    (100, 95, CRITICAL, 95.0),
    (24, 24, CRITICAL, 100.0),
    (8, 9, OVER, 112.5),
    (0, 0, ZERO_LIMIT, None),
    (0, 2, OVER, None),
    (-1, 5, NO_LIMIT, None),
])
def test_evaluate(limit, usage, status, pct):
    r = q(limit=float(limit), usage=float(usage))
    assert (r.status, r.percent) == (status, pct)


def test_exit_code():
    assert exit_code([q(usage=10)]) == 0
    assert exit_code([q(usage=10), q(usage=85)]) == 1
    assert exit_code([q(usage=85), q(usage=99)]) == 2
    assert exit_code([q(limit=0, usage=1)]) == 2
    assert exit_code([q(limit=0, usage=0)]) == 0
    assert exit_code([]) == 0


def test_select_wildcards_case_insensitive_and_global_scope():
    rows = [q("CPUS"), q("NVIDIA_T4_GPUS"), q("SNAPSHOTS", scope="global"), q("CPUS", scope="europe-west1")]
    assert [r.metric for r in select(rows, metrics=["*gpus"])] == ["NVIDIA_T4_GPUS"]
    assert [r.scope for r in select(rows, regions=["global"])] == ["global"]
    assert len(select(rows, regions=["us-*", "europe-*"])) == 3
    assert [r.metric for r in select(rows, exclude_metrics=["*GPUS", "SNAP*"])] == ["CPUS", "CPUS"]


def test_sort_percent_puts_worst_first():
    rows = [q("A", usage=50), q("B", usage=99), q("C", limit=1, usage=2), q("D", usage=85), q("E", limit=0)]
    assert [r.metric for r in sort_quotas(rows, "percent")] == ["C", "B", "D", "A", "E"]
    assert [r.metric for r in sort_quotas(rows, "metric")] == ["A", "B", "C", "D", "E"]


def test_sort_scope_puts_global_first():
    rows = [q(scope="us-east1"), q(scope="global"), q(scope="asia-east1")]
    assert [r.scope for r in sort_quotas(rows, "scope")] == ["global", "asia-east1", "us-east1"]


def region(name, quotas, **extra):
    r = {"kind": "compute#region", "name": name, "selfLink": f"{B}/regions/{name}", "quotas": quotas}
    r.update(extra)
    return r


def test_read_regions_list_and_project(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps([
        region("us-east1", [{"metric": "CPUS", "limit": 24.0, "usage": 20.0}]),
        region("us-west1", [{"metric": "CPUS", "limit": 24.0, "usage": 0.0}]),
    ]))
    (tmp_path / "p.json").write_text(json.dumps({
        "kind": "compute#project", "name": "demo-1", "selfLink": B,
        "quotas": [{"metric": "SNAPSHOTS", "limit": 5000.0, "usage": 12.0}],
    }))
    ds = read_dataset([tmp_path / "r.json", tmp_path / "p.json"])
    assert ds.regions == ["us-east1", "us-west1"]
    assert ds.projects == ["demo-1"]
    assert [(x.scope, x.metric) for x in ds.quotas] == [("us-east1", "CPUS"), ("us-west1", "CPUS"),
                                                        ("global", "SNAPSHOTS")]


def test_read_single_region_describe_and_string_numbers(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps(region("us-east1", [{"metric": "CPUS", "limit": "24", "usage": "3"}])))
    [x] = read_dataset([p]).quotas
    assert (x.limit, x.usage, x.project) == (24.0, 3.0, "demo-1")


def test_quota_status_warning_is_collected(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps(region("us-east1", [], quotaStatusWarning={"code": "UNREACHABLE", "message": "x"})))
    assert "quotas may be incomplete" in read_dataset([p]).warnings[0]


def test_classify_without_kind():
    assert classify_object({"name": "r", "zones": []}) == "region"
    assert classify_object({"name": "p", "commonInstanceMetadata": {}}) == "project"
    assert classify_object({"name": "p", "selfLink": B}) == "project"
    with pytest.raises(InputError):
        classify_object({"name": "x"})


@pytest.mark.parametrize("content,msg", [
    ("{oops", "not valid JSON"),
    ('"text"', "must be"),
    (json.dumps([{"kind": "compute#region", "name": "r"}]), "no 'quotas'"),
    (json.dumps(region("r", [{"metric": "CPUS", "limit": "lots", "usage": 1}])), "not a number"),
])
def test_bad_inputs(tmp_path, content, msg):
    p = tmp_path / "bad.json"
    p.write_text(content)
    with pytest.raises(InputError, match=msg):
        read_dataset([p])


def test_missing_file(tmp_path):
    with pytest.raises(InputError, match="cannot read"):
        read_dataset([tmp_path / "nope.json"])
