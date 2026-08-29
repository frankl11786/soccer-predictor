# Outcome highlighting + total-goals market comparison

This patch adds two match-page improvements without changing the Bayesian fit itself.

## 1. Outcome probability highlighting

The three 1X2 outcome cards now use probability-strength bands in both light and dark mode. Color is not the only signal: every card also receives a text strength label and the highest-probability outcome gets an outline plus a `highest` label.

The foreground/background pairs used by the probability bands were checked against WCAG contrast math. The lowest tested normal-text contrast is approximately 4.84:1 after the secondary-label opacity is applied; the primary text pairs are higher.

## 2. Total goals

Each match page now contains a **Total goals** card beneath the exact-score matrix. It shows:

- exact probabilities for 0, 1, 2, 3, 4, 5, and 6+ total goals;
- model Over/Under probabilities for 0.5 through 5.5 goals;
- matching Polymarket total-goals prices when a date/team-verified full-match total exists;
- matching Kalshi total-goals prices, including bid/ask information when available;
- an equal-weight prediction-market consensus for each available line;
- model-vs-consensus differences and a short plain-English interpretation.

Team-total derivatives are intentionally rejected. Prediction-market totals remain comparison-only and never enter the Bayesian fit or simulations.

The model total-goal distribution uses the same mean-xG independent-Poisson view as the exact-score matrix. Because the sum of two independent Poisson variables is Poisson, the total-goal rate is `xG_home + xG_away`. The displayed 6+ bucket includes the full upper scoring tail.

## 3. Permanent history + accuracy

Pregame total-goals data is stored in the existing immutable match archive. Each source is timestamped independently so a temporary market-source miss on a later refresh does not erase an earlier genuine quote.

After a match becomes final, the totals lines are graded with binary Brier score, log loss, side accuracy, and probability assigned to the actual over/under result. The Accuracy page adds overall and like-for-like total-goals comparisons for Model vs Polymarket, Model vs Kalshi, and all three on shared match/line observations.

Older archived matches that never contained total-goals markets are **not** retroactively manufactured. Total-goals accuracy begins when this patch starts capturing genuine pregame totals.

## Install

Upload these files to the matching paths in the repository and commit to `main`:

- `app/app.js`
- `app/styles.css`
- `predictor/archive.py`
- `predictor/config.py`
- `predictor/goal_totals.py`
- `predictor/history.py`
- `predictor/kalshi.py`
- `predictor/output.py`
- `predictor/polymarket.py`
- `predictor/run.py`
- `tests/test_goal_totals.py`
- `tests/test_prediction_archive.py`

A **full Update forecasts run is required once after installation** because the new total-goals market data is produced by the forecast pipeline. After it has been generated, ordinary CSS/JS-only changes can still use the existing fast backfill/deploy workflow.
