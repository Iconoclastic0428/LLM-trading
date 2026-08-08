from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .rules import BenchmarkRules


def _finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def _longest_false_run(mask: pd.Series) -> int:
    longest = 0
    current = 0
    for value in mask.astype(bool):
        if value:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def metrics_from_daily(daily: pd.DataFrame, rules: BenchmarkRules) -> dict[str, Any]:
    if daily.empty:
        raise ValueError("metric interval contains no returns")
    returns = daily["net_return"].astype(float)
    benchmark = daily["benchmark_return"].astype(float)
    annual = float(rules.annualization_sessions)
    observations = len(returns)
    total_return = float((1.0 + returns).prod() - 1.0)
    years = observations / annual
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 and years > 0 else -1.0
    volatility = float(returns.std(ddof=1) * math.sqrt(annual)) if observations > 1 else 0.0
    mean_annual = float(returns.mean() * annual)
    sharpe = mean_annual / volatility if volatility > 0 else float("nan")
    downside = returns.clip(upper=0.0)
    downside_deviation = float(math.sqrt((downside.pow(2).mean())) * math.sqrt(annual))
    sortino = mean_annual / downside_deviation if downside_deviation > 0 else float("nan")

    equity = (1.0 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(-drawdown.min())
    calmar = cagr / max_drawdown if max_drawdown > 0 else float("nan")

    benchmark_variance = float(benchmark.var(ddof=1))
    return_variance = float(returns.var(ddof=1))
    if benchmark_variance > 0 and observations > 1:
        covariance = float(np.cov(returns, benchmark, ddof=1)[0, 1])
        beta = covariance / benchmark_variance
        alpha_annual = float((returns.mean() - beta * benchmark.mean()) * annual)
        if return_variance > 0:
            correlation = float(returns.corr(benchmark))
            r_squared = correlation * correlation if math.isfinite(correlation) else float("nan")
        else:
            r_squared = float("nan")
    else:
        beta = float("nan")
        alpha_annual = float("nan")
        r_squared = float("nan")

    active = returns - benchmark
    tracking_error = float(active.std(ddof=1) * math.sqrt(annual)) if observations > 1 else 0.0
    information_ratio = float(active.mean() * annual / tracking_error) if tracking_error > 0 else float("nan")
    positive = returns[returns > 0].sum()
    negative = -returns[returns < 0].sum()
    profit_factor = float(positive / negative) if negative > 0 else float("nan")
    active_mask = daily["gross_exposure"].astype(float) > 1e-8

    return {
        "observations": observations,
        "total_return": _finite_or_none(total_return),
        "cagr": _finite_or_none(cagr),
        "annualized_volatility": _finite_or_none(volatility),
        "sharpe": _finite_or_none(sharpe),
        "sortino": _finite_or_none(sortino),
        "max_drawdown": _finite_or_none(max_drawdown),
        "calmar": _finite_or_none(calmar),
        "annualized_alpha": _finite_or_none(alpha_annual),
        "beta": _finite_or_none(beta),
        "r_squared": _finite_or_none(r_squared),
        "information_ratio": _finite_or_none(information_ratio),
        "profit_factor": _finite_or_none(profit_factor),
        "positive_day_fraction": _finite_or_none(float((returns > 0).mean())),
        "average_gross_exposure": _finite_or_none(float(daily["gross_exposure"].mean())),
        "active_session_fraction": _finite_or_none(float(active_mask.mean())),
        "maximum_consecutive_cash_sessions": _longest_false_run(active_mask),
        "average_positions": _finite_or_none(float(daily["position_count"].mean())),
        "annualized_turnover": _finite_or_none(float(daily["turnover"].sum() / years)),
        "modeled_cost_fraction": _finite_or_none(float(daily["trading_cost"].sum())),
    }


def benchmark_buy_and_hold_daily(daily: pd.DataFrame, rules: BenchmarkRules) -> pd.DataFrame:
    out = daily.copy()
    returns = out["benchmark_return"].astype(float).copy()
    if len(returns):
        returns.iloc[0] = (1.0 - rules.one_way_cost_rate) * (1.0 + returns.iloc[0]) - 1.0
    out["net_return"] = returns
    out["gross_return"] = out["benchmark_return"]
    out["trading_cost"] = 0.0
    if len(out):
        out.iloc[0, out.columns.get_loc("trading_cost")] = rules.one_way_cost_rate
    out["turnover"] = 0.0
    if len(out):
        out.iloc[0, out.columns.get_loc("turnover")] = 1.0
    out["gross_exposure"] = 1.0
    out["position_count"] = 1
    out["equity"] = (1.0 + out["net_return"]).cumprod()
    return out
