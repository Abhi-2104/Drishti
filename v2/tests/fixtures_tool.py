"""Test fixtures live outside git: they are copies of exchange / data-provider responses, which are data, not code.

The list of files and their SHA-256 is tracked (tests/fixtures.lock), so every machine can check it has the same set.

    python tests/fixtures_tool.py lock            # after adding or changing a fixture: rewrite the lock
    python tests/fixtures_tool.py check           # does tests/fixtures match the lock?
    python tests/fixtures_tool.py pack out.zip    # zip the fixtures to hand to another machine
    python tests/fixtures_tool.py unpack in.zip   # extract a zip and check it against the lock

Stdlib only, so it runs before `uv sync`.
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
LOCK = HERE / "fixtures.lock"
SKIP_DIRS = {"_raw"}          # raw captures stay local and are not part of the set


def files() -> list[Path]:
    if not FIX.is_dir():
        return []
    return sorted(p for p in FIX.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.relative_to(FIX).parts))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_lock() -> dict[str, str]:
    if not LOCK.exists():
        return {}
    out = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, rel = line.split(None, 1)
            out[rel.strip()] = digest
    return out


def status() -> dict:
    """missing / changed / extra relative to the lock."""
    want = read_lock()
    have = {p.relative_to(FIX).as_posix(): p for p in files()}
    missing = sorted(set(want) - set(have))
    changed = sorted(r for r in set(want) & set(have) if sha(have[r]) != want[r])
    extra = sorted(set(have) - set(want))
    return {"want": len(want), "missing": missing, "changed": changed, "extra": extra}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "check"
    if cmd == "lock":
        fs = files()
        LOCK.write_text("# sha256  path (relative to tests/fixtures). Rewrite with: python tests/fixtures_tool.py lock\n"
                        + "".join(f"{sha(p)}  {p.relative_to(FIX).as_posix()}\n" for p in fs), encoding="utf-8")
        print(f"locked {len(fs)} fixture files")
        return 0
    if cmd == "pack":
        out = Path(argv[1] if len(argv) > 1 else "drishti-fixtures.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in files():
                z.write(p, p.relative_to(FIX).as_posix())
            z.write(LOCK, "fixtures.lock")
        print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(files())} files)")
        return 0
    if cmd == "unpack":
        src = Path(argv[1])
        FIX.mkdir(exist_ok=True)
        with zipfile.ZipFile(src) as z:
            for n in z.namelist():
                if n == "fixtures.lock" or n.startswith("/") or ".." in Path(n).parts:
                    continue
                dst = FIX / n
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(z.read(n))
        argv = ["check"]
        cmd = "check"
    if cmd == "check":
        s = status()
        print(f"{s['want']} files in the lock; missing {len(s['missing'])}, changed {len(s['changed'])}, not in lock {len(s['extra'])}")
        for k in ("missing", "changed", "extra"):
            for r in s[k][:20]:
                print(f"  {k}: {r}")
        return 0 if not (s["missing"] or s["changed"]) else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
