// Paper-Trader — option chain page.
// Columns: [greeks (cycleable)] [LTP] [OI (ΔOI)] | Strike | [OI (ΔOI)] [LTP] [greeks (cycleable)].
// Hovering an LTP cell reveals Buy/Sell buttons that open the trade ticket.

const ChainState = {
  symbol: "NIFTY",
  expiry: null,
  strikesPerSide: 15,
  greekIdx: 0,        // index into GREEK_COLS per side
  greekIdxPe: 0,      // independent cycle for the put side
};

const GREEK_COLS = [
  { key: "delta", label: "Δ", fmt: (v) => num(v, 2) },
  { key: "gamma", label: "Γ", fmt: (v) => num(v, 4) },
  { key: "vega",  label: "V", fmt: (v) => num(v, 2) },
  { key: "theta", label: "Θ", fmt: (v) => num(v, 2) },
  { key: "iv",    label: "IV", fmt: (v) => (v != null ? num(v * 100, 1) + "%" : "—") },
];

function currentGreek(side) {
  const idx = side === "ce" ? ChainState.greekIdx : ChainState.greekIdxPe;
  return GREEK_COLS[idx % GREEK_COLS.length];
}

function setGreekHeader() {
  for (const side of ["ce", "pe"]) {
    const th = document.querySelector(`.greeks-th[data-side="${side}"]`);
    if (!th) continue;
    const col = currentGreek(side);
    th.textContent = col.label;
    th.title = `Click to cycle Greeks: Δ → Γ → V → Θ → IV`;
  }
}

async function initChainSelectors() {
  const instruments = await API.instruments();
  const symbolSel = document.getElementById("chain-symbol");
  symbolSel.innerHTML = instruments.instruments
    .map((i) => `<option value="${i.symbol}">${i.symbol}</option>`)
    .join("");
  symbolSel.value = ChainState.symbol;

  symbolSel.addEventListener("change", async () => {
    ChainState.symbol = symbolSel.value;
    ChainState.expiry = null;
    await loadExpiries();
    refreshChain();
  });

  document.getElementById("chain-expiry").addEventListener("change", (e) => {
    ChainState.expiry = e.target.value;
    refreshChain();
  });

  document.getElementById("chain-strikes").addEventListener("change", (e) => {
    ChainState.strikesPerSide = e.target.value === "all" ? 60 : parseInt(e.target.value, 10);
    refreshChain();
  });

  document.querySelectorAll(".greeks-th").forEach((th) =>
    th.addEventListener("click", () => {
      if (th.dataset.side === "ce") ChainState.greekIdx = (ChainState.greekIdx + 1) % GREEK_COLS.length;
      else ChainState.greekIdxPe = (ChainState.greekIdxPe + 1) % GREEK_COLS.length;
      setGreekHeader();
      renderChainTable(lastChainData);
    }));

  await loadExpiries();
  setGreekHeader();
}

let lastChainData = null;

async function loadExpiries() {
  const data = await API.expiries(ChainState.symbol);
  const sel = document.getElementById("chain-expiry");
  sel.innerHTML = data.expiries
    .map((e) => `<option value="${e}">${e}</option>`)
    .join("");
  ChainState.expiry = data.expiries[0];
}

async function refreshChain() {
  try {
    const data = await API.optionChain(
      ChainState.symbol, ChainState.expiry || undefined, ChainState.strikesPerSide);
    lastChainData = data;
    ChainState.expiry = data.expiry;
    document.getElementById("chain-expiry").value = data.expiry;
    document.getElementById("chain-spot").innerHTML =
      `<strong>${data.symbol}</strong> spot: <strong>${fmtINR0(data.spot)}</strong> · ATM: ${data.atm_strike} · expiry ${data.expiry}`;
    const stamp = data.timestamp ? ` · updated ${data.timestamp.slice(11, 19)} UTC` : "";
    document.getElementById("chain-source").innerHTML =
      `Chain data: ${sourceBadge(data.status)}`
      + (data.source ? ` <span class="muted">via ${data.source}</span>` : "")
      + stamp;
    renderChainSummary(data);
    renderChainTable(data);
    // Feed the floating quick-trade bar its ATM/expiry/strike context.
    if (typeof quickTradeApplyChain === "function") quickTradeApplyChain(data);
  } catch (err) {
    console.error("chain refresh failed", err);
  }
}

