"""One pinned, reviewed cache-contract transition; not a general hash bypass.

The predecessor used exact Yahoo metadata time. The new release only relaxes
that redundant metadata clock and preserves/revalidates all historical prices.
The active fingerprint is pinned outside the hashed files to avoid a circular
self-hash. Any other change to a hashed rule file invalidates this transition.
No cache date, original verification time, FRED provenance or price hash changes.
"""
PRIOR_RULES_SHA256 = '264dffc9dfdcfc73c2015a5494f8a5f73c3b59317ee70ce71c091faa92d7e24c'
ACTIVE_RULES_SHA256 = '58c1ad9675111f6f57fc1b4b36ce0398925d2f3ccb68ea62b635dcf25b5eafa2'


def accepts_predecessor(stored_rules, active_rules):
    return (stored_rules == PRIOR_RULES_SHA256
            and active_rules == ACTIVE_RULES_SHA256)
