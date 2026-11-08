#!/usr/bin/env python3
"""Build the input files for peer review. Plain standard-library Python so it runs anywhere (WSL included).

  pair_cases.py review <bundles-dir> <answers.jsonl> <out-dir>
      one file per answer: {case_id, bundle, answer_under_review}      (for the critic)
  pair_cases.py revise <bundles-dir> <answers.jsonl> <critiques.jsonl> <out-dir>
      one file per critique: {case_id, bundle, your_original_answer, critique}   (for the original author)
Only the blind bundle and the answers travel; the key file (which stock is which) is never read.
"""
import json
import sys
from pathlib import Path


def lines(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def bundle(bdir, cid):
    return json.loads((Path(bdir) / f"{cid}.json").read_text(encoding="utf-8"))


def main(a):
    if len(a) >= 5 and a[1] == "review":
        bdir, answers, out = a[2], a[3], Path(a[4])
        out.mkdir(parents=True, exist_ok=True)
        n = 0
        for r in lines(answers):
            (out / f"rev-{r['case_id']}.json").write_text(json.dumps({"case_id": r["case_id"], "bundle": bundle(bdir, r["case_id"]), "answer_under_review": r}, indent=1), encoding="utf-8")
            n += 1
        print(f"{n} review files -> {out}")
    elif len(a) >= 6 and a[1] == "revise":
        bdir, answers, crit, out = a[2], a[3], a[4], Path(a[5])
        out.mkdir(parents=True, exist_ok=True)
        mine = {r["case_id"]: r for r in lines(answers)}
        n = 0
        for c in lines(crit):
            cid = c["case_id"]
            if cid not in mine:
                continue
            (out / f"rvs-{cid}.json").write_text(json.dumps({"case_id": cid, "bundle": bundle(bdir, cid), "your_original_answer": mine[cid], "critique": c}, indent=1), encoding="utf-8")
            n += 1
        print(f"{n} revise files -> {out}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
