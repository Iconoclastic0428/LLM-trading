from __future__ import annotations

from typing import Protocol

import pandas as pd

from .data import MarketData


class WeightStrategy(Protocol):
    def generate_target_weights(self, data: MarketData) -> pd.DataFrame:
        """Return close-of-bar target weights for every bar and asset."""

