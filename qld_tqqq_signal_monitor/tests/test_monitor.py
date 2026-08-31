from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("signal_monitor", ROOT / "monitor.py")
assert SPEC is not None and SPEC.loader is not None
monitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)


def synthetic_prices() -> tuple[pd.Series, pd.Series]:
    dates = pd.bdate_range("2023-01-03", periods=900)
    t = np.arange(len(dates), dtype=float)
    ndx = pd.Series(
        12000.0 * np.exp(0.00045 * t + 0.012 * np.sin(t / 17)), index=dates
    )
    qqq = pd.Series(
        300.0 * np.exp(0.00043 * t + 0.011 * np.sin(t / 17)), index=dates
    )
    return qqq, ndx


def test_seeded_ema_uses_initial_sma() -> None:
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = monitor.seeded_ema(series, 3)
    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[3] == pytest.approx(3.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_snapshot_returns_bounded_weights() -> None:
    qqq, ndx = synthetic_prices()
    snapshot = monitor.snapshot_on(qqq, ndx, qqq.index[-1])
    assert 0 <= snapshot.trend_score <= 1
    assert 0 < snapshot.realized_volatility < 2
    assert {item.symbol for item in snapshot.products} == {"QLD", "TQQQ"}
    assert all(0 <= item.etf_weight <= 1 for item in snapshot.products)
    assert all(
        item.etf_weight + item.defensive_weight == pytest.approx(1)
        for item in snapshot.products
    )


def test_cross_source_rejects_wrong_data() -> None:
    qqq, _ = synthetic_prices()
    bad_ndx = 1_000_000.0 / qqq
    with pytest.raises(monitor.MonitorError, match="correlation"):
        monitor.validate_cross_source(qqq, bad_ndx, qqq.index[-1])


def test_holiday_has_no_instruction() -> None:
    qqq, ndx = synthetic_prices()
    decision = monitor.build_decision(qqq, ndx, "2026-09-07")
    assert decision.market_status == "closed"
    assert decision.decisions == tuple()
