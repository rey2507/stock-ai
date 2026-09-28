// Paper-Trader — Stage 3 API helpers.

async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const response = await fetch(url, opts);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = new Error(data.detail || `HTTP ${response.status}`);
    err.status = response.status;
    throw err;
  }
  return data;
}

const API = {
  health: () => request("GET", "/api/health"),

  watchlist: () => request("GET", "/api/watchlist"),
  addWatch: (symbol) => request("POST", "/api/watchlist", { symbol }),
  removeWatch: (symbol) => request("DELETE", `/api/watchlist/${symbol}`),

  instruments: (kind) =>
    request("GET", `/api/instruments${kind ? `?kind=${kind}` : ""}`),
  expiries: (symbol) => request("GET", `/api/expiries/${symbol}`),
  optionChain: (symbol, expiry, strikesPerSide) =>
    request("GET", `/api/option-chain/${symbol}`
      + `?strikes_per_side=${strikesPerSide ?? ""}`
      + (expiry ? `&expiry=${encodeURIComponent(expiry)}` : "")),

  orders: () => request("GET", "/api/orders"),
  placeOrder: (payload) => request("POST", "/api/orders", payload),
  cancelOrder: (id) => request("DELETE", `/api/orders/${id}`),
  orderPreview: (payload) => request("POST", "/api/order-preview", payload),

  positions: () => request("GET", "/api/positions"),
  account: () => request("GET", "/api/account"),

  chart: (symbol, interval, start, end) =>
    request("GET", `/api/chart/${symbol}?interval=${encodeURIComponent(interval)}` +
      `&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),

  verdict: (symbol) => request("GET", `/api/verdict/${encodeURIComponent(symbol)}`),
  overallVerdict: () => request("GET", "/api/overall-verdict/NIFTY"),

  macro: () => request("GET", "/api/macro"),
  macroJournal: () => request("GET", "/api/macro/journal"),
  macroJournalSave: (payload) => request("POST", "/api/macro/journal", payload),
  macroJournalDelete: (id) => request("DELETE", `/api/macro/journal/${id}`),
};
