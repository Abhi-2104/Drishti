# drishti-mcp

Drishti's own Model Context Protocol server — the equity-research tools this project owns,
kept separate from the third-party `nse-mcp` connector so our code is versioned in this repo.

## Tools
India: `get_screener`, `get_bse_announcements`, `get_ipos`, `get_macro_regime`,
`get_participant_oi`, `get_sector_rotation`.
US: `get_us_quote`, `get_us_fundamentals`, `get_us_filings` (SEC EDGAR).

## Sources
- Screener.in (fundamentals), NSE + BSE public feeds (filings, IPOs, participant OI archive),
  Yahoo Finance (quotes, macro, sector indices), SEC EDGAR `data.sec.gov` (US fundamentals/filings).

## Attribution
The NSE session/HTTP fetch layer (`src/http.ts`, `src/cache.ts`, `src/nse/`) is adapted from
[manitgupta/NSE-MCP](https://github.com/manitgupta/nse-mcp) (MIT), with hardening (browser
fingerprint, warmup timeout, request jitter) added for this project. The participant-OI
interpretation follows the Sensibull participant-data logic. Macro/sector-rotation concepts
adapted from an internal "Investor Copilot" engine (market-data logic only).

## Build
```
npm install && npm run build
```
Then point an MCP client at `dist/index.js` over stdio (see the repo's `.mcp.json`).
