"""Auditable operational gate, independent of monthly strategy calculations.

A run being eligible never authorizes a trade or proves market-data freshness.
The existing automation calendar, data validation and publication deadline remain
responsible for those decisions. This module uses only the Python standard library
because it runs before dependency installation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

EVENTS = frozenset({"schedule", "push", "workflow_dispatch", "repository_dispatch"})


def decision(event: str, now: datetime) -> dict:
    if event not in EVENTS:
        raise ValueError("Unsupported workflow event")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("An explicit timezone is required")
    pt = now.astimezone(ZoneInfo("America/Los_Angeles"))
    # Start earlier than the former 18:00 gate so a delivered post-close event is
    # not wasted. Weekends/holidays still use automation.latest_session().
    eligible = event != "schedule" or pt.hour >= 14 or pt.hour < 7
    return {
        "version": "postclose-window-v2", "event": event,
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(),
        "checked_at_pt": pt.isoformat(), "window_pt": "14:00-06:59",
        "run": eligible, "status": "eligible" if eligible else "skipped",
        "reason": ("explicit_event" if event != "schedule" else
                   "postclose_or_overnight" if eligible else "outside_data_check_window"),
        "data_verified": False, "execution_authorized": False,
    }


def main(now: datetime | None = None, env=None) -> int:
    env = os.environ if env is None else env
    result = decision(env.get("GITHUB_EVENT_NAME", "workflow_dispatch"),
                      now if now is not None else datetime.now(timezone.utc))
    result.update(run_id=env.get("GITHUB_RUN_ID"), run_attempt=env.get("GITHUB_RUN_ATTEMPT"),
                  schedule=env.get("SCHEDULE_EXPRESSION", ""))
    out = Path("run_window_audit")
    out.mkdir(exist_ok=True)
    temp = out / "window.tmp"
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(out / "window.json")
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f'run={str(result["run"]).lower()}\n')
    text = ("## 调度入口检查\n\n"
            f'实际检查时间 {result["checked_at_pt"]}；事件 {result["event"]}。\n\n')
    if result["run"]:
        text += "已进入数据核验阶段；这条入口记录不代表数据已核验或策略已执行。\n"
    else:
        text += ("**本次只检查时间窗口，跳过行情与 C20/Q70 计算。**\n"
                 "这是 skipped，不是成功更新行情。允许数据检查的窗口为 PT 14:00 至次日06:59。\n")
    text += ("策略仍只在月末决策；定时事件可能延迟或缺失，绿色工作流本身不是数据新鲜度证明。\n")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
