# Drishti bootstrap for native Windows (PowerShell).
#   Run from inside the cloned repo:  .\install.ps1
# Builds both MCP servers and writes .mcp.json with THIS machine's paths.
# (On WSL/Linux/macOS use install.sh instead.)

$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot
Set-Location $Repo
Write-Host "==> Drishti bootstrap in: $Repo"
New-Item -ItemType Directory -Force -Path reference, briefs | Out-Null

# ---------------------------------------------------------------------------
# 1) Prerequisites
# ---------------------------------------------------------------------------
$missing = $false
foreach ($c in "git", "node", "npm") {
  if (-not (Get-Command $c -ErrorAction SilentlyContinue)) {
    Write-Host "  MISSING: $c (required)"; $missing = $true
  }
}
if (Get-Command node -ErrorAction SilentlyContinue) {
  $major = [int](node -p "process.versions.node.split('.')[0]")
  if ($major -lt 20) { Write-Host "  Node $major found; need >= 20."; $missing = $true }
}
if ($missing) {
  Write-Host "==> Install the missing tools, then re-run."
  Write-Host "    Node 20+:  winget install OpenJS.NodeJS.LTS    (or nodejs.org)"
  Write-Host "    Git:       winget install Git.Git"
  exit 1
}

# poppler (for reading concall / prospectus PDFs) — warn, don't block.
if (-not (Get-Command pdftotext -ErrorAction SilentlyContinue)) {
  Write-Host "  NOTE: poppler not found — concall/prospectus PDF reading will not work."
  Write-Host "        Install:  winget install oschwartz10612.Poppler"
  Write-Host "        (or download poppler-windows, add its bin\ to PATH)"
}

# ---------------------------------------------------------------------------
# 2) Reference repos (optional; gitignored)
# ---------------------------------------------------------------------------
function Clone-Ref($url, $name) {
  if (-not (Test-Path "reference/$name")) {
    git clone --depth 1 $url "reference/$name" 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host "  (skip $name)" }
  }
}
Write-Host "==> Cloning reference repos into reference/"
Clone-Ref "https://github.com/manitgupta/NSE-MCP.git" "NSE-MCP"
Clone-Ref "https://github.com/virattt/ai-hedge-fund.git" "ai-hedge-fund"
Clone-Ref "https://github.com/TauricResearch/TradingAgents.git" "TradingAgents"

# ---------------------------------------------------------------------------
# 3) Build the MCP servers
# ---------------------------------------------------------------------------
if (Test-Path "reference/NSE-MCP") {
  Write-Host "==> Building nse-mcp (base NSE tools)"
  Push-Location reference/NSE-MCP; npm install --silent; npm run build; Pop-Location
}
Write-Host "==> Building drishti-mcp (our tools)"
Push-Location drishti-mcp; npm install --silent; npm run build; Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "  drishti-mcp build FAILED — fix before using."; exit 1 }

# ---------------------------------------------------------------------------
# 4) Optional: Kite (read-only portfolio context; never trades). Off by default.
# ---------------------------------------------------------------------------
$enableKite = ($env:DRISHTI_KITE -eq "1")
if (-not $enableKite) {
  $ans = Read-Host "Enable Kite (Zerodha) read-only portfolio context? It NEVER places trades. [y/N]"
  if ($ans -match '^[Yy]') { $enableKite = $true }
}
$kiteBlock = ""
if ($enableKite) {
  $kiteBlock = @"
,
    "kite": {
      "type": "url",
      "url": "https://mcp.kite.trade/sse",
      "_comment": "OPTIONAL, read-only portfolio context. Drishti never places, changes, or cancels orders."
    }
"@
  Write-Host "==> Kite enabled (read-only)."
} else {
  Write-Host "==> Kite skipped (enable later: set DRISHTI_KITE=1 and re-run)."
}

# ---------------------------------------------------------------------------
# 5) Write .mcp.json with THIS machine's paths (forward slashes work for Node on Windows)
# ---------------------------------------------------------------------------
$p = $Repo -replace '\\', '/'
if (Test-Path .mcp.json) { Copy-Item .mcp.json .mcp.json.bak -Force }
$json = @"
{
  "mcpServers": {
    "drishti-mcp": {
      "command": "node",
      "args": ["$p/drishti-mcp/dist/index.js"],
      "_comment": "OUR server (in-repo). Screener, BSE filings, IPOs, macro regime, participant OI, sector rotation, US EDGAR."
    },
    "nse-mcp": {
      "command": "node",
      "args": ["$p/reference/NSE-MCP/dist/index.js"],
      "_comment": "manitgupta/NSE-MCP (third-party clone). Base NSE tools."
    }$kiteBlock
  }
}
"@
Set-Content -Path .mcp.json -Value $json -Encoding UTF8

# ---------------------------------------------------------------------------
# 6) Optional: private investor profile
# ---------------------------------------------------------------------------
if ((-not (Test-Path config/investor-profile.md)) -and (Test-Path config/investor-profile.example.md)) {
  $ans = Read-Host "Create a private investor profile (optional; shapes /entry sizing + prefs)? [y/N]"
  if ($ans -match '^[Yy]') {
    Copy-Item config/investor-profile.example.md config/investor-profile.md
    Write-Host "==> Created config/investor-profile.md (private, gitignored) — edit to taste."
  }
}

Write-Host ""
Write-Host "==> Done. Everything else is OPTIONAL — Drishti works out of the box."
Write-Host "   1) (once) install poppler for PDF reading (see note above)."
Write-Host "   2) Edit config/watchlist.md with your names."
Write-Host "   3) Set commit identity:  git config user.email 2022115003@student.annauniv.edu; git config user.name Abhi-2104"
Write-Host "   4) Open this folder in Claude Code, then try:  /macro   or   /deep-dive INFY"
