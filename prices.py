"""Product + price scraper that works on any shop platform.

Order tried per site:
  1. Shopify      /products.json
  2. WooCommerce  /wp-json/wc/store/v1/products
  3. Generic      sitemap -> product pages (JSON-LD / meta tags / CSS selectors),
                  and if the sitemap is useless, a crawl from the home page.
One Scraper instance per shop (they run in parallel, one thread per shop).
"""
import html as htmllib
import json
import random
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}
OLD_PRICE_SEL = (".product-prices .regular-price, .product-information .regular-price, "
                 ".summary del .amount, .entry-summary del .amount, "
                 ".product-info-main .old-price, .product-info .compare-price")
OOS_SEL = ".out-of-stock, .outofstock, .product-unavailable, .stock.out-of-stock, .sold-out, .rupture"
SKIP = re.compile(r"(cart|panier|commande|checkout|login|connexion|account|compte|contact|wishlist|compare|"
                  r"blog|/cms|content/|\.(jpe?g|png|webp|gif|pdf|svg|css|js)$|"
                  r"[?&](order|orderby|sort|q|s|add|id_product_attribute|resultsPerPage)=)", re.I)
PRODUCT_URL = re.compile(r"\.html$|/products?/|/produit/|/p/|/shop/[^/]+/?$", re.I)


def num(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.,]", "", str(v))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def row(name, price, regular, in_stock, url, vendor="", image=""):
    if price is None or price <= 0 or not name:
        return None
    if regular is not None and (regular <= price or regular > price * 3):
        regular = None
    disc = round((regular - price) / regular * 100, 1) if regular else None
    return {"name": htmllib.unescape(str(name)).strip(), "vendor": (vendor or "").strip(), "price": price,
            "regular_price": regular, "discount_pct": disc, "in_stock": 1 if in_stock else 0,
            "url": url, "image": image or ""}


