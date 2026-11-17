"""Saves the header, the first rows and the row count of old-format daily price files to eval/old_price_samples.txt, so a reader can be written
against the real layout. Read-only against the exchanges; changes no data."""
import io
import zipfile
from pathlib import Path

from drishti.core.fetch import get
from drishti.providers.profiles import NSE_ARCHIVE, BSE_WWW

DATES = [("2021", "SEP", "15SEP2021", "150921"), ("2023", "JUN", "15JUN2023", "150623"), ("2024", "MAR", "15MAR2024", "150324")]
out = []
for yyyy, mon, mon_d, d in DATES:
    for label, profile, url in (
        ("NSE old", NSE_ARCHIVE, f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{yyyy}/{mon}/cm{mon_d}bhav.csv.zip"),
        ("BSE ISINCODE", BSE_WWW, f"https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_{d}.zip"),
    ):
        try:
            z = zipfile.ZipFile(io.BytesIO(get(url, profile, expect="text").body))
            name = z.namelist()[0]
            lines = z.read(name).decode("latin-1").splitlines()
            out.append(f"=== {label} {mon_d}: {name}, {len(lines) - 1} data rows")
            out += lines[:4]
            out.append("...")
            out += [ln for ln in lines if "RELIANCE" in ln.upper() or "532712" in ln][:2]
        except Exception as e:
            out.append(f"=== {label} {mon_d}: FAIL {str(e)[:100]}")
Path("eval/old_price_samples.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("wrote eval/old_price_samples.txt")
