# House Nutrition – veille concurrence

Double-click **run.bat**: collects House Nutrition's catalogue, competitor prices and active
Meta ads (Tunisia), then opens the dashboard (`docs/index.html`).

Dashboard tabs: price comparison (base = House Nutrition products in stock), active ads,
competitor packs, collection status. Out-of-stock products are excluded.

- **open_dashboard.bat** – open the dashboard without collecting again
- **competitors.yaml** – competitor list, Facebook page names, Ad Library keywords, settings
- **confirmed_matches.csv** – fix matches by hand: `hn_name,shop,comp_name,ok` (1 = force, 0 = reject)
- **data/hn.db** – history (SQLite) · **data/run.log** – log of the last run
- **docs/** – the dashboard (static page: can be hosted as-is)
