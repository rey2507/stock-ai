// Paper-Trader — trade ticket modal (redesigned).
// Flow: Contract → BUY/SELL → Quantity → Order type → Price fields →
// Money impact → Confirm. Everything else stays secondary.

const TradeModal = {
  context: { instrumentType: "CE", symbol: null, expiry: "", strike: 0, lotSize: null },
};

const TICKER_STATUS = {
  live:        { cls: "live",  icon: "●", label: "Live" },
  derived:     { cls: "live",  icon: "●", label: "Live" },
  simulated:   { cls: "stale", icon: "◷", label: "Simulated" },
  stale:       { cls: "stale", icon: "◷", label: "Delayed" },
  mixed:       { cls: "stale", icon: "◷", label: "Delayed" },
  unavailable: { cls: "down",  icon: "!", label: "Unavailable" },
};

function ticketStatusBadge(status) {
  const s = TICKER_STATUS[status] || TICKER_STATUS.unavailable;
  return `<span class="tt-status tt-${s.cls}">${s.icon} ${s.label}</span>`;
}

async function openTradeModal(cfg) {
  const itype = (cfg.instrumentType || "").toUpperCase();
  if (!itype || itype === "EQ") {
    console.error("openTradeModal: refusing to open ticket — instrument type missing/unsupported", cfg);
    alert("This instrument type is not tradable here (F&O only). If you reached this from a position, its instrument data is malformed.");
    return;
  }

  // Remember the last order type the user picked (feature: persistence).
  const lastType = localStorage.getItem("pt-last-order-type");

  TradeModal.context = {
    instrumentType: itype,
    symbol: cfg.symbol,
    expiry: cfg.expiry || "",
    strike: cfg.strike || 0,
    lotSize: null,
    presetSide: cfg.presetSide || "BUY",
    quote: cfg.quote || null,
    closeQty: cfg.closeQty || null,     // units held (full position)
    reduceOnly: !!cfg.reduceOnly,
    strikeEditable: !!cfg.strikeEditable,
    strikeOptions: cfg.strikeOptions || null,
    orderType: cfg.orderType || lastType || "MARKET",
  };
  renderTradeModal();

  // Resolve lot size in the background; retry a few times (a server blip
  // must not leave the ticket stuck on "Loading lot size…" forever).
  const hintEl = () => document.getElementById("tt-qty-hint");
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const lot = await lotSizeOf(cfg.symbol);
      if (!TradeModal.context || TradeModal.context.symbol !== cfg.symbol) return; // closed/switched
      if (lot) {
        TradeModal.context.lotSize = lot;
        if (TradeModal.context.closeQty) {
          TradeModal.context.closeLots = Math.max(1, Math.round(TradeModal.context.closeQty / lot));
          const input = document.getElementById("tt-lots");
          if (input) input.value = String(TradeModal.context.closeLots);
        }
        const submitBtn = document.getElementById("tt-confirm");
        if (submitBtn) { submitBtn.disabled = false; submitBtn.removeAttribute("title"); }
        updateSummary();
        break;
      }
    } catch (err) {
      console.error("lot size fetch failed", err);
    }
    if (attempt < 2) {
      if (hintEl()) hintEl().textContent = `Loading lot size… (retry ${attempt + 1}/3)`;
      await new Promise((r) => setTimeout(r, 1500));
      if (!document.querySelector(".modal")) return; // closed while waiting
    } else if (hintEl()) {
      hintEl().textContent = "⚠ Could not load lot size — close and reopen the ticket.";
    }
  }
}

function closeModal() {
  document.getElementById("modal-root").innerHTML = "";
}

function contractTitle(ctx) {
  const isFut = ctx.instrumentType === "FUT";
  if (isFut) return `${ctx.symbol} FUT`;
  // While the ATM strike is still resolving (quick trade), show a clean
  // placeholder instead of a confusing "NIFTY 0 PE".
  const strike = Number(ctx.strike) || null;
  return strike ? `${ctx.symbol} ${strike} ${ctx.instrumentType}`
    : `${ctx.symbol} ${ctx.instrumentType} (resolving strike…)`;
}

