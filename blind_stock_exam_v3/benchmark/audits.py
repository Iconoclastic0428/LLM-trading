from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from .data import MarketData
from .rules import BenchmarkRules, normalize_target_weights
from .strategy_api import WeightStrategy


class AuditError(AssertionError):
    pass


def _weights(factory: Callable[[], WeightStrategy], data: MarketData, rules: BenchmarkRules) -> pd.DataFrame:
    strategy = factory()
    return normalize_target_weights(strategy.generate_target_weights(data), data, rules)


def audit_determinism(
    factory: Callable[[], WeightStrategy],
    data: MarketData,
    rules: BenchmarkRules,
    tolerance: float = 1e-12,
) -> None:
    first = _weights(factory, data, rules)
    second = _weights(factory, data, rules)
    if not np.allclose(first.to_numpy(), second.to_numpy(), atol=tolerance, rtol=0.0):
        raise AuditError("strategy output is not deterministic")


def audit_causality(
    factory: Callable[[], WeightStrategy],
    data: MarketData,
    rules: BenchmarkRules,
    checkpoints: int = 5,
    minimum_history: int = 252,
    tolerance: float = 1e-10,
) -> None:
    if len(data.index) <= minimum_history + 2:
        raise ValueError("not enough data for causality checkpoints")
    full = _weights(factory, data, rules)
    positions = np.linspace(minimum_history, len(data.index) - 1, checkpoints, dtype=int)
    for position in sorted(set(int(value) for value in positions)):
        truncated_data = data.truncate(position + 1)
        truncated = _weights(factory, truncated_data, rules)
        expected = full.iloc[position].to_numpy(dtype=float)
        actual = truncated.iloc[-1].to_numpy(dtype=float)
        if not np.allclose(expected, actual, atol=tolerance, rtol=0.0):
            difference = float(np.max(np.abs(expected - actual)))
            raise AuditError(f"future-data dependency at bar={data.index[position]} (max diff={difference:g})")


def audit_price_scale_invariance(
    factory: Callable[[], WeightStrategy],
    data: MarketData,
    rules: BenchmarkRules,
    tolerance: float = 1e-9,
) -> None:
    base = _weights(factory, data, rules)
    scale_values = np.geomspace(0.17, 6.3, len(data.asset_ids))
    factors = dict(zip(data.asset_ids, scale_values, strict=True))
    scaled_data = data.rescale_prices(factors, benchmark_factor=3.7)
    scaled = _weights(factory, scaled_data, rules)
    if not np.allclose(base.to_numpy(), scaled.to_numpy(), atol=tolerance, rtol=0.0):
        difference = float(np.max(np.abs(base.to_numpy() - scaled.to_numpy())))
        raise AuditError(f"strategy depends on nominal price scale (max diff={difference:g})")


def audit_asset_label_invariance(
    factory: Callable[[], WeightStrategy],
    data: MarketData,
    rules: BenchmarkRules,
    tolerance: float = 1e-9,
) -> None:
    base = _weights(factory, data, rules)
    labels = list(data.asset_ids)
    mapping = {label: f"RENAMED_{len(labels) - index:02d}" for index, label in enumerate(labels)}
    relabeled_data = data.relabel_assets(mapping)
    relabeled = _weights(factory, relabeled_data, rules)
    inverse = {new: old for old, new in mapping.items()}
    restored = relabeled.rename(columns=inverse).reindex(columns=data.asset_ids)
    if not np.allclose(base.to_numpy(), restored.to_numpy(), atol=tolerance, rtol=0.0):
        difference = float(np.max(np.abs(base.to_numpy() - restored.to_numpy())))
        raise AuditError(f"strategy depends on asset labels (max diff={difference:g})")


def run_all_audits(
    factory: Callable[[], WeightStrategy],
    data: MarketData,
    rules: BenchmarkRules,
) -> dict[str, str]:
    audit_determinism(factory, data, rules)
    audit_causality(factory, data, rules)
    audit_price_scale_invariance(factory, data, rules)
    audit_asset_label_invariance(factory, data, rules)
    return {
        "determinism": "pass",
        "causality": "pass",
        "price_scale_invariance": "pass",
        "asset_label_invariance": "pass",
    }

