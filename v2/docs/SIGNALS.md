# Drishti signals, in plain words

Each signal is one reading, computed from data already stored, with the date it describes. None of them is a buy or
sell instruction on its own. The commands (`/macro`, `/entry`, `/exit`) combine them using the rulebook: two agreeing
signals before a GO, one serious red flag can veto, macro decides timing, not the thesis.

Every reading also lists what it could not see ("missing"). A missing input is never treated as neutral.
Readings marked **provisional** use numbers nobody has decided yet; treat them as context, not as calibrated.

To change a number: edit `signal-settings.toml` (run `uv run drishti paths` to see where it is). Remove the `#` in
front of a setting, change it, save. The shipped defaults and where each came from are in `drishti/signals/params.toml`.

| Signal | What it tells you | How to read it | Main settings |
|---|---|---|---|
| `macro_regime` | The global backdrop India trades against: dollar, US yields, oil, volatility, rupee | -6 (hostile) to +6 (friendly). At or below -2 is a bearish backdrop. Yahoo values are checked against FRED and any disagreement is listed | `[macro_regime]` thresholds (your brother's v1 numbers) |
| `participant_oi` | Who is long and short in index futures and options: foreign institutions and pros ("smart money") vs retail clients | Score = (FII + Pro) - Client. The percentile line says where today sits in the last year. Label by percentile (recommended) or by v1's fixed +/-4 | `label_by`, `strong_percentile`, `strong_threshold` |
| `options_flow:NIFTY` / `:BANKNIFTY` | Option positioning for the nearest expiry: put/call ratio, max pain, the strikes with the most open interest | PCR high = put-heavy (often support from put writers, or heavy hedging). Call walls = resistance, put walls = support | `label_by`, `pcr_bullish_above`, `pcr_bearish_below` |
| `fii_dii` | Foreign and domestic institutions' cash buying/selling | Today, 5 and 20 sessions, plus streaks. Confirmation, not a reason on its own. History builds from 8 Nov 2026 (NSE gives one day at a time) | `short_window`, `long_window` |
| `sector_rotation` | Which sectors are gaining or losing strength against the Nifty | Leading = strong and accelerating; Improving = weak but turning up (early rotation); Weakening = strong but fading; Lagging = weak and falling | none |
| `midcap_ratio` | Whether midcaps are beating large caps | Outperforming / lagging / mixed, from the Midcap150-to-Nifty ratio | none |
| `cycle_phase` | A rough read of where the economic cycle is: Recovery, Expansion, Late, Contraction | Probabilities from v1's /macro rules. Partial: PMI and inflation are not collected yet and are always listed as missing | none |
| `liquidity` | US money supply and the Fed's balance sheet | Expanding / contracting / mixed. The "master driver" in v0's guides | none |
| `credit_spreads` | US junk-bond spreads and the yield curve | Widening fast = genuine risk-off; calm = noise | `widening_bp`, `stress_level_pct` |
| `seasonality` | Where we are in India's calendar: quarter theme, budget, monsoon, results season, next RBI MPC | Context for timing only | none |
| `insider_cluster` (one company) | Promoters, directors and key staff buying or selling in the open market | A selling cluster is a strong negative; size buying is a strong positive (scoring.md). Gifts, ESOPs and transfers are ignored | `cluster_min_persons`, `cluster_days`, `size_buy_crore` |
| `pledge` (one company) | How much of the promoters' holding is pledged, and whether it rose | Over 25% pledged = strong caution; rising pledge = red flag (scoring.md) | `strong_caution_pledged_pct`, `rising_min_pp` |

Try any of them: `uv run drishti signal <name>` (add `--query SYMBOL` for the one-company signals,
`--replay 120 --summary` to see how often each label fired over the last 120 sessions).
