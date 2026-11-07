import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
import pandas as pd
import yfinance as yf

from app.database import get_config, update_config

logger = logging.getLogger("mstr_monitor.valuation")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _fetch_yahoo_chart(symbol: str, range_str: str = "6mo", interval: str = "1d") -> Optional[Dict[str, Any]]:
    """Fetch price and historical daily OHLCV from Yahoo Finance v8 Chart endpoint."""
    urls = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_str}&interval={interval}",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_str}&interval={interval}",
    ]
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for url in urls:
        try:
            with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("chart", {}).get("result")
                    if result and len(result) > 0:
                        return result[0]
        except Exception as exc:
            logger.warning("Yahoo chart fetch failed for %s (%s): %s", symbol, url, exc)
    return None


def _parse_chart_to_series(chart_res: Dict[str, Any]) -> Tuple[Dict[str, Any], pd.Series]:
    """Extract metadata and a date-indexed close price Series from Yahoo chart result."""
    meta = chart_res.get("meta", {})
    timestamps = chart_res.get("timestamp", [])
    quotes = chart_res.get("indicators", {}).get("quote", [{}])[0]
    closes = quotes.get("close", [])

    records = {}
    for ts, close_val in zip(timestamps, closes):
        if close_val is not None and not math.isnan(close_val):
            dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            records[dt_str] = float(close_val)

    series = pd.Series(records, dtype=float)
    series.index.name = "date"
    return meta, series


