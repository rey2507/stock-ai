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
    strike: QuickTrade.atmStrike || 0,
    presetSide: "BUY",
    strikeEditable: true,
    strikeOptions: strikes.length ? strikes : [QuickTrade.atmStrike].filter(Boolean),
  });
  // Enrich in the background (ATM/expiry/lot) and live-update the open ticket.
  quickTradeContext().then(() => {
    if (!document.querySelector(".modal")) return;
    if (QuickTrade.expiry) TradeModal.context.expiry = QuickTrade.expiry;
    if (QuickTrade.atmStrike) {
      TradeModal.context.strike = QuickTrade.atmStrike;
      TradeModal.context.strikeOptions = QuickTrade.chainMeta?.strikes || [QuickTrade.atmStrike];
      const titleEl = document.querySelector(".modal h2");
      if (titleEl) {
        titleEl.innerHTML =
          `${QuickTrade.symbol} ${QuickTrade.atmStrike} ${optionType} ${QuickTrade.expiry || ""} <span class="badge muted">${optionType}</span>`;
      }
      // Refresh the strike selector options now that we have the ladder.
      const sel = document.getElementById("t-strike");
      if (sel) {
        sel.innerHTML = TradeModal.context.strikeOptions.map((s) =>
          `<option value="${s}" ${s === QuickTrade.atmStrike ? "selected" : ""}>${s}</option>`).join("");
      }
    }
    if (QuickTrade.lotSize) TradeModal.context.lotSize = QuickTrade.lotSize;
    if (QuickTrade.lotSize) {
      const qtyLabel = document.querySelector("#t-qty")?.closest(".field")?.querySelector("span");
      if (qtyLabel) qtyLabel.textContent = `Quantity (lots of ${QuickTrade.lotSize})`;
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
