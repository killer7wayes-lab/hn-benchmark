"""Daily run: House Nutrition catalogue + competitor prices + Meta ads -> docs/data.js

Usage:  python run_daily.py                 everything
        python run_daily.py --no-ads        prices only
        python run_daily.py --only "Nutribeast,Aecor"   only these competitors (+ House Nutrition)
        python run_daily.py --open          open the dashboard as soon as first results are ready
"""
import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import yaml

from db import connect
from export import export_site
from normalize import clean, is_pack
from prices import Scraper

ROOT = Path(__file__).parent
SITE = ROOT / "docs"
LOG = ROOT / "data" / "run.log"
_lock = threading.Lock()


def log(msg):
    line = f"{dt.datetime.now():%H:%M:%S} {msg}"
    with _lock:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def domain(url):
    return urlparse(url or "").netloc.lower().replace("www.", "")


def attribute(card, shops):
    """Which competitor an ad belongs to: landing website, then Facebook page name."""
    land = domain(card.get("landing"))
    adv = clean(card.get("advertiser", ""))
    for s in shops:
        if land and s.get("url") and domain(s["url"]) == land:
            return s["name"]
    squash = lambda t: t.replace(" ", "")
    for s in shops:
        for n in (clean(s.get("fb_name") or ""), clean(s["name"])):
            if n and adv and (n == adv or n in adv or adv in n or squash(n) == squash(adv)):
                return s["name"]
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ads", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    (ROOT / "data").mkdir(exist_ok=True)
    LOG.write_text("", encoding="utf-8")
    cfg = yaml.safe_load((ROOT / "competitors.yaml").read_text(encoding="utf-8"))
    st = cfg.get("settings", {})
    day = dt.date.today().isoformat()
    con = connect()
    methods_file = ROOT / "data" / "methods.json"
    methods = json.loads(methods_file.read_text()) if methods_file.exists() else {}
    budget = float(os.environ.get("PRICE_MINUTES", st.get("price_minutes", 0)) or 0)
    deadline = time.time() + budget * 60 if budget else None
    delay = tuple(st.get("delay_seconds", [1.0, 2.0]))

    ref = dict(cfg["reference"], is_ref=1)
    comps = [dict(c, is_ref=0) for c in cfg["competitors"]]
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        comps = [c for c in comps if c["name"].lower() in wanted]
    shops = [ref] + [c for c in comps if c.get("url")]
    opened = False

    def work(shop):
        pages = st.get("max_pages_reference", 800) if shop["is_ref"] else st.get("max_pages_generic", 150)
        d = tuple(st.get("delay_reference", [0.2, 0.5])) if shop["is_ref"] else delay   # own site: faster
        sc = Scraper(delay=d, max_pages=pages, log=log, deadline=None if shop["is_ref"] else deadline)
        t0 = time.time()
        method, rows, err = sc.scrape(shop, methods.get(domain(shop["url"])))
        return shop, method, rows, err, time.time() - t0

    def save(shop, method, rows, err, secs):
        nonlocal opened
        stock = sum(r["in_stock"] for r in rows)
        log(f"[prices] {shop['name']}: {method or 'FAILED'} - {len(rows)} products, {stock} in stock"
            f" ({secs:.0f}s){' | ' + err if err else ''}")
        con.execute("DELETE FROM products WHERE day=? AND shop=?", (day, shop["name"]))
        con.execute("INSERT OR REPLACE INTO scrape_log VALUES (?,?,?,?,?,?)",
                    (day, shop["name"], method or "", len(rows), stock, err or ""))
        for r in rows:
            con.execute("INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (day, shop["name"], shop["is_ref"], r["name"], r["vendor"], r["price"],
                         r["regular_price"], r["discount_pct"], r["in_stock"], 1 if is_pack(r["name"]) else 0,
                         r["url"], r["image"]))
        if method:
            methods[domain(shop["url"])] = method
            methods_file.write_text(json.dumps(methods, indent=1))
        con.commit()
        try:
            export_site(con, cfg, SITE)
            if args.open and not opened and (SITE / "data.js").exists():
                webbrowser.open((SITE / "index.html").as_uri())
                opened = True
        except Exception as e:
            log(f"[export] error {type(e).__name__}: {e}")

    log(f"=== run {day}: House Nutrition + {len(shops) - 1} competitor sites ===")
    with ThreadPoolExecutor(max_workers=int(st.get("parallel_shops", 8))) as pool:
        futures = [pool.submit(work, s) for s in shops]

        if not args.no_ads:
            log("[ads] Meta Ad Library (Tunisia) ...")
            try:
                from ads import save_images, scrape_ads
                cards = scrape_ads(cfg.get("ad_keywords", []), st.get("ad_scrolls", 8), log=log)
                all_shops = [cfg["reference"]] + cfg["competitors"]
                keep = []
                for c in cards:
                    c["competitor"] = attribute(c, all_shops)
                    if c["competitor"] or c["is_supplement"]:
                        keep.append(c)
                save_images(keep, SITE / "ads", log=log)
                if keep:
                    con.execute("DELETE FROM ads WHERE day=?", (day,))
                for c in keep:
                    con.execute("INSERT OR REPLACE INTO ads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (day, c["library_id"], c["advertiser"], c["competitor"], c["keyword"], c["started"],
                                 c["text"], c["prices"], c["old_price"], c["new_price"], c["discounts"],
                                 c["promo_words"], c["is_pack"], c["link"], c["page_url"], c["landing"], c["image"]))
                con.commit()
                log(f"[ads] {len(keep)} supplement ads saved ({len(cards)} seen)")
            except Exception as e:
                log(f"[ads] skipped: {type(e).__name__}: {str(e)[:200]}")

        for fut in as_completed(futures):
            try:
                save(*fut.result())
            except Exception as e:
                log(f"[prices] worker error {type(e).__name__}: {e}")

    export_site(con, cfg, SITE)
    log("[done] dashboard: docs/index.html")
    if args.open and not opened:
        webbrowser.open((SITE / "index.html").as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
