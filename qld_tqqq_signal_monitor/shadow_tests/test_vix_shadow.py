from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vix_shadow as shadow


def example():
    dates = pd.bdate_range(end="2026-08-31", periods=253)
    a = 18 + 3 * np.sin(np.arange(253) / 9)
    a[-8], a[-1] = 40, 23
    return pd.Series(a, index=dates), dates


def core(f, score=0.0, vol=0.4):
    return {"signal_date": f["signal_date"], "trend_score": score,
            "realized_volatility": vol, "products": [
                {"symbol": s, "leverage": lev, "etf_weight": score * min(1, .6 / (lev * vol))}
                for s, lev in shadow.LEVERAGES.items()]}


@pytest.mark.parametrize("seed", range(12))
def test_independent_scalar_formula(seed):
    dates = pd.bdate_range(end="2026-08-31", periods=300)
    rng = np.random.default_rng(seed)
    v = pd.Series(np.exp(rng.normal(3, .25, 300)), index=dates)
    f = shadow.features(v, dates, dates[-1])
    h = max(v.iloc[-20:])
    history = v.iloc[-253:-1].tolist()
    p = sum(1 if x < h else .5 if x == h else 0 for x in history) / 252
    logs = [math.log(x) for x in v.iloc[-64:-1]]
    mean = sum(logs) / 63
    sd = math.sqrt(sum((x - mean) ** 2 for x in logs) / 62)
    r = max(0, 2 * p - 1) * math.tanh(max(0, math.log(h / v.iloc[-1]) / sd))
    assert f["peak_rank252"] == p
    assert f["log_vix_level_std63"] == pytest.approx(sd, abs=1e-14)
    assert f["c20_amplitude"] == pytest.approx(r, abs=1e-14)
    assert f["q70_amplitude"] == pytest.approx(r if p >= .7 else 0, abs=1e-14)


def test_prefix_and_future_perturbation():
    v, dates = example()
    expected = shadow.features(v, dates, dates[-1])
    future_dates = pd.bdate_range(dates[-1] + pd.Timedelta(days=1), periods=100)
    for values in ([1e9] * 100, [float("nan")] * 100, [-1] * 100):
        full = pd.concat([v, pd.Series(values, index=future_dates)])
        assert shadow.features(full, full.index, dates[-1]) == expected


def test_midrank_and_zero_scale():
    _, dates = example()
    f = shadow.features(pd.Series(20.0, index=dates), dates, dates[-1])
    assert f["peak_rank252"] == .5
    assert f["c20_amplitude"] == f["q70_amplitude"] == 0


def test_prior_windows_exclude_current():
    _, dates = example()
    v = pd.Series(np.linspace(10, 30, 253), index=dates)
    scale = np.log(v.iloc[-64:-1]).std(ddof=1)
    v.iloc[-1] = 1000
    f = shadow.features(v, dates, dates[-1])
    assert f["peak_rank252"] == 1
    assert f["peak20"] == 1000
    assert f["log_vix_level_std63"] == pytest.approx(scale)
    assert f["c20_amplitude"] == 0


@pytest.mark.parametrize("position", [0, 150, 240, 252])
@pytest.mark.parametrize("bad", [np.nan, np.inf, 0, -1])
def test_missing_or_invalid_history_never_filled(position, bad):
    v, dates = example()
    v.iloc[position] = bad
    with pytest.raises(shadow.ShadowError, match="VIX close"):
        shadow.features(v, dates, dates[-1])


def test_deleted_price_and_short_history():
    v, dates = example()
    with pytest.raises(shadow.ShadowError):
        shadow.features(v.drop(dates[40]), dates, dates[-1])
    with pytest.raises(shadow.ShadowError):
        shadow.features(v.iloc[1:], dates[1:], dates[-1])
    with pytest.raises(shadow.ShadowError):
        shadow.features(v, dates[:-1], dates[-1])


def test_duplicate_price_and_unsorted_sessions():
    v, dates = example()
    with pytest.raises(shadow.ShadowError):
        shadow.features(pd.concat([v, v.iloc[-1:]]), dates, dates[-1])
    with pytest.raises(shadow.ShadowError):
        shadow.features(v, dates[::-1], dates[-1])


