// Paper-Trader — Stage 3 formatting helpers.

const fmtINR = new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", maximumFractionDigits: 2,
});

function fmtINR0(value) {
  return value == null ? "—" : fmtINR.format(value);
}

function signedINR(value) {
  if (value == null) return "—";
  return (value >= 0 ? "+" : "−") + fmtINR.format(Math.abs(value));
}

function num(value, digits = 2) {
  return value == null ? "—" : Number(value).toFixed(digits);
}

function pct(value) {
  if (value == null) return "—";
  return (value >= 0 ? "+" : "") + Number(value).toFixed(2) + "%";
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.textContent !== text) {
    el.textContent = text;
    // Brief accent flash on value change (skipped on first fill).
    if (el.dataset.filled) {
      el.classList.remove("flash-updated");
      void el.offsetWidth;  // restart the animation
      el.classList.add("flash-updated");
    }
    el.dataset.filled = "1";
  }
}

function tint(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("pnl-up", value > 0);
  el.classList.toggle("pnl-down", value < 0);
}

// Instrument label: EQ / FUT / option descriptor.
function instrumentLabel(o) {
  if (o.instrument_type === "FUT") return `${o.symbol} FUT ${o.expiry || ""}`.trim();
  if (o.instrument_type === "CE" || o.instrument_type === "PE") {
    return `${o.symbol} ${o.strike} ${o.instrument_type} ${o.expiry || ""}`.trim();
  }
  return o.symbol;
}

function sideBadge(side) {
  return side === "BUY" ? '<span class="side-buy">BUY</span>'
                        : '<span class="side-sell">SELL</span>';
}

function statusBadge(status) {
  const map = {
    FILLED: "up", OPEN: "open", PENDING: "warn",
    CANCELLED: "muted", REJECTED: "down",
  };
  return `<span class="badge ${map[status] || "muted"}">${status}</span>`;
}
