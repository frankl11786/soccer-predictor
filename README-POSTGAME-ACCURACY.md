# Touchline Forecast — Kalshi test fix + postgame accuracy tracking

This patch contains two related updates.

## 1. Fixes the Kalshi regression-test failure

The Atlanta United / New York Red Bulls regression fixture is dated August 15, 2026. The test originally compared that hard-coded fixture to the machine's real current date, so it began failing after the match date passed even though the market-discovery logic itself was still valid.

`fetch_match_quotes()` now accepts an optional `as_of` timestamp used only for deterministic testing. Production behavior is unchanged when `as_of` is omitted. The regression test freezes its reference time to August 14, 2026.

## 2. Adds frozen pregame history, postgame review, and an Accuracy page

Starting with the first forecast generated after this patch is deployed, each league snapshot stores the latest model probabilities captured before kickoff. If Polymarket and/or Kalshi are available, their normalized pregame probabilities are frozen with the same record.

When the result becomes final, the stored pregame row is graded. The code intentionally does not reconstruct historical probabilities from a post-match model.

Each graded source records:
- probability assigned to the actual result
- whether its highest-probability 1X2 pick was correct
- multiclass Brier score (lower is better)
- log loss (lower is better)

Match pages show a **Postgame forecast review** when the match had all three pregame inputs: the Bayesian model, Polymarket, and Kalshi. The card also identifies the lowest-Brier forecast for that individual match.

A new **Accuracy** navigation page shows:
- overall recorded performance for Model / Polymarket / Kalshi / Consensus
- Model vs Polymarket on the exact same covered matches
- Model vs Kalshi on the exact same covered matches
- all three on the exact same matches
- recent graded matches linking back to their postgame review

## Important limitation

Historical tracking starts when this patch is first deployed. Older matches are not backfilled because doing so would require reconstructing probabilities after the results were already known.

## Files

- `app/index.html`
- `app/app.js`
- `app/styles.css`
- `predictor/history.py` (new)
- `predictor/output.py`
- `predictor/kalshi.py`
- `tests/test_history.py` (new)
- `tests/test_kalshi.py`

No Bayesian fitting or simulation code is changed by this patch.
