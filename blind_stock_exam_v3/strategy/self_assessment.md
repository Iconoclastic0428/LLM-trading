# Self-assessment

## Main weaknesses

- **Momentum reversal:** residual momentum can fail during abrupt leadership
  rotations. The five-session skip and rank buffer deliberately react slowly,
  so a sharp reversal can hurt before membership changes.
- **Concentration:** ten equal-weight names are much less diversified than the
  100-stock universe. The data has no sector or fundamental classifications,
  so several selections can represent the same economic exposure.
- **Residualization is approximate:** a single trailing beta and benchmark do
  not remove nonlinear, sector, or time-varying common-factor exposures. The
  fully invested sleeve's visible beta reached 1.45 in RECENT_VALIDATION.
- **Material turnover:** annualized one-way turnover was about 9--11 on the
  validation periods. The visible 25 bps stress remained profitable, but live
  spreads, market impact, taxes, and partial fills can be worse than this
  daily-bar model.
- **Trend throttle is modest:** the downside state still holds 70% risky
  exposure. It limits the worst visible drawdowns without disguising weak stock
  selection as cash timing, but it still participates materially in a fast
  market decline. A 200-session mean also lags sudden breaks and recoveries.
- **Limited regimes and selection bias:** only two visible episodes exist, and
  the point-in-time top-holdings panels are not a random sample of all stocks.
  Seventy-eight logged trials are small relative to a large automated search,
  but they still create data-mining risk. The favorable validation magnitudes
  should be expected to shrink out of sample.
- **OHLCV-only implementation:** the strategy ignores fundamentals, corporate
  events, explicit sector limits, and real-time liquidity. It relies on the
  supplied universe construction for basic liquidity quality.

## Expected failure regimes

The weakest setting is a rapid transition from persistent winners to former
losers, especially when the old winners share a crowded high-beta theme. A
second weak setting is a low-dispersion market in which cross-sectional
residual scores are mostly noise; the buffer then retains stale names. The
market throttle can also lag a sudden gap-driven decline or underexpose the
first part of a V-shaped rebound.

## Evidence that would invalidate the thesis

The stock-selection thesis should be treated as invalid if either sealed
selection sleeve has nonpositive annualized alpha, if active coverage falls
below 60%, or if alpha disappears under a modest phase or cost perturbation on
new data. Repeated sealed underperformance versus the equal-weight universe,
realized turnover materially above the modeled 9--11 range, or drawdown much
larger than the visible concentrated sleeve would likewise reject the practical
implementation. One favorable sealed interval by itself would not establish
durability.

The visible evidence is encouraging but not conclusive: 10 of 12 contiguous
walk-forward blocks were positive, while the two negative blocks demonstrate
that the signal can be wrong for extended periods. The frozen evaluation is a
genuine out-of-sample test, not a continuation of tuning.
