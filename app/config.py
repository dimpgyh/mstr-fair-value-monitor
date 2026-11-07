"""Validated inputs. Market observations, reported facts and assumptions stay separate."""

from datetime import date, datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

FACTS = (
    "btc_holdings",
    "basic_shares",
    "total_debt",
    "preferred_stock",
    "cash_and_equivalents",
    "annual_carry_cost",
)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_url: str = Field(default="", max_length=1000)
    as_of: Optional[date] = None
    status: Literal["unverified", "reported", "estimate"] = "unverified"
    note: str = Field(default="", max_length=1500)

    @model_validator(mode="after")
    def require_source(self):
        if self.status != "unverified":
            if (
                not self.source_url.startswith(("https://", "http://"))
                or not self.as_of
            ):
                raise ValueError(
                    "Reported/estimated inputs require a source URL and date"
                )
        if self.as_of and self.as_of > datetime.now(timezone.utc).date():
            raise ValueError("Evidence dates cannot be in the future")
        return self


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    btc_change_pct: float = Field(ge=-95, le=1000)
    asset_multiple: float = Field(ge=0, le=5)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    btc_holdings: float = Field(default=849327, ge=0.00000001, le=21000000)
    basic_shares: float = Field(default=423530000, ge=1, le=1e12)
    total_debt: float = Field(default=6714000000, ge=0, le=1e15)
    preferred_stock: float = Field(default=13136000000, ge=0, le=1e15)
    cash_and_equivalents: float = Field(default=4922000000, ge=0, le=1e15)
    annual_carry_cost: float = Field(default=1484000000, ge=0, le=1e15)
    software_ev: float = Field(default=1200000000, ge=0, le=1e15)
    other_net_liabilities: float = Field(default=0, ge=-1e15, le=1e15)
    discount_rate_pct: float = Field(default=12, ge=0, le=100)
    # Fixed twelve-month horizon; all funding is assumed to occur at inception.
    new_equity_usd: float = Field(default=0, ge=0, le=1e15)
    equity_issue_price: float = Field(default=130, ge=0.01, le=1e7)
    new_debt_usd: float = Field(default=0, ge=0, le=1e15)
    debt_rate_pct: float = Field(default=8, ge=0, le=100)
    new_preferred_usd: float = Field(default=0, ge=0, le=1e15)
    preferred_rate_pct: float = Field(default=10, ge=0, le=100)
    funding_fee_pct: float = Field(default=1, ge=0, le=25)
    converted_debt: float = Field(default=0, ge=0, le=1e15)
    converted_preferred: float = Field(default=0, ge=0, le=1e15)
    conversion_carry_relief: float = Field(default=0, ge=0, le=1e15)
    conversion_new_shares: float = Field(default=0, ge=0, le=1e12)
    refresh_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    scenarios: dict[str, Scenario] = Field(
        default_factory=lambda: {
            "bear": Scenario(btc_change_pct=-30, asset_multiple=0.85),
            "base": Scenario(btc_change_pct=0, asset_multiple=1),
            "bull": Scenario(btc_change_pct=30, asset_multiple=1.15),
        }
    )

    @model_validator(mode="after")
    def consistent_capital(self):
        if (
            self.converted_debt > self.total_debt
            or self.converted_preferred > self.preferred_stock
        ):
            raise ValueError("Converted claims cannot exceed outstanding claims")
        if self.conversion_carry_relief > self.annual_carry_cost:
            raise ValueError("Carry relief cannot exceed annual carry cost")
        if self.conversion_carry_relief and not (
            self.converted_debt + self.converted_preferred
        ):
            raise ValueError("Carry relief requires converted claims")
        claims = self.converted_debt + self.converted_preferred
        if bool(claims) != bool(self.conversion_new_shares):
            raise ValueError(
                "Conversion requires both released claims and additional shares"
            )
        if set(self.scenarios) != {"bear", "base", "bull"}:
            raise ValueError("Exactly bear, base and bull scenarios are required")
        rows = [self.scenarios[k] for k in ("bear", "base", "bull")]
        if any(
            a.btc_change_pct > b.btc_change_pct or a.asset_multiple > b.asset_multiple
            for a, b in zip(rows, rows[1:])
        ):
            raise ValueError(
                "Scenario BTC changes and multiples must be ordered bear ≤ base ≤ bull"
            )
        if set(self.evidence) - set(FACTS):
            raise ValueError("Unknown evidence field")
        return self


def default_settings():
    cfg = Settings().model_dump(mode="json")
    source = "https://www.strategy.com/"
    for key in FACTS:
        cfg["evidence"][key] = {
            "source_url": source,
            "as_of": "2026-11-07",
            "status": "estimate" if key == "basic_shares" else "reported",
            "note": (
                "Estimasi: market cap US$54.606 juta / harga US$128,93; angka dashboard dibulatkan."
                if key == "basic_shares"
                else "Disalin manual dari snapshot dashboard 7 Nov 2026; bukan feed laporan otomatis. "
                "Angka USD dibulatkan ke jutaan. Tanggal observasi, bukan tanggal laporan bersama."
            ),
        }
    return cfg
