from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark.audits import run_all_audits
from benchmark.backtest import (
    equal_weight_targets,
    fully_invested_selection_targets,
    run_backtest,
)
from benchmark.data import load_all_visible_market_data
from benchmark.metrics import benchmark_buy_and_hold_daily, metrics_from_daily
from benchmark.rules import BenchmarkRules
from strategy.candidate_strategy import CandidateStrategy


ROOT = Path(__file__).resolve().parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_visible_files() -> None:
    manifest_path = ROOT / "data" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("prepared dataset is missing; run the author dataset builder")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest["file_sha256"].items():
        actual = sha256_file(ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"visible data hash mismatch: {relative}")


def strategy_factory() -> CandidateStrategy:
    return CandidateStrategy()


def interval_metrics(daily, rules: BenchmarkRules, first_bar: int, last_bar: int) -> dict[str, Any]:
    interval = daily.loc[(daily.index >= first_bar) & (daily.index <= last_bar)]
    return metrics_from_daily(interval, rules)


def active_interval_metrics(daily, rules: BenchmarkRules, first_bar: int, last_bar: int) -> dict[str, Any] | None:
    interval = daily.loc[(daily.index >= first_bar) & (daily.index <= last_bar)]
    interval = interval.loc[interval["gross_exposure"] > 1e-8]
    return metrics_from_daily(interval, rules) if len(interval) >= 2 else None


def run(skip_audits: bool = False) -> dict[str, Any]:
    verify_visible_files()
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    rules = BenchmarkRules.from_json(ROOT / "config" / "benchmark.json")
    episodes = load_all_visible_market_data(ROOT / "data")
    selection_rules = replace(
        rules,
        max_positions=max(len(data.asset_ids) for data in episodes.values()),
        max_weight_per_asset=1.0,
    )

    output: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_sha256": sha256_file(ROOT / "strategy" / "candidate_strategy.py"),
        "rules": rules.__dict__,
        "audits": {},
        "splits": {},
    }
    for episode_name, data in episodes.items():
        output["audits"][episode_name] = (
            {"status": "skipped"}
            if skip_audits
            else run_all_audits(strategy_factory, data, rules)
        )
        raw_weights = strategy_factory().generate_target_weights(data)
        candidate = run_backtest(data, raw_weights, rules)
        selection = run_backtest(
            data,
            fully_invested_selection_targets(data, raw_weights, rules),
            selection_rules,
        )
        equal_weight = run_backtest(
            data,
            equal_weight_targets(data, selection_rules),
            selection_rules,
        )
        benchmark = benchmark_buy_and_hold_daily(candidate.daily, rules)

        for split_name in manifest["episodes"][episode_name]["scored_splits"]:
            split = manifest["splits"][split_name]
            first_bar = int(split["first_bar_id"])
            last_bar = int(split["last_bar_id"])
            output["splits"][split_name] = {
                "episode": episode_name,
                "candidate": interval_metrics(candidate.daily, rules, first_bar, last_bar),
                "fully_invested_selection_sleeve": interval_metrics(
                    selection.daily,
                    selection_rules,
                    first_bar,
                    last_bar,
                ),
                "selection_sleeve_active_sessions": active_interval_metrics(
                    selection.daily,
                    selection_rules,
                    first_bar,
                    last_bar,
                ),
                "equal_weight_universe": interval_metrics(
                    equal_weight.daily,
                    selection_rules,
                    first_bar,
                    last_bar,
                ),
                "benchmark_buy_hold": interval_metrics(benchmark, rules, first_bar, last_bar),
            }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-audits", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    result = run(skip_audits=args.skip_audits)
    payload = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
    print(payload)
    if args.json_out:
        args.json_out.write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
