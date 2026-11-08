import YahooFinance from "yahoo-finance2";

const yahooFinance = new YahooFinance();

// Layer 3 — sector rotation. Relative strength (RS) of each sector vs Nifty 50, its 20-day
// average, and the momentum/acceleration that place it on a relative-rotation-graph (RRG)
// quadrant: Leading / Weakening / Improving / Lagging.
const BENCH = "^NSEI"; // Nifty 50
const SECTORS: Record<string, string> = {
  IT: "^CNXIT", Bank: "^NSEBANK", Pharma: "^CNXPHARMA", FMCG: "^CNXFMCG",
  Metal: "^CNXMETAL", Auto: "^CNXAUTO", Realty: "^CNXREALTY", Energy: "^CNXENERGY",
  "Midcap 150": "NIFTYMIDCAP150.NS",
};

async function closes(symbol: string): Promise<Map<string, number>> {
  const period1 = new Date(Date.now() - 70 * 24 * 3600 * 1000);
  const r: any = await yahooFinance.chart(symbol, { period1, interval: "1d" });
  const m = new Map<string, number>();
  for (const q of r?.quotes ?? []) {
    if (q?.date && q?.close != null) m.set(new Date(q.date).toISOString().slice(0, 10), q.close);
  }
  return m;
}

function mean(a: number[]): number { return a.reduce((s, x) => s + x, 0) / a.length; }
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Yahoo intermittently returns a truncated series (~10 pts) for some sector indices, and
// throttles rapid calls. Retry up to twice when the fetch throws OR returns too little
// history to compute a 20-day average.
async function closesResilient(symbol: string): Promise<Map<string, number>> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const m = await closes(symbol);
      if (m.size >= 30) return m;
    } catch {
      /* fall through to retry */
    }
    await sleep(700);
  }
  return closes(symbol); // final attempt; caller handles a still-short series
}

export interface SectorRow {
  name: string;
  momentum: number;       // % of RS above/below its 20-day average
  acceleration: number;   // change in momentum vs prior day
  quadrant: "Leading" | "Weakening" | "Improving" | "Lagging";
  rotationAlert: boolean;  // RS crossed above its 20-day average in the last 3 sessions
}

export interface SectorRotationResult {
  asOf: string | null;
  sectors: SectorRow[];
  unavailable: string[]; // sectors Yahoo returned too little history for this run (UNKNOWN, not absent)
  note: string;
  error: string | null;
}

export async function getSectorRotation(): Promise<SectorRotationResult> {
  let bench: Map<string, number>;
  try {
    bench = await closes(BENCH);
  } catch (e) {
    return { asOf: null, sectors: [], unavailable: [], note: "", error: `benchmark fetch failed: ${(e as Error).message}` };
  }
  if (bench.size < 25) return { asOf: null, sectors: [], unavailable: [], note: "", error: "insufficient benchmark history" };

  const dates = [...bench.keys()].sort();
  const rows: SectorRow[] = [];
  const unavailable: string[] = [];

  for (const [name, sym] of Object.entries(SECTORS)) {
    let sec: Map<string, number>;
    try { sec = await closesResilient(sym); } catch { unavailable.push(name); continue; }
    await sleep(150); // pace to avoid Yahoo throttling the run
    // aligned RS series on common dates
    const rs: number[] = [];
    for (const d of dates) {
      const s = sec.get(d), b = bench.get(d);
      if (s != null && b) rs.push(s / b);
    }
    if (rs.length < 25) { unavailable.push(name); continue; }

    const ma = (i: number) => mean(rs.slice(i - 19, i + 1)); // 20-day
    const last = rs.length - 1;
    const maLast = ma(last), maPrev = ma(last - 1);
    const mom = ((rs[last] - maLast) / maLast) * 100;
    const momPrev = ((rs[last - 1] - maPrev) / maPrev) * 100;
    const accel = mom - momPrev;

    let quadrant: SectorRow["quadrant"];
    if (mom >= 0) quadrant = accel > 0 ? "Leading" : "Weakening";
    else quadrant = accel > 0 ? "Improving" : "Lagging";

    let crossed = false;
    for (let i = last - 2; i <= last; i++) {
      if (i < 20) continue;
      if (rs[i] > ma(i) && rs[i - 1] <= ma(i - 1)) crossed = true;
    }

    rows.push({
      name,
      momentum: Number(mom.toFixed(2)),
      acceleration: Number(accel.toFixed(2)),
      quadrant,
      rotationAlert: crossed,
    });
  }

  rows.sort((a, b) => b.momentum - a.momentum);
  return {
    asOf: dates[dates.length - 1] ?? null,
    sectors: rows,
    unavailable,
    note: "RS = sector index / Nifty 50. Leading = strong+accelerating; Improving = weak but turning up (early rotation); Weakening = strong but fading; Lagging = weak+falling. rotationAlert flags a fresh cross above the 20-day average.",
    error: null,
  };
}
