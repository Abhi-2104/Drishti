# Setup — getting Drishti running on a new machine

For handing the project to someone else (or setting up a fresh machine).

## Prerequisites
- **Claude Code** installed, with an Anthropic account signed in.
- **Node.js ≥ 20** and **git**.
- **WSL / Linux / macOS** (Windows: use WSL).
- Optional but recommended: **poppler-utils** (`sudo apt-get install -y poppler-utils`) —
  needed to read concall and IPO-prospectus PDFs.

## Three steps
```bash
git clone <the-repo-url> Drishti
cd Drishti
./install.sh
```

`install.sh` does everything:
- checks Node/git are present,
- clones the third-party `nse-mcp` (base NSE tools) into `reference/`,
- installs and builds both MCP servers (`drishti-mcp` = our tools, `nse-mcp` = base tools),
- writes `.mcp.json` with **this machine's paths** (this is why a plain clone isn't enough —
  the config must point at wherever the repo lives).

Then open the `Drishti` folder in **Claude Code** and try:
```
/macro
/deep-dive INFY
```

## Optional setup — nothing is required
Drishti runs out of the box with sensible defaults. Customize only what you want:
- **config/watchlist.md** — the names `/brief` and `/screen` scan (a starter set ships).
- **config/news-sources.md** — the news whitelist (default tiers ship).
- **config/investor-profile.md** — a private, opt-in personalization (risk appetite, horizon,
  max position size, sector prefs). The installer offers to create it from
  `config/investor-profile.example.md`; if absent, commands stay generic. Gitignored, never
  committed.
- **Kite (Zerodha)** — optional **read-only portfolio context** (holdings/positions/P&L) so
  `/exit` and `/entry` can reason against real positions. The installer prompts (default off; or
  `DRISHTI_KITE=1 ./install.sh`). **Drishti never places, changes, or cancels trades** — decision
  support only.

## Notes
- `.mcp.json` is **generated per-machine** (not committed) — always run `install.sh`, never copy
  someone else's. `.mcp.json.example` shows what it produces.
- If NSE feeds time out, it's usually an IP/rate issue — a mobile hotspot / residential
  connection clears it.
- Macro and US tools use free public sources (Yahoo Finance, SEC EDGAR) — no API keys.
