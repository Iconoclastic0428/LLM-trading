from __future__ import annotations

import pandas as pd

from benchmark.data import MarketData


class CandidateStrategy:
    """All-cash placeholder; replace this method in the candidate task."""

    def generate_target_weights(self, data: MarketData) -> pd.DataFrame:
        return pd.DataFrame(0.0, index=data.index, columns=data.asset_ids)

