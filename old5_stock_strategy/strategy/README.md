# Frozen old five-stock strategy

candidate_strategy.py is copied byte-for-byte from the frozen submission.

- SHA-256: a6c67728eb23ab8f236c3cb813a695e48bf1afa82905477e7aaa26b7f90b5da3
- Holdings: at most five stocks
- Full-exposure target: 20% per holding
- Rebalance grid: every five market sessions
- Incumbent buffer: retain through eligible rank 10
- Momentum: log return from bar t-63 to t-21
- Volatility: 126-session standard deviation of log returns
- Score: momentum percentile + 0.15 times volatility percentile
- Eligibility: current tradability, positive absolute momentum, and complete features
- Market brake: 50% gross exposure when the benchmark's 252-session trend is nonpositive; otherwise 100%
- Execution convention: the signal calculated after a close is implemented at the following session's open

The benchmark/data.py module supplies the MarketData interface required by the strategy.
