# Kalshi + Prediction-Market Consensus

This patch adds Kalshi as a second independent prediction-market comparison
alongside Polymarket for the Premier League and MLS.

## What changes

- Season winner markets:
  - EPL: `KXPREMIERLEAGUE-27`
  - MLS: `KXMLSCUP-26`
- Match-result series:
  - EPL: `KXEPLGAME`
  - MLS: `KXMLSGAME`
- Kalshi season probabilities are normalized across the exact configured
  mutually-exclusive event when enough contracts have usable prices.
- Kalshi match probabilities are published only when the fixture can be
  matched conservatively by both clubs and date and all Home/Draw/Away
  outcomes have usable prices.
- Kalshi's best Yes bid/ask midpoint is used when the book is reasonably
  tight. If it is not usable, the most recent trade is used as the estimate.
  Raw bid, ask, last trade, spread, volume and the estimation method are kept
  in the JSON for transparency.
- A Market Consensus value is the equal-weight mean of the available
  normalized Polymarket and Kalshi estimates. If only one external market is
  available, consensus uses that one source.
- Polymarket, Kalshi and Market Consensus are comparison-only. None of these
  values is supplied to the Bayesian model or the season simulations.

## Where it appears

Kalshi and Market Consensus are surfaced throughout the existing site,
including Overview, Forecast, projected tables, Schedule, Match Detail, Team
pages, Matchup Lab, season-race/playoff views, Model News and Methodology.

## API credentials

No new GitHub secret is required. The integration uses Kalshi's public market
data REST endpoints and does not place trades or access an account.

## Files in this patch

- `app/app.js`
- `app/styles.css`
- `predictor/kalshi.py`
- `predictor/config.py`
- `predictor/run.py`
- `predictor/output.py`
- `tests/test_kalshi.py`
- `tests/test_snapshots.py`

The patch intentionally does not include `app/data/*.json` or the GitHub
Actions workflow. The existing workflow should rebuild fresh snapshots after
the patch is committed.

## Validation performed

- Python compilation for `predictor/` and `tests/`
- JavaScript syntax validation with `node --check`
- 25 applicable unit/source/snapshot tests
- Output-schema integration test covering simultaneous Polymarket + Kalshi
  season and match quotes and consensus calculations
- Browser rendering smoke test across EPL and MLS Overview, Forecast, Table,
  Schedule, Matchup Lab, Methodology, Team and Match pages using synthetic v5
  market data; no JavaScript page or console errors were observed

The full NumPyro production fit was not rerun in the packaging environment
because NumPyro is not installed there. This patch does not modify `bayes.py`,
`simulate.py`, `data_prep.py`, or the underlying Bayesian model mathematics.
Your GitHub Actions environment installs the project requirements and remains
the end-to-end production check.
