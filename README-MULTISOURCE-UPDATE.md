# MLS schedule completion update

This patch keeps American Soccer Analysis as the source for completed MLS results and advanced history, then adds ESPN as the source for the full current regular-season schedule.

The pipeline now validates the MLS schedule before publishing:

- exactly 510 regular-season matches
- exactly 34 appearances for each of the 30 clubs

If those checks fail, the GitHub workflow stops and the existing live forecast remains untouched.
