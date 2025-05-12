import csv
import io
import json
from pathlib import Path

import pytest

from gcp_quota_check.cli import main

EX = Path(__file__).resolve().parent.parent / "examples"
REGIONS = str(EX / "regions.json")
PROJECT = str(EX / "project.json")


def run(capsys, *args):
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def test_example_default_table(capsys):
    code, out, _ = run(capsys, REGIONS, PROJECT)
    assert code == 2
    lines = out.splitlines()
    assert lines[0].startswith("Checked 105 quota(s) (3 region(s), project-wide) for acme-prod-1234")
    rows = [l.split() for l in lines[3:] if l and not l.startswith("Summary")]
    assert rows[0][:2] == ["us-central1", "N2_CPUS"] and rows[0][-1] == "CRITICAL"
    assert [r[1] for r in rows] == ["N2_CPUS", "CPUS", "CPUS_ALL_REGIONS", "NETWORKS", "URL_MAPS",
                                    "STATIC_ADDRESSES", "SSD_TOTAL_GB", "FIREWALLS"]
    assert "19 with limit 0 (hidden; --include-zero to list)" in out


def test_higher_thresholds_change_exit_code(capsys):
    assert run(capsys, REGIONS, PROJECT, "--warn", "95", "--critical", "101")[0] == 1
    assert run(capsys, REGIONS, PROJECT, "--warn", "101", "--critical", "101")[0] == 0


def test_filters_and_no_rows_message(capsys):
    code, out, _ = run(capsys, REGIONS, "--region", "europe-west1")
    assert code == 0
    assert "No quota at or above the warning threshold." in out


def test_include_zero_lists_gpu_quotas(capsys):
    code, out, _ = run(capsys, REGIONS, "--metric", "NVIDIA_T4_GPUS", "--include-zero")
    assert code == 0
    assert out.count("ZERO-LIMIT") == 3


def test_json_output(capsys):
    code, out, _ = run(capsys, REGIONS, PROJECT, "--format", "json", "--top", "2")
    doc = json.loads(out)
    assert code == 2
    assert [x["metric"] for x in doc["quotas"]] == ["N2_CPUS", "CPUS"]
    assert doc["summary"]["critical"] == 2 and doc["summary"]["warning"] == 6
    assert doc["regions"] == ["europe-west1", "us-central1", "us-east1"]


def test_csv_all(capsys):
    code, out, _ = run(capsys, PROJECT, "--format", "csv", "--all", "--sort", "metric")
    rows = list(csv.DictReader(io.StringIO(out)))
    assert len(rows) == 26  # 27 project quotas minus GPUS_ALL_REGIONS (limit 0, hidden)
    assert rows[0]["metric"] == "BACKEND_SERVICES" and rows[0]["percent"] == "18.0"


def test_stdin(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(Path(PROJECT).read_text()))
    code, out, _ = run(capsys, "-", "--metric", "NETWORKS")
    assert code == 1 and "NETWORKS" in out


def test_unmatched_filter_warns(capsys):
    code, _, err = run(capsys, REGIONS, "--metric", "NO_SUCH")
    assert code == 0 and "no quota matched" in err


@pytest.mark.parametrize("args", [
    ["--warn", "90", "--critical", "80"],
    ["--warn", "0"],
    ["--top", "0"],
    ["--format", "xml"],
])
def test_bad_arguments_exit_3(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main([REGIONS, *args])
    assert exc.value.code == 3


def test_bad_file_exits_3(tmp_path, capsys):
    p = tmp_path / "x.json"
    p.write_text("[1, 2]")
    code, _, err = run(capsys, str(p))
    assert code == 3 and "error" in err
