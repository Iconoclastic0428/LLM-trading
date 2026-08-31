from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from io import StringIO
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

FRED_NDX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQ100"
YAHOO_QQQ_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=max&interval=1d&events=history&includeAdjustedClose=true",
    "https://query2.finance.yahoo.com/v8/finance/chart/QQQ?range=max&interval=1d&events=history&includeAdjustedClose=true",
)
MARKET_TIMEZONE = "America/New_York"
CALENDAR_NAME = "XNYS"
TREND_PAIRS: tuple[tuple[int, int], ...] = ((50, 200), (55, 220), (60, 240))
VOLATILITY_WINDOW = 21
TARGET_ANNUALIZED_VOLATILITY = 0.60
PRODUCTS: tuple[tuple[str, float], ...] = (("QLD", 2.0), ("TQQQ", 3.0))
MIN_OBSERVATIONS = 500
ISSUE_MENTION = "@Iconoclastic0428"


class MonitorError(RuntimeError):
    """Raised when no safe, auditable signal can be generated."""


@dataclass(frozen=True)
class TrendState:
    fast: int
    slow: int
    sma_fast: float
    sma_slow: float
    ema_fast: float
    ema_slow: float
    sma_on: bool
    ema_on: bool
    model_on: bool


@dataclass(frozen=True)
class ProductTarget:
    symbol: str
    leverage: float
    etf_weight: float
    defensive_weight: float
    index_exposure: float


@dataclass(frozen=True)
class Snapshot:
    signal_date: str
    qqq_close: float
    ndx_close: float
    realized_volatility: float
    active_models: int
    total_models: int
    trend_score: float
    trends: tuple[TrendState, ...]
    products: tuple[ProductTarget, ...]


@dataclass(frozen=True)
class ProductDecision:
    symbol: str
    current_weight: float
    new_weight: float
    delta_weight: float
    defensive_weight: float
    action: str


