# Market Discovery Reliability Fix

This patch strengthens match-market discovery for Kalshi and Polymarket without changing the Bayesian model, simulations, schedule sources, season-winner market configuration, or frontend.

## Kalshi

- Removes dependence on `status=open` for series discovery.
- Retrieves upcoming events using `series_ticker` + `min_close_ts` with nested markets.
- Independently retrieves markets for the same series and merges them by `event_ticker` as a fallback.
- Fixes outcome classification for Kalshi game tickers that contain both teams' abbreviations (for example `ATLNYRB`). Outcome classification now prefers `yes_sub_title` / subtitle / title and only uses the ticker's final outcome suffix as a fallback.
- Adds the `NYRB` alias for Red Bull New York.
- Retains strict fixture-date, both-team, full 1X2, and probability-normalization safeguards.

## Polymarket

- Keeps exact season-winner slug lookup unchanged.
- Discovers active soccer events through the paginated `/events` endpoint before text search.
- Keeps `/public-search` as a per-fixture fallback when systematic discovery does not yield a valid market.
- Prioritizes sports-specific `gameStartTime` / `eventStartTime` fields over generic `startDate` fields when matching a market to a fixture.
- Retains strict both-team, date, full 1X2, and derivative-market rejection safeguards.

## Tests

The patch includes regression tests for:

- Atlanta United vs Red Bull New York via Kalshi's markets fallback.
- Kalshi discovery without a status filter.
- Kalshi ticker contamination during outcome classification.
- Polymarket systematic event discovery without public search.
- Polymarket public-search fallback.
- Sports-specific kickoff-time precedence.

No workflow, model, output schema, or app files are replaced by this patch.
