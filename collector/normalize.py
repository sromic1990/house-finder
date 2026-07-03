"""Finnish listing text -> canonical feature vocabulary.

These parsers translate the free-text / attribute strings the portals use into
the structured `features` the criteria engine expects. Shared by every real
adapter. Kept pure + tested (tests/test_normalize.py) because this is where
subtle mistakes would silently mis-rank listings.

Return None when the text gives no signal — "unknown" must stay unknown so the
criteria can apply their on_unknown policy rather than us guessing.
"""
from __future__ import annotations

import re
from typing import Optional


def _has(text: str, *needles: str) -> bool:
    t = text.lower()
    return any(n in t for n in needles)


def _tokens(text: str) -> set[str]:
    """Split a Finnish room code ("4h+k+2xwc+kph+ph+s") into normalized tokens."""
    return {tok.strip(" .") for tok in re.split(r"[+,/;]", (text or "").lower())
            if tok.strip(" .")}


def parse_sauna(text: str) -> Optional[dict]:
    """Detect sauna and whether it's the apartment's own (private) vs shared.

    Handles the room-code abbreviation 's' (e.g. "4h+k+s"), 's-osasto', and the
    full word 'sauna'. A sauna listed among the unit's own rooms is private.
    """
    t = (text or "").lower()
    toks = _tokens(t)
    token_sauna = "s" in toks or "s-osasto" in toks or "saunaosasto" in toks
    if not (token_sauna or _has(t, "sauna")):
        return None
    shared_markers = ["taloyhtiön sauna", "taloyhtion sauna", "yhteissauna",
                      "yhteinen sauna", "saunavuoro", "shared sauna"]
    private_markers = ["oma sauna", "huoneistokohtainen sauna", "asunnossa sauna",
                       "private sauna", "sauna,", "sauna ja", "+ s", "+s"]
    is_shared = _has(t, *shared_markers)
    is_private = token_sauna or _has(t, *private_markers)
    if is_shared and not is_private:
        return {"present": True, "private": False, "shared": True}
    if is_private:
        return {"present": True, "private": True, "shared": is_shared}
    return {"present": True, "private": False, "shared": is_shared}


def parse_balcony(text: str) -> Optional[dict]:
    """Detect balcony and glazing. parveke=balcony, lasitettu=glazed."""
    t = (text or "").lower()
    if not _has(t, "parveke", "parvekkeel", "balcony", "terassi"):
        return None
    if _has(t, "lasitettu", "lasitus", "glazed", "lasitettava"):
        return {"present": True, "glazed": True}
    if _has(t, "lasittamaton", "avoin parveke", "unglazed", "not glazed"):
        return {"present": True, "glazed": False}
    return {"present": True, "glazed": None}  # present, glazing unknown


def parse_property_type(text: str) -> Optional[str]:
    """Map free text to a canonical property type (see models.PROPERTY_TYPES)."""
    t = (text or "").lower()
    # order matters: check compound words before generic 'talo'
    table = [
        ("omakotitalo", ["omakotitalo", "ok-talo", "detached house"]),
        ("paritalo", ["paritalo", "semi-detached"]),
        ("rivitalo", ["rivitalo", "terraced", "row house", "townhouse"]),
        ("erillistalo", ["erillistalo"]),
        ("luhtitalo", ["luhtitalo"]),
        ("kerrostalo", ["kerrostalo", "apartment", "flat"]),
    ]
    for canon, needles in table:
        if _has(t, *needles):
            return canon
    return None


def parse_land_ownership(text: str) -> Optional[str]:
    """Map a 'Tontin omistus' value to own | rented | optional_rental | None.

    oma -> own, vuokra(tontti) -> rented, valinnainen vuokratontti -> optional.
    """
    t = (text or "").lower()
    if not t:
        return None
    if "valinnai" in t:                 # valinnainen vuokratontti
        return "optional_rental"
    if t.startswith("oma") or "oma tontti" in t or t == "own":
        return "own"
    if "vuokra" in t or t == "rent":
        return "rented"
    return None


def parse_duplex(text: str) -> Optional[bool]:
    """Two-floor apartment (maisonette). Returns True/None (absence != 'not duplex')."""
    t = (text or "").lower()
    if _has(t, "kaksikerroksinen", "kahdessa tasossa", "kahdessa kerroksessa",
            "kahdessa tasossa", "maisonette", "duplex", "kaksitasoinen",
            "two floors", "two levels", "two-storey", "two storey"):
        return True
    return None


def parse_toilets(text: str) -> Optional[int]:
    """Count toilets. Handles '2xwc', '2 wc', '2 erill.wc', 'erill.wc' + kph.

    Returns an int or None if unstated.
    """
    t = (text or "").lower()
    # explicit numbered forms: "2xwc", "2 wc", "2 erill.wc", "2x ask.h"→no
    nums = re.findall(r"(\d+)\s*x?\s*(?:erill\.?\s*)?(?:wc|kylpyhuone)", t)
    if nums:
        return max(int(n) for n in nums)
    words = {"kaksi": 2, "kolme": 3, "neljä": 4, "two": 2, "three": 3}
    for w, n in words.items():
        if _has(t, f"{w} wc", f"{w} kylpyhuone"):
            return n
    wc_hits = len(re.findall(r"\bwc\b", t))
    if wc_hits >= 2:
        return 2
    # a separate WC plus a bathroom (which has its own WC) implies two
    if _has(t, "erill.wc", "erill. wc", "erillinen wc", "erillis wc", "erillis-wc") \
            and _has(t, "kph", "kylpyhuone"):
        return 2
    if _has(t, "wc", "kylpyhuone", "kph"):
        return 1
    return None


def parse_parking(text: str) -> Optional[dict]:
    """Map parking description to a canonical type (best-first).

    Handles room-code abbreviations 'at' (autotalli/garage) and 'ah'
    (autohalli/parking hall) as standalone tokens.
    """
    t = (text or "").lower()
    toks = _tokens(t)
    if "at" in toks or _has(t, "autotalli", "garage", "lämmin autopaikka",
                            "lammin autopaikka"):
        return {"type": "garage"}
    if "ah" in toks or _has(t, "autohalli", "pysäköintihalli", "pysakointihalli",
                            "parking hall", "parkkihalli"):
        return {"type": "hall"}
    if _has(t, "katettu", "autokatos", "carport", "covered"):
        return {"type": "covered"}
    # match inflected stems: tolppa/tolpalla/tolpat, lämmitys-, pistoke
    if _has(t, "tolp", "lämmitys", "lammitys", "heating pole", "pistoke",
            "sähköpaikka", "sahkopaikka"):
        return {"type": "open_pole"}
    # a dedicated own spot (no pole): "oma autopaikka", "asunnolle kuuluu autopaikka"
    if _has(t, "oma autopaikka", "oma paikka", "oma pysäköintipaikka") \
            or re.search(r"kuuluu\s+(?:oma\s+)?autopaikka", t) \
            or re.search(r"asunnolle kuuluu[^.]{0,40}autopaikka", t):
        return {"type": "own_spot"}
    if _has(t, "autopaikka", "parkkipaikka", "pysäköinti", "pysakointi",
            "pihapaikka", "piha-paikka", "parking"):
        return {"type": "open"}
    return None
