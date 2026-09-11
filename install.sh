#!/usr/bin/env bash
# Drishti installer — run once from inside the unzipped Drishti folder (in WSL).
# Places the project at /home/abhi/Abhijith/Drishti, clones reference repos, builds NSE-MCP.
set -euo pipefail

TARGET="/home/abhi/Abhijith/Drishti"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Drishti setup"

# 1) Put the project at the target path (skip if already running from there)
if [ "$HERE" != "$TARGET" ]; then
  echo "==> Copying project to $TARGET"
  mkdir -p "$TARGET"
  cp -r "$HERE"/. "$TARGET"/
fi
cd "$TARGET"
mkdir -p reference briefs

# 2) Clone reference repos to MINE (read-only study; /port-patterns uses these)
echo "==> Cloning reference repos into reference/"
clone() { [ -d "reference/$2" ] || git clone --depth 1 "$1" "reference/$2" || echo "  (skip $2)"; }
clone https://github.com/virattt/ai-hedge-fund.git            ai-hedge-fund
clone https://github.com/TauricResearch/TradingAgents.git     TradingAgents
clone https://github.com/HKUDS/Vibe-Trading.git               Vibe-Trading
clone https://github.com/manitgupta/NSE-MCP.git               NSE-MCP

# 3) Build NSE-MCP (needs Node >=20)
if [ -d reference/NSE-MCP ]; then
  echo "==> Building NSE-MCP"
  ( cd reference/NSE-MCP && npm install && npm run build ) || echo "  (build NSE-MCP manually; see its README)"
fi

echo ""
echo "==> Done. Next:"
echo "   1) Edit config/watchlist.md with your names."
echo "   2) Confirm paths in .mcp.json (nse-mcp points to reference/NSE-MCP/dist/index.js)."
echo "   3) Pick/instal your nse-bse MCP (see config/reference-repos.md)."
echo "   4) Open this folder in Claude Code, then run:  /port-patterns"
echo "   5) Claude Code reads MASTER.md automatically-ish; if not, tell it: 'read MASTER.md'."