function closeNotice(ctx) {
  if (!ctx.closeQty) return "";
  const side = (ctx.presetSide || "").toUpperCase();
  const verb = side === "SELL" ? "SELL" : "BUY back";
  const heldSide = side === "SELL" ? "LONG" : "SHORT";
  return `<div class="tt-close-note">
    <strong>Closing ${heldSide} · ${ctx.closeQty} qty</strong>
    <span>This order will ${verb} your existing position.</span>
  </div>`;
}

function renderTradeModal() {
  const ctx = TradeModal.context;
  const isFut = ctx.instrumentType === "FUT";
  const lotKnown = ctx.lotSize != null;
  const status = ctx.quote?.status || "unavailable";
  const ltp = ctx.quote?.ltp;
  const defaultLots = ctx.closeLots || 1;

  const dateLabel = ctx.expiry
    ? new Date(ctx.expiry + "T00:00:00").toLocaleDateString("en-IN",
        { day: "numeric", month: "short", year: "numeric" })
    : "";

  document.getElementById("modal-root").innerHTML = `
    <div class="modal-backdrop" id="modal-backdrop">
      <div class="modal tt-modal">
        <div class="tt-head">
          <div class="tt-title">
            <h2>${contractTitle(ctx)}</h2>
            <div class="tt-sub">${dateLabel}${ltp != null ? ` · LTP ${fmtINR0(ltp)}` : " · LTP —"}</div>
          </div>
          ${ticketStatusBadge(status)}
        </div>

        ${ctx.strikeEditable ? `<label class="field tt-strike-field"><span>Strike</span>
          <select id="tt-strike">${(ctx.strikeOptions || [ctx.strike]).map((s) =>
            `<option value="${s}" ${Number(s) === Number(ctx.strike) ? "selected" : ""}>${s}</option>`).join("")}</select>
        </label>` : ""}

        <div class="tt-seg" id="tt-side-seg" role="group" aria-label="Side">
          <button type="button" class="tt-side tt-buy" data-side="BUY">BUY</button>
          <button type="button" class="tt-side tt-sell" data-side="SELL">SELL</button>
        </div>
        ${closeNotice(ctx)}

        <div class="tt-row">
          <div class="tt-qty-block">
            <span class="tt-label">Quantity</span>
            <div class="tt-stepper">
              <button type="button" id="tt-minus" aria-label="Fewer lots">−</button>
              <input id="tt-lots" type="number" min="1" step="1" value="${defaultLots}"
                     ${lotKnown ? "" : "disabled"} aria-label="Lots" />
              <span class="tt-lots-word">lot${defaultLots === 1 ? "" : "s"}</span>
              <button type="button" id="tt-plus" aria-label="More lots">+</button>
            </div>
            <div class="tt-hint" id="tt-qty-hint">${lotKnown ? `1 lot = ${ctx.lotSize} qty` : "Loading lot size…"}</div>
          </div>
        </div>

        <div class="tt-seg tt-types" id="tt-type-seg" role="group" aria-label="Order type">
          ${[["MARKET", "Market"], ["LIMIT", "Limit"], ["SL", "Stop-Loss"], ["BRACKET", "Bracket"]]
            .map(([v, label]) => `<button type="button" class="tt-type" data-type="${v}">${label}</button>`).join("")}
        </div>

        <div class="tt-price-fields">
          <label class="field" id="tt-price-field" hidden><span>Limit price ₹</span>
            <input id="tt-price" type="number" min="0.05" step="0.05" inputmode="decimal" />
          </label>
          <label class="field" id="tt-trigger-field" hidden><span>Trigger price ₹</span>
            <input id="tt-trigger" type="number" min="0.05" step="0.05" inputmode="decimal" />
          </label>
          <label class="field" id="tt-target-field" hidden><span>Target ₹</span>
            <input id="tt-target" type="number" min="0.05" step="0.05" inputmode="decimal" />
          </label>
          <label class="field" id="tt-stop-field" hidden><span>Stop-loss ₹</span>
            <input id="tt-stop" type="number" min="0.05" step="0.05" inputmode="decimal" />
          </label>
        </div>

        <div class="tt-summary">
          <div class="tt-summary-title">Order summary</div>
          <table class="tt-summary-table">
            <tr><td>Contract</td><td id="tt-s-contract">—</td></tr>
            <tr><td>Side</td><td id="tt-s-side">—</td></tr>
            <tr><td>Quantity</td><td id="tt-s-qty">—</td></tr>
            <tr><td>Price</td><td id="tt-s-price">Market</td></tr>
            <tr><td>Est. value</td><td id="tt-s-value">—</td></tr>
            <tr><td>Required margin</td><td id="tt-s-margin">—</td></tr>
          </table>
          <div class="tt-money">
            <div><span>Available funds</span><strong id="tt-m-avail">—</strong></div>
            <div><span>Margin used</span><strong id="tt-m-used">—</strong></div>
            <div><span>After this order</span><strong id="tt-m-after">—</strong></div>
          </div>
        </div>

        <div class="feedback" id="tt-feedback" role="status" aria-live="polite"></div>

        <div class="tt-actions">
          <button class="btn" id="tt-cancel">Cancel</button>
          <button class="btn primary tt-confirm" id="tt-confirm"
                  ${lotKnown ? "" : "disabled title=\"Loading lot size…\""}>Place Order</button>
        </div>
      </div>
    </div>`;

  const $ = (id) => document.getElementById(id);

  // --- state helpers -------------------------------------------------------
  function setSide(side) {
    ctx.presetSide = side;
    document.querySelectorAll("#tt-side-seg .tt-side").forEach((b) =>
      b.classList.toggle("selected", b.dataset.side === side));
    updateSummary();
    updateConfirmLabel();
  }

  function setType(type) {
    ctx.orderType = type;
    localStorage.setItem("pt-last-order-type", type);
    document.querySelectorAll("#tt-type-seg .tt-type").forEach((b) =>
      b.classList.toggle("selected", b.dataset.type === type));
    $("tt-price-field").hidden = !(type === "LIMIT" || type === "SL");
    $("tt-trigger-field").hidden = type !== "SL";
    $("tt-target-field").hidden = type !== "BRACKET";
    $("tt-stop-field").hidden = type !== "BRACKET";
    updateSummary();
    updateConfirmLabel();
  }

  function lots() { return Math.max(0, Number($("tt-lots").value) || 0); }
  function qty() { return lots() * (ctx.lotSize || 0); }

  function updateConfirmLabel() {
    const btn = $("tt-confirm");
    if (!btn) return;
    const verb = ctx.presetSide || "BUY";
    btn.textContent = ctx.closeQty
      ? "Place Close Order"
      : `${verb} ${contractTitle(ctx)}`;
  }

  function updateQtyHint() {
    if (ctx.lotSize == null) return;
    const n = lots();
    $("tt-qty-hint").textContent =
      `${n} lot${n === 1 ? "" : "s"} = ${qty()} qty` +
      (ltp != null ? ` · est. value ${fmtINR0(qty() * ltp)}` : "");
  }

  function payload() {
    const p = {
      symbol: ctx.symbol,
      side: ctx.presetSide,
      order_type: ctx.orderType,
      instrument_type: ctx.instrumentType,
      quantity: qty(),
    };
    if (ctx.expiry) p.expiry = ctx.expiry;
    if (ctx.strike && ctx.instrumentType !== "FUT") p.strike = Number(ctx.strike);
    if (ctx.reduceOnly) p.reduce_only = true;
    const t = ctx.orderType;
    if (t === "LIMIT" || t === "SL") p.price = Number($("tt-price").value) || undefined;
    if (t === "SL") p.trigger_price = Number($("tt-trigger").value) || undefined;
    if (t === "BRACKET") {
      p.price = Number($("tt-price").value) || undefined;      // entry (limit)
      p.target_price = Number($("tt-target").value) || undefined;
      p.stoploss_price = Number($("tt-stop").value) || undefined;
    }
    return p;
  }

  async function updateSummary() {
    const sContract = $("tt-s-contract"), sSide = $("tt-s-side"),
          sQty = $("tt-s-qty"), sPrice = $("tt-s-price"),
          sValue = $("tt-s-value"), sMargin = $("tt-s-margin");
    if (!sContract) return;   // modal closed mid-update
    updateQtyHint();

    sContract.textContent = contractTitle(ctx) + (ctx.expiry ? ` ${ctx.expiry}` : "");
    sSide.textContent = ctx.presetSide || "—";
    sSide.className = ctx.presetSide === "SELL" ? "tt-sell-text" : "tt-buy-text";
    sQty.textContent = ctx.lotSize ? `${qty()} (${lots()} lot${lots() === 1 ? "" : "s"})` : "…";
    const t = ctx.orderType;
    sPrice.textContent = t === "MARKET" ? "Market"
      : t === "LIMIT" ? ($("tt-price").value ? `₹${$("tt-price").value}` : "—")
      : t === "SL" ? ($("tt-trigger").value ? `SL ₹${$("tt-trigger").value}` : "—")
      : ($("tt-price").value ? `₹${$("tt-price").value}` : "Market");

    const px = t === "LIMIT" && $("tt-price").value ? Number($("tt-price").value)
      : t === "BRACKET" && $("tt-price").value ? Number($("tt-price").value)
      : (ltp != null ? ltp : null);
    sValue.textContent = px != null && qty() > 0 ? fmtINR0(px * qty()) : "—";

    // Margin + money impact via the read-only preview endpoint.
    try {
      const preview = await API.orderPreview(payload());
      sMargin.textContent = fmtINR0(preview.margin_required);
      $("tt-m-avail").textContent = fmtINR0(preview.available_funds);
      $("tt-m-used").textContent = fmtINR0(preview.used_margin);
      $("tt-m-after").textContent = fmtINR0(preview.available_after);
    } catch { /* non-fatal: summary rows stay as-is */ }
  }

  // --- wire-up --------------------------------------------------------------
  document.querySelectorAll("#tt-side-seg .tt-side").forEach((b) =>
    b.addEventListener("click", () => setSide(b.dataset.side)));
  document.querySelectorAll("#tt-type-seg .tt-type").forEach((b) =>
    b.addEventListener("click", () => setType(b.dataset.type)));

  $("tt-minus").addEventListener("click", () => {
    $("tt-lots").value = Math.max(1, lots() - 1);
    updateSummary();
  });
  $("tt-plus").addEventListener("click", () => {
    $("tt-lots").value = lots() + 1;
    updateSummary();
  });
  $("tt-lots").addEventListener("input", updateSummary);

  ["tt-price", "tt-trigger", "tt-target", "tt-stop"].forEach((id) =>
    $(id).addEventListener("input", updateSummary));
  ["tt-price", "tt-trigger", "tt-target", "tt-stop"].forEach((id) =>
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !$("tt-confirm").disabled) submitTrade();
    }));

  $("tt-strike")?.addEventListener("change", (e) => {
    ctx.strike = Number(e.target.value);
    document.querySelector(".tt-title h2").textContent = contractTitle(ctx);
    updateSummary();
    updateConfirmLabel();
  });

  $("tt-cancel").addEventListener("click", closeModal);
  $("modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "modal-backdrop") closeModal();
  });
  document.addEventListener("keydown", function escClose(ev) {
    if (ev.key === "Escape") { closeModal(); document.removeEventListener("keydown", escClose); }
  });

  $("tt-confirm").addEventListener("click", submitTrade);

  // Initial state — never both sides selected.
  setSide(ctx.presetSide || "BUY");
  setType(ctx.orderType || "MARKET");
  updateSummary();
}

