# Permanent Pregame Prediction Archive

This update makes pregame model and prediction-market probabilities durable instead of relying on the current `app/data/*.json` snapshot.

## What is now preserved

For each EPL/MLS fixture inside the 72-hour pregame capture window, the publish job maintains:

- `history/<league>/<fixture-id>/pregame.json`
- `history/<league>/<fixture-id>/final.json` after the result is known

The pregame file records the Bayesian model, Polymarket, Kalshi and market-consensus probabilities that genuinely existed before kickoff. Each source has its own capture timestamp, so a temporary market-data outage on a later run does not erase an earlier valid quote.

Once `final.json` is created, it is locked and future forecast runs do not rewrite it.

## Nightly workflow order

1. Rebuild EPL and MLS in parallel.
2. The publish job checks out the newest `main` branch with full Git history.
3. Before downloading the newly rebuilt `app/data` files, it archives the currently published pregame probabilities for matches inside the 72-hour window.
4. It downloads the new forecast results.
5. It finalizes newly completed matches against their archived pregame probabilities.
6. It rebuilds the embedded postgame analysis and historical-accuracy summary from the durable archive.
7. It commits `app/data`, `history`, model files and source caches.
8. It rebases onto the latest `main` before pushing, with retries if `main` changed during the long model run.
9. It deploys the site to GoDaddy.

## One-time historical initialization

The included `.github/workflows/backfill-history.yml` action recovers any genuine pregame snapshots that already exist in Git history, writes them into the permanent archive, updates the postgame/accuracy data, commits the archive, and deploys the site.

Run this once after installing the update:

**Actions → Backfill historical match comparisons → Run workflow**

This backfill does not refit NumPyro or rerun season simulations.

## Files in this patch

- `.github/workflows/update-forecast.yml`
- `.github/workflows/backfill-history.yml`
- `predictor/archive.py`
- `predictor/history.py`
- `scripts/prediction_archive.py`
- `scripts/backfill_history.py`
- `tests/test_prediction_archive.py`
- `tests/test_kalshi.py`
- `history/.gitkeep`

## Validation performed

- Python compilation passed for the archive/history scripts.
- 41 applicable non-NumPyro tests passed.
- All Kalshi regression tests were made time-deterministic so they do not start failing merely because their sample fixture date has passed.
- Existing app JavaScript passed `node --check`.
- The NumPyro-dependent model-correction test was not run locally because NumPyro is not installed in the packaging environment; this patch does not change the Bayesian model.
