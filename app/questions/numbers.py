"""Number reading and tolerant comparison for options and claims."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NumberReading:
    value: float
    decimals: int        # digits written after the decimal separator (defines the tolerance)


def read_number(text):
    """All plausible readings of a written number.

    '1.234,5' -> [1234.5]       '1,234.5' -> [1234.5]      '12,5%' -> [12.5]
    '1.234'   -> [1.234, 1234]  (ambiguous: decimal point or thousands separator)
    Returns [] when the text is not a number.
    """
    t = text.strip().lower()
    t = re.sub(r"(usd|cop|eur|us\$|\$|€|%)", "", t)
    t = re.sub(r"[\s  ]", "", t)
    m = re.fullmatch(r"([-+]?)(\d[\d.,]*)", t)
    if not m:
        return []
    sign = -1.0 if m.group(1) == "-" else 1.0
    body = m.group(2)
    if body[-1] in ".,":
        return []

    if "." in body and "," in body:
        dec = "." if body.rfind(".") > body.rfind(",") else ","
        thousands = "," if dec == "." else "."
        whole, frac = body.rsplit(dec, 1)
        if dec in whole or not _valid_groups(whole, thousands):
            return []
        return [NumberReading(sign * float(whole.replace(thousands, "") + "." + frac), len(frac))]

    sep = "." if "." in body else ("," if "," in body else None)
    if sep is None:
        return [NumberReading(sign * float(body), 0)]
    parts = body.split(sep)
    if len(parts) > 2:
        return [NumberReading(sign * float("".join(parts)), 0)] if _valid_groups(body, sep) else []
    whole, frac = parts
    decimal = NumberReading(sign * float(f"{whole}.{frac}"), len(frac))
    if len(frac) == 3 and whole != "0" and len(whole) <= 3:
        return [decimal, NumberReading(sign * float(whole + frac), 0)]
    return [decimal]


def _valid_groups(text, sep):
    groups = text.split(sep)
    return 1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])


def matches(computed, reading):
    """True when `computed` rounds to the written number at its written precision.

    '273.40' accepts 273.396..273.404; '15' accepts 14.5..15.5.
    """
    tolerance = 0.5 * 10 ** (-reading.decimals) + 1e-9 * max(1.0, abs(computed))
    return abs(float(computed) - reading.value) <= tolerance


def format_number(value, decimals=4):
    """Readable number: up to `decimals` decimals, no trailing zeros."""
    if value is None:
        return "NULL"
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")
