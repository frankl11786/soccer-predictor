# Historical prediction-market backfill

This update recovers genuine pregame match probabilities from older committed `app/data/epl.json` and `app/data/mls.json` snapshots in the repository's Git history.

It does **not** recalculate old predictions after the result is known. A historical row is accepted only when the archived snapshot was generated before that fixture's kickoff.

## What changes on match pages

For completed matches with an archived pregame snapshot, the match page now shows:

- the frozen Bayesian-model home/draw/away probabilities;
- frozen Polymarket probabilities when that snapshot contained an exact market;
- frozen Kalshi probabilities when that snapshot contained an exact market;
- the stored prediction-market consensus;
- the actual final result;
- each source's top pick;
- probability assigned to the result that actually happened;
- Brier score and log loss;
- which available forecast produced the best probability forecast on that match.

A completed match can be reviewed when the model plus at least one prediction market was captured. It no longer requires both Polymarket and Kalshi to be present.

## Historical coverage

Coverage is limited to real data that existed in committed pregame snapshots. Therefore:

- model history can reach back as far as the repository has committed forecast snapshots;
- Polymarket history begins only where committed snapshots actually contained Polymarket match data;
- Kalshi history begins only where committed snapshots actually contained Kalshi match data;
- a match with no stored pregame market data remains unavailable rather than being fabricated.

The recovery process prefers a pregame snapshot containing both prediction markets over a newer snapshot with less market coverage. Within the same coverage level it uses the latest snapshot before kickoff.

## One-time fast backfill

`backfill-history.yml` is a manual GitHub Actions workflow. Put it in:

`.github/workflows/backfill-history.yml`

Then run **Actions → Backfill historical match comparisons → Run workflow**.

This workflow does not refit NumPyro or rerun the season simulations. It checks out full Git history, recovers historical snapshots, updates `app/data/epl.json` and `app/data/mls.json`, commits those recovered rows, and deploys the existing site to GoDaddy.

Future nightly forecast runs preserve these recovered history records and continue capturing new pregame snapshots normally.
