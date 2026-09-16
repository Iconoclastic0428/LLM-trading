# September 15, 2026 PT: delivered event skipped, later schedule records absent

## Verified observations

At approximately 21:21 PT on September 15 (04:21 UTC September 16), the latest
repository workflow run was `35038799165`, created at 00:09:34 UTC September 16
(17:09:34 PT September 15), event `schedule`, commit `d67663d`.
Its first-attempt job `104613811831` logged:

```
Actual runner time: 2026-09-15T17:09:39.812688-07:00; eligible=False
```

All dependency, test, market-data, publication and C20/Q70 steps were skipped.
The workflow was green because its time-window check and cleanup succeeded,
not because September 15 market data or Q70 were calculated.

The repository runs API query for `event=schedule` and
`created=>=2026-09-16T01:00:00Z` returned `total_count=0` before remediation.
That is the PT window from September 15 18:00 onward. The missing 18:07, 19:07,
20:07 and 21:07 opportunities had not produced visible runs at inspection time.
There was no failed or queued run for those opportunities in the retrieved list.

This establishes an absent-run scheduling symptom and a local eligibility gate
that discarded a valid post-close opportunity. It does NOT establish GitHub's
internal reason for the absence (delayed delivery, dropped events or another
scheduler/account condition). Repository logs cannot prove those internals.
The YAML remained on main, and retriggering its job worked without a code change.
GitHub documents that schedule events may be delayed or dropped:
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

## Immediate recovery

Job `104613811831` was rerun through the Actions API. Run `35038799165`, attempt 2,
completed the pipeline on the SAME commit. It verified report/expected session
2026-09-15 at 2026-09-16T04:22:36.964704+00:00 (21:22:36 PT), and generated a
successful Q70 shadow audit at 04:22:43 UTC. Publication plan `already_published`
refers to the existing August month-end signal, not a missing daily trade.
The shadow signal date remains 2026-08-31 because this is a monthly strategy.
The program did not send orders or promote Q70 to the formal strategy.

Downloaded audit ZIPs were checked against GitHub's artifact digests:

- BASE artifact `10430750882`, SHA256
  `002eab342d8a44131047d3a2d540d6cf4d767cf6eabac190b9b9c54b2565d632`.
- Shadow artifact `10430178483`, SHA256
  `6f2193e9f1915b83a51ef4d80ab499bb2faf2ce930b27fc07c761aac2f3941a5`.

Recovery run: https://github.com/Iconoclastic0428/LLM-trading/actions/runs/35038799165/attempts/2

## Repository mitigations

1. Start scheduled data checks at PT 14:00 rather than 18:00. The delivered
   17:09 event now qualifies. End remains 06:59. Actual market/session and source
   freshness validation remain in the existing automation, not in this gate.
2. Change cron from `7 * * * *` to `7,37 * * * *`, giving another delivery
   opportunity each hour. This is still one GitHub scheduler, not an independent
   backup. No future cron delivery is guaranteed by a successful manual/push run.
3. Extract the standard-library-only gate into `run_window.py` and always record
   eligibility versus skipped status in the run summary and `window.json`.
   A skip does not write or refresh the production or shadow success artifacts.
4. Add 17 regression cases, including the incident timestamp, summer/winter
   boundaries, DST fall-back, explicit recovery events, malformed inputs and
   auditable skipped output. These 17 tests passed locally before upload.

The strategy calculators, price verification, month-end publication cutoff,
idempotent publisher and shadow-only boundaries are unchanged. A broader
operational window and more checks do not mean more strategy rebalance dates.
Earlier checks can encounter sources that have not updated yet; they must fail
freshness validation and retry rather than silently use the previous close.

## Limits and recovery entrypoint

If no workflow is created, code inside that workflow cannot diagnose or repair
the absent trigger. Automatic guaranteed failover requires an independently
scheduled dispatcher/watchdog, not merely another cron in the same repository.
No external service or new credential was configured in this change.

For a current-data recovery from GitHub's Actions UI, choose `QLD TQQQ monthly
signal`, `Run workflow`, branch `main`, and leave `report_date` EMPTY. Supplying a
report_date deliberately enables historical audit and disables live heartbeat
publication. Existing code rejects incomplete future sessions and expired
month-end execution windows; never backfill a missed trade at an old price.

On a non-month-end, absence of a new issue trade comment is expected. Check the
latest heartbeat's report date and `shadow.json` status instead. A shadow
`signal_date` in the previous month is also expected between month-ends.
