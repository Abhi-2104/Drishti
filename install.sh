#!/usr/bin/env bash
# Drishti bootstrap — run once from inside the cloned repo.
#   git clone <repo> && cd Drishti && ./install.sh
# Works from wherever the repo lives (no hardcoded paths); regenerates .mcp.json
# with this machine's real path so the MCP servers resolve.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"
echo "==> Drishti bootstrap in: $REPO"
mkdir -p reference briefs

# ---------------------------------------------------------------------------
# 1) Prerequisites
# ---------------------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1; }
missing=0
for c in git node npm; do
  if ! need "$c"; then echo "  MISSING: $c (required)"; missing=1; fi
done
if need node; then
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 20 ] || { echo "  Node $major found; need >= 20."; missing=1; }
fi
if [ "$missing" = 1 ]; then
  echo "==> Install the missing tools, then re-run. (Node >=20, git, npm.)"
  exit 1
fi

# poppler-utils: needed to read concall / prospectus PDFs. Warn, don't block.
if ! need pdftotext; then
  echo "  NOTE: poppler-utils not found — concall/prospectus PDF reading will not work."
  echo "        Install it with:  sudo apt-get install -y poppler-utils"
fi

# ---------------------------------------------------------------------------
# 2) Reference repos (optional study material for /port-patterns; gitignored)
# ---------------------------------------------------------------------------
echo "==> Cloning reference repos into reference/ (non-fatal if offline)"
clone() { [ -d "reference/$2" ] || git clone --depth 1 "$1" "reference/$2" || echo "  (skip $2)"; }
clone https://github.com/manitgupta/NSE-MCP.git            NSE-MCP        # base NSE tools (used)
clone https://github.com/virattt/ai-hedge-fund.git         ai-hedge-fund  # /port-patterns study
clone https://github.com/TauricResearch/TradingAgents.git  TradingAgents  # /port-patterns study

# ---------------------------------------------------------------------------
# 3) Build the MCP servers
# ---------------------------------------------------------------------------
if [ -d reference/NSE-MCP ]; then
  echo "==> Building nse-mcp (base NSE tools)"
  ( cd reference/NSE-MCP && npm install --silent && npm run build ) || echo "  (build nse-mcp manually; see its README)"
fi

echo "==> Building drishti-mcp (our tools: Screener, BSE, IPOs, macro, participant OI, sector, US EDGAR)"
( cd drishti-mcp && npm install --silent && npm run build ) || { echo "  drishti-mcp build FAILED — fix before using."; exit 1; }

# ---------------------------------------------------------------------------
# 4) Generate .mcp.json with THIS machine's paths
# ---------------------------------------------------------------------------
echo "==> Writing .mcp.json for $REPO"
[ -f .mcp.json ] && cp .mcp.json .mcp.json.bak
cat > .mcp.json <<JSON
{
  "mcpServers": {
    "drishti-mcp": {
      "command": "node",
      "args": ["$REPO/drishti-mcp/dist/index.js"],
      "_comment": "OUR server (in-repo, committed). Screener, BSE filings, IPOs, macro regime, participant OI, sector rotation, US EDGAR."
    },
    "nse-mcp": {
      "command": "node",
      "args": ["$REPO/reference/NSE-MCP/dist/index.js"],
      "_comment": "manitgupta/NSE-MCP (third-party clone). Base NSE tools: quotes, insider, bulk/block deals, FII/DII, announcements, corp actions, movers, indices, short-selling."
    },
    "kite": {
      "type": "url",
      "url": "https://mcp.kite.trade/sse",
      "_comment": "OPTIONAL — Zerodha Kite MCP (your own holdings). Needs your Zerodha login on first use. Remove if unused."
    }
  }
}
JSON

echo ""
echo "==> Done."
echo "   1) (once)  sudo apt-get install -y poppler-utils     # if not already, for PDF reading"
echo "   2) Edit    config/watchlist.md with your names."
echo "   3) Open this folder in Claude Code."
echo "   4) It reads CLAUDE.md automatically; for full context tell it: 'read MASTER.md'."
echo "   5) Try:    /macro     then    /deep-dive <NAME>"
echo ""
echo "   Kite (your Zerodha holdings) is optional — it authenticates in-app on first use."
