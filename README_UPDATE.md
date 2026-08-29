# Touchline Forecast — Total Goals & Accuracy Update
Build date: 2026-08-29

This bundle makes total goals a first-class forecast target on top of the project's current Bayesian state-space model.

## Included
- Absolute `expected_total_goals = lambda_home + lambda_away` for every modeled fixture.
- Persistent frozen pre-match accuracy history using the previous published snapshot.
- Leakage-resistant calibration: results are never used to create their own prediction.
- Out-of-sample monotonic/isotonic calibration after 40 resolved frozen forecasts.
- MAE, RMSE, bias, average predicted vs actual, within ±0.5, within ±1.0 and rounded-total hit rate.
- Calibration by predicted total-goal range.
- Over 1.5 / 2.5 / 3.5 / 4.5 probabilities, actual hit rates and Brier scores.
- New dark-theme Accuracy page at `app/accuracy.html?league=epl` and `?league=mls`.
- Nightly deployment validation for the new accuracy payload and total-goal field.
- Unit tests.
- Prediction markets remain comparison-only, consistent with the current project methodology.

## Apply
```bash
python scripts/apply_touchline_update.py /path/to/touchline-forecast
```

Then:
```bash
python -m unittest discover -s tests -v
python -m predictor.run --league epl --refresh
python -m predictor.run --league mls --refresh
```

## Important implementation choice
The current project already uses historical Bayesian state-space attack/defense fitting, so this update does not replace it with the older seeded demo model. It adds the missing frozen-forecast calibration layer.

The prior website analysis also recommended Dixon–Coles, richer xG/shot/player availability, MLS travel/turf/heat/international-duty factors, EPL rest/cup congestion and richer scoreline backtests. Those require the current scoring/simulation source (`simulate.py` or equivalent), which was not present in the project files surfaced in this session. This bundle deliberately does not guess at those internals. The frozen accuracy archive added here is what lets us test those upgrades honestly when that source is available.
