MLS postponed-match fix

Root cause:
FC Cincinnati vs D.C. United on 2026-09-05 was postponed, but the current pipeline
collapsed every non-final ASA status to NS ("Not Started"). The stale-fixture guard
then treated the legitimate postponement as missing result data.

Replace these files in the repository:
- predictor/asa.py
- predictor/data_prep.py
- predictor/simulate.py
- tests/test_source_reliability.py

Behavior after the fix:
- ASA statuses such as Postponed/Abandoned/Suspended/Cancelled remain distinct.
- The validated 510-match MLS schedule spine overlays those statuses from ASA.
- The public snapshot publishes "postponed"/"abandoned"/etc. instead of "scheduled".
- The stale scheduled-fixture guard no longer falsely blocks a genuinely postponed match.
- The postponed match remains unplayed and is still included in season simulations until rescheduled.

Validation performed:
- All four Python files passed syntax parsing.
- tests.test_source_reliability ran locally: 6 tests passed.
- A regression test specifically overlays FC Cincinnati vs D.C. United as postponed
  and verifies the prepared MLS schedule retains PST / Postponed.
