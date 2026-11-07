"""Asset backing and explicit 12-month financing scenarios; no blended buy signal."""

import math
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import httpx
from app.config import FACTS, Settings
from app.database import get_config

MODEL_VERSION = "2.0"


class MarketDataError(RuntimeError):
    pass


def fetch_quote(symbol):
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            with httpx.Client(timeout=12, follow_redirects=True) as client:
                response = client.get(
                    f"https://{host}/v8/finance/chart/{symbol}",
                    params={"range": "5d", "interval": "1d"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
                meta = response.json()["chart"]["result"][0]["meta"]
                price, stamp = (
                    float(meta["regularMarketPrice"]),
                    int(meta["regularMarketTime"]),
                )
                if not math.isfinite(price) or price <= 0:
                    raise ValueError("Invalid quote")
                if stamp > datetime.now(timezone.utc).timestamp() + 300:
                    raise ValueError("Future quote timestamp")
                return {
                    "price": price,
                    "observed_at": datetime.fromtimestamp(
                        stamp, timezone.utc
                    ).isoformat(),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "source": "Yahoo Finance",
                    "source_url": f"https://finance.yahoo.com/quote/{symbol}/",
                    "kind": "Kuotasi pasar terakhir dari penyedia; dapat tertunda",
                }
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    raise MarketDataError(
        f"{symbol}: quote unavailable. No substitute price was used. "
        + "; ".join(errors)
    )


def fetch_market():
    with ThreadPoolExecutor(max_workers=2) as pool:
        mstr = pool.submit(fetch_quote, "MSTR")
        btc = pool.submit(fetch_quote, "BTC-USD")
        return {"mstr": mstr.result(), "btc": btc.result()}


def changes(value, price):
    return {
        "upside_pct": (value / price - 1) * 100,
        "discount_pct": (1 - price / value) * 100 if value > 0 else None,
    }


def asset_backing(cfg, btc_price):
    treasury = cfg["btc_holdings"] * btc_price
    net = (
        treasury
        + cfg["cash_and_equivalents"]
        + cfg["software_ev"]
        - cfg["total_debt"]
        - cfg["preferred_stock"]
        - cfg["other_net_liabilities"]
    )
    shares = cfg["basic_shares"]
    converted = net + cfg["converted_debt"] + cfg["converted_preferred"]
    converted_shares = shares + cfg["conversion_new_shares"]
    return {
        "treasury_usd": treasury,
        "net_assets_usd": net,
        "net_asset_per_share": net / shares,
        "common_equity_reference": max(0, net / shares),
        "gross_btc_per_share_usd": treasury / shares,
        "converted_net_asset_per_share": converted / converted_shares,
        "converted_shares": converted_shares,
        "remaining_debt": cfg["total_debt"] - cfg["converted_debt"],
        "remaining_preferred": cfg["preferred_stock"] - cfg["converted_preferred"],
    }


def project(cfg, btc_price, price, scenario, funding=True):
    """All funding buys BTC now; one year of cash costs; terminal price discounted once."""
    equity = cfg["new_equity_usd"] if funding else 0
    debt = cfg["new_debt_usd"] if funding else 0
    pref = cfg["new_preferred_usd"] if funding else 0
    proceeds = equity + debt + pref
    fees = proceeds * cfg["funding_fee_pct"] / 100
    holdings = cfg["btc_holdings"] + (proceeds - fees) / btc_price
    shares = (
        cfg["basic_shares"]
        + cfg["conversion_new_shares"]
        + equity / cfg["equity_issue_price"]
    )
    debt_claim = cfg["total_debt"] - cfg["converted_debt"] + debt
    pref_claim = cfg["preferred_stock"] - cfg["converted_preferred"] + pref
    carry = (
        cfg["annual_carry_cost"]
        - cfg["conversion_carry_relief"]
        + debt * cfg["debt_rate_pct"] / 100
        + pref * cfg["preferred_rate_pct"] / 100
    )
    terminal_btc = btc_price * (1 + scenario["btc_change_pct"] / 100)
    treasury = holdings * terminal_btc
    # Carry is paid from USD assets; a shortfall is exposed rather than hidden.
    cash_after = cfg["cash_and_equivalents"] - carry
    net = (
        treasury
        + cfg["software_ev"]
        + cash_after
        - debt_claim
        - pref_claim
        - cfg["other_net_liabilities"]
    )
    terminal_equity = (
        treasury * scenario["asset_multiple"]
        + cfg["software_ev"]
        + cash_after
        - debt_claim
        - pref_claim
        - cfg["other_net_liabilities"]
    )
    terminal_price = max(0, terminal_equity / shares)
    present_price = terminal_price / (1 + cfg["discount_rate_pct"] / 100)
    return {
        "btc_price": terminal_btc,
        "asset_multiple": scenario["asset_multiple"],
        "holdings": holdings,
        "shares": shares,
        "fees_usd": fees,
        "annual_carry_usd": carry,
        "funding_gap_usd": max(0, -cash_after),
        "net_assets_per_share": net / shares,
        "terminal_price": terminal_price,
        "present_value": present_price,
        "asset_backing_pv": max(0, net / shares) / (1 + cfg["discount_rate_pct"] / 100),
        "premium_component_pv": present_price
        - max(0, net / shares) / (1 + cfg["discount_rate_pct"] / 100),
        **changes(present_price, price),
    }


def build_valuation(cfg, market, now=None):
    cfg = Settings.model_validate(cfg).model_dump(mode="json")
    now = now or datetime.now(timezone.utc)
    warnings = []
    for key in ("mstr", "btc"):
        quote = market[key]
        if not math.isfinite(quote["price"]) or quote["price"] <= 0:
            raise MarketDataError("Missing or invalid market price")
        age = (
            now - datetime.fromisoformat(quote["observed_at"])
        ).total_seconds() / 3600
        if age < -5 / 60:
            raise MarketDataError("Future market observation")
        quote["age_hours"] = max(0, age)
        # Weekend closure is legitimate; observation time is always shown.
        quote["stale"] = age > (96 if key == "mstr" else 2)
        if quote["stale"]:
            warnings.append(f"{key.upper()}: observasi harga sudah kedaluwarsa")
    evidence = []
    for key in FACTS:
        item = cfg["evidence"].get(
            key, {"status": "unverified", "source_url": "", "as_of": None, "note": ""}
        )
        age_days = (
            (now.date() - datetime.fromisoformat(item["as_of"]).date()).days
            if item.get("as_of")
            else None
        )
        stale = age_days is None or age_days > 14
        evidence.append(
            {
                "field": key,
                "value": cfg[key],
                **item,
                "age_days": age_days,
                "stale": stale,
            }
        )
        if item["status"] != "reported" or stale:
            warnings.append(
                f"{key}: "
                + {
                    "unverified": "belum diverifikasi",
                    "estimate": "estimasi",
                    "reported": "dilaporkan",
                }[item["status"]]
                + (
                    " / tanggal sumber belum ada atau lebih dari 14 hari"
                    if stale
                    else ""
                )
            )
    warnings.append(
        "Nilai software dan kewajiban bersih lain adalah asumsi. Pajak, modal kerja dan dampak eksekusi belum dimodelkan sepenuhnya."
    )
    if cfg["annual_carry_cost"] == 0:
        warnings.append(
            "Bunga dan dividen preferred tahunan belum diisi (nol); proyeksi belum mencakup biaya yang ada."
        )
    btc, price = market["btc"]["price"], market["mstr"]["price"]
    backing = asset_backing(cfg, btc)
    scenarios = []
    for name in ("bear", "base", "bull"):
        result = project(cfg, btc, price, cfg["scenarios"][name])
        baseline = project(cfg, btc, price, cfg["scenarios"][name], funding=False)
        result.update(
            name=name,
            funding_delta_nav_per_share=result["net_assets_per_share"]
            - baseline["net_assets_per_share"],
        )
        if result["funding_gap_usd"]:
            warnings.append(
                f"{name}: aset USD tidak cukup membayar biaya model; perlu pendanaan ulang atau penjualan BTC"
            )
        scenarios.append(result)
    market_cap = price * cfg["basic_shares"]
    enterprise = (
        market_cap
        + cfg["total_debt"]
        + cfg["preferred_stock"]
        - cfg["cash_and_equivalents"]
    )
    # This named custom metric is not represented as Strategy's official mNAV.
    asset_multiple = (
        enterprise - cfg["software_ev"] + cfg["other_net_liabilities"]
    ) / backing["treasury_usd"]
    matrix = []
    for change in (-50, -30, 0, 30, 60):
        matrix.append(
            {
                "btc_price": btc * (1 + change / 100),
                "change_pct": change,
                "cells": [
                    project(
                        cfg, btc, price, {"btc_change_pct": change, "asset_multiple": m}
                    )["present_value"]
                    for m in (0.75, 1, 1.25, 1.5, 2)
                ],
            }
        )
    return {
        "model_version": MODEL_VERSION,
        "timestamp_utc": now.isoformat(),
        "config": cfg,
        "market": market,
        "backing": {**backing, **changes(backing["common_equity_reference"], price)},
        "scenarios": scenarios,
        "sensitivity": {"multiples": [0.75, 1, 1.25, 1.5, 2], "rows": matrix},
        "relative": {
            "custom_asset_multiple": asset_multiple,
            "market_cap": market_cap,
            "enterprise_value": enterprise,
        },
        "evidence": evidence,
        "warnings": warnings,
        "quality": "stale"
        if any(q["stale"] for q in market.values())
        else "indicative",
        "horizon_months": 12,
    }


def compute_full_valuation():
    return build_valuation(get_config(), fetch_market())
