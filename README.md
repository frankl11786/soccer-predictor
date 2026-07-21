# Touchline Forecast — Live Bayesian Edition

This repository powers the EPL and MLS forecasting site at `predictor.francislavelle.com`.

It replaces the demonstration pipeline with:

1. **API-Football** fixtures, teams, standings, and results.
2. A **Bayesian state-space Poisson model** fitted separately to EPL and MLS history.
3. **20,000 full-season simulations** for each league.
4. Read-only **Polymarket** winner prices when an active matching market exists.
5. A scheduled **GitHub Actions** workflow that rebuilds and deploys the site.

## Plain-English workflow

```text
API-Football results
        ↓
The model learns each team's changing attack and defense
        ↓
Every remaining match is simulated 20,000 times
        ↓
Polymarket prices are compared with model probabilities
        ↓
New EPL and MLS JSON snapshots are uploaded to GoDaddy
```

## Repository layout

- `app/` — the static website uploaded to GoDaddy.
- `predictor/api_football.py` — downloads and caches soccer data.
- `predictor/bayes.py` — fits the Bayesian state-space Poisson model with NumPyro.
- `predictor/simulate.py` — simulates the EPL season and MLS regular season/playoffs.
- `predictor/polymarket.py` — discovers active winner markets and reads prices.
- `predictor/run.py` — runs the complete update pipeline.
- `.github/workflows/update-forecast.yml` — automatic daily update and deployment.
- `model/data/teams_*.csv` — manually maintained squad values, colors, abbreviations, and MLS conference mappings.

## Model

For each match:

```text
home goals ~ Poisson(lambda_home)
away goals ~ Poisson(lambda_away)

log(lambda_home) = scoring level + home advantage
                   + home attack - away defense
                   + squad-value effect

log(lambda_away) = scoring level
                   + away attack - home defense
                   - squad-value effect
```

Attack and defense evolve in 28-day steps through Gaussian random walks. The system fits the latent states and global coefficients with NumPyro stochastic variational inference. Season simulations draw from the fitted posterior, so model uncertainty is included rather than added manually afterward.

## Required GitHub secrets

The workflow expects these repository secrets:

- `API_FOOTBALL_KEY`
- `FTP_HOST`
- `FTP_USERNAME`
- `FTP_PASSWORD`
- `FTP_REMOTE_DIR`
- `FTP_TLS`

The API key and FTP password must never be written into a code file.

## Manual model run

Python 3.11 is recommended.

```bash
python -m pip install -r requirements.txt
export API_FOOTBALL_KEY="your-private-key"
python -m predictor.run --league all --refresh
python -m unittest discover -s tests -v
```

The updated public snapshots are written to:

```text
app/data/epl.json
app/data/mls.json
```

## Free-plan limitation

API-Football states that its free plan limits which historical seasons are available. The pipeline requests four seasons and skips seasons the account cannot access. A league model requires at least 80 completed fixtures; otherwise the run stops rather than silently publishing an unreliable forecast.

## Polymarket behavior

Polymarket market data is read-only and does not require a trading account. The system searches for active EPL and MLS winner events and tries to match each binary team market. When no suitable market is found, the website displays a dash rather than inventing a price.

## Current limitations

- Squad market values are maintained in CSV files rather than pulled from a licensed valuation feed.
- Injuries, suspensions, travel, rest, and lineups are not yet included in the fitted model.
- Independent Poisson scoring should eventually be backtested against a Dixon–Coles correction.
- API coverage and market availability can vary by competition and season.
