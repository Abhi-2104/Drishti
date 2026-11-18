"""The weekly evidence pack for the Saturday Claude review, and the import of Claude's answers.

`build_pack` writes `<private>/weekly/<last session>/`:
  bundles/            one non-blind evidence bundle per case (holdings at the exit gate, candidates at the entry gate)
  bundles_key.json    case_id -> case (next to the folder, as `decisions export` does)
  candidates.json     the full screen output: who passed, what was dropped and why, the UNPROVEN label
  pack.json           index: cases, skipped holdings with the reason, files expected back, status
  README.md           the answer format
rules-v1 is run on the same cases first, so Claude's calls are logged next to the baseline's and scored the same way later.

`import_found_answers` looks in each pack for `claude-*.jsonl` and imports each file once (a `.imported` marker holds its hash).
Each line carries its own `model`; lines are grouped by it, because the log stores one model per call.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .core.canary import IST
from .core.envelope import DrishtiError, ErrorKind, Result
from .decisions import candidates as cand
from .decisions import cases as cs
from .portfolio import holdings

HORIZON = cs.HORIZON
AGENT = "claude"

README = """# Weekly pack {as_of}

Prepared by `drishti catchup` (Saturday). Review it, then write two files next to this README:

* `claude-{as_of}.jsonl` : one JSON object per line, one line per case you decide (skip none you can decide).
* `review-{as_of}.md`    : the human report: per holding and per candidate, the call, the reason, what would change your mind.

## Cases (`pack.json` lists them)
* `exit-*` : a stock the investor owns. Actions: HOLD, TRIM, EXIT.
* `entry-*`: a screened new candidate (UNPROVEN screen: momentum only). Actions: ENTER, WAIT, AVOID.
Evidence for each case is `bundles/<case_id>.json`. It is NOT blind: you may add public news per stock, and must say so in the thesis.
Funds and anything Drishti has no price data for are listed under `skipped` in `pack.json`: do not decide them.

## Answer line (JSON, one per line)
{{"case_id": "...", "model": "opus-5.5", "action": "HOLD", "horizon_days": {horizon}, "confidence": 0.60, "thesis": "20+ characters",
 "invalidation": "10+ characters: what would prove the call wrong", "signals": ["price_context", "pledge"]}}

