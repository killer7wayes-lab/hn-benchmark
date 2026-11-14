"""Build docs/data.js (the data the dashboard page reads) from the database.

Uses, for every shop, its most recent day of data, so a partial run keeps the other shops.
Out-of-stock products are left out everywhere.
"""
import datetime as dt
import json
import re
from pathlib import Path

from match import load_library, match_all, prep, save_library

SUPP = re.compile(r"whey|prot[ée]in|cr[ée]atine|gainer|bcaa|eaa|amino|vitamin|omega|pre.?workout|mass|isolate|"
                  r"collag|glutamin|br[uû]leur|burner|booster|zinc|magn[ée]sium|ashwagandha|cas[ée]in|carnitin|"
                  r"citrullin|musculation|s[èe]che|prise de|nutrition|shaker|testo", re.I)
LATEST = "day = (SELECT MAX(day) FROM {t} t2 WHERE t2.shop = {t}.shop)"


def export_site(con, cfg, site: Path):
    site.mkdir(exist_ok=True)
    ref_name = cfg["reference"]["name"]
    rows = [dict(r) for r in con.execute(
        "SELECT shop, is_reference, name, brand AS vendor, price, regular_price, discount_pct, in_stock, url, image, day "
        f"FROM products WHERE {LATEST.format(t='products')}")]
    names = {cfg["reference"]["name"]} | {c["name"] for c in cfg["competitors"]}
    rows = [r for r in rows if r["shop"] in names]            # shops removed from the list disappear
    hn_all = prep([r for r in rows if r["is_reference"]], reference=True)
    comp_all = prep([r for r in rows if not r["is_reference"]])
    hn = sorted([r for r in hn_all if r["in_stock"] and not r["is_pack"]], key=lambda r: r["name"].lower())
    comp = [r for r in comp_all if r["in_stock"]]
    library = load_library()
    matches = match_all(hn, [c for c in comp if not c["is_pack"]], library=library) if hn else {}
    if matches:
        save_library(library)

    products = [{"name": h["name"], "brand": h["brand"], "price": h["price"], "regular": h["regular_price"],
                 "url": h["url"], "image": h["image"], "matches": matches.get(i, [])} for i, h in enumerate(hn)]

    packs = [{"shop": r["shop"], "own": bool(r["is_reference"]), "name": r["name"], "price": r["price"],
              "regular": r["regular_price"], "discount": r["discount_pct"], "url": r["url"], "image": r["image"]}
             for r in comp_all + hn_all if r["in_stock"] and r["is_pack"] and SUPP.search(r["name"])]

    ad_day = con.execute("SELECT MAX(day) FROM ads").fetchone()[0]
    ads = [dict(r) for r in con.execute(
        "SELECT library_id, advertiser, competitor, started, text, prices, old_price, new_price, discounts, "
        "promo_words, is_pack, link, page_url, landing, image FROM ads WHERE day=?", (ad_day,))] if ad_day else []

    log = [dict(r) for r in con.execute(
        f"SELECT shop, method, products, in_stock, error, day FROM scrape_log WHERE {LATEST.format(t='scrape_log')}")]
    order = {c["name"]: i for i, c in enumerate([cfg["reference"]] + cfg["competitors"])}
    log = [r for r in log if r["shop"] in names]
    log.sort(key=lambda r: order.get(r["shop"], 999))
    known = {r["shop"] for r in log}
    for c in cfg["competitors"]:
        if c["name"] not in known:
            log.append({"shop": c["name"], "method": "", "products": 0, "in_stock": 0, "day": "",
                        "error": "pas de site web (suivi par les publicités)" if not c.get("url") else "pas encore collecté"})

    data = {
        "updated": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "reference": ref_name,
        "products": products, "packs": packs, "ads": ads, "log": log,
        "hidden_out_of_stock": {"hn": sum(1 for r in hn_all if not r["in_stock"]),
                                "competitors": sum(1 for r in comp_all if not r["in_stock"])},
    }
    (site / "data.js").write_text("window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    return data
