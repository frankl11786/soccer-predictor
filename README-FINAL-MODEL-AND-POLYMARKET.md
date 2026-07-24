# Final EPL calibration and Polymarket comparison update

This package combines the corrected EPL preseason model with a read-only Polymarket comparison layer for both the Premier League and MLS.

## Non-negotiable separation

Polymarket is **not** used in the Bayesian fit, preseason transition, team ratings, expected goals, match simulation, season simulation, playoff simulation, or backtest. The model runs first. Market prices are fetched afterward and attached only to the published JSON snapshot.

The data schema labels both season and match market records with `comparison_only: true`.

## EPL model corrections

- The historical state ends at the last completed match. No unsupported June/July random-walk steps are added during the offseason.
- The EPL preseason state blends the fitted final state with recent EPL scoring performance and maintained attack/defense seeds.
- Clubs without recent EPL history rely more heavily on preseason priors.
- The preseason adjustment fades during each club's first 10 league matches.
- Future simulated rating paths use modest mean reversion.
- Current squad values are used only for future fixtures, not retroactively in historical seasons.
- A time-ordered holdout fit reports Brier score, log loss and skill versus a naive baseline before the full EPL fit. Current 2026/27 preseason seeds and all Polymarket data are excluded from that historical validation, preventing future-information leakage.
- Large model-versus-market differences create review warnings but never alter the model.

## Polymarket season-winner comparison

Exact event slugs are configured for:

- EPL: `epl-2027-champion-20260701200428749`
- MLS: `mls-cup-winner-2026`

For every active club contract that can be matched safely, the snapshot stores:

- raw Yes price;
- normalized event probability;
- model probability;
- model minus market edge;
- event and market identifiers;
- question, liquidity, volume and update time.

The normalization denominator includes every active Yes contract in the exact event, including an `Other` contract when present. If the exact event cannot be retrieved, the site publishes no season-market comparison rather than falling back to a similarly named event.

## Polymarket match comparison

The nightly process searches a bounded set of near-term scheduled fixtures. A match quote is published only when all of the following are true:

1. Both clubs match the event title or slug.
2. A verifiable market kickoff/date is present and falls within 36 hours of the scheduled fixture.
3. The market represents the full-match result rather than a spread, total, half, score, cards, corners or other derivative.
4. Home win, draw and away win are all present.
5. The quote comes from one coherent three-way market when available; otherwise each binary contract must map unambiguously to exactly one of home, draw or away.
6. The three raw prices have a plausible combined total.

The three prices are normalized to 100%. Missing markets stay missing; the code never substitutes sportsbook odds or estimates a Polymarket price.

## Site locations

### Overview

- Bayesian season favorite
- Polymarket season favorite
- Model, Polymarket and edge for the leading teams
- Model and market 1X2 probabilities for quoted upcoming fixtures
- Number of exact match markets found

### Forecast

- Polymarket season-winner column for every club with an active contract
- Model-edge column
- Sortable across both values

### Projected table

- Full-width Model outcome, Polymarket outcome and model-edge columns

### Schedule

- Bayesian and Polymarket home/draw/away probabilities
- Coverage counter
- Exact-match detail link
- Explicit no-market state instead of a fabricated value

### Match detail

- Separate three-outcome comparison card
- Model, market and edge for home/draw/away
- Raw-total normalization note
- Volume, liquidity, update time and source link

### Team pages

- Season model-versus-market panel
- Market update metadata and source link
- Polymarket values beside the club's upcoming quoted matches

### Other pages

- Season Races / MLS bracket: market comparison for the championship outcome
- Matchup Lab: Polymarket appears only when the selected teams and venue match an exact scheduled fixture
- Model News: season and match coverage, source errors and large divergence warnings
- Methodology: documents normalization, matching rules and strict model/market separation

## Automated validation

The package adds tests that verify:

- the EPL time axis ends at the last completed match;
- the preseason transition uses recent performance and seeds without Polymarket;
- exact season-winner events are normalized and every current EPL/MLS club name maps one-to-one to its intended contract;
- complete three-outcome match markets are normalized;
- incomplete match markets are rejected;
- spreads are not mistaken for match-result markets;
- generated season and match edges equal model probability minus market probability;
- every published match market contains exactly home, draw and away probabilities summing to 100%;
- events without a verifiable date are rejected;
- derivative and qualification markets are rejected;
- an exact season-event retrieval failure never falls back to a broad search;
- positive display-oriented `defense_strength` values remain exactly consistent with the model's backward-compatible negative defensive effect.

## Files to upload

Upload the extracted `app`, `predictor` and `tests` folders plus this README to the repository root. These files are intentionally omitted from the package:

- `.github/workflows/update-forecast.yml` — your existing parallel nightly workflow remains in place;
- `app/data/*.json` — the next workflow run will generate fresh snapshots;
- API cache and fitted-model outputs — the next workflow run will rebuild them.

## Expected first run

The first run after this commit will:

1. refresh EPL and MLS data;
2. run the EPL temporal holdout;
3. fully refit each Bayesian model;
4. run 20,000 season simulations per league;
5. retrieve exact EPL and MLS Cup winner events;
6. search eligible near-term match markets;
7. validate the generated snapshots;
8. deploy the updated site.

A dash on the site means no exact active Polymarket contract was matched. That is expected for many individual games until closer to kickoff.

## Local verification completed

The package passed Python bytecode compilation, JavaScript syntax validation and 13 local unit tests covering Polymarket normalization/rejection rules, snapshot consistency, ESPN parsing and source identity. The full NumPyro production fit cannot be executed in this container because NumPyro is not installed here; GitHub Actions, which installs the repository requirements, is therefore the first complete end-to-end Bayesian validation.
