from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MONITOR_SPEC = importlib.util.spec_from_file_location("monitor", ROOT / "monitor.py")
assert MONITOR_SPEC is not None and MONITOR_SPEC.loader is not None
monitor = importlib.util.module_from_spec(MONITOR_SPEC)
sys.modules["monitor"] = monitor
MONITOR_SPEC.loader.exec_module(monitor)

EXACT_SPEC = importlib.util.spec_from_file_location(
    "exact_monitor", ROOT / "exact_monitor.py"
)
assert EXACT_SPEC is not None and EXACT_SPEC.loader is not None
exact_monitor = importlib.util.module_from_spec(EXACT_SPEC)
EXACT_SPEC.loader.exec_module(exact_monitor)


def synthetic_prices_with_non_month_end_crash() -> tuple[pd.Series, pd.Series]:
    sessions = monitor._normalize_sessions(
        monitor._calendar().sessions_in_range("2022-01-03", "2026-08-31")
    )
    t = np.arange(len(sessions), dtype=float)
    base = np.exp(0.00045 * t + 0.01 * np.sin(t / 19.0))
    qqq = pd.Series(350.0 * base, index=sessions)
    ndx = pd.Series(14000.0 * base, index=sessions)

    crash_date = pd.Timestamp("2026-08-28")
    qqq.loc[crash_date] *= 0.70
    ndx.loc[crash_date] *= 0.70
    return qqq, ndx


def test_non_month_end_crash_does_not_create_trade_or_publish() -> None:
    qqq, ndx = synthetic_prices_with_non_month_end_crash()
    decision = monitor.build_decision(qqq, ndx, "2026-08-28")
    report = exact_monitor.render_exact_markdown(decision)
    payload = exact_monitor.exact_payload(decision)

    assert decision.is_month_end is False
    assert all(item.current_weight == item.new_weight for item in decision.decisions)
    assert "今日不调仓" in report
    assert "即使当日出现大跌" in report
    assert "| 不调仓 |" in report
    assert payload["strategy_frequency"] == "month_end"
    assert payload["notification_frequency"] == "month_end_only"
    assert payload["calendar_poll_frequency"] == "weekday"
    assert payload["publish_notification"] is False
    assert payload["intramonth_crash_override"] is False
    assert all(
        item["instruction"] == "no_rebalance" for item in payload["decisions"]
    )
    assert all(item["actual_account_weight"] is None for item in payload["decisions"])
    assert all("current_weight" not in item for item in payload["decisions"])


def test_month_end_instruction_is_rebalance_to_target_and_publishable() -> None:
    qqq, ndx = synthetic_prices_with_non_month_end_crash()
    decision = monitor.build_decision(qqq, ndx, "2026-08-31")
    report = exact_monitor.render_exact_markdown(decision)
    payload = exact_monitor.exact_payload(decision)

    assert decision.is_month_end is True
    assert decision.execution_date == "2026-09-01"
    assert "月末再平衡" in report
    assert "上期月末目标" in report
    assert "按实际账户再平衡至" in report
    assert "当前模型权重" not in report
    assert payload["publish_notification"] is True
    assert all(
        item["instruction"] == "rebalance_actual_account_to_target"
        for item in payload["decisions"]
    )
    assert all("prior_month_end_target" in item for item in payload["decisions"])
    assert all("target_weight" in item for item in payload["decisions"])


def test_workflow_posts_success_only_when_payload_is_publishable() -> None:
    workflow = (
        ROOT.parent / ".github" / "workflows" / "qld-tqqq-signal.yml"
    ).read_text(encoding="utf-8")

    assert workflow.startswith("name: QLD TQQQ monthly signal")
    assert 'payload.get("publish_notification", False)' in workflow
    assert "steps.generate.outputs.publish == 'true'" in workflow
    assert "Post month-end signal or validation failure" in workflow
