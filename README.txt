Soccer Predictor reliability fix — 2026-09-06

Replace/add these files in the repository using the same paths:
- predictor/epl_schedule.py  (NEW)
- predictor/run.py
- predictor/identity.py
- tests/test_schedule_ui.py
- tests/test_source_reliability.py

What this fixes:
1. Adds FixtureDownload as an independent 380-match EPL schedule/results source.
2. Persists its cache using the existing epl_schedule_*.json staging rule, so no workflow YAML change is required.
3. Adds an 18-hour stale-fixture preflight BEFORE Bayesian fitting to avoid wasting a long model run.
4. Removes the brittle hard-coded '181 upcoming MLS fixtures' assertion.
5. Adds the 'Spurs' alias required by FixtureDownload's EPL naming.
6. Adds focused EPL FixtureDownload parser/identity regression coverage.

All five Python files passed syntax parsing when this package was created.
