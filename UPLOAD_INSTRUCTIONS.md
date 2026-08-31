# TOUCHLINE FORECAST — GITHUB-READY UPDATE

Upload the CONTENTS of this ZIP into the ROOT of the Touchline Forecast GitHub repository, preserving folders.

GitHub should show:
- `.github/workflows/update-forecast.yml` as REPLACED
- all other included files as NEW

Then commit with:

`Fix stale results and add total-goals accuracy calibration`

After the commit:
1. Open **Actions**
2. Open **Update forecasts**
3. Click **Run workflow**
4. Wait for both league builds and the publish job to finish
5. Hard-refresh `http://predictor.francislavelle.com/#/schedule`
6. Open `http://predictor.francislavelle.com/accuracy.html?league=epl`

## What this fixes/adds
- Authoritative API-Football result reconciliation.
- Completed matches stop appearing under Upcoming.
- Final scores/statuses are corrected.
- Postponed/cancelled/abandoned matches are handled explicitly.
- Current standings are recomputed from reconciled results.
- Market-coverage denominator includes only eligible scheduled/live matches.
- Absolute Expected Total Goals are added to every fixture with home/away goal rates.
- Existing Schedule cards get an Expected Total Goals line automatically.
- Frozen pre-match total-goals history is persisted.
- MAE, RMSE, bias, ±0.5, ±1.0, calibration bins, and O/U Brier calibration are calculated.
- A standalone Accuracy page is added.
- Deployment refuses to publish silently stale past fixtures.

## Important
The workflow preserves the prior published JSON BEFORE rebuilding. That is what allows completed games to be scored against the actual forecast that existed before the result was known.
