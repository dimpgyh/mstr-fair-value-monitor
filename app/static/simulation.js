/* Pure local what-if calculations. No requests, persistence, or market-data changes. */
(function (root) {
  "use strict";
  function changes(value, price) {
    return {
      upside_pct: (value / price - 1) * 100,
      discount_pct: value > 0 ? (1 - price / value) * 100 : null,
    };
  }
  function project(c, btc, price, scenario, funding = true) {
    const equity = funding ? c.new_equity_usd : 0,
      debt = funding ? c.new_debt_usd : 0,
      pref = funding ? c.new_preferred_usd : 0;
    const fees = ((equity + debt + pref) * c.funding_fee_pct) / 100;
    const holdings = c.btc_holdings + (equity + debt + pref - fees) / btc;
    const shares =
      c.basic_shares + c.conversion_new_shares + equity / c.equity_issue_price;
    const debtClaim = c.total_debt - c.converted_debt + debt;
    const prefClaim = c.preferred_stock - c.converted_preferred + pref;
    const carry =
      c.annual_carry_cost -
      c.conversion_carry_relief +
      (debt * c.debt_rate_pct) / 100 +
      (pref * c.preferred_rate_pct) / 100;
    const terminalBTC = btc * (1 + scenario.btc_change_pct / 100),
      treasury = holdings * terminalBTC;
    const cash = c.cash_and_equivalents - carry;
    const residual =
      c.software_ev + cash - debtClaim - prefClaim - c.other_net_liabilities;
    const net = treasury + residual;
    const terminal = Math.max(
      0,
      (treasury * scenario.asset_multiple + residual) / shares,
    );
    const discount = 1 + c.discount_rate_pct / 100,
      pv = terminal / discount;
    const assetPV = Math.max(0, net / shares) / discount;
    return {
      btc_price: terminalBTC,
      asset_multiple: scenario.asset_multiple,
      holdings,
      shares,
      fees_usd: fees,
      annual_carry_usd: carry,
      funding_gap_usd: Math.max(0, -cash),
      net_assets_per_share: net / shares,
      terminal_price: terminal,
      present_value: pv,
      asset_backing_pv: assetPV,
      premium_component_pv: pv - assetPV,
      ...changes(pv, price),
    };
  }
  function simulateValuation(observation, btc) {
    if (!Number.isFinite(btc) || btc <= 0)
      throw new RangeError("BTC price must be positive and finite");
    const v = structuredClone(observation),
      c = v.config,
      price = v.market.mstr.price;
    const treasury = c.btc_holdings * btc;
    const net =
      treasury +
      c.cash_and_equivalents +
      c.software_ev -
      c.total_debt -
      c.preferred_stock -
      c.other_net_liabilities;
    const convertedShares = c.basic_shares + c.conversion_new_shares;
    v.backing = {
      treasury_usd: treasury,
      net_assets_usd: net,
      net_asset_per_share: net / c.basic_shares,
      common_equity_reference: Math.max(0, net / c.basic_shares),
      gross_btc_per_share_usd: treasury / c.basic_shares,
      converted_net_asset_per_share:
        (net + c.converted_debt + c.converted_preferred) / convertedShares,
      converted_shares: convertedShares,
      remaining_debt: c.total_debt - c.converted_debt,
      remaining_preferred: c.preferred_stock - c.converted_preferred,
      ...changes(Math.max(0, net / c.basic_shares), price),
    };
    v.scenarios = ["bear", "base", "bull"].map((name) => {
      const result = project(c, btc, price, c.scenarios[name]);
      return {
        ...result,
        name,
        funding_delta_nav_per_share:
          result.net_assets_per_share -
          project(c, btc, price, c.scenarios[name], false).net_assets_per_share,
      };
    });
    v.relative.custom_asset_multiple =
      (v.relative.enterprise_value - c.software_ev + c.other_net_liabilities) /
      treasury;
    v.sensitivity.rows = [-50, -30, 0, 30, 60].map((change) => ({
      btc_price: btc * (1 + change / 100),
      change_pct: change,
      cells: v.sensitivity.multiples.map(
        (m) =>
          project(c, btc, price, { btc_change_pct: change, asset_multiple: m })
            .present_value,
      ),
    }));
    // Original quote, provenance, and observation history remain authoritative.
    return v;
  }
  root.simulateValuation = simulateValuation;
})(globalThis);
