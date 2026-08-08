from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd


ASSET_COLUMNS = ("bar_id", "asset_id", "tradable", "open", "high", "low", "close", "volume")
BENCHMARK_COLUMNS = ("bar_id", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class MarketData:
    """Aligned equity panel supplied to a strategy.

    Price frames share an integer `bar_id` index and asset columns. Benchmark
    series use the same index. Values are adjusted research bars, not live order
    quotes.
    """

    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    tradable: pd.DataFrame
    benchmark_open: pd.Series
    benchmark_close: pd.Series

    def __post_init__(self) -> None:
        frames = (self.open, self.high, self.low, self.close, self.volume, self.tradable)
        base_index = self.close.index
        base_columns = self.close.columns
        if len(base_index) == 0 or len(base_columns) == 0:
            raise ValueError("market data must contain bars and assets")
        if not base_index.is_monotonic_increasing or base_index.has_duplicates:
            raise ValueError("bar_id index must be strictly increasing")
        for frame in frames:
            if not frame.index.equals(base_index) or not frame.columns.equals(base_columns):
                raise ValueError("all asset fields must have identical index and columns")
        for series in (self.benchmark_open, self.benchmark_close):
            if not series.index.equals(base_index):
                raise ValueError("benchmark and asset indexes must match")
        if self.tradable.dtypes.ne(bool).any():
            raise ValueError("tradable must be a boolean frame")
        availability = self.close.notna()
        for frame in frames[:4]:
            if not frame.notna().equals(availability):
                raise ValueError("OHLC fields must share the same availability mask")
            values = frame.to_numpy(dtype=float)
            present = availability.to_numpy(dtype=bool)
            if not np.isfinite(values[present]).all() or (values[present] <= 0).any():
                raise ValueError("available OHLC values must be finite and positive")
        if not np.isfinite(self.volume.to_numpy(dtype=float)).all():
            raise ValueError("volume must be finite")
        if (self.volume.to_numpy(dtype=float) < 0).any():
            raise ValueError("volume must be nonnegative")
        if (self.tradable & ~availability).any().any():
            raise ValueError("a tradable cell must have an available price")
        if (self.tradable & (self.volume <= 0)).any().any():
            raise ValueError("a tradable cell must have positive volume")
        for series in (self.benchmark_open, self.benchmark_close):
            values = series.to_numpy(dtype=float)
            if not np.isfinite(values).all() or (values <= 0).any():
                raise ValueError("benchmark prices must be finite and positive")
        if (self.low > pd.concat([self.open, self.close], axis=0).groupby(level=0).min()).any().any():
            raise ValueError("low exceeds open or close")
        if (self.high < pd.concat([self.open, self.close], axis=0).groupby(level=0).max()).any().any():
            raise ValueError("high is below open or close")

    @property
    def index(self) -> pd.Index:
        return self.close.index

    @property
    def asset_ids(self) -> pd.Index:
        return self.close.columns

    def truncate(self, length: int) -> "MarketData":
        if length < 1 or length > len(self.index):
            raise ValueError("truncate length is outside the data range")
        return MarketData(
            open=self.open.iloc[:length].copy(),
            high=self.high.iloc[:length].copy(),
            low=self.low.iloc[:length].copy(),
            close=self.close.iloc[:length].copy(),
            volume=self.volume.iloc[:length].copy(),
            tradable=self.tradable.iloc[:length].copy(),
            benchmark_open=self.benchmark_open.iloc[:length].copy(),
            benchmark_close=self.benchmark_close.iloc[:length].copy(),
        )

    def rescale_prices(
        self,
        factors: Mapping[str, float],
        benchmark_factor: float = 1.0,
    ) -> "MarketData":
        scale = pd.Series(factors, dtype=float).reindex(self.asset_ids)
        if scale.isna().any() or (scale <= 0).any():
            raise ValueError("every asset needs a positive scale factor")
        return MarketData(
            open=self.open.mul(scale, axis=1),
            high=self.high.mul(scale, axis=1),
            low=self.low.mul(scale, axis=1),
            close=self.close.mul(scale, axis=1),
            volume=self.volume.copy(),
            tradable=self.tradable.copy(),
            benchmark_open=self.benchmark_open * float(benchmark_factor),
            benchmark_close=self.benchmark_close * float(benchmark_factor),
        )

    def relabel_assets(self, mapping: Mapping[str, str]) -> "MarketData":
        if set(mapping) != set(self.asset_ids):
            raise ValueError("mapping must cover every current asset label")
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("new labels must be unique")

        def renamed(frame: pd.DataFrame) -> pd.DataFrame:
            return frame.rename(columns=mapping).sort_index(axis=1)

        return MarketData(
            open=renamed(self.open),
            high=renamed(self.high),
            low=renamed(self.low),
            close=renamed(self.close),
            volume=renamed(self.volume),
            tradable=renamed(self.tradable),
            benchmark_open=self.benchmark_open.copy(),
            benchmark_close=self.benchmark_close.copy(),
        )


def _read_asset_file(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if tuple(frame.columns) != ASSET_COLUMNS:
        raise ValueError(f"unexpected asset schema in {path}")
    if frame.duplicated(["bar_id", "asset_id"]).any():
        raise ValueError(f"duplicate asset bars in {path}")
    if frame["tradable"].dtype != bool:
        normalized = frame["tradable"].astype(str).str.strip().str.lower()
        if not normalized.isin({"true", "false"}).all():
            raise ValueError(f"invalid tradable values in {path}")
        frame["tradable"] = normalized.eq("true")
    return frame


def _read_benchmark_file(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if tuple(frame.columns) != BENCHMARK_COLUMNS:
        raise ValueError(f"unexpected benchmark schema in {path}")
    if frame["bar_id"].duplicated().any():
        raise ValueError(f"duplicate benchmark bars in {path}")
    return frame


def market_data_from_long_frames(asset_frame: pd.DataFrame, benchmark_frame: pd.DataFrame) -> MarketData:
    asset_frame = asset_frame.sort_values(["bar_id", "asset_id"]).reset_index(drop=True)
    benchmark_frame = benchmark_frame.sort_values("bar_id").reset_index(drop=True)
    if tuple(asset_frame.columns) != ASSET_COLUMNS:
        raise ValueError("unexpected in-memory asset schema")
    if tuple(benchmark_frame.columns) != BENCHMARK_COLUMNS:
        raise ValueError("unexpected in-memory benchmark schema")
    if asset_frame.duplicated(["bar_id", "asset_id"]).any():
        raise ValueError("duplicate in-memory asset bars")
    if benchmark_frame["bar_id"].duplicated().any():
        raise ValueError("duplicate in-memory benchmark bars")
    bar_count = asset_frame["bar_id"].nunique()
    asset_count = asset_frame["asset_id"].nunique()
    if len(asset_frame) != bar_count * asset_count:
        raise ValueError("asset panel is not rectangular")
    if set(asset_frame["bar_id"]) != set(benchmark_frame["bar_id"]):
        raise ValueError("asset and benchmark bars differ")
    fields: dict[str, pd.DataFrame] = {}
    for field in ("open", "high", "low", "close", "volume"):
        fields[field] = (
            asset_frame.pivot(index="bar_id", columns="asset_id", values=field)
            .sort_index()
            .sort_index(axis=1)
        )
        fields[field].index = fields[field].index.astype("int64")
        fields[field].index.name = "bar_id"
        fields[field].columns.name = None

    tradable = (
        asset_frame.pivot(index="bar_id", columns="asset_id", values="tradable")
        .sort_index()
        .sort_index(axis=1)
        .astype(bool)
    )
    tradable.index = tradable.index.astype("int64")
    tradable.index.name = "bar_id"
    tradable.columns.name = None

    benchmark = benchmark_frame.set_index("bar_id").sort_index()
    benchmark.index = benchmark.index.astype("int64")
    benchmark.index.name = "bar_id"
    return MarketData(
        open=fields["open"],
        high=fields["high"],
        low=fields["low"],
        close=fields["close"],
        volume=fields["volume"],
        tradable=tradable,
        benchmark_open=benchmark["open"].rename("benchmark_open"),
        benchmark_close=benchmark["close"].rename("benchmark_close"),
    )


def load_visible_long_frames(
    data_dir: str | Path,
    episode: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load one continuous visible episode declared in ``manifest.json``.

    The revised benchmark has two independent histories.  The pre-stress
    episode ends immediately before the sealed 2022 interval, while the recent
    episode ends immediately before the sealed 2026-YTD interval.  Keeping the
    episodes separate prevents a rolling feature from treating the hidden 2022
    gap as a one-session return.
    """

    root = Path(data_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    episodes = manifest.get("episodes", {})
    if episode not in episodes:
        raise ValueError(f"unknown visible episode: {episode}")
    split_names = tuple(episodes[episode]["visible_splits"])
    if not split_names:
        raise ValueError(f"visible episode has no splits: {episode}")
    assets = pd.concat(
        [_read_asset_file(root / f"{name}.csv.gz") for name in split_names],
        ignore_index=True,
    )
    benchmark = pd.concat(
        [_read_benchmark_file(root / f"benchmark_{name}.csv.gz") for name in split_names],
        ignore_index=True,
    )
    return assets, benchmark


def load_visible_market_data(data_dir: str | Path, episode: str) -> MarketData:
    assets, benchmark = load_visible_long_frames(data_dir, episode)
    return market_data_from_long_frames(assets, benchmark)


def load_all_visible_market_data(data_dir: str | Path) -> dict[str, MarketData]:
    root = Path(data_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return {
        episode: load_visible_market_data(root, episode)
        for episode in manifest.get("episodes", {})
    }
