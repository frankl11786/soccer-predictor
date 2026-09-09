Fix for Update forecasts run #84 / MLS postponed-match failure

Verified root cause:
- The run used the correct new commit.
- ESPN live requests still return 403 and the committed ESPN cache marks
  FC Cincinnati vs D.C. United as NS / Scheduled.
- The current ASA response did not supply a usable Postponed status for that match.
- FixtureDownload also left the match unplayed.
- MLS's official site confirms the Sept. 5 match is postponed.

This package:
1. Adds an explicit, source-cited official fixture-status override layer.
2. Adds the Sept. 5 FC Cincinnati vs D.C. United postponement override.
3. Applies overrides by exact teams + original UTC calendar date only.
4. Never overwrites a final result.
5. Stops applying automatically once a schedule source moves the fixture to a new date.
6. Adds tests for both application and reschedule non-application.
7. Adds a preflight job so BOTH leagues must pass source freshness checks before
   either expensive Bayesian rebuild begins.

Upload all files preserving their paths. Do not rerun Update forecasts until
the commit containing all five files is on main and has been verified.
