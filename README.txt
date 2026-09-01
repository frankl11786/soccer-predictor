V3 FIX

Replace these TWO files in GitHub:
  predictor/fixture_reconcile.py
  scripts/postprocess_snapshot.py

This removes the 2026 API-Football paid-season requirement and uses ESPN's public soccer scoreboard only for post-build result/status reconciliation.

Keep the v2 update-forecast.yml you already uploaded.

Commit:
Use ESPN for current fixture result reconciliation

Then manually run Update forecasts again.
