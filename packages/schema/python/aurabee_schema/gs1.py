"""GS1 identifiers, EPC URNs and Digital Link URIs.

Why this exists: we invented our own identifier scheme and our own custody
event vocabulary, and both already exist as global standards that this sector
has converged on. The literature on why blockchain agri-traceability projects
fail names *lack of standardisation* as a primary barrier, so being a bespoke
island is a known failure mode, not a stylistic preference.

Adopting GS1 costs us a mapping layer and buys interoperability with retail
scanners, customs systems, and any downstream buyer already speaking EPCIS.

## The mapping

| AuraBee concept          | GS1 key | EPC URN                        | Digital Link      |
|--------------------------|---------|--------------------------------|-------------------|
| Actor, apiary, facility  | GLN     | `urn:epc:id:sgln:P.R.E`        | `/414/{gln}`      |
| Batch (a mass of honey)  | LGTIN   | `urn:epc:class:lgtin:P.IR.lot` | `/01/{gtin}/10/{lot}` |
| Sealed jar (serialised)  | SGTIN   | `urn:epc:id:sgtin:P.IR.serial` | `/01/{gtin}/21/{serial}` |
| Drum / logistic unit     | SSCC    | `urn:epc:id:sscc:P.SR`         | `/00/{sscc}`      |

A batch is deliberately an **LGTIN class**, not a serialised SGTIN: honey in a
drum is a quantity, not an item, and EPCIS models that with
`quantityList`/`uom: KGM`. Only once it is in a jar does it become a serialised
thing with an SGTIN. That distinction is the whole reason EPCIS has both.

## The company prefix is NOT ours to invent

`COMPANY_PREFIX` below is a placeholder. Real GTINs and GLNs require a licensed
GS1 Company Prefix (from GS1 India for an Indian issuer). Shipping product with
invented GS1 keys would collide with somebody else's real ones and is exactly
the kind of thing that fails an export audit. Set `GS1_COMPANY_PREFIX` from a
licensed prefix before anything leaves a pilot.
"""

from __future__ import annotations

import os
import re

# 890 is the GS1 India prefix range. This specific value is a placeholder for
# development only -- see the module docstring.
COMPANY_PREFIX = os.getenv("GS1_COMPANY_PREFIX", "8901234")

# Where a Digital Link QR resolves. In production this is a GS1-conformant
# resolver; in development it is the local web app.
RESOLVER = os.getenv("GS1_RESOLVER", "https://aurabee.in").rstrip("/")

# UN/CEFACT unit codes used in EPCIS quantityList
UOM_KILOGRAM = "KGM"
UOM_GRAM = "GRM"


def check_digit(digits: str) -> str:
    """GS1 mod-10 check digit.

    Weights alternate 3 and 1 from the RIGHTMOST digit of the payload. Getting
    the direction wrong yields plausible-looking codes that fail every scanner,
    so this is worth reading carefully rather than trusting.
    """
    if not digits.isdigit():
        raise ValueError(f"non-numeric payload: {digits!r}")
    total = 0
    for i, ch in enumerate(reversed(digits)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return str((10 - (total % 10)) % 10)


def _pad(value: int | str, width: int) -> str:
    s = str(value)
    if not s.isdigit():
        raise ValueError(f"reference must be numeric: {value!r}")
    if len(s) > width:
        raise ValueError(f"reference {s} exceeds {width} digits")
    return s.zfill(width)


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------
def gtin14(item_ref: int | str, indicator: str = "0", prefix: str | None = None) -> str:
    """GTIN-14. Indicator 0 for a consumer trade item."""
    p = prefix or COMPANY_PREFIX
    ref_width = 12 - len(p)          # indicator + prefix + ref = 13 before check
    payload = indicator + p + _pad(item_ref, ref_width)
    return payload + check_digit(payload)


def gln13(location_ref: int | str, prefix: str | None = None) -> str:
    p = prefix or COMPANY_PREFIX
    payload = p + _pad(location_ref, 12 - len(p))
    return payload + check_digit(payload)


def sscc18(serial_ref: int | str, extension: str = "0", prefix: str | None = None) -> str:
    p = prefix or COMPANY_PREFIX
    payload = extension + p + _pad(serial_ref, 17 - 1 - len(p))
    return payload + check_digit(payload)


# --------------------------------------------------------------------------
# EPC URNs (what goes inside EPCIS events)
# --------------------------------------------------------------------------
def _split_gtin(gtin: str, prefix: str | None = None) -> tuple[str, str]:
    """GTIN-14 -> (companyPrefix, indicatorAndItemRef) for URN form."""
    p = prefix or COMPANY_PREFIX
    indicator = gtin[0]
    item_ref = gtin[1 + len(p):-1]    # strip indicator, prefix and check digit
    return p, indicator + item_ref


def sgtin_urn(gtin: str, serial: str, prefix: str | None = None) -> str:
    """A single serialised item -- one sealed jar."""
    p, ir = _split_gtin(gtin, prefix)
    return f"urn:epc:id:sgtin:{p}.{ir}.{_sanitise_serial(serial)}"


def lgtin_urn(gtin: str, lot: str, prefix: str | None = None) -> str:
    """A lot-level *class* -- a batch of honey, counted by mass."""
    p, ir = _split_gtin(gtin, prefix)
    return f"urn:epc:class:lgtin:{p}.{ir}.{_sanitise_serial(lot)}"


def sgln_urn(gln: str, extension: str = "0", prefix: str | None = None) -> str:
    p = prefix or COMPANY_PREFIX
    location_ref = gln[len(p):-1]
    return f"urn:epc:id:sgln:{p}.{location_ref}.{extension}"


def sscc_urn(sscc: str, prefix: str | None = None) -> str:
    p = prefix or COMPANY_PREFIX
    serial_ref = sscc[1 + len(p):-1]
    return f"urn:epc:id:sscc:{p}.{sscc[0]}{serial_ref}"


_SERIAL_OK = re.compile(r"[^0-9A-Za-z\-_.:]")


def _sanitise_serial(value: str) -> str:
    """EPC serial/lot components allow a restricted character set. Our batch
    codes and jar serials already fit; anything odd is normalised rather than
    silently producing an invalid URN."""
    return _SERIAL_OK.sub("-", value)


# --------------------------------------------------------------------------
# Digital Link (what the QR code actually contains)
# --------------------------------------------------------------------------
def dl_jar(gtin: str, serial: str, resolver: str | None = None) -> str:
    """The consumer QR. One code that a retail scanner reads as a GTIN and a
    phone resolves to the verification page -- which is the whole point of the
    2D migration retail is making by around 2027."""
    return f"{resolver or RESOLVER}/01/{gtin}/21/{serial}"


def dl_lot(gtin: str, lot: str, resolver: str | None = None) -> str:
    return f"{resolver or RESOLVER}/01/{gtin}/10/{lot}"


def dl_location(gln: str, resolver: str | None = None) -> str:
    return f"{resolver or RESOLVER}/414/{gln}"


DL_PATH = re.compile(
    r"^/01/(?P<gtin>\d{14})(?:/10/(?P<lot>[^/]+))?(?:/21/(?P<serial>[^/]+))?/?$"
)


def parse_dl(path: str) -> dict | None:
    """Parse the path of a Digital Link URI. Returns None if it is not one --
    the web app falls back to treating the value as a bare AuraBee serial, so
    old-style QRs keep working."""
    m = DL_PATH.match(path if path.startswith("/") else "/" + path)
    return {k: v for k, v in m.groupdict().items() if v} if m else None