// ---------------------------------------------------------------------------
// Confirm step + submission
// ---------------------------------------------------------------------------

let _confirmOpen = false;

function showConfirmCard(payload) {
  const ctx = TradeModal.context;
  const $ = (id) => document.getElementById(id);
  const feedback = $("tt-feedback");
  _confirmOpen = true;

  const lotsN = payload.quantity / (ctx.lotSize || 1);
  const px = payload.order_type === "MARKET" ? "Market"
    : `₹${payload.price ?? $("tt-price").value ?? "—"}`;
  const ltp = ctx.quote?.ltp;
  const est = px === "Market" && ltp != null ? ltp * payload.quantity : null;

  feedback.className = "feedback tt-confirm-card";
  feedback.innerHTML = `
    <strong>Confirm paper order?</strong>
    <span>${payload.side} · ${lotsN} lot${lotsN === 1 ? "" : "s"} (${payload.quantity} qty) · ${payload.order_type}</span>
    <span>${contractTitle(ctx)}${ctx.expiry ? ` ${ctx.expiry}` : ""}</span>
    <span>Est. value: ${est != null ? fmtINR0(est) : px}</span>
    <span class="tt-confirm-actions">
      <button class="btn small" id="tt-confirm-no">Cancel</button>
      <button class="btn small primary" id="tt-confirm-yes">Confirm ${payload.side}</button>
    </span>`;
  $("tt-confirm-no").addEventListener("click", () => {
    _confirmOpen = false;
    feedback.className = "feedback";
    feedback.innerHTML = "";
  });
  $("tt-confirm-yes").addEventListener("click", () => {
    _confirmOpen = false;
    doPlaceOrder(payload);
  });
}

