// Paper-Trader — floating quick-trade bar.
// One-tap ATM CE/PE ticket from anywhere in the app. Opens instantly with
// cached ATM/expiry; the strike selector in the ticket allows changing it.

const QuickTrade = {
  symbol: "NIFTY",
  expiry: null,
  atmStrike: null,
  lotSize: null,       // server-authoritative; resolved via lotSizeOf()
  chainMeta: null,     // last known chain context {expiry, strikes[]}
};

// Reuse the option chain's live chain data when available (it refreshes on
// the chain page); otherwise fall back to a cached snapshot per symbol.
function quickTradeApplyChain(data) {
  if (!data || data.symbol !== QuickTrade.symbol) return;
  QuickTrade.expiry = data.expiry;
  QuickTrade.atmStrike = data.atm_strike;
  QuickTrade.chainMeta = {
    expiries: data.expiries || [],
    strikes: data.rows.map((r) => r.strike),
    spot: data.spot,
    rows: data.rows,          // kept so the ticket can show a live LTP
  };
}

async function quickTradeContext() {
  // Fast path: chain page already refreshed our snapshot.
  if (typeof lastChainData !== "undefined" && lastChainData) {
    quickTradeApplyChain(lastChainData);
  }
  if (QuickTrade.atmStrike && QuickTrade.expiry) return;

  // Slow path (first tap): chain + instruments in parallel.
  try {
    const [data, lot] = await Promise.all([
      API.optionChain(QuickTrade.symbol, undefined, 5),
      lotSizeOf(QuickTrade.symbol),
    ]);
    quickTradeApplyChain(data);
    if (lot) QuickTrade.lotSize = lot;
  } catch (err) {
    console.error("quick trade context failed", err);
  }
}

function openQuickTicket(optionType) {
  // Open the ticket IMMEDIATELY with whatever we know — no waiting.
  const strikes = QuickTrade.chainMeta?.strikes || [];
  openTradeModal({
    symbol: QuickTrade.symbol,
    instrumentType: optionType,
    expiry: QuickTrade.expiry || "",
    strike: QuickTrade.atmStrike || null,   // resolved by enrichment shortly
    presetSide: "BUY",
    strikeEditable: true,
    strikeOptions: strikes.length ? strikes : [QuickTrade.atmStrike].filter(Boolean),
  });
  // Enrich in the background (ATM/expiry/lot/LTP) and redraw the open
  // ticket with the resolved contract. The old code wrote to element ids
  // that no longer exist after the ticket redesign — re-rendering is the
  // only reliable way to reflect enrichment in the current UI.
  quickTradeContext().then(() => {
    if (!document.querySelector(".modal")) return;
    const ctx = TradeModal.context;
    if (QuickTrade.expiry) ctx.expiry = QuickTrade.expiry;
    if (QuickTrade.atmStrike) {
      ctx.strike = QuickTrade.atmStrike;
      ctx.strikeOptions = QuickTrade.chainMeta?.strikes || [QuickTrade.atmStrike];
      const row = (QuickTrade.chainMeta?.rows || [])
        .find((r) => Number(r.strike) === Number(QuickTrade.atmStrike));
      const q = row ? (optionType === "CE" ? row.ce : row.pe) : null;
      if (q) ctx.quote = { ltp: q.ltp, iv: q.iv, delta: q.delta, oi: q.oi, status: "live" };
    }
    if (QuickTrade.lotSize) ctx.lotSize = QuickTrade.lotSize;
    // Preserve whatever the user already typed, then redraw.
    const lotsEl = document.getElementById("tt-lots");
    const userLots = lotsEl ? Number(lotsEl.value) : null;
    renderTradeModal();
    const fresh = document.getElementById("tt-lots");
    if (fresh && userLots) {
      fresh.value = String(userLots);
      fresh.dispatchEvent(new Event("input"));
    }
  });
}

function wireQuickTradeBar() {
  document.getElementById("qt-buy-ce")?.addEventListener("click", () => openQuickTicket("CE"));
  document.getElementById("qt-buy-pe")?.addEventListener("click", () => openQuickTicket("PE"));

  // Track the symbol the user is looking at (chain page changes propagate).
  const observer = new MutationObserver(() => {
    if (typeof ChainState !== "undefined" && ChainState.symbol) {
      if (ChainState.symbol !== QuickTrade.symbol) {
        QuickTrade.symbol = ChainState.symbol;
        QuickTrade.atmStrike = null;
        QuickTrade.expiry = null;
        QuickTrade.chainMeta = null;
      }
    }
  });
  const chainSel = document.getElementById("chain-symbol");
  if (chainSel) observer.observe(chainSel, { attributes: true, attributeFilter: ["value"] });
}
