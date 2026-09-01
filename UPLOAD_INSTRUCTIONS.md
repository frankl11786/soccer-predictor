# Final repo-aligned update

This package was rebuilt after auditing the actual public GitHub repository.

Replace these files in GitHub:
- `.github/workflows/update-forecast.yml`
- `predictor/espn.py`
- `predictor/run.py`
- `app/index.html`
- `app/touchline-enhancements.js`

Do NOT use the old post-build reconciliation step. The new workflow intentionally removes it because it overwrote the repo's existing `accuracy` schema.

You can leave these old files in the repo for now; the corrected workflow no longer calls them:
- `scripts/postprocess_snapshot.py`
- `scripts/validate_fixture_freshness.py`
- `predictor/fixture_reconcile.py`
- `predictor/goal_accuracy.py`

Commit message:
`Fix current results ingestion and preserve forecast accuracy`

Then manually run `Update forecasts`.

What changed:
1. ESPN EPL + MLS current results are loaded BEFORE `prepare_league()` and the Bayesian fit.
2. Newly completed matches therefore update the current table, team ratings, future match probabilities, season simulations, and existing prediction-history grading.
3. The workflow no longer replaces the production `accuracy` payload with a second incompatible schema.
4. The workflow fails if a fixture remains `scheduled` more than 18 hours after kickoff.
5. The schedule enhancement uses the existing `goal_totals.model.lambda` as Expected Total Goals and filters terminal statuses from the Upcoming view.
6. The existing full Accuracy page and frozen prediction-history architecture remain intact.
