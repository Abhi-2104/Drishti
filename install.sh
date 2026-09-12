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
# 4) Optional: Kite (Zerodha) — read-only portfolio context. NEVER trades. Off by default.
# ---------------------------------------------------------------------------
enable_kite="n"
[ "${DRISHTI_KITE:-}" = "1" ] && enable_kite="y"
if [ -t 0 ] && [ "$enable_kite" = "n" ]; then
  read -rp "Enable Kite (Zerodha) read-only portfolio context? It NEVER places trades. [y/N] " ans || true
  case "${ans:-}" in [Yy]*) enable_kite="y" ;; esac
fi
KITE_BLOCK=""
if [ "$enable_kite" = "y" ]; then
  KITE_BLOCK=',
    "kite": {
      "type": "url",
      "url": "https://mcp.kite.trade/sse",
      "_comment": "OPTIONAL, read-only portfolio context (holdings/positions/P&L). Drishti never places, changes, or cancels orders. Signs in on first use."
    }'
  echo "==> Kite enabled (read-only portfolio context)."
else
  echo "==> Kite skipped (enable later: re-run with DRISHTI_KITE=1 ./install.sh, or add the block to .mcp.json)."
fi

# ---------------------------------------------------------------------------
# 5) Generate .mcp.json with THIS machine's paths
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
    }$KITE_BLOCK
  }
}
JSON

# ---------------------------------------------------------------------------
# 6) Optional: private investor profile (personalizes sizing/preferences). Off by default.
# ---------------------------------------------------------------------------
if [ ! -f config/investor-profile.md ] && [ -f config/investor-profile.example.md ]; then
  make_profile="n"
  [ "${DRISHTI_PROFILE:-}" = "1" ] && make_profile="y"
  if [ -t 0 ] && [ "$make_profile" = "n" ]; then
    read -rp "Create a private investor profile (optional; shapes /entry sizing + sector prefs)? [y/N] " ans || true
    case "${ans:-}" in [Yy]*) make_profile="y" ;; esac
  fi
  if [ "$make_profile" = "y" ]; then
    cp config/investor-profile.example.md config/investor-profile.md
    echo "==> Created config/investor-profile.md (private, gitignored) — edit it to taste."
  fi
fi

echo ""
echo "==> Done. Everything below is OPTIONAL — Drishti works out of the box with defaults."
echo ""
echo "   System:"
echo "     - (once) sudo apt-get install -y poppler-utils   # for reading concall/prospectus PDFs"
echo ""
echo "   Optional config (all have working defaults; edit only what you want):"
echo "     - config/watchlist.md          names /brief and /screen scan (a starter set is included)"
echo "     - config/news-sources.md       the news whitelist (sensible tiers included)"
echo "     - config/investor-profile.md   private sizing/prefs (opt-in above; generic if absent)"
echo "     - Kite (Zerodha)               read-only portfolio context, never trades (opt-in above)"
echo ""
echo "   Then: open this folder in Claude Code (it reads CLAUDE.md automatically; say"
echo "   'read MASTER.md' for full context), and try:   /macro    then   /deep-dive <NAME>"
