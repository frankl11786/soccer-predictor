# Completed-match pregame forecast + FTP retry

This patch makes two focused changes:

1. Completed match pages use the archived/frozen pregame model 1X2 probabilities from `postgame_analysis.sources.model` for the primary Outcome Forecast when those probabilities exist. The page labels them as frozen pregame probabilities and does not show current posterior intervals beside archived values.
2. `scripts/deploy_ftp.py` retries temporary FTP connection failures up to four total attempts, waiting 5s, 10s, and 20s between retries. Authentication/permanent FTP errors are not intentionally masked by this retry loop.

The existing historical market comparison, postgame grading, model fit, simulations, and prediction-market ingestion are unchanged.
