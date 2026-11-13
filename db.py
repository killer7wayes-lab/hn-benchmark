"""SQLite storage with daily history."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "hn.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    day TEXT, shop TEXT, is_reference INTEGER, name TEXT, brand TEXT,
    price REAL, regular_price REAL, discount_pct REAL,
    in_stock INTEGER, is_pack INTEGER, url TEXT, image TEXT,
    PRIMARY KEY (day, shop, url, name)
);
CREATE TABLE IF NOT EXISTS ads (
    day TEXT, library_id TEXT, advertiser TEXT, competitor TEXT, keyword TEXT,
    started TEXT, text TEXT, prices TEXT, old_price REAL, new_price REAL, discounts TEXT,
    promo_words TEXT, is_pack INTEGER, link TEXT, page_url TEXT, landing TEXT, image TEXT,
    PRIMARY KEY (day, library_id)
);
CREATE TABLE IF NOT EXISTS scrape_log (
    day TEXT, shop TEXT, method TEXT, products INTEGER, in_stock INTEGER, error TEXT,
    PRIMARY KEY (day, shop)
);
"""


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
