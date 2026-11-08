# Decision layer: log, rule baseline, scorer, and how to compare authors

Providers and signals only report. This layer is where a call is made, written down, and later graded.

## Pieces
- **Decision log** (`decision` table, `drishti/decisions/log.py`). One row per call: gate, stock (ISIN) or market, action, horizon in
  trading sessions, confidence, thesis, invalidation, the signals cited, author (`agent` + `model`), the evidence hash. Append only: a
  correction is a new row with `supersedes`. The log recomputes every cited signal itself and refuses a citation that has no reading.
- **Gates**: `entry` (ENTER / WAIT / AVOID), `exit` (HOLD / TRIM / EXIT), `screen` (SHORTLIST / SKIP), `macro` (RISK_ON / NEUTRAL / RISK_OFF).
  WAIT, SKIP and NEUTRAL are "no call": never graded as right or wrong, always counted.
- **Confidence** means one thing: the probability the call turns out right over the horizon (0.50..0.99).
- **Evidence bundle** (`gather_evidence`): the stored signals as of the date, unknowns listed with reasons, a hash. Two authors with the
  same hash saw the same evidence. `blind` hides the stock's name, symbol, ISIN and insider names.
- **rules-v1** (`rules.toml`): fixed-threshold points from the same signals. Frozen: the model name carries the file's hash, so a changed
  number is a different author. Skips a case with fewer than 60 stored sessions rather than guessing.
- **Scorer** (`score.py`): stock calls against the Nifty 50, market calls against cash (6.5% a year), over the horizon, from adjusted
  closes. Cost 0.4% on ENTER / EXIT / TRIM / SHORTLIST. Fill at the as_of close for everyone. Pending and no-data cases are counted.
- **Free baseline**: `baseline/always-long` is added to every scorecard. An author who cannot beat it adds nothing.

## Comparing authors fairly (the rules, fixed before any run)
1. Same cases: one case file, one horizon (60 sessions) for everyone. `drishti decisions cases` draws stocks by seed from the 300 most
   traded names on each date.
2. Same evidence: bundles exported once; each author's answer must carry the bundle hash.
3. Primary measure: mean net edge (stance x excess return, after cost) on cases both authors decided and that are scored.
   Secondary: hit rate and Brier score. Calibration buckets show whether "70% sure" means 70%.
4. A winner is named only if the 95% interval (dates resampled as blocks) clears zero on at least 30 shared cases over 8 dates.
   Otherwise the answer is "not enough evidence" or "no clear difference". The tool says so itself.
5. No tuning after looking. Changing a rule or a prompt is a new version and a fresh run.

## Limits to keep in mind
- **Memory leak**: a model may remember how a stock did after a past date. Blind bundles help but do not hide the date or the
  market. Dates after the model's knowledge cutoff are clean; earlier dates are not. Report them as separate tracks.
- **One regime**: stored prices cover about a year, so a past-date test sees one market. It is a smoke test, not proof.
- **The clean test is forward**: log decisions today from rules and agents alike, and score them as horizons mature.

## Commands (PowerShell, from `v2`)
```
uv run drishti decisions cases --start 2026-07-01 --end 2026-08-10 --every 3 --n-stocks 8 --gate entry,macro --cases eval\cases.json
uv run drishti decisions rules  --cases eval\cases.json
uv run drishti decisions export --cases eval\cases.json --out eval\bundles          # blind by default; the key goes to eval\bundles_key.json, outside the folder agents read
uv run drishti decisions import --key eval\bundles_key.json --answers eval\claude.jsonl --agent claude --model <model name>
uv run drishti decisions score
uv run drishti decisions compare --a claude/<model> --b rules/<rules version> --gate entry
```
An answers line: `{"case_id": "case-00007", "action": "ENTER", "confidence": 0.62, "thesis": "...", "invalidation": "...", "signals": ["price_context"]}`.
