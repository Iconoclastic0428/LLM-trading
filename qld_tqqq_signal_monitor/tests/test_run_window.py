from datetime import datetime
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_window


@pytest.mark.parametrize("stamp,eligible", [
    ("2026-09-15T20:59:59+00:00", False),
    ("2026-09-15T21:00:00+00:00", True),
    ("2026-09-16T00:09:39+00:00", True),  # incident: 17:09 PT was formerly skipped
    ("2026-09-16T13:59:59+00:00", True),
    ("2026-09-16T14:00:00+00:00", False),
    ("2026-12-16T21:59:59+00:00", False),
    ("2026-12-16T22:00:00+00:00", True),
    ("2026-12-17T14:59:59+00:00", True),
    ("2026-12-17T15:00:00+00:00", False),
    ("2026-11-01T08:30:00+00:00", True),
    ("2026-11-01T09:30:00+00:00", True),
])
def test_timezone_window_and_incident_regression(stamp, eligible):
    d = run_window.decision("schedule", datetime.fromisoformat(stamp))
    assert d["run"] is eligible
    assert d["status"] == ("eligible" if eligible else "skipped")
    assert d["data_verified"] is False and d["execution_authorized"] is False


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "repository_dispatch"])
def test_explicit_recovery_bypasses_window_only(event):
    d = run_window.decision(event, datetime.fromisoformat("2026-09-15T18:00:00+00:00"))
    assert d["run"] and d["reason"] == "explicit_event"
    assert not d["execution_authorized"]


def test_naive_timestamp_and_unknown_event_rejected():
    with pytest.raises(ValueError):
        run_window.decision("schedule", datetime(2026, 9, 15, 17))
    with pytest.raises(ValueError):
        run_window.decision("untrusted\nrun=true", datetime.fromisoformat("2026-09-16T00:00:00+00:00"))


@pytest.mark.parametrize("stamp,eligible", [
    ("2026-09-15T18:00:00+00:00", False),
    ("2026-09-16T00:09:39+00:00", True),
])
def test_audit_and_summary_never_claim_verified_data(tmp_path, monkeypatch, stamp, eligible):
    monkeypatch.chdir(tmp_path)
    env = {"GITHUB_EVENT_NAME": "schedule", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2",
           "GITHUB_OUTPUT": str(tmp_path / "outputs"), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}
    assert run_window.main(datetime.fromisoformat(stamp), env) == 0
    d = json.loads((tmp_path / "run_window_audit/window.json").read_text())
    assert d["run"] is eligible and d["data_verified"] is False
    assert (tmp_path / "outputs").read_text() == f"run={str(eligible).lower()}\n"
    assert "调度入口检查" in (tmp_path / "summary").read_text()
    if not eligible:
        assert "skipped" in (tmp_path / "summary").read_text()
    assert not (tmp_path / "signal_output").exists()
    assert not (tmp_path / "shadow_output").exists()
