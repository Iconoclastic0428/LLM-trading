from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_STRATEGY_SHA256 = "a6c67728eb23ab8f236c3cb813a695e48bf1afa82905477e7aaa26b7f90b5da3"
EXPECTED_YEARS = list(range(2000, 2027))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    for relative, expected in manifest["file_sha256"].items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing: {relative}")
        elif sha256(path) != expected:
            failures.append(f"hash mismatch: {relative}")
    if failures:
        raise AssertionError("\n".join(failures))


def verify_structure() -> None:
    strategy = ROOT / "strategy" / "candidate_strategy.py"
    if sha256(strategy) != EXPECTED_STRATEGY_SHA256:
        raise AssertionError("frozen strategy hash mismatch")

    annual = pd.read_csv(ROOT / "data" / "annual_performance.csv")
    if annual["year"].tolist() != EXPECTED_YEARS:
        raise AssertionError("annual year coverage is not 2000-2026")

    weekly = pd.read_csv(ROOT / "history" / "weekly_holdings.csv")
    if weekly.duplicated(["year", "iso_week"]).any():
        raise AssertionError("duplicate weekly snapshots")
    if int(weekly["position_count"].max()) > 5:
        raise AssertionError("weekly history exceeds five positions")
    if float(weekly["gross_target"].max()) > 1.0 + 1e-12:
        raise AssertionError("weekly history exceeds 100% gross target")

    daily = pd.read_csv(ROOT / "data" / "daily_performance.csv", parse_dates=["date"])
    for row in annual.itertuples(index=False):
        year_rows = daily.loc[daily["date"].dt.year.eq(row.year)]
        if len(year_rows) != row.sessions:
            raise AssertionError(f"session count mismatch for {row.year}")
        year_dir = ROOT / "years" / str(row.year)
        for name in ("README.md", "performance.csv", "performance.png", "weekly_holdings.csv"):
            if not year_dir.joinpath(name).is_file():
                raise AssertionError(f"missing years/{row.year}/{name}")
        curve = pd.read_csv(year_dir / "performance.csv")
        expected = {
            "strategy_return_index": row.strategy_return,
            "spy_return_index": row.spy_return,
            "qqq_return_index": row.qqq_return,
        }
        for column, value in expected.items():
            if not np.isclose(curve.iloc[-1][column], value, rtol=0.0, atol=1e-10):
                raise AssertionError(f"{row.year} {column} annual return mismatch")

    signal = json.loads((ROOT / "signals" / "current_target_after_2026-08-26.json").read_text(encoding="utf-8"))
    weights = signal["next_target"]
    if len(weights) != 5 or not np.isclose(sum(weights.values()), 1.0):
        raise AssertionError("current target is not five fully invested positions")


def main() -> None:
    verify_manifest()
    verify_structure()
    print("old5_stock_strategy package verification passed")


if __name__ == "__main__":
    main()
