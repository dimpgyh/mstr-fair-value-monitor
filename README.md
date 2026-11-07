# MSTR Observatory

Hourly MSTR asset-backing monitor with explicit capital structure, financing scenarios and a responsive Indonesian dashboard. **No automatic buy/sell signal and no untested seven-model composite.**

## Valuation

- **Current net asset reference** = `(BTC × quote + USD assets + software EV − debt − preferred liquidation claims − other net liabilities) / basic Class A + B shares`. Signed net assets remain visible; modeled common equity prices have a zero lower bound. This is a simplified asset-backing reference, not a liquidation floor or GAAP book value.
- **Conversion what-if** releases only the debt/preferred amount explicitly entered and adds the associated shares. No estimated diluted-share multiplier. Carry relief must be entered separately; the app does not infer instrument terms or conversion likelihood.
- **12-month scenarios**: equity, debt and preferred issuance at inception; net proceeds after issuance fees purchase BTC at the current quote. Equity adds shares, debt/preferred adds senior claims. One year of existing interest/dividends plus new funding costs is paid from USD assets. Software EV remains unchanged. A negative cash balance is exposed as a funding gap; BTC-sale timing, tax and refinancing execution are not simulated.
- **Terminal common equity** = `max(0, terminal BTC value × assumed asset multiple + software EV + cash after carry − remaining debt − remaining preferred − other net liabilities) / post-funding shares`. Discount once by `1 + annual discount rate` for present value. All scenarios share a 12-month horizon; none is assigned a probability.
- The dashboard separates asset backing from assumed premium/discount. Financing accretion compares terminal **net assets per share** with the same scenario without new financing; it does not conflate gross BTC/share with common-shareholder value.
- **Custom asset multiple** = `(market cap + debt + preferred − USD assets − software EV + other net liabilities) / gross BTC value`. This is explicitly **not Strategy's official mNAV**, whose definitions may change.
- Upside uses market price as denominator; discount to reference uses the reference value.

## Data and limitations

MSTR and BTC quotes come from Yahoo Finance chart metadata (`regularMarketPrice` and `regularMarketTime`), refreshed every hour. Delayed/closed-market prices retain their original observation times. BTC older than two hours and MSTR older than 96 hours are flagged stale. The latter is a simple weekend allowance, not a complete exchange holiday calendar. Provider failures never produce substitute prices or synthetic indicators; the last successful snapshot remains visible with an error.

Capital facts are **manual inputs**, not an automated SEC/treasury feed. Each records a URL, source date, status and note. “Reported” is the operator's attribution, not independent verification by the app. Dates older than 14 days are flagged. Edited facts lose their old evidence in the UI. Software value and other net liabilities remain explicit assumptions. Taxes, operating cash flows, other claims and execution costs beyond the entered issuance fee are not fully modeled.

The optional reviewed snapshot was manually transcribed from [Strategy's dashboard](https://www.strategy.com/) on **2026-11-07**: 849,327 BTC, debt $6.714B, preferred $13.136B, USD reserve plus cash $4.922B, annual interest/dividends $1.484B. USD values are rounded. Basic shares approximately 423.53M are **estimated** from displayed $54.606B market cap / $128.93 quote, not filing-verified. Dashboard observation date is not a shared effective filing date for all components. See [Strategy's metric limitations](https://www.strategy.com/notes).

Existing installations retain their old capital inputs with **unverified evidence** and unset annual carry (zero, with a warning). Use **Parameter → Muat snapshot resmi 7 Nov → Simpan** to deliberately apply the reviewed snapshot. It is never silently substituted for existing settings.

## History and evaluation

V2 observations are stored in a separate `snapshots_v2` table with the complete input configuration, source metadata, market timestamps and model version. Legacy tables and V1 snapshots remain intact. The chart uses only captured V2 observations with the same configuration as the latest observation. There is no backward reconstruction with today's holdings. V2 retention is three years by timestamp.

Evaluate matured 12-month forecasts against an unchanged market-price benchmark and an unchanged net-asset benchmark:

```sh
python -m scripts.evaluate --database data/mstr_monitor.db
```

The evaluator uses one forecast per UTC day and a captured market quote at/after day 365, allowing seven days for market closure. Before enough observations mature, it returns `insufficient_matured_history`. Overlapping cohorts are descriptive, not independent trials. **Improved model mechanics do not establish superior prediction accuracy.** There is no historical performance claim against V1.

## Run

Python 3.9+ (Docker uses 3.11):

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [the local dashboard](http://localhost:8000). Change `DATA_DIR` to isolate a preview database. The scheduler runs in the app process: use **one Uvicorn worker**. Manual refresh and configuration saves reset the next refresh deadline. Startup does not block the UI while fetching quotes. A successful refresh persists a snapshot; failed refreshes preserve the last result and expose a degraded status.

## Deployment

The existing entry points remain:

```sh
./push-to-vps.sh <IP_PUBLIC_VPS> <SSH_USER>
sudo bash deploy-biznetgio.sh
docker compose up -d --build
```

Before upgrading, back up `data/mstr_monitor.db` and record the deployed Git revision. No legacy-table changes are required. For rollback restore the previous code revision; V1 continues reading its own untouched config/table. V2 configuration lives under `app_config_v2`.

The dashboard retains the original public-access model: anyone with same-origin access can change shared parameters. Cross-origin browser writes are rejected, but this is **not authentication**. Keep write access behind an authenticated reverse proxy if exposed beyond trusted users. The existing install script still uses its original service account and network configuration; this change does not claim VPS hardening or a completed production deployment.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
# Run the app on 127.0.0.1:8017 first (use a disposable DATA_DIR).
.venv/bin/python tests/browser_check.py
```

Unit/API checks cover claim priority, conversion, financing fees and dilution, carry, cash gaps, stale sources, validation, missing prices, cache retention and legacy migration. Browser checks exercise the actual app feed, desktop/mobile layout and configuration controls. Browser fixture checks, when used, are labeled synthetic and do not establish provider availability.
