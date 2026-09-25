# Drishti v2 — Phase 0 probe report

Run: 2026-09-26 04:59 IST · attempts per source: 3 · python 3.14.4 · Linux 6.18.33.1-microsoft-standard-WSL2

🟢 works from a public/official source every time · 🟡 works but only via scrape/unofficial source or not every attempt · 🔴 no working source · ⚪ not probed (needs a decision)

## Summary by signal

| | Layer | Signal | Best result |
|---|---|---|---|
| 🟢 | L2 | PCR / max pain / OI walls | NSE F&O bhavcopy (UDiFF): 3/3 OK — NIFTY 2026-09-29: PCR(OI)=0.93, max pain=23250, call wall=24000, put wall=23000, 37165 rows |
| 🟢 | L4 | EOD prices (all stocks) | NSE cash bhavcopy (UDiFF): 3/3 OK — 2660 EQ stocks; RELIANCE close=1226.00 |
| 🟢 | L2 | Participant OI (FII/Pro/Client) | NSE archive (nsearchives host): 3/3 OK — FII index futures net = -312,001 contracts |
| 🟢 | L3 | All index closes (RRG inputs, India VIX) | NSE ind_close_all: 3/3 OK — 166 indices; NIFTY IT=28160.9; INDIA VIX=12.16 |
| 🟢 | L2 | Bulk deals | NSE archive bulk.csv: 3/3 OK — 237 rows |
| 🟢 | L2 | Block deals | NSE archive block.csv: 3/3 OK — 6 rows |
| 🔴 | L0/L3 | Index history (NIFTY IT, 45d) | niftyindices.com: 0/3 OK — BLOCKED_HTML (HTML page returned where data was expected) |
| 🟢 | L1 | Dollar index (broad) | FRED DTWEXBGS: 3/3 OK — DTWEXBGS latest = 119.5133 (5193 obs since 2006-01-02) |
| 🟢 | L1 | US 10Y yield | FRED DGS10: 3/3 OK — DGS10 latest = 5.18 (16168 obs since 1962-01-02) |
| 🟢 | L1 | Brent crude | FRED DCOILBRENTEU: 3/3 OK — DCOILBRENTEU latest = 114.89 (9092 obs since 1987-05-20) |
| 🟢 | L1 | US VIX | FRED VIXCLS: 3/3 OK — VIXCLS latest = 14.21 (9279 obs since 1990-01-02) |
| 🟢 | L1 | USD/INR | FRED DEXINUS: 3/3 OK — DEXINUS latest = 95.8700 (13463 obs since 1973-01-02) |
| 🟢 | L1 | Fed balance sheet | FRED WALCL: 3/3 OK — WALCL latest = 6747704 (1241 obs since 2002-12-18) |
| 🟢 | L1 | US M2 | FRED M2SL: 3/3 OK — M2SL latest = 23342.8 (812 obs since 1959-01-01) |
| 🟢 | L1 | HY credit spread | FRED BAMLH0A0HYM2: 3/3 OK — BAMLH0A0HYM2 latest = 2.80 (787 obs since 2023-09-26) |
| 🟢 | L1 | Yield curve 10y-2y | FRED T10Y2Y: 3/3 OK — T10Y2Y latest = 0.36 (12577 obs since 1976-06-01) |
| 🟡 | L1 | Dollar index | Yahoo DX-Y.NYB: 3/3 OK — DX-Y.NYB = 101.035 |
| 🟡 | L1 | India VIX | Yahoo ^INDIAVIX: 3/3 OK — ^INDIAVIX = 12.16 |
| 🟡 | L3 | Nifty 50 | Yahoo ^NSEI: 3/3 OK — ^NSEI = 23140.5 |
| 🟡 | L3 | Nifty IT | Yahoo ^CNXIT: 3/3 OK — ^CNXIT = 28160.9 |
| 🟡 | L1 | Gold | Yahoo GC=F: 3/3 OK — GC=F = 4320.5 |
| 🟡 | L1 | Copper | Yahoo HG=F: 3/3 OK — HG=F = 6.779 |
| 🟡 | L0/L3 | Nifty IT 6mo history (niftyindices substitute) | Yahoo ^CNXIT range=6mo: 3/3 OK — 129 daily points, 2026-03-25 to 2026-09-25 |
| 🟡 | L4 | RELIANCE live-quote substitute | Yahoo RELIANCE.NS: 3/3 OK — RELIANCE.NS last=1226.0, age=49416s |
| 🔴 | - | NSE live session warm-up | nseindia.com homepage: 0/1 OK — BLOCKED (HTTP 403) |
| 🟡 | L2 | FII/DII cash flows | NSE live /api/fiidiiTradeReact: 3/3 OK — DII: net 2838.17; FII/FPI: net -3693.93 |
| 🟡 | L2 | Gift Nifty | NSE live /api/marketStatus: 3/3 OK — Gift Nifty=23237.5 (26-Sep-2026 02:39); market=['Close', 'Close'] |
| 🔴 | L4 | Live quote | NSE live /api/quote-equity: 0/3 OK — BLOCKED (HTTP 403) |
| 🟡 | L4 | Announcements | NSE live /api/corporate-announcements: 3/3 OK — 20 announcements |
| 🔴 | L2 | Option chain (live) | NSE live /api/option-chain-indices: 0/3 OK — NOT_FOUND (HTTP 404) |
| 🟡 | L2 | Short selling | NSE live /api/snapshot-capital-market-largedeal: 3/3 OK — 75 symbols short-sold; total qty=252955 |
| 🟢 | L4 | Announcements / insider / pledges | BSE API (v1 source): 3/3 OK — 5 announcements on page 1 |
| 🟢 | L2 | FII/FPI flows (official) | NSDL FPI daily report: 3/3 OK — page OK (57 KB), contains Equity |
| 🟢 | L2 | COT positioning | CFTC financial futures (weekly): 3/3 OK — 96 contracts; E-mini S&P row found |
| 🟡 | L4 | Fundamentals | Screener.in page: 3/3 OK — page OK (225 KB), contains Market Cap |
| 🟢 | L4 | US filings / 13F | SEC EDGAR: 3/3 OK — Apple Inc.: 1000 recent filings |
| ⚪ | L1 | India CPI/WPI/IIP | NOT PROBED: MoSPI/Office of Economic Adviser publish PDFs/press releases; needs a scraper decision |
| ⚪ | L1 | RBI forex reserves / stance | NOT PROBED: RBI WSS + MPC statements are PDFs/HTML; needs a scraper decision |
| 🔴 | L1 | PMI (India/US/China) | NOT PROBED: owned by S&P Global/ISM; no public data file |
| ⚪ | L4 | Credit ratings | NOT PROBED: agency sites; BSE disclosures partly cover it |
| ⚪ | L4 | Concall transcripts | NOT PROBED: come through BSE announcements (PDF attachments) |
| ⚪ | L6 | Holdings incl. SBI (deferred) | NOT PROBED: NSDL/CDSL CAS PDF is your own statement; test when L6 un-parks |

