"""Which older daily price files still exist? Prints one line per URL. Read-only; touches no data."""
from drishti.core.fetch import get
from drishti.providers.profiles import NSE_ARCHIVE, BSE_WWW

CHECKS = []
for dmy, mon_dir, mon, d in (("15062023", "2023/JUN", "15JUN2023", "150623"), ("15092021", "2021/SEP", "15SEP2021", "150921")):
    CHECKS += [
        (NSE_ARCHIVE, f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{mon_dir}/cm{mon}bhav.csv.zip"),
        (NSE_ARCHIVE, f"https://archives.nseindia.com/content/historical/EQUITIES/{mon_dir}/cm{mon}bhav.csv.zip"),
        (NSE_ARCHIVE, f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dmy}.csv"),
        (BSE_WWW, f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{d}_CSV.ZIP"),
        (BSE_WWW, f"https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_{d}.zip"),
    ]
for profile, url in CHECKS:
    try:
        r = get(url, profile, expect="text")
        head = r.body[:60].replace(b"\n", b" ")
        print("OK  ", len(r.body), "bytes", url.split("/", 3)[3], head)
    except Exception as e:
        print("FAIL", url.split("/", 3)[3], str(e)[:80])