function renderChainTable(data) {
  if (!data) return;

  const tbody = document.querySelector("#chain-table tbody");

  // Skip re-render while the user is hovering the chain (about to click B/S)
  // or the trade ticket is open — prevents the poller from yanking the UI.
  if (document.querySelector(".modal-backdrop")) return;
  if (tbody.matches(":hover")) {
    pendingChainData = data;
    return;
  }

  // Max OI per side for bar scaling + MAX chip.
  let maxCeOi = 0;
  let maxPeOi = 0;
  data.rows.forEach((row) => {
    if (row.ce.oi != null && row.ce.oi > maxCeOi) maxCeOi = row.ce.oi;
    if (row.pe.oi != null && row.pe.oi > maxPeOi) maxPeOi = row.pe.oi;
  });

  // OI column with proportional bar (real OI values only).
  const oiCol = (q, maxOi, isMax, align) => {
    if (q.oi == null) return '<span class="muted">—</span>';
    const barPct = maxOi > 0 ? Math.round((q.oi / maxOi) * 100) : 0;
    const bar = `<span class="oi-bar ${align}"><span style="width:${barPct}%"></span></span>`;
    return `<span class="oi-wrap">${bar}<span class="oi-num${isMax ? " oi-max" : ""}">${num(q.oi, 0)}</span></span>`
      + (isMax ? ' <span class="max-oi" title="Max OI">MAX</span>' : "");
  };

  // ΔOI column: provider value verbatim, colored + / − / 0.
  const oiChgCol = (q) => {
    if (q.oi_change == null) return '<span class="muted">—</span>';
    const v = q.oi_change;
    const cls = v > 0 ? "oi-up" : v < 0 ? "oi-down" : "muted";
    const sign = v > 0 ? "+" : v < 0 ? "−" : "";
    return `<span class="oi-chg ${cls}">${sign}${num(Math.abs(v), 0)}</span>`;
  };

  tbody.innerHTML = data.rows.map((row) => {
    const isAtm = row.strike === data.atm_strike;
    let rowClass = "";
    if (isAtm) rowClass += " atm";
    if (row.strike < data.spot) rowClass += " itm-call";
    if (row.strike > data.spot) rowClass += " itm-put";

    return `
      <tr class="${rowClass}">
        <td class="oi-cell">${oiCol(row.ce, maxCeOi, row.ce.oi === maxCeOi && maxCeOi > 0, "right")}</td>
        <td class="oi-cell">${oiChgCol(row.ce)}</td>
        <td class="ltp-cell">
          <strong>${num(row.ce.ltp, 2)}</strong>
          <span class="trade-actions">
            <button class="trade-btn buy" data-ce-buy="${row.strike}" title="Buy ${row.strike} CE">B</button>
            <button class="trade-btn sell" data-ce-sell="${row.strike}" title="Sell ${row.strike} CE">S</button>
          </span>
        </td>
        <td class="chain-strike">${row.strike}</td>
        <td class="ltp-cell">
          <strong>${num(row.pe.ltp, 2)}</strong>
          <span class="trade-actions">
            <button class="trade-btn buy" data-pe-buy="${row.strike}" title="Buy ${row.strike} PE">B</button>
            <button class="trade-btn sell" data-pe-sell="${row.strike}" title="Sell ${row.strike} PE">S</button>
          </span>
        </td>
        <td class="oi-cell">${oiChgCol(row.pe)}</td>
        <td class="oi-cell">${oiCol(row.pe, maxPeOi, row.pe.oi === maxPeOi && maxPeOi > 0, "left")}</td>
      </tr>`;
  }).join("");

  wireChainTradeButtons(tbody, data);
}

// OI summary strip: max OI / max ΔOI per side (computed from real chain data).
function renderChainSummary(data) {
  const el = document.getElementById("chain-summary");
  if (!el) return;
  let maxCe = null, maxPe = null, maxCeChg = null, maxPeChg = null;
  for (const r of data.rows) {
    if (r.ce.oi != null && (!maxCe || r.ce.oi > maxCe.oi)) maxCe = { strike: r.strike, oi: r.ce.oi };
    if (r.pe.oi != null && (!maxPe || r.pe.oi > maxPe.oi)) maxPe = { strike: r.strike, oi: r.pe.oi };
    if (r.ce.oi_change != null && (!maxCeChg || r.ce.oi_change > maxCeChg.chg)) maxCeChg = { strike: r.strike, chg: r.ce.oi_change };
    if (r.pe.oi_change != null && (!maxPeChg || r.pe.oi_change > maxPeChg.chg)) maxPeChg = { strike: r.strike, chg: r.pe.oi_change };
  }
  if (!maxCe && !maxPe) { el.hidden = true; return; }
  const item = (label, val) => val
    ? `<div class="summary-item"><span class="metric-label">${label}</span><span class="summary-val">${val.strike} <span class="muted">(${num(val.oi ?? val.chg, 0)})</span></span></div>`
    : "";
  el.innerHTML =
    item("CALL MAX OI", maxCe) + item("PUT MAX OI", maxPe)
    + item("CALL MAX ΔOI", maxCeChg) + item("PUT MAX ΔOI", maxPeChg);
  el.hidden = false;
}

// Data held back while the user hovers the chain; flushed on mouseleave.
let pendingChainData = null;
document.addEventListener("mouseout", (e) => {
  if (!pendingChainData) return;
  const table = document.getElementById("chain-table");
  if (table && !table.contains(e.relatedTarget)) {
    const d = pendingChainData;
    pendingChainData = null;
    renderChainTable(d);
  }
});

function wireChainTradeButtons(tbody, data) {
  const open = (btn, itype, side) => {
    const strike = Number(btn.dataset[side]);
    const row = data.rows.find((r) => r.strike === strike);
    const q = row ? (itype === "CE" ? row.ce : row.pe) : null;
    openTradeModal({
      symbol: data.symbol, instrumentType: itype,
      expiry: data.expiry, strike,
      presetSide: side === "ceBuy" || side === "peBuy" ? "BUY" : "SELL",
      quote: q ? { ltp: q.ltp, oi: q.oi, oi_change: q.oi_change, iv: q.iv, delta: q.delta } : null,
    });
  };
  tbody.querySelectorAll("[data-ce-buy]").forEach((b) =>
    b.addEventListener("click", () => open(b, "CE", "ceBuy")));
  tbody.querySelectorAll("[data-ce-sell]").forEach((b) =>
    b.addEventListener("click", () => open(b, "CE", "ceSell")));
  tbody.querySelectorAll("[data-pe-buy]").forEach((b) =>
    b.addEventListener("click", () => open(b, "PE", "peBuy")));
  tbody.querySelectorAll("[data-pe-sell]").forEach((b) =>
    b.addEventListener("click", () => open(b, "PE", "peSell")));
}