## Every source

| Layer | Signal | Source | Kind | OK | Statuses | Median ms | Data date | Derived / note |
|---|---|---|---|---|---|---|---|---|
| L2 | PCR / max pain / OI walls | NSE F&O bhavcopy (UDiFF) | public-file | 3/3 | OK,OK,OK | 72 | 2026-09-25 | NIFTY 2026-09-29: PCR(OI)=0.93, max pain=23250, call wall=24000, put wall=23000, 37165 rows |
| L4 | EOD prices (all stocks) | NSE cash bhavcopy (UDiFF) | public-file | 3/3 | OK,OK,OK | 40 | 2026-09-25 | 2660 EQ stocks; RELIANCE close=1226.00 |
| L2 | Participant OI (FII/Pro/Client) | NSE archive (nsearchives host) | public-file | 3/3 | OK,OK,OK | 31 | Sep 25, 2026"",,,,,,,,,,,,,, | FII index futures net = -312,001 contracts |
| L2 | Participant OI (FII/Pro/Client) | NSE archive (archives host, v1's URL) | public-file | 3/3 | OK,OK,OK | 114 | Sep 25, 2026"",,,,,,,,,,,,,, | FII index futures net = -312,001 contracts |
| L3 | All index closes (RRG inputs, India VIX) | NSE ind_close_all | public-file | 3/3 | OK,OK,OK | 55 | 25-09-2026 | 166 indices; NIFTY IT=28160.9; INDIA VIX=12.16 |
| L2 | Bulk deals | NSE archive bulk.csv | public-file | 3/3 | OK,OK,OK | 62 | 25-SEP-2026 | 237 rows |
| L2 | Block deals | NSE archive block.csv | public-file | 3/3 | OK,OK,OK | 100 | 25-SEP-2026 | 6 rows |
| L0/L3 | Index history (NIFTY IT, 45d) | niftyindices.com | unofficial-api | 0/3 | BLOCKED_HTML,BLOCKED_HTML,BLOCKED_HTML | 527 |  | HTML page returned where data was expected |
| L1 | Dollar index (broad) | FRED DTWEXBGS | official-api | 3/3 | OK,OK,OK | 67 | 2026-09-18 | DTWEXBGS latest = 119.5133 (5193 obs since 2006-01-02) |
| L1 | US 10Y yield | FRED DGS10 | official-api | 3/3 | OK,OK,OK | 67 | 2026-09-24 | DGS10 latest = 5.18 (16168 obs since 1962-01-02) |
| L1 | Brent crude | FRED DCOILBRENTEU | official-api | 3/3 | OK,OK,OK | 44 | 2026-09-22 | DCOILBRENTEU latest = 114.89 (9092 obs since 1987-05-20) |
| L1 | US VIX | FRED VIXCLS | official-api | 3/3 | OK,OK,OK | 62 | 2026-09-22 | VIXCLS latest = 14.21 (9279 obs since 1990-01-02) |
| L1 | USD/INR | FRED DEXINUS | official-api | 3/3 | OK,OK,OK | 45 | 2026-09-18 | DEXINUS latest = 95.8700 (13463 obs since 1973-01-02) |
| L1 | Fed balance sheet | FRED WALCL | official-api | 3/3 | OK,OK,OK | 30 | 2026-09-23 | WALCL latest = 6747704 (1241 obs since 2002-12-18) |
| L1 | US M2 | FRED M2SL | official-api | 3/3 | OK,OK,OK | 111 | 2026-08-01 | M2SL latest = 23342.8 (812 obs since 1959-01-01) |
| L1 | HY credit spread | FRED BAMLH0A0HYM2 | official-api | 3/3 | OK,OK,OK | 39 | 2026-09-24 | BAMLH0A0HYM2 latest = 2.80 (787 obs since 2023-09-26) |
| L1 | Yield curve 10y-2y | FRED T10Y2Y | official-api | 3/3 | OK,OK,OK | 103 | 2026-09-25 | T10Y2Y latest = 0.36 (12577 obs since 1976-06-01) |
| L1 | Dollar index | Yahoo DX-Y.NYB | unofficial-api | 3/3 | OK,OK,OK | 113 | 2026-09-26 02:29 | DX-Y.NYB = 101.035 |
| L1 | US 10Y yield | Yahoo ^TNX | unofficial-api | 3/3 | OK,OK,OK | 102 | 2026-09-26 00:29 | ^TNX = 5.184 |
| L1 | Brent crude | Yahoo BZ=F | unofficial-api | 3/3 | OK,OK,OK | 110 | 2026-09-26 02:29 | BZ=F = 97.47 |
| L1 | USD/INR | Yahoo INR=X | unofficial-api | 3/3 | OK,OK,OK | 112 | 2026-09-26 04:29 | INR=X = 95.802 |
| L1 | India VIX | Yahoo ^INDIAVIX | unofficial-api | 3/3 | OK,OK,OK | 124 | 2026-09-25 15:42 | ^INDIAVIX = 12.16 |
| L3 | Nifty 50 | Yahoo ^NSEI | unofficial-api | 3/3 | OK,OK,OK | 101 | 2026-09-25 15:32 | ^NSEI = 23140.5 |
| L3 | Nifty IT | Yahoo ^CNXIT | unofficial-api | 3/3 | OK,OK,OK | 71 | 2026-09-25 15:32 | ^CNXIT = 28160.9 |
| L1 | Gold | Yahoo GC=F | unofficial-api | 3/3 | OK,OK,OK | 113 | 2026-09-26 02:29 | GC=F = 4320.5 |
| L1 | Copper | Yahoo HG=F | unofficial-api | 3/3 | OK,OK,OK | 114 | 2026-09-26 02:29 | HG=F = 6.779 |
| L0/L3 | Nifty IT 6mo history (niftyindices substitute) | Yahoo ^CNXIT range=6mo | unofficial-api | 3/3 | OK,OK,OK | 49 | 2026-09-25 | 129 daily points, 2026-03-25 to 2026-09-25 |
| L4 | RELIANCE live-quote substitute | Yahoo RELIANCE.NS | unofficial-api | 3/3 | OK,OK,OK | 108 | 1790329499 | RELIANCE.NS last=1226.0, age=49416s |
| - | NSE live session warm-up | nseindia.com homepage | live-site | 0/1 | BLOCKED | 100 |  | HTTP 403 |
| L2 | FII/DII cash flows | NSE live /api/fiidiiTradeReact | live-site | 3/3 | OK,OK,OK | 108 | 25-Sep-2026 | DII: net 2838.17; FII/FPI: net -3693.93 |
| L1 | India VIX | NSE live /api/allIndices | live-site | 3/3 | OK,OK,OK | 32 | 25-Sep-2026 15:30 | 139 indices; INDIA VIX=12.18 |
| L2 | Gift Nifty | NSE live /api/marketStatus | live-site | 3/3 | OK,OK,OK | 34 | 26-Sep-2026 02:39 | Gift Nifty=23237.5 (26-Sep-2026 02:39); market=['Close', 'Close'] |
| L4 | Live quote | NSE live /api/quote-equity | live-site | 0/3 | BLOCKED,BLOCKED,BLOCKED | 28 |  | HTTP 403 |
| L4 | Announcements | NSE live /api/corporate-announcements | live-site | 3/3 | OK,OK,OK | 39 | 26-Sep-2026 00:29:48 | 20 announcements |
| L2 | Option chain (live) | NSE live /api/option-chain-indices | live-site | 0/3 | NOT_FOUND,NOT_FOUND,NOT_FOUND | 39 |  | HTTP 404 |
| L2 | Short selling | NSE live /api/snapshot-capital-market-largedeal | live-site | 3/3 | OK,OK,OK | 41 | 24-Sep-2026 | 75 symbols short-sold; total qty=252955 |
| L4 | Announcements / insider / pledges | BSE API (v1 source) | official-api | 3/3 | OK,OK,OK | 23 | 2026-09-25T22:13:19.383 | 5 announcements on page 1 |
| L2 | FII/FPI flows (official) | NSDL FPI daily report | public-file | 3/3 | OK,OK,OK | 160 |  | page OK (57 KB), contains Equity |
| L2 | COT positioning | CFTC financial futures (weekly) | public-file | 3/3 | OK,OK,OK | 43 | 2026-09-22 | 96 contracts; E-mini S&P row found |
| L4 | Fundamentals | Screener.in page | live-site | 3/3 | OK,OK,OK | 153 |  | page OK (225 KB), contains Market Cap |
| L4 | US filings / 13F | SEC EDGAR | official-api | 3/3 | OK,OK,OK | 84 |  | Apple Inc.: 1000 recent filings |
