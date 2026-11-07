"""ISIN helpers. ISIN is the join key for everything, so a mistyped one must fail loudly."""
from __future__ import annotations

import re

_ISIN = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")  # Indian ISINs: 'IN' + 9 alphanumerics + check digit


def normalize_isin(value: object) -> str:
    return str(value).strip().upper() if value is not None else ""


def _luhn_ok(isin: str) -> bool:
    digits = "".join(str(int(c, 36)) for c in isin)  # letters A..Z -> 10..35, then Luhn over the digit string
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def is_valid_isin(value: object) -> bool:
    """Indian-format ISIN with a correct check digit (all 2,630 NSE EQUITY_L ISINs pass)."""
    isin = normalize_isin(value)
    return bool(_ISIN.match(isin)) and _luhn_ok(isin)