async function submitTrade() {
  if (_confirmOpen) return;   // waiting on the confirm card
  const ctx = TradeModal.context;
  const $ = (id) => document.getElementById(id);
  const feedback = $("tt-feedback");
  const submitBtn = $("tt-confirm");

  const lotSize = ctx.lotSize;
  if (!lotSize) {
    feedback.className = "feedback err";
    feedback.textContent = "⏳ Lot size still loading — try again in a second.";
    return;
  }

  const p = payload();
  if (p.quantity <= 0) {
    feedback.className = "feedback err";
    feedback.textContent = "Quantity must be at least 1 lot.";
    return;
  }
  if ((p.order_type === "LIMIT" || p.order_type === "SL" || p.order_type === "BRACKET")
      && p.order_type !== "BRACKET" && !p.price) {
    feedback.className = "feedback err";
    feedback.textContent = `Enter a price for ${p.order_type} orders.`;
    return;
  }
  if (p.order_type === "SL" && !p.trigger_price) {
    feedback.className = "feedback err";
    feedback.textContent = "Enter a trigger price for stop-loss orders.";
    return;
  }
  if (p.order_type === "BRACKET" && (!p.target_price || !p.stoploss_price)) {
    feedback.className = "feedback err";
    feedback.textContent = "Bracket orders need target and stop-loss prices.";
    return;
  }

  showConfirmCard(p);
}

