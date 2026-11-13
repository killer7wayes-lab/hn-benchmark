"""Active Meta ads in Tunisia, read from the public Ad Library website with Playwright.

One keyword search per entry in ad_keywords (country TN, active ads). Each ad card gives:
advertiser, text, start date, Facebook page link, landing website, and the main image
(saved locally as a small file, because Facebook image links expire after a few days).
"""
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests
from playwright.sync_api import sync_playwright

LIB = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
       "&country=TN&media_type=all&search_type=keyword_unordered&q={q}")
ID_RE = r"(?:Library ID|ID dans la biblioth[èe]que|Identifiant de la biblioth[èe]que)\s*:?\s*(\d+)"
SPONSORED = ("Sponsored", "Sponsorisé", "Sponsorisée")
PRICE = re.compile(r"(\d{2,4}(?:[.,]\d{1,3})?)\s*(?:dt|tnd|dinars?|د\.?ت|دينار)(?![a-z])", re.I)
AU_LIEU = re.compile(r"(\d{2,4}(?:[.,]\d{1,3})?)\s*(?:dt|tnd)?\s*au lieu de\s*(\d{2,4}(?:[.,]\d{1,3})?)", re.I)
DISC = re.compile(r"-\s?\d{1,2}\s?%|\d{1,2}\s?%\s?(?:off|de r[ée]duction|remise)", re.I)
PROMO_WORDS = ["promo", "solde", "offert", "gratuit", "livraison gratuite", "pack", "black friday",
               "white friday", "remise", "réduction", "offre", "1+1", "تخفيض", "عرض", "مجانا", "هدية"]
STRONG = re.compile(r"whey|cr[ée]atine|gainer|bcaa|\\beaa\\b|pre.?workout|isolate|كرياتين|واي بروتين", re.I)
ADV_OK = re.compile(r"nutrition|protein|whey|muscle|suppl|gym|fitness", re.I)
ADV_NO = re.compile(r"beaut|cosm|\\bspa\\b|calorie|\\bapp\\b|pharma|\\bbio\\b|hair|esth|coiff|clinic|diet", re.I)


def relevant(advertiser, text):
    """Untracked advertiser: keep only real sports-supplement sellers."""
    return bool((STRONG.search(text) or ADV_OK.search(advertiser)) and not ADV_NO.search(advertiser))


JS_CARDS = r"""
() => {
  const re = /^(Library ID|ID dans la biblioth|Identifiant de la biblioth)/;
  const cnt = t => ((t || '').match(/Library ID|ID dans la biblioth|Identifiant de la biblioth/g) || []).length;
  const leaves = [...document.querySelectorAll('span,div')].filter(e => e.childElementCount === 0 && re.test(e.textContent.trim()));
  return leaves.map(n => {
    let el = n;
    while (el.parentElement && cnt(el.parentElement.innerText) <= 1) el = el.parentElement;
    const imgs = [...el.querySelectorAll('img')].filter(i => i.naturalWidth >= 200).map(i => i.src);
    const posters = [...el.querySelectorAll('video')].map(v => v.poster).filter(Boolean);
    const links = [...el.querySelectorAll('a[href]')].map(a => a.href);
    return {text: el.innerText, img: imgs[0] || posters[0] || '', links};
  });
}
"""


def _f(s):
    return float(s.replace(",", "."))


