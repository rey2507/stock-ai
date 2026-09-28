// Paper-Trader — watchlist page: search with suggestions, add/chain/chart actions.

async function refreshWatchlist() {
  try {
    const body = await API.watchlist();
    const tbody = document.querySelector("#watch-table tbody");
    if (!body.watchlist.length) {
      tbody.innerHTML = '      <tr class="empty"><td colspan="9">Watchlist empty — add a symbol above.</td></tr>';
      return;
    }
    tbody.innerHTML = body.watchlist.map((q) => `
      <tr>
        <td><strong>${q.symbol}</strong></td>
        <td class="muted">${q.name}</td>
        <td><span class="badge muted">${q.kind}</span></td>
        <td>${q.lot_size}</td>
        <td>${fmtINR0(q.ltp)}</td>
        <td><span class="chg ${q.change_pct >= 0 ? "chg-up" : "chg-down"}">${pct(q.change_pct)}</span></td>
        <td>${sourceBadge(q.status)}</td>
        <td>
          <button class="btn small btn-trade fut" data-trade-fut="${q.symbol}">Futures</button>
          <button class="btn small btn-trade chain" data-chain="${q.symbol}">Chain</button>
          <button class="btn small btn-trade chart" data-chart="${q.symbol}">Chart</button>
        </td>
        <td><button class="btn small btn-icon danger" data-remove="${q.symbol}" title="Remove ${q.symbol} from watchlist">✕</button></td>
      </tr>`).join("");

    tbody.querySelectorAll("[data-trade-fut]").forEach((b) =>
      b.addEventListener("click", async () => {
        const exp = await API.expiries(b.dataset.tradeFut);
        openTradeModal({ symbol: b.dataset.tradeFut, instrumentType: "FUT", expiry: exp.expiries[0] });
      }));
    tbody.querySelectorAll("[data-chain]").forEach((b) =>
      b.addEventListener("click", () => showRoute("chain", b.dataset.chain)));
    tbody.querySelectorAll("[data-chart]").forEach((b) =>
      b.addEventListener("click", () => showRoute("chart", b.dataset.chart)));
    tbody.querySelectorAll("[data-remove]").forEach((b) =>
      b.addEventListener("click", async () => {
        await API.removeWatch(b.dataset.remove);
        refreshWatchlist();
      }));
  } catch (err) {
    console.error("watchlist refresh failed", err);
  }
}

function wireWatchlist() {
  document.getElementById("watch-add").addEventListener("click", addWatchSymbol);
  const input = document.getElementById("watch-input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") addWatchSymbol();
    if (e.key === "Escape") hideSuggestions();
  });
  input.addEventListener("input", () => showSuggestions(input.value.trim().toUpperCase()));
  input.addEventListener("blur", () => setTimeout(hideSuggestions, 150));
  document.addEventListener("click", (e) => {
    if (!document.getElementById("watch-search-wrap").contains(e.target)) hideSuggestions();
  });
}

// ---------------------------------------------------------------- search

let allInstruments = null;   // cached [{symbol, name, kind, lot_size}]

async function fetchInstruments() {
  if (!allInstruments) {
    const data = await API.instruments();
    allInstruments = data.instruments.map((i) =>
      ({ symbol: i.symbol, name: i.name, kind: i.kind, lot_size: i.lot_size }));
  }
  return allInstruments;
}

// Server-authoritative lot size for a symbol (null while unknown).
async function lotSizeOf(symbol) {
  const inst = (await fetchInstruments()).find((i) => i.symbol === symbol);
  return inst ? inst.lot_size : null;
}

async function showSuggestions(query) {
  const wrap = document.getElementById("watch-suggestions");
  if (!query) { hideSuggestions(); return; }

  const items = await fetchInstruments();
  const matches = items
    .filter((i) => i.symbol.includes(query) || i.name.toUpperCase().includes(query))
    .slice(0, 8);

  if (!matches.length) {
    wrap.innerHTML = '<div class="suggestion-empty">No match for “' + query + '”</div>';
    wrap.hidden = false;
    return;
  }

  const watchSyms = new Set(
    [...document.querySelectorAll("#watch-table td:first-child strong")].map((s) => s.textContent));

  wrap.innerHTML = matches.map((i) => `
    <div class="suggestion${watchSyms.has(i.symbol) ? " in-watch" : ""}" data-sym="${i.symbol}">
      <div class="suggestion-main">
        <strong>${i.symbol}</strong>
        <span class="muted">${i.name}</span>
      </div>
      <span class="badge muted">${i.kind}</span>
      <div class="suggestion-actions">
        ${watchSyms.has(i.symbol) ? '<span class="muted" style="font-size:var(--text-xs);">in watchlist</span>'
          : `<button class="btn small primary" data-sugg-add="${i.symbol}">+ Add</button>`}
        <button class="btn small btn-trade chain" data-sugg-chain="${i.symbol}">Chain</button>
        <button class="btn small btn-trade chart" data-sugg-chart="${i.symbol}">Chart</button>
      </div>
    </div>`).join("");
  wrap.hidden = false;

  wrap.querySelectorAll("[data-sugg-add]").forEach((b) =>
    b.addEventListener("mousedown", async (e) => {
      e.preventDefault();
      try {
        await API.addWatch(b.dataset.suggAdd);
        b.outerHTML = '<span class="muted" style="font-size:var(--text-xs);">added ✓</span>';
        refreshWatchlist();
      } catch (err) {
        alert(err.message);
      }
    }));
  wrap.querySelectorAll("[data-sugg-chain]").forEach((b) =>
    b.addEventListener("mousedown", (e) => {
      e.preventDefault();
      hideSuggestions();
      showRoute("chain", b.dataset.suggChain);
    }));
  wrap.querySelectorAll("[data-sugg-chart]").forEach((b) =>
    b.addEventListener("mousedown", (e) => {
      e.preventDefault();
      hideSuggestions();
      showRoute("chart", b.dataset.suggChart);
    }));
}

function hideSuggestions() {
  const wrap = document.getElementById("watch-suggestions");
  if (wrap) wrap.hidden = true;
}

async function addWatchSymbol() {
  const input = document.getElementById("watch-input");
  const symbol = input.value.trim().toUpperCase();
  if (!symbol) return;
  try {
    await API.addWatch(symbol);
    input.value = "";
    hideSuggestions();
    refreshWatchlist();
  } catch (err) {
    alert(err.message);
  }
}