Rules: `horizon_days` must be {horizon}. `confidence` is 0.50..0.99 for HOLD, TRIM, EXIT, ENTER, AVOID (the probability the call turns out right
against the Nifty 50 over {horizon} sessions); omit it for WAIT. Cite only signals that appear in the bundle with a reading: an UNKNOWN is unknown, not neutral.
`model` is the model that wrote the call. A correction to an earlier call of yours needs `"supersedes": "<decision id>"`.
Never place orders. This is decision support: the investors decide.
"""


def private_dir(override: str | Path | None = None) -> Path:
    """Where holdings and weekly packs live: --private-dir, else $DRISHTI_PRIVATE, else `../private` next to the v2 folder
    (the repo layout), else `./private`. Always git-ignored and never uploaded."""
    if override:
        return Path(override)
    if os.environ.get("DRISHTI_PRIVATE"):
        return Path(os.environ["DRISHTI_PRIVATE"])
    up = Path.cwd().parent / "private"
    return up if up.is_dir() else Path.cwd() / "private"


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")


def build_pack(store, private: Path, *, as_of: str | None = None, force: bool = False, profile=None, top_n: int = cand.TOP_N) -> Result:
    as_of = as_of or cand.latest_session(store)
    if not as_of:
        return Result(error=DrishtiError(ErrorKind.EMPTY, "weekly pack: no stored NSE prices"))
    out = Path(private) / "weekly" / as_of
    if (out / "pack.json").exists() and not force:
        return Result(data={"as_of": as_of, "built": False, "why": "pack already exists", "dir": str(out)}, as_of=as_of)
    snap = holdings.latest_snapshot(store)
    skipped, cases, meta = [], [], {}
    for h in snap:
        if h["kind"] != "stock":
            skipped.append({"isin": h["isin"], "name": h["name"], "why": "fund: Drishti has no NAV data, review it by hand"})
            continue
        cid = f"exit-{as_of}-{h['isin']}"
        cases.append({"case_id": cid, "gate": "exit", "as_of": as_of, "isin": h["isin"], "symbol": h["symbol"], "horizon_days": HORIZON})
        meta[cid] = {"role": "holding", "symbol": h["symbol"], "name": h["name"]}
    scr = cand.screen(store, as_of, top_n=top_n)
    candidates = scr.data if scr.ok else None
    if candidates:
        for c in candidates["candidates"]:
            cid = f"entry-{as_of}-{c['isin']}"
            cases.append({"case_id": cid, "gate": "entry", "as_of": as_of, "isin": c["isin"], "symbol": c["symbol"], "horizon_days": HORIZON})
            meta[cid] = {"role": "candidate", "symbol": c["symbol"], "name": c["name"], "rank": c["rank"]}
    if not cases:
        why = scr.error.detail if not scr.ok else "no holdings and no candidates"
        return Result(error=DrishtiError(ErrorKind.EMPTY, f"weekly pack: nothing to review ({why})"))
    out.mkdir(parents=True, exist_ok=True)
    rules_n = cs.run_rules(store, cases)                     # the baseline's calls on the same cases, logged before Claude sees anything
    ex = cs.export_bundles(store, cases, out / "bundles", blind=False, profile=profile)
    failed = {f["case_id"]: f["why"] for f in ex["failed"]}
    _write(out / "candidates.json", candidates if candidates else {"error": scr.error.to_dict()})
    pack = {"as_of": as_of, "built_at": datetime.now(IST).isoformat(timespec="seconds"), "status": "UNPROVEN screen; decision support, not advice",
            "params_version": cand.params_version(), "horizon_days": HORIZON, "rules": rules_n,
            "cases": [{"case_id": c["case_id"], "gate": c["gate"], "isin": c["isin"], **meta[c["case_id"]],
                       **({"bundle_failed": failed[c["case_id"]]} if c["case_id"] in failed else {})} for c in cases],
            "skipped": skipped, "candidate_screen": ({"ok": True, "passed_readings": candidates["passed_readings"], "after_red_flags": candidates["after_red_flags"]}
                                                     if candidates else {"ok": False, "error": scr.error.to_dict()}),
            "bundles_written": ex["written"], "bundles_failed": ex["failed_count"], "key": "bundles_key.json",
            "answers_expected": f"claude-{as_of}.jsonl", "report_expected": f"review-{as_of}.md"}
    (out / "README.md").write_text(README.format(as_of=as_of, horizon=HORIZON), encoding="utf-8")
    _write(out / "pack.json", pack)                          # written last: its presence means the pack is complete
    return Result(data={"as_of": as_of, "built": True, "dir": str(out), "holdings": sum(1 for m in meta.values() if m["role"] == "holding"),
                        "candidates": sum(1 for m in meta.values() if m["role"] == "candidate"), "skipped": len(skipped),
                        "bundles": ex["written"], "bundles_failed": ex["failed_count"], "rules": rules_n,
                        "screen_ok": scr.ok}, as_of=as_of)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def import_found_answers(store, private: Path) -> dict:
    """Import every `claude-*.jsonl` in every pack, once. Returns counts and the problems found (never raises on bad content)."""
    done, problems = [], []
    for pack in sorted((Path(private) / "weekly").glob("*/pack.json")):
        d = pack.parent
        key = d / "bundles_key.json"
        for f in sorted(d.glob("claude-*.jsonl")):
            marker = f.with_suffix(f.suffix + ".imported")
            sha = _sha(f)
            if marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("sha") == sha:
                continue
            if not key.exists():
                problems.append({"file": str(f), "why": "bundles_key.json is missing next to the pack"})
                continue
            by_model: dict[str, list[str]] = {}
            bad = 0
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    m = str(json.loads(line).get("model") or "").strip()
                except (ValueError, AttributeError):
                    m = ""
                if not m:
                    bad += 1
                    continue
                by_model.setdefault(m, []).append(line)
            rec = rej = 0
            reasons = []
            for model, lines in by_model.items():
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td) / "answers.jsonl"
                    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    r = cs.import_answers(store, key, tmp, agent=AGENT, model=model)
                rec += r["recorded"]
                rej += r["rejected"]
                reasons += r["rejections"][:5]
            rej += bad
            if bad:
                reasons.append({"why": f"{bad} line(s) not JSON or without a model field"})
            marker.write_text(json.dumps({"sha": sha, "recorded": rec, "rejected": rej, "at": datetime.now(IST).isoformat(timespec="seconds"),
                                          "models": sorted(by_model)}, indent=1), encoding="utf-8")
            done.append({"file": f.name, "pack": d.name, "recorded": rec, "rejected": rej})
            if rej:
                problems.append({"file": str(f), "why": f"{rej} line(s) rejected", "examples": reasons[:5]})
    return {"imported": done, "problems": problems}