def parse_card(raw, keyword):
    text = raw.get("text") or ""
    m = re.search(ID_RE, text)
    if not m:
        return None
    lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip() != "​"]
    started = next((re.sub(r"^(Started running on|Début de diffusion le)\s*", "", l) for l in lines
                    if l.startswith(("Started running on", "Début de diffusion"))), "")
    idx = next((i for i, l in enumerate(lines) if l in SPONSORED), None)
    advertiser = lines[idx - 1] if idx else ""
    body = "\n".join(lines[idx + 1:])[:1500] if idx is not None else ""
    low = body.lower()
    prices = [_f(p) for p in PRICE.findall(body)]
    old = new = None
    al = AU_LIEU.search(body)
    if al:
        new, old = _f(al.group(1)), _f(al.group(2))
    elif len(prices) >= 2 and prices[0] > prices[1]:
        old, new = prices[0], prices[1]
    elif prices:
        new = prices[0]
    page_url, landing = "", ""
    for h in raw.get("links") or []:
        u = urlparse(h)
        if "l.facebook.com" in u.netloc and not landing:
            landing = (parse_qs(u.query).get("u") or [""])[0]
        elif "facebook.com" in u.netloc and not page_url and "/ads/" not in u.path:
            page_url = h.split("?")[0]
    return {
        "library_id": m.group(1), "advertiser": advertiser, "keyword": keyword, "started": started,
        "text": body, "prices": ", ".join(f"{p:g}" for p in dict.fromkeys(prices)),
        "old_price": old if (old and new and old > new) else None, "new_price": new,
        "discounts": ", ".join(dict.fromkeys(DISC.findall(body))),
        "promo_words": ", ".join(w for w in PROMO_WORDS if w in low),
        "is_pack": 1 if re.search(r"\bpacks?\b|\bcombo\b|\bbundle\b", low) else 0,
        "link": f"https://www.facebook.com/ads/library/?id={m.group(1)}",
        "page_url": page_url, "landing": landing, "img_src": raw.get("img") or "",
        "is_supplement": relevant(advertiser, body),
    }


def scrape_ads(keywords, scrolls=8, log=print, headless=True):
    found = {}
    with sync_playwright() as pw:
        browser, errors = None, []
        # bundled Chromium first, then the Chrome / Edge already installed on the computer
        for opts in ({}, {"channel": "chrome"}, {"channel": "msedge"}, {"channel": "chromium"}):
            try:
                browser = pw.chromium.launch(headless=headless, **opts)
                log(f"  browser: {opts.get('channel', 'bundled chromium')}")
                break
            except Exception as e:
                errors.append(f"{opts.get('channel', 'bundled')}: {str(e).splitlines()[0][:80]}")
        if browser is None:
            raise RuntimeError("no browser could start - " + " | ".join(errors))
        page = browser.new_page(locale="fr-FR", viewport={"width": 1280, "height": 900})
        for kw in keywords:
            try:
                page.goto(LIB.format(q=quote(kw)), wait_until="domcontentloaded", timeout=60000)
                time.sleep(5)
                for label in ("Refuser les cookies optionnels", "Decline optional cookies",
                              "Autoriser uniquement les cookies essentiels", "Only allow essential cookies"):
                    btn = page.get_by_role("button", name=label)
                    if btn.count():
                        btn.first.click()
                        time.sleep(1)
                        break
                last = 0
                for _ in range(scrolls):
                    page.mouse.wheel(0, 5000)
                    time.sleep(2.2)
                    n = page.evaluate("document.body.innerText.length")
                    if n == last:
                        break
                    last = n
                cards = [c for c in (parse_card(r, kw) for r in page.evaluate(JS_CARDS)) if c]
                for c in cards:
                    found.setdefault(c["library_id"], c)
                log(f"  ads '{kw}': {len(cards)} ads")
            except Exception as e:
                log(f"  ads '{kw}': error {type(e).__name__}: {str(e)[:120]}")
            time.sleep(2)
        browser.close()
    return list(found.values())


def save_images(cards, folder: Path, log=print):
    """Download each ad's main image to docs/ads/<id>.jpg; remove images of ads no longer active."""
    folder.mkdir(parents=True, exist_ok=True)
    keep = set()
    for c in cards:
        c["image"] = ""
        src = c.get("img_src")
        if not src:
            continue
        f = folder / f"{c['library_id']}.jpg"
        try:
            if not f.exists():
                r = requests.get(src, timeout=25)
                if r.status_code != 200 or len(r.content) < 500:
                    continue
                f.write_bytes(r.content)
            c["image"] = f"ads/{f.name}"
            keep.add(f.name)
        except requests.RequestException:
            continue
    for old in folder.glob("*.jpg"):
        if old.name not in keep:
            old.unlink()
    log(f"  ad images saved: {len(keep)}")
