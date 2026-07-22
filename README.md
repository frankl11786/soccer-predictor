# Touchline Forecast — Free Multi-Source Bayesian Edition

This repository powers `predictor.francislavelle.com`.

## Data pipeline

- **API-Football free plan:** EPL and MLS historical results for seasons 2022–2024.
- **OpenFootball:** 2025/26 and 2026/27 Premier League fixtures and results.
- **American Soccer Analysis:** 2025 and 2026 MLS fixtures and results.
- **Polymarket:** independent EPL-title and MLS-Cup market probabilities when active markets exist.

Polymarket is not used to train the model. It is kept independent so the displayed model-versus-market edge remains meaningful.

## Model

Each league is fitted separately using a Bayesian state-space Poisson model. Team attack and defense vary through 28-day time steps. Squad values from the maintained CSV files enter as a covariate. Posterior uncertainty is carried into 20,000 full competition simulations.

## Automated workflow

GitHub Actions downloads the data, refits both models, simulates the seasons, writes `app/data/epl.json` and `app/data/mls.json`, then deploys the static app to GoDaddy through the restricted FTP account.

## Required repository secrets

- `API_FOOTBALL_KEY`
- `FTP_HOST`
- `FTP_USERNAME`
- `FTP_PASSWORD`
- `FTP_REMOTE_DIR`
- `FTP_TLS`

No additional key is required for OpenFootball, American Soccer Analysis, or read-only Polymarket data.
