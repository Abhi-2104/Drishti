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

## Notes
- `.mcp.json` is **generated per-machine** (not committed) — always run `install.sh`, never copy
  someone else's `.mcp.json`.
- `.mcp.json.example` shows what it produces.
- **Kite** (Zerodha holdings) is optional; it signs in inside Claude Code on first use.
- If the NSE feeds time out, it's usually an IP/rate issue — a mobile hotspot / residential
  connection clears it (see the operating notes).
- The macro and US tools use free public sources (Yahoo Finance, SEC EDGAR) — no API keys.
