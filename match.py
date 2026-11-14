"""Match competitor products to House Nutrition products (rapidfuzz, no AI)."""
import csv
import html
import json
import re
from pathlib import Path

from rapidfuzz import fuzz

from normalize import BRANDS, GENERIC_BRAND_WORDS, brand_key, brand_of, clean, core_name, count_of, is_pack, size_grams

CONFIRMED = Path(__file__).parent / "confirmed_matches.csv"   # hn_name,shop,comp_name,ok (1/0)
_BRAND_WORDS = {w for b in BRANDS for w in clean(b).split()} | GENERIC_BRAND_WORDS | {
    "tunisie", "tunisia", "house", "100", "100%", "original", "officiel", "prix",
    # weak descriptive words: they must not decide a match
    "pure", "micronized", "micronised", "micronisee", "monohydrate", "powder", "poudre", "premium", "complement",
    "alimentaire", "gelules", "capsules", "tablets", "caps", "tabs", "softgels", "new", "nouveau", "format",
    "supplement", "food", "dietary", "with", "ultra", "super", "plus", "pre", "workout", "preworkout",
    "extreme", "booster", "formula", "advanced", "series", "the", "and", "for", "of",
    # flavours
    "chocolate", "chocolat", "vanilla", "vanille", "strawberry", "fraise", "cookies", "cream", "creme", "banana",
    "banane", "caramel", "orange", "lemon", "citron", "mango", "mangue", "cherry", "cerise", "fruit", "punch",
    "unflavoured", "unflavored", "nature", "neutre", "watermelon", "pasteque", "cola", "blueberry", "raspberry"}