@pytest.mark.parametrize("score", [1 / 3, 2 / 3, 1.0])
def test_positive_core_bitwise_unchanged(score):
    v, dates = example()
    f = shadow.features(v, dates, dates[-1])
    snapshot = core(f, score=score)
    old = copy.deepcopy(snapshot)
    result = shadow.targets(snapshot, f)
    assert snapshot == old
    for model in result:
        for p in snapshot["products"]:
            assert result[model][p["symbol"]]["hypothetical_etf_target"] == p["etf_weight"]
            assert result[model][p["symbol"]]["overlay_target"] == 0


@pytest.mark.parametrize("vol", [.1, .5, 2, 20])
def test_zero_core_respects_caps_and_funding(vol):
    v, dates = example()
    f = shadow.features(v, dates, dates[-1])
    result = shadow.targets(core(f, vol=vol), f)
    for symbol, lev in shadow.LEVERAGES.items():
        assert result["BASE"][symbol]["hypothetical_etf_target"] == 0
        for model in ("C20", "Q70"):
            w = result[model][symbol]["hypothetical_etf_target"]
            assert 0 <= w <= min(1, .5 / lev, .6 / (lev * vol))
            assert w + result[model][symbol]["defensive_target"] == 1


def test_low_rank_q70_off_c20_on():
    _, dates = example()
    a = np.r_[np.linspace(10, 40, 233), np.linspace(25, 20, 20)]
    f = shadow.features(pd.Series(a, index=dates), dates, dates[-1])
    assert .5 < f["peak_rank252"] < .7
    assert f["c20_amplitude"] > 0 and f["q70_amplitude"] == 0
    result = shadow.targets(core(f), f)
    assert result["Q70"]["QLD"]["hypothetical_etf_target"] == 0
    assert result["C20"]["QLD"]["hypothetical_etf_target"] > 0


def test_inconsistent_core_rejected():
    v, dates = example()
    f = shadow.features(v, dates, dates[-1])
    snapshot = core(f)
    snapshot["products"][0]["etf_weight"] = .1
    with pytest.raises(shadow.ShadowError):
        shadow.targets(snapshot, f)


def test_frozen_r4_goldens():
    cases = json.loads((Path(__file__).parent / "fixtures/vix_r4_goldens.json").read_text())["cases"]
    for case in cases:
        dates = pd.bdate_range(end=case["signal_date"], periods=253)
        f = shadow.features(pd.Series(case["vix"], index=dates), dates, dates[-1])
        for key, val in case["expected"].items():
            assert f[key] == pytest.approx(val, abs=1e-12), (case["signal_date"], key)


def fake_bundle(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "status.json").write_text('{"production":"untouched"}')
    v, grid = example()
    f = shadow.features(v, grid, grid[-1])
    status = {"mode": "live", "report_date": "2026-09-01"}
    audit = {"sha256": {"status.json": hashlib.sha256((base / "status.json").read_bytes()).hexdigest()},
             "timing": {"execution_date": "2026-09-01"}}
    verifier = lambda *a: (status, core(f), grid, audit)
    loader = lambda *a: (v, {"name": "test"})
    return base, tmp_path / "shadow", verifier, loader


def test_generate_is_read_only_and_non_executable(tmp_path):
    base, out, verifier, loader = fake_bundle(tmp_path)
    before = (base / "status.json").read_bytes()
    assert shadow.generate(base, out, verifier=verifier, loader=loader) == 0
    p = json.loads((out / "shadow.json").read_text())
    assert p["status"] == "ok" and p["production_strategy"] == "BASE"
    assert p["publish_notification"] is p["execution_authorized"] is False
    assert p["historical_target_only"] is True
    assert p["signal_date"] == "2026-08-31"  # never trade on September 1 close
    assert (base / "status.json").read_bytes() == before
    assert not (out / "signal.json").exists()


def test_failure_overwrites_prior_success_with_no_targets(tmp_path):
    base, out, verifier, loader = fake_bundle(tmp_path)
    shadow.generate(base, out, verifier=verifier, loader=loader)
    def broken(*a):
        raise shadow.ShadowError("source unavailable")
    assert shadow.generate(base, out, verifier=verifier, loader=broken) == 2
    p = json.loads((out / "shadow.json").read_text())
    assert p["status"] == "error" and p["models"] is None
    assert p["publish_notification"] is False
    assert "source unavailable" in (out / "report.md").read_text()


