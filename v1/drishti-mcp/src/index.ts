#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

import { getScreener } from "./tools/screener.js";
import { getBseAnnouncements } from "./tools/bseAnnouncements.js";
import { getIpos } from "./tools/ipos.js";
import { getMacroRegime } from "./tools/macroRegime.js";
import { getParticipantOi } from "./tools/participantOi.js";
import { getSectorRotation } from "./tools/sectorRotation.js";
import { getUsQuote } from "./tools/usQuote.js";
import { getUsFundamentals } from "./tools/usFundamentals.js";
import { getUsFilings } from "./tools/usFilings.js";

const server = new Server({ name: "drishti-mcp", version: "0.1.0" }, { capabilities: { tools: {} } });

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "get_screener",
      description:
        "Company fundamentals from Screener.in (NSE AND BSE). Resolves symbol/name/BSE-code to the right company. Returns top ratios (market cap, P/E, ROCE, ROE, book value, dividend yield, high/low), the P&L, quarterly, balance-sheet, cash-flow, ratios and shareholding tables as text, plus concall and annual-report document links.",
      inputSchema: { type: "object", properties: { symbol: { type: "string", description: "NSE symbol (TITAN), BSE code (511658), or name (Nettlinx). Required." } }, required: ["symbol"] },
    },
    {
      name: "get_bse_announcements",
      description:
        "BSE corporate filings for a company — the only disclosure path for BSE-only names. Resolves NSE symbol / name / 6-digit BSE code. Returns date, category, sub-category, headline, critical flag, and a direct PDF link per filing. Filter category for insider / SAST / pledge.",
      inputSchema: { type: "object", properties: { symbol: { type: "string", description: "NSE symbol, name, or 6-digit BSE code. Required." }, daysBack: { type: "number", description: "Look back N days. Default 90." }, limit: { type: "number", description: "Max filings. Default 40." } }, required: ["symbol"] },
    },
    {
      name: "get_ipos",
      description:
        "Mainboard IPOs from NSE — currently open and upcoming public issues with company, symbol, price band, issue size, lot size, dates, status. A failed feed is reported in `errors` (UNKNOWN, never 'no IPOs'). GMP (grey-market premium) is deliberately not provided.",
      inputSchema: { type: "object", properties: { status: { type: "string", enum: ["current", "upcoming", "all"], description: "Default all." }, query: { type: "string", description: "Optional name/symbol filter." } } },
    },
    {
      name: "get_macro_regime",
      description:
        "Global macro backdrop for Indian equities: DXY (US dollar index), US 10-year yield, Brent crude, India VIX, USD/INR, US VIX — with a macro-only regime score and plain-English signals. Thresholds are medium-confidence heuristics. Combine with get_participant_oi.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "get_participant_oi",
      description:
        "Derivatives 'smart money' read from NSE's participant-wise open-interest (OI) archive. FII/DII/Pro/Client net futures + options with Sensibull labels, and Smart-Money Score = (FII+Pro) - Client (positive = institutions long, retail short = bullish). FII/Pro = smart money; Client = contrarian; DII ignored for F&O. Failed fetch is UNKNOWN, never 'neutral'.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "get_sector_rotation",
      description:
        "Sector rotation via relative strength vs Nifty 50: each sector's momentum, acceleration, RRG quadrant (Leading/Weakening/Improving/Lagging), and a fresh-cross rotation alert. Sectors Yahoo couldn't serve enough history for appear in `unavailable`.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "get_us_quote",
      description:
        "Live US stock quote (Yahoo Finance) in USD: price, 52-week range, market cap, P/E, dividend yield, next earnings date. For US-listed names (AAPL, MSFT, NVDA).",
      inputSchema: { type: "object", properties: { ticker: { type: "string", description: "US ticker, no suffix. Required." } }, required: ["ticker"] },
    },
    {
      name: "get_us_fundamentals",
      description:
        "US company fundamentals from SEC EDGAR (XBRL). Restated annual (10-K) revenue, net income, operating income, assets, liabilities, equity, and operating cash flow across recent fiscal years, in USD. Derive margins/ROE from these.",
      inputSchema: { type: "object", properties: { ticker: { type: "string", description: "US ticker. Required." }, years: { type: "number", description: "Fiscal years back. Default 4." } }, required: ["ticker"] },
    },
    {
      name: "get_us_filings",
      description:
        "Recent US SEC filings for a company: 10-K/10-Q (reports), 8-K (material events), Form 4 (insider transactions), SC 13D/G (large stakes) — with direct document links. Fetch a link + Read it for detail.",
      inputSchema: { type: "object", properties: { ticker: { type: "string", description: "US ticker. Required." }, forms: { type: "array", items: { type: "string" }, description: "Form-type filter, e.g. [\"10-K\",\"4\"]. Omit for the common set." }, limit: { type: "number", description: "Max filings. Default 25." } }, required: ["ticker"] },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;
  const a = (args ?? {}) as Record<string, unknown>;
  const str = (k: string) => (typeof a[k] === "string" ? (a[k] as string) : undefined);
  const num = (k: string) => (typeof a[k] === "number" ? (a[k] as number) : undefined);

  try {
    let result: unknown;
    switch (name) {
      case "get_screener": {
        const s = str("symbol"); if (!s) throw new Error("symbol is required");
        result = await getScreener({ symbol: s }); break;
      }
      case "get_bse_announcements": {
        const s = str("symbol"); if (!s) throw new Error("symbol is required");
        result = await getBseAnnouncements({ symbol: s, daysBack: num("daysBack"), limit: num("limit") }); break;
      }
      case "get_ipos":
        result = await getIpos({ status: str("status") as any, query: str("query") }); break;
      case "get_macro_regime":
        result = await getMacroRegime(); break;
      case "get_participant_oi":
        result = await getParticipantOi(); break;
      case "get_sector_rotation":
        result = await getSectorRotation(); break;
      case "get_us_quote": {
        const t = str("ticker"); if (!t) throw new Error("ticker is required");
        result = await getUsQuote({ ticker: t }); break;
      }
      case "get_us_fundamentals": {
        const t = str("ticker"); if (!t) throw new Error("ticker is required");
        result = await getUsFundamentals({ ticker: t, years: num("years") }); break;
      }
      case "get_us_filings": {
        const t = str("ticker"); if (!t) throw new Error("ticker is required");
        result = await getUsFilings({ ticker: t, forms: Array.isArray(a.forms) ? (a.forms as string[]) : undefined, limit: num("limit") }); break;
      }
      default:
        throw new Error(`Unknown tool: ${name}`);
    }
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  } catch (err) {
    return { content: [{ type: "text", text: `Error: ${err instanceof Error ? err.message : String(err)}` }], isError: true };
  }
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("drishti-mcp running on stdio");
}
main().catch((e) => { console.error(e); process.exit(1); });
