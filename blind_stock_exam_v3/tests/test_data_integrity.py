from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from benchmark.data import load_all_visible_market_data
from run_visible import verify_visible_files


ROOT = Path(__file__).resolve().parents[1]


def test_visible_dataset_is_complete_and_hashed() -> None:
    verify_visible_files()
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    episodes = load_all_visible_market_data(ROOT / "data")
    assert set(episodes) == set(manifest["episodes"])
    for episode_name, data in episodes.items():
        split_names = manifest["episodes"][episode_name]["visible_splits"]
        expected_sessions = sum(int(manifest["splits"][name]["sessions"]) for name in split_names)
        assert len(data.index) == expected_sessions
        assert len(data.asset_ids) == manifest["episodes"][episode_name]["asset_count"]
        assert len(data.asset_ids) >= 100
        assert data.index[0] == 0
        assert np.diff(data.index.to_numpy()).tolist() == [1] * (len(data.index) - 1)
        assert not data.volume.isna().any().any()
        assert data.tradable.dtypes.eq(bool).all()
        for frame in (data.open, data.high, data.low, data.close):
            assert np.isfinite(frame.to_numpy()[data.tradable.to_numpy()]).all()
            assert frame.isna().equals(data.close.isna())
