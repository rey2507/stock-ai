// Paper-Trader — Stage 3 positions page.

async function refreshPositions() {
  try {
    // Fetch positions and orders in parallel — they are independent.
    const [data, orders] = await Promise.all([API.positions(), API.orders()]);
    setText("pg-unrealized", signedINR(data.total_unrealized_pnl));
    tint("pg-unrealized", data.total_unrealized_pnl);

    const g = data.greeks || {};
    setText("pg-delta", num(g.delta));
    setText("pg-gamma", num(g.gamma, 4));
    setText("pg-vega", num(g.vega, 2));
    setText("pg-theta", num(g.theta, 2));

    const tbody = document.querySelector("#positions-table tbody");
    if (!data.positions.length) {
      tbody.innerHTML = '<tr class="empty"><td colspan="13">No open positions.</td></tr>';
    } else {
      tbody.innerHTML = data.positions.map((p) => {
        const sign = p.side === "LONG" ? 1 : -1;
        return `
        <tr>
          <td><strong>${instrumentLabel(p)}</strong></td>
          <td>${sideBadge(p.side === "LONG" ? "BUY" : "SELL")}</td>
          <td>${p.quantity}</td>
          <td>${fmtINR0(p.avg_price)}</td>
          <td>${fmtINR0(p.current_price)}</td>
          <td class="${p.unrealized_pnl >= 0 ? "side-buy" : "side-sell"}">${signedINR(p.unrealized_pnl)}</td>
          <td class="greeks-cell">${num(p.delta != null ? p.delta * sign * p.quantity : null, 2)}</td>
          <td class="greeks-cell">${num(p.gamma != null ? p.gamma * sign * p.quantity : null, 4)}</td>
          <td class="greeks-cell">${num(p.vega != null ? p.vega * sign * p.quantity : null, 2)}</td>
          <td class="greeks-cell ${p.theta != null && p.theta * sign * p.quantity < 0 ? "theta-neg" : ""}">${num(p.theta != null ? p.theta * sign * p.quantity : null, 2)}</td>
          <td class="greeks-cell">${p.iv != null ? num(p.iv * 100, 1) + "%" : "—"}</td>
          <td>${inlineSourceBadge(p.status)}</td>
          <td>${p.instrument_type === "EQ" ? "—" : `<button class="btn small" data-close="${encodeURIComponent(JSON.stringify({
            symbol: p.symbol, instrument_type: p.instrument_type,
            expiry: p.expiry, strike: p.strike,
            side: p.side === "LONG" ? "SELL" : "BUY",
            quantity: p.quantity,
            reduceOnly: true,
            quote: { ltp: p.current_price, status: p.status },
          }))}">Close</button>`}</td>
        </tr>`;
      }).join("");
    }

    tbody.querySelectorAll("[data-close]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const spec = JSON.parse(decodeURIComponent(btn.dataset.close));
        openTradeModal({
          symbol: spec.symbol,
          instrumentType: spec.instrument_type,   // same key as written above
          expiry: spec.expiry,
          strike: spec.strike,
          presetSide: spec.side,
          closeQty: spec.quantity,                 // pre-fill full position size
        });
      }));

    const obody = document.querySelector("#orders-table tbody");
    obody.innerHTML = orders.orders.length
      ? orders.orders.map((o) => `
        <tr>
          <td>${o.id}</td>
          <td>${instrumentLabel(o)}</td>
          <td>${sideBadge(o.side)}</td>
          <td>${o.quantity}</td>
          <td>${o.order_type}</td>
          <td>${o.trigger_price ? `trig ${num(o.trigger_price, 2)}` : o.price ? num(o.price, 2) : "—"}</td>
          <td>${statusBadge(o.status)}</td>
          <td class="muted">${o.leg_role}</td>
          <td>${o.status === "OPEN" ? `<button class="btn small danger" data-cancel="${o.id}">✕</button>` : ""}</td>
        </tr>`).join("")
      : '<tr class="empty"><td colspan="9">No orders yet.</td></tr>';

    obody.querySelectorAll("[data-cancel]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        try {
          await API.cancelOrder(btn.dataset.cancel);
          refreshPositions();
        } catch (err) {
          alert(err.message);
        }
      }));

    document.getElementById("orders-clear-all").addEventListener("click", async () => {
      if (!confirm("Clear all orders? This cannot be undone.")) return;
      try {
        await fetch("/api/orders", { method: "DELETE" });
        refreshPositions();
      } catch (err) {
        alert(err.message);
      }
    });
  } catch (err) {
    console.error("positions refresh failed", err);
  }
}
