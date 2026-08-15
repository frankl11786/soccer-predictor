# Match prediction-market explainers

This patch adds a short plain-English interpretation beneath each Home / Draw / Away prediction-market comparison card on match-detail pages.

The explainer is generated from the values already published in the match snapshot. It does not alter the Bayesian model, Polymarket ingestion, Kalshi ingestion, normalization, or market-consensus calculation.

The text adapts to the data:
- calls out whether the market consensus is above, below, or close to the model;
- describes the size of the model/market gap;
- notes whether Polymarket and Kalshi are closely aligned, broadly similar, or materially different;
- works when only one prediction market is available;
- appears only when a market consensus exists.

Example using the Atlanta card shown during development:

> The prediction markets see ATL winning as much more likely than our model does. Polymarket (48.7%) and Kalshi (49.7%) are closely aligned around a 49.2% consensus—about 15 percentage points above the model's 34.4%.

## Files

- `app/app.js`
- `app/styles.css`

Replace those two files in the repository and commit to `main`.