def test_disabled_does_not_read_base_or_request_data(tmp_path):
    def forbidden(*a):
        pytest.fail("disabled module accessed data")
    out = tmp_path / "shadow"
    assert shadow.generate(tmp_path / "base", out, enabled=False, verifier=forbidden, loader=forbidden) == 0
    assert json.loads((out / "shadow.json").read_text())["status"] == "disabled"


@pytest.mark.parametrize("relative", ["", "child", ".."])
def test_output_cannot_overlap_production(tmp_path, relative):
    base = tmp_path / "base"
    with pytest.raises(shadow.ShadowError):
        shadow.generate(base, base / relative, enabled=False)


def test_http_fallback_and_coverage_validation():
    v, dates = example()
    text = "observation_date,VIXCLS\n" + "\n".join(f"{d.date()},{x}" for d, x in v.items())
    class Session:
        def get(self, url, **kwargs):
            body = "DATE,CLOSE\n08/31/2026,20\n" if url == shadow.CBOE_URL else text
            return SimpleNamespace(text=body, content=body.encode(), raise_for_status=lambda: None)
    attempts = []
    result, source = shadow.fetch_vix(dates[-1], dates, attempts, session=Session())
    assert source["name"] == "FRED"
    assert len(attempts) == 3 and attempts[0]["status"] == "error"
    assert shadow.features(result, dates, dates[-1])["q70_amplitude"] > 0


def verified_real_bundle(tmp_path):
    # These integration tests run with the real production modules in GitHub CI.
    if importlib.util.find_spec("automation") is None:
        pytest.skip("production modules are not copied into the local staging directory")
    import monitor
    import automation
    now = pd.Timestamp("2026-09-02T02:00:00Z")
    report = automation.latest_session(now)
    timing = automation.monthly_timing(report, now, replay=True)
    signal = pd.Timestamp(timing["signal_date"])
    dates = monitor._normalize_sessions(monitor._calendar().sessions_in_range(report-pd.Timedelta(days=480), report))[-300:]
    frame = pd.DataFrame({"QQQ": 500.0, "NDX": 20000.0}, index=dates)
    base = tmp_path / "base"
    base.mkdir()
    frame.to_csv(base / "verified_closes.csv", index_label="date")
    status = {"status": "ok", "mode": "live", "report_date": str(report.date()),
              "completed_at": now.isoformat(),
              "data_sha256": hashlib.sha256((base / "verified_closes.csv").read_bytes()).hexdigest()}
    f = {"signal_date": str(signal.date())}
    snapshot = {**core(f, score=1, vol=.4), "qqq_close": 500.0, "ndx_close": 20000.0}
    monthly = {"report_date": str(signal.date()), "proposed_snapshot": snapshot,
               "execution_rule": "next_session_open", "intramonth_rebalance": False}
    (base / "status.json").write_text(json.dumps(status))
    (base / "month_end.json").write_text(json.dumps(monthly))
    return base, now


def test_verify_real_production_calendar(tmp_path):
    base, now = verified_real_bundle(tmp_path)
    status, snapshot, grid, audit = shadow.verify_bundle(base, now)
    assert snapshot["signal_date"] == "2026-08-31"
    assert len(grid) == 253 and str(grid[-1].date()) == "2026-08-31"
    assert status["report_date"] == "2026-09-01"


def test_verify_rejects_stale_live_bundle(tmp_path):
    base, now = verified_real_bundle(tmp_path)
    with pytest.raises(shadow.ShadowError, match="stale"):
        shadow.verify_bundle(base, now + pd.Timedelta(days=1))


def test_verify_rejects_tampered_base_bytes(tmp_path):
    base, now = verified_real_bundle(tmp_path)
    with (base / "verified_closes.csv").open("a") as fh:
        fh.write("\n")
    with pytest.raises(shadow.ShadowError, match="hash mismatch"):
        shadow.verify_bundle(base, now)