def _fetch_coinbase_btc_spot() -> Optional[float]:
    """Fallback live BTC-USD spot price from Coinbase API."""
    try:
        with httpx.Client(timeout=6.0, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get("https://api.coinbase.com/v2/prices/BTC-USD/spot")
            if resp.status_code == 200:
                amount = resp.json().get("data", {}).get("amount")
                if amount:
                    return float(amount)
    except Exception as exc:
        logger.warning("Coinbase BTC spot fallback failed: %s", exc)
    return None


def _compute_rsi(series: pd.Series, period: int = 14) -> float:
    """Compute RSI-14 from a price series."""
    if len(series) < period + 1:
        return 50.0
    delta = series.diff().dropna()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean().iloc[-1]
    avg_loss = loss.rolling(window=period, min_periods=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def fetch_market_and_fundamentals(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch live MSTR & BTC-USD prices, 90-day history, and fundamental parameters."""
    mstr_meta: Dict[str, Any] = {}
    btc_meta: Dict[str, Any] = {}
    mstr_series = pd.Series(dtype=float)
    btc_series = pd.Series(dtype=float)

    # Primary: yfinance history (handles Yahoo crumb/cookie session automatically)
    try:
        hist = yf.Ticker("MSTR").history(period="1y", interval="1d")
        if not hist.empty:
            mstr_series = pd.Series(
                hist["Close"].values,
                index=[d.strftime("%Y-%m-%d") for d in hist.index],
                dtype=float,
            )
    except Exception as exc:
        logger.warning("yfinance MSTR history failed: %s", exc)

    try:
        hist = yf.Ticker("BTC-USD").history(period="1y", interval="1d")
        if not hist.empty:
            btc_series = pd.Series(
                hist["Close"].values,
                index=[d.strftime("%Y-%m-%d") for d in hist.index],
                dtype=float,
            )
    except Exception as exc:
        logger.warning("yfinance BTC history failed: %s", exc)

    # Fallback to direct chart API if needed
    if mstr_series.empty:
        mstr_chart = _fetch_yahoo_chart("MSTR", range_str="1y", interval="1d")
        if mstr_chart:
            mstr_meta, mstr_series = _parse_chart_to_series(mstr_chart)
    if btc_series.empty:
        btc_chart = _fetch_yahoo_chart("BTC-USD", range_str="1y", interval="1d")
        if btc_chart:
            btc_meta, btc_series = _parse_chart_to_series(btc_chart)

    # Determine live MSTR price
    mstr_price = float(
        mstr_meta.get("regularMarketPrice")
        or (mstr_series.iloc[-1] if not mstr_series.empty else 145.0)
    )
    mstr_prev_close = float(
        mstr_meta.get("chartPreviousClose")
        or mstr_meta.get("previousClose")
        or (mstr_series.iloc[-2] if len(mstr_series) > 1 else mstr_price)
    )
    mstr_52w_high = float(
        mstr_meta.get("fiftyTwoWeekHigh")
        or (mstr_series.max() if not mstr_series.empty else mstr_price * 1.5)
    )
    mstr_52w_low = float(
        mstr_meta.get("fiftyTwoWeekLow")
        or (mstr_series.min() if not mstr_series.empty else mstr_price * 0.5)
    )

    # Determine live BTC price
    btc_price = float(
        btc_meta.get("regularMarketPrice")
        or (btc_series.iloc[-1] if not btc_series.empty else 85000.0)
    )
    btc_prev_close = float(
        btc_meta.get("chartPreviousClose")
        or btc_meta.get("previousClose")
        or (btc_series.iloc[-2] if len(btc_series) > 1 else btc_price)
    )
    btc_52w_high = float(
        btc_meta.get("fiftyTwoWeekHigh")
        or (btc_series.max() if not btc_series.empty else btc_price * 1.25)
    )
    btc_52w_low = float(
        btc_meta.get("fiftyTwoWeekLow")
        or (btc_series.min() if not btc_series.empty else btc_price * 0.65)
    )

    # Try enriching fundamental fields from yfinance .info if auto_sync_fundamentals is enabled
    synced_updates: Dict[str, Any] = {}
    if cfg.get("auto_sync_fundamentals", True):
        try:
            t_info = yf.Ticker("MSTR").info or {}
            shares_out = t_info.get("sharesOutstanding") or t_info.get("impliedSharesOutstanding")
            if shares_out and shares_out > 100_000_000:
                synced_updates["basic_shares"] = float(shares_out)
                # Keep diluted shares at least 8% above basic shares if basic grew
                if cfg["diluted_shares"] < float(shares_out) * 1.06:
                    synced_updates["diluted_shares"] = round(float(shares_out) * 1.10, 0)

            total_debt = t_info.get("totalDebt")
            if total_debt and total_debt > 1_000_000_000:
                synced_updates["total_debt"] = float(total_debt)

            total_cash = t_info.get("totalCash")
            if total_cash and total_cash > 10_000_000:
                synced_updates["cash_and_equivalents"] = float(total_cash)

            target_mean = t_info.get("targetMeanPrice")
            if target_mean and target_mean > 20:
                synced_updates["analyst_target_mean"] = float(target_mean)

            target_high = t_info.get("targetHighPrice")
            if target_high and target_high > 20:
                synced_updates["analyst_target_high"] = float(target_high)

            target_low = t_info.get("targetLowPrice")
            if target_low and target_low > 10:
                synced_updates["analyst_target_low"] = float(target_low)

            num_analysts = t_info.get("numberOfAnalystOpinions")
            if num_analysts and int(num_analysts) > 0:
                synced_updates["analyst_count"] = int(num_analysts)

            if synced_updates:
                cfg = update_config(synced_updates)
        except Exception as exc:
            logger.info("Skipping live yfinance .info enrichment (using stored parameters): %s", exc)

    return {
        "mstr_price": mstr_price,
        "mstr_prev_close": mstr_prev_close,
        "mstr_52w_high": mstr_52w_high,
        "mstr_52w_low": mstr_52w_low,
        "btc_price": btc_price,
        "btc_prev_close": btc_prev_close,
        "btc_52w_high": btc_52w_high,
        "btc_52w_low": btc_52w_low,
        "mstr_series": mstr_series,
        "btc_series": btc_series,
        "config": cfg,
    }


def compute_full_valuation() -> Dict[str, Any]:
    """Run all 7 MSTR valuation models, combine into Composite Fair Value, and build dashboard payload."""
    cfg = get_config()
    raw = fetch_market_and_fundamentals(cfg)
    cfg = raw["config"]

    mstr_price = raw["mstr_price"]
    mstr_prev_close = raw["mstr_prev_close"]
    btc_price = raw["btc_price"]
    btc_prev_close = raw["btc_prev_close"]
    mstr_series: pd.Series = raw["mstr_series"]
    btc_series: pd.Series = raw["btc_series"]

    # Update latest date in series with live price for accurate regression & technicals
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not mstr_series.empty:
        mstr_series.loc[today_str] = mstr_price
    if not btc_series.empty:
        btc_series.loc[today_str] = btc_price

    # Extract core treasury & balance sheet parameters
    btc_holdings = float(cfg["btc_holdings"])
    btc_avg_cost = float(cfg["btc_avg_cost"])
    basic_shares = float(cfg["basic_shares"])
    diluted_shares = max(basic_shares, float(cfg["diluted_shares"]))
    total_debt = float(cfg["total_debt"])
    preferred_stock = float(cfg["preferred_stock"])
    cash = float(cfg["cash_and_equivalents"])
    software_ev = float(cfg["software_ev"])
    annual_software_rev = float(cfg["annual_software_revenue"])

    target_basic_mnav = float(cfg["target_basic_mnav"])
    target_ev_mnav = float(cfg["target_ev_mnav"])
    annual_btc_yield = float(cfg["annual_btc_yield_pct"]) / 100.0
    discount_rate = float(cfg["discount_rate_pct"]) / 100.0
    horizon_years = float(cfg["yield_horizon_years"])
    optionality_premium = float(cfg["optionality_premium_pct"]) / 100.0

    # 1. Core Bitcoin Treasury & NAV Metrics
    btc_treasury_value = btc_holdings * btc_price
    btc_cost_basis_total = btc_holdings * btc_avg_cost
    btc_unrealized_pnl = btc_treasury_value - btc_cost_basis_total
    btc_unrealized_pnl_pct = ((btc_price - btc_avg_cost) / btc_avg_cost * 100.0) if btc_avg_cost > 0 else 0.0

    market_cap_basic = mstr_price * basic_shares
    market_cap_diluted = mstr_price * diluted_shares
    enterprise_value = market_cap_basic + total_debt + preferred_stock - cash

    # Current mNAV Multiples
    current_basic_mnav = market_cap_basic / btc_treasury_value if btc_treasury_value > 0 else 0.0
    current_ev_mnav = (enterprise_value - software_ev) / btc_treasury_value if btc_treasury_value > 0 else 0.0
    current_diluted_mnav = market_cap_diluted / btc_treasury_value if btc_treasury_value > 0 else 0.0

    btc_per_share = btc_holdings / basic_shares
    sats_per_share = btc_per_share * 100_000_000.0
    btc_per_1000_shares = btc_per_share * 1000.0
    btc_nav_per_share_pure = btc_treasury_value / basic_shares
    implied_btc_price_in_mstr = (market_cap_basic - software_ev) / btc_holdings if btc_holdings > 0 else btc_price

    # 2. Quantitative OLS Regression & Technical Indicators (90 Trading Days)
    joined = pd.DataFrame({"mstr": mstr_series, "btc": btc_series}).dropna().tail(90)
    if len(joined) >= 15:
        x_vals = joined["btc"].values
        y_vals = joined["mstr"].values
        slope, intercept = np.polyfit(x_vals, y_vals, 1)
        y_pred = intercept + slope * x_vals
        residuals = y_vals - y_pred
        std_error = float(np.std(residuals, ddof=1)) if len(residuals) > 2 else 10.0
        corr_matrix = np.corrcoef(x_vals, y_vals)
        corr_r = float(corr_matrix[0, 1]) if not np.isnan(corr_matrix[0, 1]) else 0.85
        r_squared = corr_r ** 2
        ols_expected_price = max(10.0, float(intercept + slope * btc_price))
        z_score = (mstr_price - ols_expected_price) / std_error if std_error > 0 else 0.0
    else:
        slope = btc_per_share * target_basic_mnav
        intercept = software_ev / basic_shares
        std_error = mstr_price * 0.10
        corr_r = 0.85
        r_squared = 0.72
        ols_expected_price = (btc_treasury_value * target_basic_mnav + software_ev) / basic_shares
        z_score = 0.0

    # Technical indicators on MSTR
    rsi_14 = _compute_rsi(mstr_series, 14) if len(mstr_series) >= 15 else 50.0
    sma_20 = float(mstr_series.tail(20).mean()) if len(mstr_series) >= 5 else mstr_price
    sma_50 = float(mstr_series.tail(50).mean()) if len(mstr_series) >= 10 else mstr_price
    sma_200 = float(mstr_series.tail(200).mean()) if len(mstr_series) >= 20 else mstr_price

    mstr_ret_30d = mstr_series.pct_change().dropna().tail(30)
    btc_ret_30d = btc_series.pct_change().dropna().tail(30)
    mstr_vol_30d = float(mstr_ret_30d.std() * math.sqrt(252) * 100.0) if len(mstr_ret_30d) > 5 else 85.0
    btc_vol_30d = float(btc_ret_30d.std() * math.sqrt(252) * 100.0) if len(btc_ret_30d) > 5 else 52.0
    vol_multiplier = mstr_vol_30d / btc_vol_30d if btc_vol_30d > 0 else 1.6

    # =========================================================================
    # 3. THE 7 VALUATION MODELS
    # =========================================================================

    # Model 1: Target Basic mNAV Valuation
    val_target_mnav = (btc_treasury_value * target_basic_mnav + software_ev) / basic_shares

    # Model 2: EV & Debt/Dilution-Adjusted mNAV Valuation
    ev_adj_equity = (
        (btc_treasury_value * target_ev_mnav)
        + software_ev
        + cash
        - total_debt
        - preferred_stock
    )
    val_ev_adjusted_mnav = max(10.0, ev_adj_equity / diluted_shares)

    # Model 3: Saylor "BTC Yield" Accretion DCF Model
    accretion_factor = ((1.0 + annual_btc_yield) / (1.0 + discount_rate)) ** horizon_years
    val_btc_yield_dcf = (btc_nav_per_share_pure * accretion_factor) + (software_ev / basic_shares)

    # Model 4: Quantitative 90-Day OLS Regression Model
    val_ols_regression = ols_expected_price

    # Model 5: Wall Street Analyst Consensus Target
    val_analyst_consensus = float(cfg["analyst_target_mean"])

    # Model 6: FASB Balance Sheet Mark-to-Market + Convertible Optionality Model
    net_balance_sheet_equity = (
        btc_treasury_value
        + (btc_treasury_value * optionality_premium)
        + (annual_software_rev * 3.0)
        + cash
        - total_debt
        - preferred_stock
    )
    val_balance_sheet_mtm = max(10.0, net_balance_sheet_equity / basic_shares)

    # Model 7: 1.0x Basic NAV Floor + Core Software
    val_basic_nav_floor = (btc_treasury_value + software_ev) / basic_shares

    # Build normalized weights
    raw_weights = cfg.get("weights", {})
    model_defs = [
        {
            "id": "target_mnav",
            "name": f"Target Basic mNAV ({target_basic_mnav:.2f}x)",
            "category": "Bitcoin Treasury Multiplier",
            "formula": f"(BTC Value × {target_basic_mnav:.2f}x + Software EV) / Basic Shares",
            "description": "Menilai MSTR berdasarkan target premi mNAV historis atas kepemilikan Bitcoin ditambah bisnis software.",
            "fair_value": val_target_mnav,
            "raw_weight": float(raw_weights.get("target_mnav", 22.0)),
        },
        {
            "id": "ev_adjusted_mnav",
            "name": f"EV Debt & Dilution-Adjusted ({target_ev_mnav:.2f}x)",
            "category": "Enterprise Value / Diluted",
            "formula": f"(BTC Value × {target_ev_mnav:.2f}x + Software + Cash - Debt - Pref) / Diluted Shares",
            "description": "Memperhitungkan penuh utang obligasi konversi, saham preferen, kas, dan jumlah saham terdilusi.",
            "fair_value": val_ev_adjusted_mnav,
            "raw_weight": float(raw_weights.get("ev_adjusted_mnav", 20.0)),
        },
        {
            "id": "btc_yield_dcf",
            "name": f"BTC Yield Accretion DCF ({cfg['annual_btc_yield_pct']:.1f}%/thn)",
            "category": "Intrinsic BTC Compounder",
            "formula": f"NAV/Share × ((1 + {cfg['annual_btc_yield_pct']:.1f}%) / (1 + {cfg['discount_rate_pct']:.1f}%))^{horizon_years:.0f}",
            "description": "Memodelkan kemampuan Strategy mengakresi jumlah Bitcoin per saham (BTC Yield) selama horizon investasi.",
            "fair_value": val_btc_yield_dcf,
            "raw_weight": float(raw_weights.get("btc_yield_dcf", 18.0)),
        },
        {
            "id": "ols_regression",
            "name": "Regresi Statistik OLS 90-Hari (BTC-MSTR)",
            "category": "Quantitative / Statistical",
            "formula": f"α ({intercept:.2f}) + β ({slope:.4f}) × BTC_Price (R² = {r_squared:.2f})",
            "description": "Harga wajar statistik MSTR terhadap harga BTC saat ini berdasarkan regresi linier 90 hari bursa terakhir.",
            "fair_value": val_ols_regression,
            "raw_weight": float(raw_weights.get("ols_regression", 15.0)),
        },
        {
            "id": "analyst_consensus",
            "name": f"Konsensus Analis Wall Street ({cfg['analyst_count']} Analis)",
            "category": "Institutional Target",
            "formula": f"Mean Target ${val_analyst_consensus:.2f} (Low ${cfg['analyst_target_low']:.0f} – High ${cfg['analyst_target_high']:.0f})",
            "description": "Target harga konsensus 12 bulan dari analis sekuritas Wall Street untuk saham MSTR.",
            "fair_value": val_analyst_consensus,
            "raw_weight": float(raw_weights.get("analyst_consensus", 10.0)),
        },
        {
            "id": "balance_sheet_mtm",
            "name": "Neraca FASB Mark-to-Market + Optionality",
            "category": "Fundamental Balance Sheet",
            "formula": f"(BTC MTM + {cfg['optionality_premium_pct']:.0f}% Vol Optionality + 3x Rev - Net Debt) / Shares",
            "description": "Nilai buku ekuitas berbasis akuntansi nilai wajar kripto (FASB) ditambah nilai opsi volatilitas obligasi konversi.",
            "fair_value": val_balance_sheet_mtm,
            "raw_weight": float(raw_weights.get("balance_sheet_mtm", 10.0)),
        },
        {
            "id": "basic_nav_floor",
            "name": "1.0x Basic Bitcoin NAV Floor (Nilai Dasar)",
            "category": "Liquidation Floor",
            "formula": "(1.0x BTC Treasury Value + Software EV) / Basic Shares",
            "description": "Batas bawah (floor) nilai intrinsik 1:1 terhadap aset Bitcoin yang dimiliki tanpa premi mNAV.",
            "fair_value": val_basic_nav_floor,
            "raw_weight": float(raw_weights.get("basic_nav_floor", 5.0)),
        },
    ]

    total_weight = sum(m["raw_weight"] for m in model_defs)
    if total_weight <= 0:
        total_weight = 100.0

    composite_fair_value = 0.0
    all_model_prices = []
    for m in model_defs:
        norm_w = (m["raw_weight"] / total_weight) * 100.0
        contribution = m["fair_value"] * (norm_w / 100.0)
        upside = ((m["fair_value"] - mstr_price) / mstr_price * 100.0) if mstr_price > 0 else 0.0
        m["weight_pct"] = round(norm_w, 2)
        m["weighted_contribution"] = round(contribution, 2)
        m["fair_value"] = round(m["fair_value"], 2)
        m["upside_pct"] = round(upside, 2)
        composite_fair_value += contribution
        all_model_prices.append(m["fair_value"])

    composite_fair_value = round(composite_fair_value, 2)
    median_fair_value = round(float(np.median(all_model_prices)), 2)
    trimmed_prices = sorted(all_model_prices)[1:-1]
    trimmed_mean_fair_value = round(float(np.mean(trimmed_prices)), 2)

    upside_pct = ((composite_fair_value - mstr_price) / mstr_price * 100.0) if mstr_price > 0 else 0.0
    price_to_fair_value = mstr_price / composite_fair_value if composite_fair_value > 0 else 1.0

    # Bear / Base / Bull Scenario Range
    bear_case_val = round(min(val_basic_nav_floor, val_ev_adjusted_mnav * 0.85), 2)
    bull_case_val = round(max(val_target_mnav * 1.25, float(cfg["analyst_target_high"]) * 0.75), 2)

    # Determine Verdict Badge
    if upside_pct >= 25.0:
        verdict_code = "STRONG_UNDERVALUED"
        verdict_label = "SANGAT UNDERVALUED (Strong Buy Zone)"
        verdict_color = "emerald"
        verdict_summary = (
            f"Harga MSTR (${mstr_price:.2f}) berada {upside_pct:.1f}% di bawah Nilai Wajar Gabungan "
            f"(${composite_fair_value:.2f}) dengan mNAV {current_basic_mnav:.2f}x."
        )
    elif upside_pct >= 8.0:
        verdict_code = "UNDERVALUED"
        verdict_label = "UNDERVALUED (Akumulasi / Diskon Wajar)"
        verdict_color = "green"
        verdict_summary = (
            f"Harga MSTR (${mstr_price:.2f}) memiliki potensi upside +{upside_pct:.1f}% menuju "
            f"Nilai Wajar Gabungan (${composite_fair_value:.2f})."
        )
    elif upside_pct >= -8.0:
        verdict_code = "FAIRLY_VALUED"
        verdict_label = "FAIRLY VALUED (Harga Wajar / Seimbang)"
        verdict_color = "blue"
        verdict_summary = (
            f"Harga MSTR (${mstr_price:.2f}) diperdagangkan dekat dengan Nilai Wajar Gabungan "
            f"(${composite_fair_value:.2f}, selisih {upside_pct:+.1f}%)."
        )
    elif upside_pct >= -25.0:
        verdict_code = "OVERVALUED"
        verdict_label = "OVERVALUED (Premi Tinggi / Waspada)"
        verdict_color = "amber"
        verdict_summary = (
            f"Harga MSTR (${mstr_price:.2f}) berada {abs(upside_pct):.1f}% di atas Nilai Wajar Gabungan "
            f"(${composite_fair_value:.2f})."
        )
    else:
        verdict_code = "EXTREME_OVERVALUED"
        verdict_label = "EXTREME PREMIUM (Rawan Mean-Reversion)"
        verdict_color = "rose"
        verdict_summary = (
            f"Harga MSTR (${mstr_price:.2f}) diperdagangkan jauh di atas Nilai Wajar Gabungan "
            f"(${composite_fair_value:.2f}, {upside_pct:+.1f}%)."
        )

    # =========================================================================
    # 4. 90-DAY HISTORICAL CHART SERIES (MSTR vs Composite Fair Value vs 1.0x NAV)
    # =========================================================================
    historical_series = []
    # Ratio between composite fair value and pure BTC NAV per share for historical band reconstruction
    composite_to_nav_ratio = (
        composite_fair_value / btc_nav_per_share_pure if btc_nav_per_share_pure > 0 else target_basic_mnav
    )
    for dt_idx, row in joined.iterrows():
        hist_mstr = float(row["mstr"])
        hist_btc = float(row["btc"])
        hist_nav_1x = (btc_holdings * hist_btc + software_ev) / basic_shares
        hist_comp_fv = hist_nav_1x * (composite_to_nav_ratio / ((btc_treasury_value + software_ev) / btc_treasury_value))
        hist_mnav = (hist_mstr * basic_shares) / (btc_holdings * hist_btc) if hist_btc > 0 else 1.0
        historical_series.append(
            {
                "date": str(dt_idx),
                "mstr_price": round(hist_mstr, 2),
                "btc_price": round(hist_btc, 2),
                "nav_1x": round(hist_nav_1x, 2),
                "composite_fv": round(hist_comp_fv, 2),
                "basic_mnav": round(hist_mnav, 3),
            }
        )

    historical_mnav_values = [p["basic_mnav"] for p in historical_series]
    avg_90d_mnav = float(np.mean(historical_mnav_values)) if historical_mnav_values else current_basic_mnav
    min_90d_mnav = float(np.min(historical_mnav_values)) if historical_mnav_values else current_basic_mnav
    max_90d_mnav = float(np.max(historical_mnav_values)) if historical_mnav_values else current_basic_mnav

    # =========================================================================
    # 5. SENSITIVITY MATRIX (BTC Price Scenarios × mNAV Multipliers)
    # =========================================================================
    base_btc_rounded = round(btc_price / 5000.0) * 5000.0
    btc_scenarios = sorted(
        list(
            {
                max(30000.0, base_btc_rounded - 30000.0),
                max(40000.0, base_btc_rounded - 15000.0),
                round(btc_price, 0),
                base_btc_rounded + 15000.0,
                base_btc_rounded + 35000.0,
                base_btc_rounded + 65000.0,
                base_btc_rounded + 100000.0,
            }
        )
    )
    mnav_multipliers = [0.85, 1.00, 1.25, 1.50, round(target_basic_mnav, 2), 2.00, 2.50]
    mnav_multipliers = sorted(list(set(mnav_multipliers)))

    sensitivity_matrix = []
    for b_price in btc_scenarios:
        row_cells = []
        for mult in mnav_multipliers:
            implied_share_price = ((btc_holdings * b_price * mult) + software_ev) / basic_shares
            diff_pct = ((implied_share_price - mstr_price) / mstr_price * 100.0) if mstr_price > 0 else 0.0
            row_cells.append(
                {
                    "mnav": mult,
                    "implied_price": round(implied_share_price, 2),
                    "upside_pct": round(diff_pct, 1),
                }
            )
        sensitivity_matrix.append(
            {
                "btc_price": b_price,
                "is_current_btc": abs(b_price - btc_price) < 10.0,
                "cells": row_cells,
            }
        )

    now_utc = datetime.now(timezone.utc)
    mstr_change_pct = ((mstr_price - mstr_prev_close) / mstr_prev_close * 100.0) if mstr_prev_close > 0 else 0.0
    btc_change_pct = ((btc_price - btc_prev_close) / btc_prev_close * 100.0) if btc_prev_close > 0 else 0.0

    return {
        "timestamp_utc": now_utc.isoformat(),
        "market": {
            "mstr_price": round(mstr_price, 2),
            "mstr_prev_close": round(mstr_prev_close, 2),
            "mstr_change_pct": round(mstr_change_pct, 2),
            "mstr_52w_high": round(raw["mstr_52w_high"], 2),
            "mstr_52w_low": round(raw["mstr_52w_low"], 2),
            "btc_price": round(btc_price, 2),
            "btc_prev_close": round(btc_prev_close, 2),
            "btc_change_pct": round(btc_change_pct, 2),
            "btc_52w_high": round(raw["btc_52w_high"], 2),
            "btc_52w_low": round(raw["btc_52w_low"], 2),
        },
        "composite": {
            "fair_value": composite_fair_value,
            "median_fair_value": median_fair_value,
            "trimmed_mean_fair_value": trimmed_mean_fair_value,
            "bear_case": bear_case_val,
            "bull_case": bull_case_val,
            "upside_pct": round(upside_pct, 2),
            "price_to_fair_value": round(price_to_fair_value, 3),
            "verdict_code": verdict_code,
            "verdict_label": verdict_label,
            "verdict_color": verdict_color,
            "verdict_summary": verdict_summary,
        },
        "treasury": {
            "btc_holdings": btc_holdings,
            "btc_avg_cost": btc_avg_cost,
            "btc_treasury_value": round(btc_treasury_value, 2),
            "btc_cost_basis_total": round(btc_cost_basis_total, 2),
            "btc_unrealized_pnl": round(btc_unrealized_pnl, 2),
            "btc_unrealized_pnl_pct": round(btc_unrealized_pnl_pct, 2),
            "btc_supply_pct": round(btc_holdings / 21_000_000.0 * 100.0, 3),
            "btc_per_1000_shares": round(btc_per_1000_shares, 4),
            "sats_per_share": round(sats_per_share, 0),
            "btc_nav_per_share_pure": round(btc_nav_per_share_pure, 2),
            "implied_btc_price_in_mstr": round(implied_btc_price_in_mstr, 2),
        },
        "mnav_metrics": {
            "current_basic_mnav": round(current_basic_mnav, 3),
            "current_ev_mnav": round(current_ev_mnav, 3),
            "current_diluted_mnav": round(current_diluted_mnav, 3),
            "target_basic_mnav": round(target_basic_mnav, 2),
            "target_ev_mnav": round(target_ev_mnav, 2),
            "avg_90d_mnav": round(avg_90d_mnav, 3),
            "min_90d_mnav": round(min_90d_mnav, 3),
            "max_90d_mnav": round(max_90d_mnav, 3),
        },
        "balance_sheet": {
            "basic_shares": basic_shares,
            "diluted_shares": diluted_shares,
            "market_cap_basic": round(market_cap_basic, 2),
            "market_cap_diluted": round(market_cap_diluted, 2),
            "enterprise_value": round(enterprise_value, 2),
            "total_debt": total_debt,
            "preferred_stock": preferred_stock,
            "cash_and_equivalents": cash,
            "software_ev": software_ev,
            "debt_to_btc_nav_pct": round((total_debt / btc_treasury_value * 100.0) if btc_treasury_value > 0 else 0.0, 2),
        },
        "quant_and_technicals": {
            "ols_slope": round(float(slope), 5),
            "ols_intercept": round(float(intercept), 2),
            "ols_r_squared": round(r_squared, 3),
            "ols_correlation": round(corr_r, 3),
            "ols_std_error": round(std_error, 2),
            "ols_z_score": round(z_score, 2),
            "rsi_14": round(rsi_14, 1),
            "sma_20": round(sma_20, 2),
            "sma_50": round(sma_50, 2),
            "sma_200": round(sma_200, 2),
            "mstr_vol_30d_pct": round(mstr_vol_30d, 1),
            "btc_vol_30d_pct": round(btc_vol_30d, 1),
            "vol_multiplier": round(vol_multiplier, 2),
        },
        "models": model_defs,
        "historical_90d": historical_series,
        "sensitivity": {
            "mnav_headers": mnav_multipliers,
            "rows": sensitivity_matrix,
        },
        "config": cfg,
    }
