"""Read-only BASE/C20/Q70 month-end target comparison; never a trade publisher.

The verified production month_end.json is authoritative for core targets. This
module only adds the frozen R4 VIX overlays to hypothetical, separate portfolios.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import math
import os
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

RULE_VERSION = "c20-q70-r4-shadow-v1"
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
LEVERAGES = {"QLD": 2.0, "TQQQ": 3.0}


class ShadowError(ValueError):
    """No auditable shadow target can be produced."""


def day(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is not None or stamp != stamp.normalize():
        raise ShadowError("Expected an unambiguous date without a time or timezone")
    return stamp


def dated_series(series: pd.Series, cutoff) -> pd.Series:
    """Validate only the historical prefix; future values cannot change results."""
    out = series.copy()
    out.index = pd.DatetimeIndex(out.index)
    if out.index.tz is not None or out.index.hasnans:
        raise ShadowError("Price dates must be valid and timezone-free")
    if not out.index.equals(out.index.normalize()):
        raise ShadowError("Daily prices must use dates, not intraday timestamps")
    out = out.loc[out.index <= day(cutoff)].sort_index()
    if out.index.has_duplicates:
        raise ShadowError("Duplicate historical price dates")
    return pd.to_numeric(out, errors="coerce")


def features(vix: pd.Series, sessions: pd.DatetimeIndex, signal_date) -> dict:
    """Frozen C20 and Q70, on the NDX session grid, with no forward filling.

    H20 includes t. Rank252 and sample std63(log VIX levels) exclude t. Ties
    use midrank. Missing history is unavailable, not a valid zero-strength signal.
    """
    signal = day(signal_date)
    grid = pd.DatetimeIndex(sessions)
    if grid.tz is not None or grid.hasnans or not grid.equals(grid.normalize()):
        raise ShadowError("Invalid session dates")
    grid = grid[grid <= signal]
    if grid.has_duplicates or not grid.is_monotonic_increasing:
        raise ShadowError("Session grid must be unique and increasing")
    if len(grid) < 253 or grid[-1] != signal:
        raise ShadowError("Need the signal session and its 252 previous sessions")
    window = dated_series(vix, signal).reindex(grid[-253:])
    a = window.to_numpy(dtype=float)
    if not np.isfinite(a).all() or (a <= 0).any():
        bad = window.index[~np.isfinite(a) | (a <= 0)]
        raise ShadowError("Missing/invalid VIX close(s): " + ", ".join(str(d.date()) for d in bad))
    peak = float(a[-20:].max())
    history = a[:-1]
    rank = float(((history < peak).sum() + 0.5 * (history == peak).sum()) / 252)
    scale = float(np.log(a[-64:-1]).std(ddof=1))
    z = max(0.0, math.log(peak / a[-1]) / scale) if scale > 0 else 0.0
    c20 = float(np.clip(max(0.0, 2 * rank - 1) * math.tanh(z), 0, 1))
    return {
        "signal_date": str(signal.date()), "history_start": str(window.index[0].date()),
        "vix_close": float(a[-1]), "peak20": peak, "peak_rank252": rank,
        "log_vix_level_std63": scale, "pullback_from_peak": float(1 - a[-1] / peak),
        "c20_amplitude": c20, "q70_pass": bool(rank >= 0.70),
        "q70_amplitude": c20 if rank >= 0.70 else 0.0,
        "zero_scale": bool(scale == 0),
    }


def targets(snapshot: dict, f: dict) -> dict:
    """Never edit the authoritative snapshot or increase a positive core target."""
    if snapshot["signal_date"] != f["signal_date"]:
        raise ShadowError("Core/VIX signal dates do not match")
    score, vol = float(snapshot["trend_score"]), float(snapshot["realized_volatility"])
    if not (math.isfinite(score) and 0 <= score <= 1 and math.isfinite(vol) and vol > 0):
        raise ShadowError("Invalid core score or realized volatility")
    products = snapshot["products"]
    if len(products) != 2 or {p["symbol"] for p in products} != set(LEVERAGES):
        raise ShadowError("Expected two alternative portfolios: QLD and TQQQ")
    result = {model: {} for model in ("BASE", "C20", "Q70")}
    for p in products:
        symbol = p["symbol"]
        leverage = LEVERAGES[symbol]
        base = float(p["etf_weight"])
        cap = min(1.0, 0.60 / (leverage * vol))
        if (float(p["leverage"]) != leverage or not math.isfinite(base)
                or not 0 <= base <= 1 or abs(base - score * cap) > 1e-10):
            raise ShadowError("Core target is inconsistent with the frozen strategy")
        for model in result:
            amp = 0.0 if model == "BASE" else float(f[model.lower() + "_amplitude"])
            if not math.isfinite(amp) or not 0 <= amp <= 1:
                raise ShadowError("Invalid overlay amplitude")
            extra = min(cap, 0.5 * amp / leverage) if score == 0 else 0.0
            weight = base + extra
            result[model][symbol] = {
                "hypothetical_etf_target": weight, "defensive_target": 1.0 - weight,
                "core_target": base, "overlay_target": extra,
                "index_exposure": leverage * weight, "actual_account_weight": None,
            }
    return result


def parse_vix(text: str, source: str, signal_date) -> pd.Series:
    frame = pd.read_csv(StringIO(text))
    frame.columns = [str(c).strip().upper() for c in frame.columns]
    if source == "CBOE":
        date_col, value_col = "DATE", "CLOSE"
        if date_col not in frame or value_col not in frame:
            raise ShadowError("Unexpected Cboe VIX CSV schema")
        dates = pd.to_datetime(frame[date_col], format="%m/%d/%Y", errors="coerce")
    else:
        date_col = "OBSERVATION_DATE" if "OBSERVATION_DATE" in frame else "DATE"
        value_col = "VIXCLS"
        if date_col not in frame or value_col not in frame:
            raise ShadowError("Unexpected FRED VIXCLS CSV schema")
        dates = pd.to_datetime(frame[date_col], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise ShadowError("Invalid VIX CSV date")
    values = pd.to_numeric(frame[value_col], errors="coerce")
    return dated_series(pd.Series(values.to_numpy(), index=dates, name="VIX"), signal_date)


def fetch_vix(signal_date, sessions, attempts: list, session=None) -> tuple[pd.Series, dict]:
    """Bounded source fallback; never fill a missing market close with older data."""
    session = session or requests.Session()
    for source, url in (("CBOE", CBOE_URL), ("FRED", FRED_URL)):
        for attempt in (1, 2):
            record = {"source": source, "attempt": attempt}
            attempts.append(record)
            try:
                response = session.get(url, timeout=(10, 30), headers={
                    "User-Agent": "q70-shadow-validation/1.0", "Cache-Control": "no-cache"})
                response.raise_for_status()
                if len(response.content) > 5_000_000:
                    raise ShadowError("Unexpectedly large VIX CSV")
                series = parse_vix(response.text, source, signal_date)
                features(series, sessions, signal_date)  # coverage before accepting fallback
                record["status"] = "ok"
                return series, {"name": source, "url": url,
                    "raw_sha256": hashlib.sha256(response.content).hexdigest(),
                    "latest_used_date": str(day(signal_date).date())}
            except (requests.RequestException, ValueError, KeyError) as exc:
                record.update(status="error", error=str(exc)[:400])
    raise ShadowError("No source supplied a complete, valid VIX history for the signal date")


def verify_bundle(base_dir: Path, now=None) -> tuple[dict, dict, pd.DatetimeIndex, dict]:
    """Read current-run production artifacts; never re-seed/recompute core EMAs."""
    # Lazy import keeps signal mathematics independent and reuses the exact calendar.
    from automation import latest_session, monthly_timing, utc
    import monitor

    names = ("status.json", "month_end.json", "verified_closes.csv")
    bodies = {name: (base_dir / name).read_bytes() for name in names}
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
    status = json.loads(bodies["status.json"])
    monthly = json.loads(bodies["month_end.json"])
    if status.get("status") != "ok" or status.get("mode") not in ("live", "replay"):
        raise ShadowError("Production data verification did not succeed")
    if status.get("data_sha256") != hashes["verified_closes.csv"]:
        raise ShadowError("Verified production close-file hash mismatch")
    current = utc(now)
    report = day(status["report_date"])
    expected = latest_session(current)
    if report > expected or (status["mode"] == "live" and report != expected):
        raise ShadowError("Production bundle is stale or refers to an incomplete session")
    finished = utc(status["completed_at"])
    if status["mode"] == "live" and not pd.Timedelta(0) <= current - finished <= pd.Timedelta(hours=2):
        raise ShadowError("Live production artifacts must come from this recent run")
    timing = monthly_timing(report, current, replay=True)
    signal = day(timing["signal_date"])
    snapshot = monthly.get("proposed_snapshot")
    if (not snapshot or snapshot.get("signal_date") != timing["signal_date"]
            or monthly.get("report_date") != timing["signal_date"]
            or monthly.get("execution_rule") != "next_session_open"
            or monthly.get("intramonth_rebalance") is not False):
        raise ShadowError("Production month-end snapshot/schema mismatch")
    frame = pd.read_csv(StringIO(bodies["verified_closes.csv"].decode()), index_col="date", parse_dates=True)
    if not {"NDX", "QQQ"}.issubset(frame.columns):
        raise ShadowError("Verified core close-file schema mismatch")
    if signal not in frame.index or frame.index.has_duplicates:
        raise ShadowError("Signal session missing or duplicated in verified closes")
    for name, field in (("NDX", "ndx_close"), ("QQQ", "qqq_close")):
        if not math.isclose(float(frame.loc[signal, name]), float(snapshot[field]), rel_tol=1e-10):
            raise ShadowError("Month-end snapshot does not match verified core closes")
    cal = monitor._calendar()
    grid = monitor._normalize_sessions(cal.sessions_in_range(signal - pd.Timedelta(days=450), signal))[-253:]
    # Dropping a missing index row would shorten the VIX windows and change R4 rules.
    core_grid = frame.loc[:signal].tail(253)
    if not core_grid.index.equals(grid) or core_grid[["QQQ", "NDX"]].isna().any().any():
        raise ShadowError("Verified NDX/QQQ dates do not match the complete session grid")
    return status, snapshot, grid, {"sha256": hashes, "timing": timing}


def _atomic_json(path: Path, payload: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def render(payload: dict) -> str:
    lines = ["## BASE / C20 / Q70 影子验证", "",
             "**仅比较模型月末目标；正式策略仍为 BASE。不发布调仓通知，不连接券商，不自动下单。**", ""]
    if payload["status"] != "ok":
        lines.append(f'状态：{payload["status"]}。{payload.get("error", "影子模块已关闭。" if payload["status"] == "disabled" else "比较未完成，不可使用历史输出。")}')
        return "\n".join(lines) + "\n"
    lines += [f'已核验报告日 {payload["report_date"]}；比较的月末信号日 {payload["signal_date"]}；'
              f'原规则的参考执行日 {payload["reference_execution_date"]}。',
              "这些是月末设定的目标，不是随价格漂移后的实际持仓；历史目标不得追溯补单。", "",
              "| 替代账户 | BASE ETF目标 | C20 ETF目标 | Q70 ETF目标 | Q70增强仓 |",
              "|---|---:|---:|---:|---:|"]
    for symbol in LEVERAGES:
        models = payload["models"]
        vals = [models[m][symbol]["hypothetical_etf_target"] for m in ("BASE", "C20", "Q70")]
        lines.append(f'| {symbol} | {vals[0]:.4%} | {vals[1]:.4%} | {vals[2]:.4%} | '
                     f'{models["Q70"][symbol]["overlay_target"]:.4%} |')
    f = payload["vix_features"]
    lines += ["", f'VIX {f["vix_close"]:.2f}；20日峰值 {f["peak20"]:.2f}；'
              f'峰值历史分位 {f["peak_rank252"]:.2%}；Q70门槛通过 {f["q70_pass"]}。',
              f'核心得分 {payload["core_score"]:.4f}；C20强度 {f["c20_amplitude"]:.6f}；'
              f'Q70强度 {f["q70_amplitude"]:.6f}。',
              "QLD与TQQQ为两个替代账户，不把两者目标相加。仅核心得分为零时允许增强。",
              "未模拟成交、费用或资金净值，不能把本报告当作已实现收益或前瞻回测。",
              f'规则版本 `{RULE_VERSION}`；VIX来源 {payload["vix_source"]["name"]}。']
    return "\n".join(lines) + "\n"


def generate(base_dir: Path, output_dir: Path, *, enabled=True, now=None,
             verifier: Callable = verify_bundle, loader: Callable = fetch_vix) -> int:
    base_dir, output_dir = Path(base_dir).resolve(), Path(output_dir).resolve()
    if base_dir == output_dir or base_dir in output_dir.parents or output_dir in base_dir.parents:
        raise ShadowError("Shadow output must be separate from production output")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"status": "disabled" if not enabled else "running", "mode": "shadow_only",
               "rule_version": RULE_VERSION, "production_strategy": "BASE",
               "publish_notification": False, "execution_authorized": False,
               "intramonth_rebalance": False, "models": None, "attempts": [],
               "run_id": os.environ.get("GITHUB_RUN_ID"),
               "commit_sha": os.environ.get("GITHUB_SHA")}
    _atomic_json(output_dir / "shadow.json", payload)
    (output_dir / "report.md").write_text(render(payload), encoding="utf-8")
    (output_dir / "vix_used.csv").unlink(missing_ok=True)
    code = 0
    try:
        if enabled:
            status, snapshot, grid, audit = verifier(base_dir, now)
            signal = snapshot["signal_date"]
            vix, source = loader(signal, grid, payload["attempts"])
            f = features(vix, grid, signal)
            result = targets(snapshot, f)
            used = dated_series(vix, signal).reindex(grid[-253:])
            used.to_csv(output_dir / "vix_used.csv", index_label="date", header=["VIX"])
            payload.update(status="ok", source_mode=status["mode"], report_date=status["report_date"],
                           signal_date=signal, reference_execution_date=audit["timing"]["execution_date"],
                           historical_target_only=True, core_score=snapshot["trend_score"],
                           vix_features=f, vix_source=source, models=result, production_audit=audit,
                           vix_window_sha256=hashlib.sha256((output_dir / "vix_used.csv").read_bytes()).hexdigest())
            # Never mutate the base bundle, even when a provider or future edit misbehaves.
            for name, digest in audit["sha256"].items():
                if hashlib.sha256((base_dir / name).read_bytes()).hexdigest() != digest:
                    raise ShadowError("Production artifacts changed during shadow comparison")
    except Exception as exc:
        code = 2
        payload.update(status="error", error=str(exc), models=None)
    payload["generated_at"] = (pd.Timestamp(now).isoformat() if now is not None
                               else datetime.now(timezone.utc).isoformat())
    _atomic_json(output_dir / "shadow.json", payload)
    text = render(payload)
    (output_dir / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default="signal_output")
    parser.add_argument("--output-dir", default="shadow_output")
    args = parser.parse_args(argv)
    flag = os.environ.get("VIX_SHADOW_ENABLED", "true").lower()
    if flag not in ("true", "false"):
        parser.error("VIX_SHADOW_ENABLED must be true or false")
    return generate(Path(args.base_dir), Path(args.output_dir), enabled=flag == "true")


if __name__ == "__main__":
    raise SystemExit(main())