@dataclass(frozen=True)
class DailyDecision:
    report_date: str
    market_status: str
    is_month_end: bool
    active_signal_date: str | None
    proposed_signal_date: str | None
    execution_date: str | None
    active_snapshot: Snapshot | None
    today_snapshot: Snapshot | None
    proposed_snapshot: Snapshot | None
    decisions: tuple[ProductDecision, ...]
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _http_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "qld-tqqq-signal-monitor/1.0 "
                "(notification-only quantitative model; GitHub issue #1)"
            )
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _validate_series(series: pd.Series, label: str) -> pd.Series:
    series = pd.Series(series, dtype=float).dropna().sort_index()
    index = pd.DatetimeIndex(series.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    series.index = index.normalize()
    if len(series) < MIN_OBSERVATIONS:
        raise MonitorError(
            f"{label} only returned {len(series)} valid rows; {MIN_OBSERVATIONS} are required."
        )
    if series.index.has_duplicates:
        raise MonitorError(f"{label} contains duplicate dates.")
    if not series.index.is_monotonic_increasing:
        raise MonitorError(f"{label} dates are not increasing.")
    if not np.isfinite(series.to_numpy()).all() or not (series > 0).all():
        raise MonitorError(f"{label} contains an invalid or non-positive close.")
    return series


def fetch_ndx_close(session: requests.Session | None = None) -> pd.Series:
    session = session or _http_session()
    try:
        response = session.get(FRED_NDX_URL, timeout=(10, 45))
    except requests.RequestException as exc:
        raise MonitorError(f"FRED NASDAQ-100 request failed: {exc}") from exc
    if response.status_code != 200:
        raise MonitorError(f"FRED NASDAQ-100 returned HTTP {response.status_code}.")
    try:
        frame = pd.read_csv(StringIO(response.text))
    except Exception as exc:
        raise MonitorError(f"FRED NASDAQ-100 CSV could not be parsed: {exc}") from exc
    date_column = next(
        (name for name in ("observation_date", "DATE", "date") if name in frame.columns),
        None,
    )
    if date_column is None or "NASDAQ100" not in frame.columns:
        raise MonitorError(f"Unexpected FRED schema: {list(frame.columns)!r}.")
    dates = pd.to_datetime(frame[date_column], errors="coerce")
    values = pd.to_numeric(frame["NASDAQ100"], errors="coerce")
    series = pd.Series(values.to_numpy(), index=dates, name="NDX")
    return _validate_series(series, "FRED NASDAQ-100")


def fetch_qqq_close(session: requests.Session | None = None) -> pd.Series:
    session = session or _http_session()
    errors: list[str] = []
    for url in YAHOO_QQQ_URLS:
        try:
            response = session.get(url, timeout=(10, 45))
        except requests.RequestException as exc:
            errors.append(f"request failed: {exc}")
            continue
        if response.status_code != 200:
            errors.append(f"HTTP {response.status_code}")
            continue
        try:
            payload = response.json()
            chart = payload["chart"]
            if chart.get("error") is not None:
                raise ValueError(str(chart["error"]))
            result = chart["result"][0]
            timestamps = result["timestamp"]
            closes = result["indicators"]["quote"][0]["close"]
            if len(timestamps) != len(closes):
                raise ValueError("timestamp/close length mismatch")
            dates = (
                pd.to_datetime(timestamps, unit="s", utc=True)
                .tz_convert(MARKET_TIMEZONE)
                .tz_localize(None)
                .normalize()
            )
            values = pd.to_numeric(pd.Series(closes), errors="coerce")
            # Use quote.close rather than adjclose so cash distributions do not
            # alter the price-only trend signal used by the workbook.
            series = pd.Series(values.to_numpy(), index=dates, name="QQQ")
            return _validate_series(series, "Yahoo QQQ")
        except Exception as exc:
            errors.append(f"invalid response: {exc}")
    raise MonitorError("Yahoo QQQ failed on both endpoints: " + "; ".join(errors))


def validate_cross_source(qqq: pd.Series, ndx: pd.Series, report_date: pd.Timestamp) -> None:
    if report_date not in qqq.index:
        raise MonitorError(
            f"QQQ data for {report_date.date()} is unavailable; latest is {qqq.index.max().date()}."
        )
    if report_date not in ndx.index:
        raise MonitorError(
            f"NASDAQ-100 data for {report_date.date()} is unavailable; latest is {ndx.index.max().date()}."
        )
    common = pd.concat(
        [qqq.pct_change().rename("qqq"), ndx.pct_change().rename("ndx")], axis=1
    ).dropna().loc[:report_date].tail(60)
    if len(common) < 40:
        raise MonitorError("Insufficient overlapping QQQ/NDX rows for source validation.")
    correlation = float(common["qqq"].corr(common["ndx"]))
    if not math.isfinite(correlation) or correlation < 0.95:
        raise MonitorError(
            f"QQQ/NDX trailing return correlation is {correlation:.4f}; source validation failed."
        )


def seeded_ema(series: pd.Series, span: int) -> pd.Series:
    if span <= 0:
        raise ValueError("EMA span must be positive.")
    values = series.to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < span:
        return pd.Series(result, index=series.index, name=f"EMA{span}")
    result[span - 1] = float(np.mean(values[:span]))
    alpha = 2.0 / (span + 1.0)
    for index in range(span, len(values)):
        result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
    return pd.Series(result, index=series.index, name=f"EMA{span}")


def snapshot_on(
    qqq_close: pd.Series,
    ndx_close: pd.Series,
    signal_date: str | pd.Timestamp,
    trend_pairs: Iterable[tuple[int, int]] = TREND_PAIRS,
    volatility_window: int = VOLATILITY_WINDOW,
    target_volatility: float = TARGET_ANNUALIZED_VOLATILITY,
) -> Snapshot:
    date_value = pd.Timestamp(signal_date).tz_localize(None).normalize()
    qqq = qqq_close.loc[:date_value]
    ndx = ndx_close.loc[:date_value]
    if qqq.empty or qqq.index[-1] != date_value:
        raise MonitorError(f"QQQ close is missing on {date_value.date()}.")
    if ndx.empty or ndx.index[-1] != date_value:
        raise MonitorError(f"NASDAQ-100 close is missing on {date_value.date()}.")

    pairs = tuple(trend_pairs)
    required = max(slow for _, slow in pairs)
    if len(qqq) < required:
        raise MonitorError(f"Only {len(qqq)} QQQ rows; {required} are required.")

    trends: list[TrendState] = []
    for fast, slow in pairs:
        if fast >= slow:
            raise MonitorError(f"Invalid trend pair: {fast}/{slow}.")
        sma_fast = float(qqq.rolling(fast).mean().iloc[-1])
        sma_slow = float(qqq.rolling(slow).mean().iloc[-1])
        ema_fast = float(seeded_ema(qqq, fast).iloc[-1])
        ema_slow = float(seeded_ema(qqq, slow).iloc[-1])
        values = (sma_fast, sma_slow, ema_fast, ema_slow)
        if not all(math.isfinite(value) for value in values):
            raise MonitorError(f"Incomplete trend indicators for {fast}/{slow}.")
        sma_on = sma_fast > sma_slow
        ema_on = ema_fast > ema_slow
        trends.append(
            TrendState(
                fast=fast,
                slow=slow,
                sma_fast=sma_fast,
                sma_slow=sma_slow,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                sma_on=sma_on,
                ema_on=ema_on,
                model_on=sma_on or ema_on,
            )
        )

    log_returns = np.log(ndx / ndx.shift(1))
    realized_volatility = float(
        log_returns.rolling(volatility_window).std(ddof=1).iloc[-1] * math.sqrt(252.0)
    )
    if not math.isfinite(realized_volatility) or realized_volatility <= 0:
        raise MonitorError("21-day realized volatility is unavailable or non-positive.")

    active_models = sum(state.model_on for state in trends)
    trend_score = active_models / len(trends)
    products: list[ProductTarget] = []
    for symbol, leverage in PRODUCTS:
        index_exposure = trend_score * min(
            leverage, target_volatility / realized_volatility
        )
        etf_weight = float(np.clip(index_exposure / leverage, 0.0, 1.0))
        products.append(
            ProductTarget(
                symbol=symbol,
                leverage=leverage,
                etf_weight=etf_weight,
                defensive_weight=1.0 - etf_weight,
                index_exposure=float(index_exposure),
            )
        )

    return Snapshot(
        signal_date=date_value.strftime("%Y-%m-%d"),
        qqq_close=float(qqq.iloc[-1]),
        ndx_close=float(ndx.iloc[-1]),
        realized_volatility=realized_volatility,
        active_models=active_models,
        total_models=len(trends),
        trend_score=trend_score,
        trends=tuple(trends),
        products=tuple(products),
    )


def _calendar() -> object:
    return xcals.get_calendar(CALENDAR_NAME)


def _normalize_sessions(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(index)
    if index.tz is not None:
        index = index.tz_localize(None)
    return index.normalize()


def latest_completed_report_date(now: datetime | None = None) -> pd.Timestamp:
    zone = ZoneInfo(MARKET_TIMEZONE)
    current = now.astimezone(zone) if now else datetime.now(zone)
    close_buffer = datetime.combine(current.date(), time(16, 30), tzinfo=zone)
    chosen = current.date() if current >= close_buffer else current.date() - timedelta(days=1)
    return pd.Timestamp(chosen)


def _month_end_sessions(start: pd.Timestamp, report: pd.Timestamp) -> pd.DatetimeIndex:
    calendar = _calendar()
    sessions = _normalize_sessions(
        calendar.sessions_in_range(start, report + pd.offsets.MonthEnd(1))
    )
    frame = pd.DataFrame(index=sessions)
    month_ends = frame.groupby(frame.index.to_period("M")).tail(1).index
    return month_ends[month_ends <= report]


def _next_session(session: pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(_calendar().next_session(session))
    if result.tz is not None:
        result = result.tz_localize(None)
    return result.normalize()


def _target_map(snapshot: Snapshot) -> dict[str, ProductTarget]:
    return {product.symbol: product for product in snapshot.products}


def _action(current: float, new: float) -> str:
    tolerance = 1e-10
    delta = new - current
    if abs(delta) <= tolerance:
        return "持有"
    if new <= tolerance:
        return "全部卖出并转入短债/现金"
    if current <= tolerance:
        return "买入"
    return "增持" if delta > 0 else "减持"


def build_decision(
    qqq_close: pd.Series,
    ndx_close: pd.Series,
    report_date: str | date | datetime | pd.Timestamp,
) -> DailyDecision:
    report = pd.Timestamp(report_date).tz_localize(None).normalize()
    calendar = _calendar()
    if not calendar.is_session(report):
        return DailyDecision(
            report_date=report.strftime("%Y-%m-%d"),
            market_status="closed",
            is_month_end=False,
            active_signal_date=None,
            proposed_signal_date=None,
            execution_date=None,
            active_snapshot=None,
            today_snapshot=None,
            proposed_snapshot=None,
            decisions=tuple(),
            message="该日不是美国股票市场交易日；无交易指令。",
        )

    validate_cross_source(qqq_close, ndx_close, report)
    month_ends = _month_end_sessions(max(qqq_close.index.min(), ndx_close.index.min()), report)
    if len(month_ends) == 0:
        raise MonitorError("No completed month-end signal exists.")

    is_month_end = report in month_ends
    today_snapshot = snapshot_on(qqq_close, ndx_close, report)
    if is_month_end:
        previous = month_ends[month_ends < report]
        if len(previous) == 0:
            raise MonitorError("A previous month-end target is required.")
        active_signal = previous[-1]
        proposed_signal = report
        execution = _next_session(report)
    else:
        active_signal = month_ends[-1]
        proposed_signal = active_signal
        execution = None

    active_snapshot = snapshot_on(qqq_close, ndx_close, active_signal)
    proposed_snapshot = today_snapshot if proposed_signal == report else active_snapshot
    current_targets = _target_map(active_snapshot)
    new_targets = _target_map(proposed_snapshot)
    decisions: list[ProductDecision] = []
    for symbol, _ in PRODUCTS:
        current = current_targets[symbol]
        new = new_targets[symbol]
        decisions.append(
            ProductDecision(
                symbol=symbol,
                current_weight=current.etf_weight,
                new_weight=new.etf_weight,
                delta_weight=new.etf_weight - current.etf_weight,
                defensive_weight=new.defensive_weight,
                action=_action(current.etf_weight, new.etf_weight),
            )
        )

    if is_month_end:
        message = (
            f"月末收盘信号已生成；按回测规则在 {execution.strftime('%Y-%m-%d')} "
            "下一交易日开盘执行。QLD 与 TQQQ 是两个替代方案，不能叠加。"
        )
    else:
        message = (
            "今日不是月末再平衡日；继续执行最近一次月末目标。"
            "今日指标只作监控，不把已回测的月度策略改成每日调仓。"
        )

    return DailyDecision(
        report_date=report.strftime("%Y-%m-%d"),
        market_status="open",
        is_month_end=is_month_end,
        active_signal_date=active_signal.strftime("%Y-%m-%d"),
        proposed_signal_date=proposed_signal.strftime("%Y-%m-%d"),
        execution_date=execution.strftime("%Y-%m-%d") if execution is not None else None,
        active_snapshot=active_snapshot,
        today_snapshot=today_snapshot,
        proposed_snapshot=proposed_snapshot,
        decisions=tuple(decisions),
        message=message,
    )


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_markdown(decision: DailyDecision) -> str:
    marker = f"<!-- qld-tqqq-signal:{decision.report_date}:success -->"
    lines = [
        marker,
        f"## QLD/TQQQ 模型信号：{decision.report_date}",
        "",
        ISSUE_MENTION,
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
        lines.append(
            f"**结论：月末再平衡；在 {decision.execution_date} 开盘按下表执行。**"
        )
    else:
        lines.append("**结论：继续持有最近一次月末目标。**")
    lines.extend(
        [
            "",
            decision.message,
            "",
            "### 执行目标",
            "",
            "| 方案 | 当前模型权重 | 新模型权重 | 变化 | 操作 | 短债/现金 |",
            "|---|---:|---:|---:|---|---:|",
        ]
    )
    for item in decision.decisions:
        lines.append(
            f"| {item.symbol} | {_percent(item.current_weight)} | "
            f"{_percent(item.new_weight)} | {_percent(item.delta_weight)} | "
            f"{item.action} | {_percent(item.defensive_weight)} |"
        )
    lines.extend(
        [
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
            "QLD 与 TQQQ 是同一策略的两种独立实现。操作相对于上一模型目标计算，"
            "并未读取你的实际账户仓位。该系统只通知，不自动交易。",
        ]
    )
    return "\n".join(lines)


def write_outputs(decision: DailyDecision, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "signal.json").write_text(
        json.dumps(decision.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_markdown(decision) + "\n", encoding="utf-8"
    )


def write_error(report_date: str, error: Exception, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"status": "error", "report_date": report_date, "error": str(error)}
    (output_dir / "signal.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = "\n".join(
        [
            f"<!-- qld-tqqq-signal:{report_date}:error -->",
            f"## QLD/TQQQ 模型信号：{report_date}",
            "",
            ISSUE_MENTION,
            "",
            "**结论：数据校验失败，拒绝生成交易指令。**",
            "",
            f"错误：`{error}`",
            "",
            "为避免错误交易，本次没有沿用旧价格、代理价格或猜测值。",
        ]
    )
    (output_dir / "report.md").write_text(report + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the audited QLD/TQQQ month-end trend-volatility signal."
    )
    parser.add_argument(
        "--report-date", help="YYYY-MM-DD; defaults to latest completed U.S. close"
    )
    parser.add_argument("--output-dir", default="signal_output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report_date = args.report_date or latest_completed_report_date().strftime("%Y-%m-%d")
    output_dir = Path(args.output_dir)
    try:
        session = _http_session()
        qqq = fetch_qqq_close(session)
        ndx = fetch_ndx_close(session)
        decision = build_decision(qqq, ndx, report_date)
        write_outputs(decision, output_dir)
        print(render_markdown(decision))
        return 0
    except Exception as exc:
        write_error(report_date, exc, output_dir)
        print(f"Signal generation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