class Scraper:
    def __init__(self, delay=(1.0, 2.0), max_pages=150, log=print, deadline=None):
        self.delay, self.max_pages, self.log, self.deadline = delay, max_pages, log, deadline
        self.codes = {}
        self.s = requests.Session()
        self.s.headers.update(HEADERS)

    def _get(self, url, **kw):
        r = self.s.get(url, timeout=25, **kw)
        self.codes[r.status_code] = self.codes.get(r.status_code, 0) + 1
        if r.status_code in (429, 403, 503) and self.codes[r.status_code] <= 3:
            time.sleep(20)                                   # the shop is rate-limiting: wait and retry once
            r = self.s.get(url, timeout=25, **kw)
        time.sleep(random.uniform(*self.delay))
        return r

    def _late(self):
        return bool(self.deadline and time.time() > self.deadline)

    # ---------- 1. Shopify ----------
    def shopify(self, base):
        out = []
        for page in range(1, 40):
            r = self._get(f"{base}/products.json", params={"limit": 250, "page": page})
            if r.status_code != 200 or '"products"' not in r.text[:300]:
                return out or None
            items = r.json().get("products", [])
            if not items:
                break
            for p in items:
                url = f"{base}/products/{p.get('handle')}"
                img = (p.get("images") or [{}])[0].get("src", "")
                groups = {}
                for v in p.get("variants", []):
                    price = num(v.get("price"))
                    g = groups.setdefault(price, {"reg": num(v.get("compare_at_price")), "stock": False, "titles": []})
                    g["stock"] = g["stock"] or bool(v.get("available", True))
                    g["titles"].append(v.get("title") or "")
                for price, g in groups.items():
                    name = p["title"]
                    if len(groups) > 1:      # variants with different prices are usually sizes
                        name = f"{name} - {g['titles'][0]}"
                    r_ = row(name, price, g["reg"], g["stock"], url, p.get("vendor", ""), img)
                    if r_:
                        out.append(r_)
        return out

    # ---------- 2. WooCommerce Store API ----------
    def woocommerce(self, base):
        out = []
        for page in range(1, 80):
            r = self._get(f"{base}/wp-json/wc/store/v1/products", params={"per_page": 100, "page": page})
            if r.status_code != 200 or not r.text.lstrip().startswith("["):
                return out or None
            items = r.json()
            if not items:
                break
            for p in items:
                pr = p.get("prices", {}) or {}
                div = 10 ** int(pr.get("currency_minor_unit", 0) or 0)
                price, regular = num(pr.get("price")), num(pr.get("regular_price"))
                price = price / div if price is not None else None
                regular = regular / div if regular is not None else None
                brand = ", ".join(b.get("name", "") for b in (p.get("brands") or []))
                img = (p.get("images") or [{}])[0].get("src", "")
                r_ = row(BeautifulSoup(p.get("name", ""), "lxml").get_text(), price, regular,
                         p.get("is_in_stock", True), p.get("permalink", ""), brand, img)
                if r_:
                    out.append(r_)
            if len(items) < 100:
                break
        return out

    # ---------- 2b. Vendure (GraphQL shop-api) ----------
    def vendure(self, base):
        q = ("query($s:Int!){search(input:{groupByProduct:false,take:100,skip:$s}){totalItems items{"
             "productName productVariantName slug inStock priceWithTax{...on PriceRange{min max} "
             "...on SinglePrice{value}} productAsset{preview}}}}")
        out, skip = {}, 0
        while skip < 5000:
            r = self.s.post(f"{base}/shop-api?languageCode=fr", json={"query": q, "variables": {"s": skip}}, timeout=25)
            time.sleep(random.uniform(*self.delay))
            if r.status_code != 200 or '"search"' not in r.text[:200]:
                return list(out.values()) or None
            res = r.json()["data"]["search"]
            for it in res["items"]:
                pw = it.get("priceWithTax") or {}
                price = (pw.get("value") or pw.get("min") or 0) / 100
                name = re.split(r"\s/\s", it.get("productVariantName") or it["productName"])[0]   # drop the flavour
                url = f"{base}/fr/products/{it['slug']}"
                r_ = row(name, price, None, it.get("inStock", True), url, "", (it.get("productAsset") or {}).get("preview", ""))
                if r_:
                    k = (name, price)
                    if k in out:
                        out[k]["in_stock"] = max(out[k]["in_stock"], r_["in_stock"])
                    else:
                        out[k] = r_
            skip += 100
            if skip >= res["totalItems"] or not res["items"]:
                break
        return list(out.values())

    # ---------- 3. Generic ----------
    def _sitemap_urls(self, base):
        roots = []
        try:
            roots += re.findall(r"(?im)^sitemap:\s*(\S+)", self._get(f"{base}/robots.txt").text)
        except requests.RequestException:
            pass
        roots += [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/1_index_sitemap.xml",
                  f"{base}/product-sitemap.xml"]
        seen, urls, queue = set(), [], list(dict.fromkeys(roots))
        while queue and len(seen) < 40:
            sm = queue.pop(0)
            if sm in seen:
                continue
            seen.add(sm)
            try:
                r = self._get(sm)
            except requests.RequestException:
                continue
            if r.status_code != 200 or "<loc" not in r.text:
                continue
            locs = [htmllib.unescape(re.sub(r"^<!\[CDATA\[|\]\]>$", "", l.strip()).strip())
                    for l in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, re.S)]
            if "<sitemapindex" in r.text:
                queue += [l for l in locs if re.search(r"product|produit", l, re.I)] or locs
            else:
                urls += locs
        urls = [u for u in dict.fromkeys(urls) if not SKIP.search(u)]
        prod = [u for u in urls if PRODUCT_URL.search(u)]
        return prod or urls

    def parse_page(self, url, html, selectors=None):
        soup = BeautifulSoup(html, "lxml")
        og_img = soup.find("meta", property="og:image")
        og_img = og_img.get("content", "") if og_img else ""
        oos_dom = bool(soup.select_one(OOS_SEL))
        if selectors:
            def pick(key):
                el = soup.select_one(selectors[key]) if selectors.get(key) else None
                return el.get_text(" ", strip=True) if el else None
            r_ = row(pick("name"), num(pick("price")), num(pick("old_price")), not oos_dom, url, "", og_img)
            if r_:
                return r_
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or tag.get_text() or "", strict=False)
            except (json.JSONDecodeError, TypeError):
                continue
            nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
            for n in nodes:
                if not isinstance(n, dict) or "Product" not in str(n.get("@type")):
                    continue
                offers = n.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = num(offers.get("price") or offers.get("lowPrice"))
                stock = not re.search(r"OutOfStock|SoldOut|Discontinued", str(offers.get("availability", "")))
                brand = n.get("brand")
                brand = brand.get("name") if isinstance(brand, dict) else (brand if isinstance(brand, str) else "")
                img = n.get("image")
                img = img[0] if isinstance(img, list) and img else img
                img = img.get("url") if isinstance(img, dict) else img
                old = soup.select_one(OLD_PRICE_SEL)
                r_ = row(n.get("name"), price, num(old.get_text()) if old else None, stock, url, brand,
                         img if isinstance(img, str) else og_img)
                if r_:
                    return r_
        meta = (soup.find("meta", property="product:price:amount") or soup.find("meta", itemprop="price")
                or soup.find("meta", property="og:price:amount")
                or soup.select_one(".product-prices [itemprop=price], .product-info-main [itemprop=price]")
                or soup.find(attrs={"itemprop": "price"}))
        if meta:
            h1, title = soup.find("h1"), soup.find("meta", property="og:title")
            name = (h1.get_text(" ", strip=True) if h1 else None) or (title.get("content") if title else None)
            old = soup.select_one(OLD_PRICE_SEL)
            avail = soup.find("meta", property="product:availability")
            avail = (avail.get("content") or "").lower() if avail else ""
            if not avail:
                link = soup.find(attrs={"itemprop": "availability"})
                avail = ((link.get("href") or link.get("content") or "") if link else "").lower()
            stock = not ("out" in avail or "rupture" in avail or (not avail and oos_dom))
            return row(name, num(meta.get("content") or meta.get_text()),
                       num(old.get_text()) if old else None, stock, url, "", og_img)
        return None

    def crawl(self, base, selectors=None, have=None):
        """Walk the shop from its home page: listing pages give links, product pages give prices."""
        host = urlparse(base).netloc.replace("www.", "")
        seen = set(have or [])
        prod_q, list_q, out, fetched = [], [base + "/"], [], 0
        prod_set, list_set = set(), {base + "/"}
        while (prod_q or list_q) and fetched < self.max_pages * 2 and len(out) < self.max_pages:
            if self._late():
                break
            # alternate: mostly product pages, but keep discovering listings so the queue never runs dry
            take_list = list_q and (not prod_q or fetched % 4 == 0)
            u = list_q.pop(0) if take_list else prod_q.pop(0)
            if u in seen:
                continue
            seen.add(u)
            try:
                r = self._get(u)
            except requests.RequestException:
                continue
            fetched += 1
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                continue
            new_prod = 0
            for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
                link = urljoin(u, a["href"]).split("#")[0]
                if urlparse(link).netloc.replace("www.", "") != host or link in seen or SKIP.search(link):
                    continue
                if PRODUCT_URL.search(link):
                    new_prod += 1
                    if link not in prod_set:
                        prod_set.add(link)
                        prod_q.append(link)
                elif link not in list_set and len(list_set) < 2000:
                    list_set.add(link)
                    list_q.append(link)
            is_product_url = bool(PRODUCT_URL.search(u))
            if is_product_url or new_prod < 6:               # a page full of product links is a listing, not a product
                r_ = self.parse_page(u, r.text, selectors)
                if r_:
                    out.append(r_)
        return out

    def generic(self, base, selectors=None):
        urls = self._sitemap_urls(base)[: self.max_pages]
        out, tried = [], 0
        for u in urls:
            if self._late() or (tried >= 15 and not out):
                break
            tried += 1
            try:
                r = self._get(u)
                if r.status_code == 200:
                    r_ = self.parse_page(u, r.text, selectors)
                    if r_:
                        out.append(r_)
            except requests.RequestException:
                continue
        if len(out) < 20 and not self._late():
            out += self.crawl(base, selectors, have={r["url"] for r in out})
        return out or None

    # ---------- entry ----------
    def scrape(self, shop, known=None):
        base = shop["url"].rstrip("/")
        order = ["shopify", "woocommerce", "vendure", "generic"]
        if known in order:
            order.remove(known)
            order.insert(0, known)
        last_err = None
        for m in order:
            try:
                rows = self.generic(base, shop.get("selectors")) if m == "generic" else getattr(self, m)(base)
            except Exception as e:
                rows, last_err = None, f"{m}: {type(e).__name__}: {e}"[:200]
            if rows:
                seen, uniq = set(), []
                for r_ in rows:
                    k = (r_["url"], r_["name"])
                    if k not in seen:
                        seen.add(k)
                        uniq.append(r_)
                if shop.get("brand"):
                    for r_ in uniq:
                        r_["vendor"] = r_["vendor"] or shop["brand"]
                self.log(f"   {shop['name']}: http {self.codes}")
                return m, uniq, None
        self.log(f"   {shop['name']}: http {self.codes}")
        return None, [], last_err or "no products found (add selectors in competitors.yaml)"