def _load_confirmed():
    ok, bad = set(), set()
    if CONFIRMED.exists():
        with open(CONFIRMED, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = (r["hn_name"], r["shop"], r["comp_name"])
                (ok if (r.get("ok") or "1").strip() == "1" else bad).add(key)
    return ok, bad


def _sing(t):
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def display_name(name):
    return re.split(r"\s\|\s", html.unescape(name or ""))[0].strip()        # drop " | House Nutrition | Tunisie"


def prep(rows, reference=False):
    for r in rows:
        r["name"] = display_name(r["name"])
        brand = r.get("vendor") or ""
        if brand and not reference and set(clean(brand).split()) <= set(clean(r.get("shop", "")).split()) | {"tn", "store", "shop"}:
            brand = ""                                       # the shop put its own name as the brand
        if not brand and reference and " - " in r["name"]:   # HN names end with " - Brand"
            tail = r["name"].rsplit(" - ", 1)[1]
            if not re.search(r"\d", tail):
                brand = tail
        r["brand"] = brand or brand_of(r["name"])
        r["_bkey"] = brand_key(r["brand"])
        r["_clean"] = " " + clean(r["name"]) + " "
        r["_size"] = size_grams(r["name"])
        r["_count"] = count_of(r["name"])
        r["_core"] = core_name(r["name"])
        r["_keys"] = {_sing(t) for t in r["_core"].split()
                      if t not in _BRAND_WORDS and t not in r["_bkey"] and len(t) > 1 and not t.isdigit()}
        r["is_pack"] = 1 if is_pack(r["name"]) else 0
    return rows


def _aud(text):
    if re.search(r" (women|woman|femme|femmes|her) ", text):
        return "f"
    if re.search(r" (men|man|homme|hommes|him) ", text):
        return "m"
    if re.search(r" (kids|enfant|enfants|junior) ", text):
        return "k"
    return ""


def _brand_relation(h, c):
    """+1 same brand, -1 different brand, 0 unknown."""
    if not h["_bkey"]:
        return 0
    if c["_bkey"]:
        if h["_bkey"] & c["_bkey"]:
            return 1
        if all(f" {t} " in c["_clean"] for t in h["_bkey"]):
            return 1
        return -1
    if all(f" {t} " in c["_clean"] for t in h["_bkey"]) or "".join(sorted(h["_bkey"])) in c["_clean"].replace(" ", ""):
        return 1
    return 0


def score(h, c):
    if h["is_pack"] or c["is_pack"]:
        return 0
    if _aud(h["_clean"]) != _aud(c["_clean"]):               # for women vs for men, kids...
        return 0
    if not h["_keys"] or not c["_keys"]:
        return 0
    rel = _brand_relation(h, c)
    if rel < 0:
        return 0
    if h["_size"] and c["_size"] and abs(h["_size"] - c["_size"]) / max(h["_size"], c["_size"]) > 0.06:
        return 0
    if h["_count"] and c["_count"] and h["_count"] != c["_count"]:
        return 0
    common = h["_keys"] & c["_keys"]
    cover = len(common) / len(h["_keys"])
    if cover < 0.6:
        return 0
    extra = len(c["_keys"] - h["_keys"]) / len(c["_keys"])
    hk, ck = " ".join(sorted(h["_keys"])), " ".join(sorted(c["_keys"]))
    s = 0.5 * fuzz.token_sort_ratio(hk, ck) + 0.5 * fuzz.token_set_ratio(hk, ck)
    s = s * (0.7 + 0.3 * cover) - 15 * extra
    s += 8 if rel > 0 else -8
    has_measure_h = bool(h["_size"] or h["_count"])
    has_measure_c = bool(c["_size"] or c["_count"])
    if has_measure_h and has_measure_c:
        s += 6
    elif has_measure_h != has_measure_c:
        # one side has no size: automatic only when HN sells a single version of this product
        s = min(s, 82 if (rel > 0 and cover == 1 and extra == 0 and not h.get("_variants")) else 78)
    elif len(common) < 2:
        s = min(s, 78)                      # one generic word and no size: too weak
    if rel == 0 and h["_bkey"]:
        s = min(s, 78)                      # HN brand not found on the competitor product
    return max(0, min(100, s))


LIBRARY = Path(__file__).parent / "data" / "library.json"   # {hn url: {shop: competitor url}} - remembered matches


def load_library():
    try:
        return json.loads(LIBRARY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def match_all(hn_rows, comp_rows, auto=80, review=62, library=None):
    """Returns {hn index: [match dicts]} - best in-stock match per competitor shop.
    `library` remembers earlier confident matches by product URL, so a match survives a renamed product."""
    ok, bad = _load_confirmed()
    library = library if library is not None else {}
    seen_keys = {}
    for h in hn_rows:
        k = (frozenset(h["_bkey"]), frozenset(h["_keys"]))
        seen_keys[k] = seen_keys.get(k, 0) + 1
    for h in hn_rows:
        h["_variants"] = seen_keys[(frozenset(h["_bkey"]), frozenset(h["_keys"]))] > 1
    by_url = {}
    for c in comp_rows:
        by_url.setdefault((c["shop"], c["url"]), []).append(c)
    by_shop = {}
    for c in comp_rows:
        by_shop.setdefault(c["shop"], []).append(c)
    results = {}
    for i, h in enumerate(hn_rows):
        found = []
        for shop, items in by_shop.items():
            best, best_score = None, 0
            known = library.get(h["url"], {}).get(shop)
            if known and (shop, known) in by_url:
                cands = [c for c in by_url[(shop, known)] if (h["name"], shop, c["name"]) not in bad
                         and not (h["_size"] and c["_size"] and abs(h["_size"] - c["_size"]) / max(h["_size"], c["_size"]) > 0.06)]
                if cands:
                    best, best_score = min(cands, key=lambda c: c["price"]), 100
            for c in ([] if best else items):
                key = (h["name"], shop, c["name"])
                if key in bad:
                    continue
                s = 100 if key in ok else score(h, c)
                if s > best_score or (s == best_score and best and c["price"] < best["price"]):
                    best, best_score = c, s
            if best and best_score >= review:
                found.append({"shop": shop, "name": best["name"], "price": best["price"],
                              "regular": best.get("regular_price"), "url": best["url"],
                              "score": round(best_score), "sure": best_score >= auto})
        results[i] = sorted(found, key=lambda m: m["price"])
        for mm in found:
            if mm["sure"]:
                library.setdefault(h["url"], {})[mm["shop"]] = mm["url"]
    return results


def save_library(library):
    LIBRARY.parent.mkdir(exist_ok=True)
    LIBRARY.write_text(json.dumps(library, ensure_ascii=False, indent=0), encoding="utf-8")
