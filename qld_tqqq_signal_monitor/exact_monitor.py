from __future__ import annotations

import json
import sys
from pathlib import Path

import monitor


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def exact_payload(decision: monitor.DailyDecision) -> dict:
    """Return structured output without treating a prior target as a live weight."""

    payload = decision.to_dict()
    payload["strategy_frequency"] = "month_end"
    payload["intramonth_rebalance"] = False
    payload["intramonth_crash_override"] = False
    payload["execution_rule"] = "next_session_open"
    payload["actual_account_weights_available"] = False

    instruction = (
        "rebalance_actual_account_to_target"
        if decision.is_month_end and decision.market_status == "open"
        else "no_rebalance"
    )
    normalized_decisions: list[dict] = []
    for item in payload.get("decisions", []):
        normalized_decisions.append(
            {
                "symbol": item["symbol"],
                "prior_month_end_target": item["current_weight"],
                "target_weight": item["new_weight"],
                "target_change": item["delta_weight"],
                "defensive_weight": item["defensive_weight"],
                "actual_account_weight": None,
                "instruction": instruction,
            }
        )
    payload["decisions"] = normalized_decisions
    return payload


def render_exact_markdown(decision: monitor.DailyDecision) -> str:
    """Render instructions that preserve the backtested monthly execution rule.

    The prior month-end target is not the account's current weight because ETF and
    defensive holdings drift between rebalance dates. Without brokerage holdings,
    the exact executable instruction is to rebalance the actual account to the newly
    calculated target on a month-end execution date.
    """

    marker = f"<!-- qld-tqqq-signal:{decision.report_date}:success -->"
    lines = [
        marker,
        f"## QLD/TQQQ 模型信号：{decision.report_date}",
        "",
        monitor.ISSUE_MENTION,
        "",
    ]

    if decision.market_status == "closed":
        lines.extend(
            [
                "**结论：休市，无操作。**",
                "",
                decision.message,
                "",
                "该系统只提供模型通知，不读取账户持仓，也不自动下单。",
            ]
        )
        return "\n".join(lines)

    today = decision.today_snapshot
    proposed = decision.proposed_snapshot
    assert today is not None and proposed is not None

    if decision.is_month_end:
        lines.extend(
            [
                f"**结论：月末再平衡；在 {decision.execution_date} 开盘把实际账户调整到本期目标。**",
                "",
                "信号使用本交易日收盘数据，因此不能在本交易日收盘前执行。",
                "策略没有盘中止损、单日跌幅阈值或非月末应急调仓规则。",
            ]
        )
    else:
        lines.extend(
            [
                "**结论：今日不调仓，继续执行最近一次月末目标。**",
                "",
                "今日运行只负责监控。即使当日出现大跌、波动率跳升或均线状态变化，",
                "已回测策略也要等到本月最后一个交易日收盘后才生成新目标。",
            ]
        )

    lines.extend(
        [
            "",
            "### 策略目标",
            "",
            "| 方案 | 上期月末目标 | 本期目标 | 目标变化 | 本次策略指令 | 短债/现金目标 |",
            "|---|---:|---:|---:|---|---:|",
        ]
    )

    for item in decision.decisions:
        instruction = (
            f"按实际账户再平衡至 {_percent(item.new_weight)}"
            if decision.is_month_end
            else "不调仓"
        )
        lines.append(
            f"| {item.symbol} | {_percent(item.current_weight)} | "
            f"{_percent(item.new_weight)} | {_percent(item.delta_weight)} | "
            f"{instruction} | {_percent(item.defensive_weight)} |"
        )

    lines.extend(
        [
            "",
            "“上期月末目标”是模型上次设定的目标，不代表账户经过月内涨跌后的实际权重。",
            "月末执行时，应以实际账户为起点交易到“本期目标”；因此系统在不知道账户持仓时不猜测具体买卖数量。",
            "",
            "### 今日监控指标",
            "",
            f"- QQQ 收盘：{today.qqq_close:,.2f}",
            f"- NASDAQ-100 收盘：{today.ndx_close:,.2f}",
            f"- 三模型开启数：{today.active_models}/{today.total_models}",
            f"- 趋势分数：{_percent(today.trend_score)}",
            f"- 21 日 NDX 年化已实现波动率：{_percent(today.realized_volatility)}",
            f"- 当前生效的月末信号日：{decision.active_signal_date}",
            f"- 本次拟议信号日：{decision.proposed_signal_date}",
            "",
            "### 今日趋势子模型",
            "",
            "| 参数 | SMA | EMA | 子模型 |",
            "|---|---|---|---|",
        ]
    )

    for trend in today.trends:
        lines.append(
            f"| {trend.fast}/{trend.slow} | "
            f"{'开启' if trend.sma_on else '关闭'} | "
            f"{'开启' if trend.ema_on else '关闭'} | "
            f"{'开启' if trend.model_on else '关闭'} |"
        )

    lines.extend(
        [
            "",
            "QLD 与 TQQQ 是同一策略的两种替代实现，不能叠加。",
            "该系统只通知，不连接券商，也不自动交易。",
        ]
    )
    return "\n".join(lines)


def write_exact_outputs(decision: monitor.DailyDecision, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "signal.json").write_text(
        json.dumps(exact_payload(decision), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_exact_markdown(decision) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    args = monitor.parse_args(argv)
    report_date = (
        args.report_date
        or monitor.latest_completed_report_date().strftime("%Y-%m-%d")
    )
    output_dir = Path(args.output_dir)
    try:
        session = monitor._http_session()
        qqq = monitor.fetch_qqq_close(session)
        ndx = monitor.fetch_ndx_close(session)
        decision = monitor.build_decision(qqq, ndx, report_date)
        write_exact_outputs(decision, output_dir)
        print(render_exact_markdown(decision))
        return 0
    except Exception as exc:
        monitor.write_error(report_date, exc, output_dir)
        print(f"Signal generation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
