// Participant-wise Open Interest (OI) — the "smart money" derivatives read.
// Source: NSE's daily public archive CSV (archives.nseindia.com), which stays reachable
// even when the www.nseindia.com/api endpoints are rate-blocked.
//
// Interpretation (Sensibull logic): FII + Pro are sophisticated and usually right; Client
// (retail) is usually wrong, so a contrarian tell; DII is SIP/cash-driven, ignored for F&O.
// Smart-Money Score = (FII_score + Pro_score) - Client_score.

const ARCHIVE = "https://archives.nseindia.com/content/nsccl/fao_participant_oi_";
const UA =
  process.env.NSE_MCP_UA ??
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

type Seg = "Index Futures" | "Call Options" | "Put Options";
const SEGMENTS: Seg[] = ["Index Futures", "Call Options", "Put Options"];
const PLAYERS = ["FII", "DII", "Pro", "Client"] as const;
type Player = (typeof PLAYERS)[number];

interface Nets { "Index Futures": number; "Call Options": number; "Put Options": number; }

function ddmmyyyy(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}${p(d.getMonth() + 1)}${d.getFullYear()}`;
}

async function fetchCsv(dstr: string): Promise<string | null> {
  try {
    const res = await fetch(`${ARCHIVE}${dstr}.csv`, {
      headers: { "User-Agent": UA, Referer: "https://www.nseindia.com/" },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return null;
    const text = await res.text();
    return text.includes("Client Type") ? text : null;
  } catch {
    return null;
  }
}

/** Find the most recent weekday CSV at/or before `start`, returning up to `count` days. */
async function fetchRecent(start: Date, count: number): Promise<Array<{ date: string; text: string }>> {
  const out: Array<{ date: string; text: string }> = [];
  const d = new Date(start);
  for (let i = 0; i < 10 && out.length < count; i++, d.setDate(d.getDate() - 1)) {
    if (d.getDay() === 0 || d.getDay() === 6) continue;
    const dstr = ddmmyyyy(d);
    const text = await fetchCsv(dstr);
    if (text) out.push({ date: dstr, text });
  }
  return out;
}

function parse(text: string): Record<Player, Nets> {
  const lines = text.split(/\r?\n/);
  const hi = lines.findIndex((l) => l.startsWith("Client Type"));
  const cols = lines[hi].split(",").map((c) => c.trim());
  const idx = (name: string) => cols.indexOf(name);
  const iFL = idx("Future Index Long"), iFS = idx("Future Index Short");
  const iCL = idx("Option Index Call Long"), iCS = idx("Option Index Call Short");
  const iPL = idx("Option Index Put Long"), iPS = idx("Option Index Put Short");

  const result = {} as Record<Player, Nets>;
  for (let i = hi + 1; i < lines.length; i++) {
    const cells = lines[i].split(",").map((c) => c.trim());
    const who = cells[0] as Player;
    if (!(PLAYERS as readonly string[]).includes(who)) continue;
    const num = (j: number) => (j >= 0 ? Number(cells[j] || 0) : 0);
    result[who] = {
      "Index Futures": num(iFL) - num(iFS),
      "Call Options": num(iCL) - num(iCS),
      "Put Options": num(iPL) - num(iPS),
    };
  }
  return result;
}

// Sensibull: for puts, net SHORT is bullish; for futures/calls, net LONG is bullish.
// Combine absolute stance with day-change direction into strong/mild/indecisive.
function label(net: number, change: number, seg: Seg): { label: string; cls: string } {
  const bullishNet = seg === "Put Options" ? net < 0 : net > 0;
  const bullishChg = seg === "Put Options" ? change < 0 : change > 0;
  if (bullishNet && bullishChg) return { label: "Strong Bullish", cls: "bull-strong" };
  if (bullishNet && !bullishChg) return { label: "Mild Bullish", cls: "bull-mild" };
  if (!bullishNet && !bullishChg) return { label: "Strong Bearish", cls: "bear-strong" };
  return { label: "Mild Bearish", cls: "bear-mild" };
}

const SCORE: Record<string, number> = {
  "bull-strong": 2, "bull-mild": 1, neu: 0, "bear-mild": -1, "bear-strong": -2,
};

export interface ParticipantOiResult {
  asOf: string | null;
  smartMoneyScore: number | null; // (FII + Pro) - Client ; positive = institutions long vs retail
  participantScores: Record<Player, number> | null;
  stance: Record<Player, Array<{ segment: Seg; net: number; change: number; label: string }>> | null;
  interpretation: string;
  note: string;
  error: string | null;
}

export async function getParticipantOi(): Promise<ParticipantOiResult> {
  const days = await fetchRecent(new Date(), 2);
  if (days.length === 0) {
    return {
      asOf: null, smartMoneyScore: null, participantScores: null, stance: null,
      interpretation: "", note: "",
      error: "Participant-OI CSV unavailable for recent days (UNKNOWN, not 'neutral').",
    };
  }
  const today = parse(days[0].text);
  const prior = days[1] ? parse(days[1].text) : null;

  const stance = {} as Record<Player, Array<{ segment: Seg; net: number; change: number; label: string }>>;
  const participantScores = {} as Record<Player, number>;

  for (const p of PLAYERS) {
    stance[p] = [];
    let sum = 0;
    for (const seg of SEGMENTS) {
      const net = today[p]?.[seg] ?? 0;
      const change = net - (prior?.[p]?.[seg] ?? net);
      const { label: lab, cls } = label(net, change, seg);
      sum += SCORE[cls] ?? 0;
      stance[p].push({ segment: seg, net, change, label: lab });
    }
    participantScores[p] = sum;
  }

  const smart = (participantScores.FII + participantScores.Pro) - participantScores.Client;
  let interp: string;
  if (smart >= 4) interp = `Strong bullish: institutions (FII+Pro) positioned long while retail (Client) is short (score +${smart}).`;
  else if (smart <= -4) interp = `Strong bearish: institutions positioned short while retail is long (score ${smart}).`;
  else interp = `No strong smart-money vs retail divergence (score ${smart}).`;

  return {
    asOf: days[0].date,
    smartMoneyScore: smart,
    participantScores,
    stance,
    interpretation: interp,
    note: "FII/Pro = smart money (usually right); Client = retail (contrarian); DII ignored for F&O direction. Change vs prior trading day.",
    error: null,
  };
}
