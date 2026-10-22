# FRED transport outage: verified history plus live NDX tail

The October 21 17:34 PDT scheduled run 39700459843 passed 270 regression tests but failed after FRED NASDAQ100 timed out. The older fallback also downloaded its anchor from FRED, so it shared the failed dependency. Its restored checkpoint was dated October 19 and therefore correctly could not be accepted as an October 21 report.

The new route reads an already verified full-history checkpoint as a historical anchor only. It requires the existing checksum, code-contract, original verification-time, complete-grid and cross-source validations. The latest requested date still requires live post-close daily USD ^NDX OHLC. Existing 20-of-25 overlap checks and 0.01% tolerance remain unchanged. The full historical EMA seed is preserved, with only missing tail sessions appended.

The allowed gap is at most five trading sessions since the original FRED observation, not merely five since the last cached append. The original FRED date is preserved in the saved source description so daily checkpoints cannot indefinitely renew the allowance. Unknown provenance, prior price conflicts, modified hashes, stale verification, and missing new bars fail closed. Same-session checkpoint restore still rejects a different report date. No quotes or QQQ-derived NDX values are created.

Read-only CI restores an existing main-branch checkpoint into an isolated folder. The NDX probe forces all FRED transports to fail and requires a genuine new Yahoo daily bar through the actual loader. The existing QQQ probe receives a validated seed but still separately tests current finalized-close recovery and same-session offline restore. Neither probe saves a production cache or publishes signals.

Local validation before submission: all existing 323 tests retained plus 21 new cases, 344 passed. Live validation is recorded in the PR after it runs; local tests alone do not establish source availability. Strategy formulas, production schedule, publication deadlines, risk limits and execution permissions are unchanged. BASE remains formal; C20/Q70 remain shadow-only. Futures research is not deployed.