async function doPlaceOrder(p) {
  const $ = (id) => document.getElementById(id);
  const feedback = $("tt-feedback");
  const submitBtn = $("tt-confirm");

  // Disable while processing — prevents double-click orders.
  submitBtn.disabled = true;
  submitBtn.textContent = "Placing…";
  feedback.className = "feedback";
  feedback.textContent = "";

  const placedAt = new Date().toLocaleTimeString("en-IN");
  try {
    const result = await API.placeOrder(p);
    feedback.className = "feedback ok";
    const when = `<span class="tt-ts">${placedAt}</span>`;
    if (result.status === "FILLED" && result.fill_price != null) {
      feedback.innerHTML =
        `✅ Filled @ ${fmtINR0(result.fill_price)} · order #${result.order_id} ${when}`;
    } else if (result.status === "OPEN") {
      feedback.innerHTML =
        `⏱ Working · order #${result.order_id}${result.trigger_price ? ` · trigger ${fmtINR0(result.trigger_price)}` : ""} ${when}`;
    } else {
      feedback.innerHTML = `✅ Bracket placed · parent #${result.parent_id} ${when}`;
    }
    refreshAccount && refreshAccount();
    ROUTES[App.route].load();
    setTimeout(closeModal, 2200);
  } catch (err) {
    feedback.className = "feedback err";
    feedback.textContent = `❌ ${err.message}`;
    submitBtn.disabled = false;
    submitBtn.textContent = ctx.closeQty ? "Place Close Order" : `${ctx.presetSide} ${contractTitle(ctx)}`;
  }
}

function wireTradeModal() { /* keyboard hooks handled per-modal */ }
