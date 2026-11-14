"""Name / size / brand normalization used for matching (no AI)."""
import html
import re
import unicodedata

BRANDS = [
    "optimum nutrition", "muscletech", "biotech usa", "biotechusa", "impact", "ostrovit",
    "olimp", "applied nutrition", "dymatize", "animal", "universal", "ultimate nutrition",
    "scitec", "gsn", "cellucor", "rule one", "kevin levrone", "big ramy", "real pharm",
    "american wolf", "physique nutrition", "7nutrition", "eric favre", "bpi", "quamtrax",
    "mutant", "nutrend", "proactive", "hx nutrition", "amix", "gaspari", "trec", "nutrex",
    "redcon1", "ghost", "myprotein", "weider", "ronnie coleman", "musclepharm", "grenade",
    "perfect nutrition", "yava labs", "worth it", "miravella", "activlab", "bad ass", "dy nutrition",
    "life pro", "prozis", "now foods", "swedish supplements", "warrior", "per4m", "naughty boy",
]
GENERIC_BRAND_WORDS = {"nutrition", "sport", "sports", "labs", "lab", "usa", "series", "signature", "the"}

_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|kgs|g|gr|grs|gramme?s?|lbs?|livres?|ml|l)\b")
_COUNT = re.compile(r"(\d+)\s*(caps?|capsules?|gel+ules?|tabs?|tablets?|comprimes?|softgels?|chewables?|chews?|"
                    r"packs?|paks?|sachets?|servings?|serv|doses?|vcaps?|pieces?|pcs|shots?)\b")
PACK = re.compile(r"(?<!\d)(?<!\d\s)\bpacks?\b(?!\s*$)|^\s*packs?\b|\bbundle\b|\bcombo\b|\bduo\b|\btrio\b|\s\+\s|\boffert[es]?\b|\b1\s?\+\s?1\b", re.I)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def clean(s: str) -> str:
    s = strip_accents(html.unescape(s or "").lower())
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)                # French decimal comma: 2,5 kg -> 2.5 kg
    s = re.sub(r"\b([a-z]{3,})[ -](\d)\b", r"\1\2", s)     # lipo 6 / lipo-6 -> lipo6
    s = re.sub(r"[–—\-|/(),:®™’'`]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def size_grams(name: str):
    m = _SIZE.search(clean(name))
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit.startswith("kg") or unit == "l":
        return round(v * 1000)
    if unit.startswith("lb") or unit.startswith("livre"):
        return round(v * 453.6)
    return round(v)


def count_of(name: str):
    m = _COUNT.search(clean(name))
    return int(m.group(1)) if m else None


def brand_of(name: str) -> str:
    text = " " + clean(name) + " "
    for b in sorted(BRANDS, key=len, reverse=True):
        if f" {b} " in text:
            return "biotech usa" if b == "biotechusa" else b
    return ""


def brand_key(brand: str):
    b = clean(brand).replace("biotechusa", "biotech").replace("bio tech", "biotech").replace("realpharm", "real pharm")
    return {t for t in b.split() if t not in GENERIC_BRAND_WORDS}


def core_name(name: str) -> str:
    s = _SIZE.sub(" ", clean(name))
    s = _COUNT.sub(" ", s)
    s = re.sub(r"\b(prix|tunisie|tunisia|en|de|la|le|du|des|avec|saveur|gout|pot|sachet|bag|et|a|au|pour)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_pack(name: str) -> bool:
    return bool(PACK.search(strip_accents(name or "")))
