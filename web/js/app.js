// Paper-Trader — shell: router, theme, adaptive polling, health.

const App = {
  route: "watchlist",
  chainContext: null,
  pollTimer: null,
  pollCount: 0,
  docHidden: false,
};

// ------------------------------------------------------------------ theme

function initTheme() {
  const saved = localStorage.getItem("pt-theme");
  const theme = saved === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById("theme-toggle");
  if (btn) btn.textContent = theme === "light" ? "🌙 Dark" : "☀️ Light";
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("pt-theme", next);
  initTheme();
}

// ------------------------------------------------------------------ router

const ROUTES = {
  watchlist: { el: "page-watchlist", load: refreshWatchlist },
  chain: { el: "page-chain", load: refreshChain },
  positions: { el: "page-positions", load: refreshPositions },
  account: { el: "page-account", load: refreshAccount },
  chart: { el: "page-chart", load: loadChart },
  macro: { el: "page-macro", load: refreshMacro },
};

function showRoute(route, pageSymbol) {
  if (route === "chain" && pageSymbol) {
    ChainState.symbol = pageSymbol;
    ChainState.expiry = null;
  }
  if (route === "chart" && pageSymbol) {
    ChartState.symbol = pageSymbol;
    const sel = document.getElementById("chart-symbol");
    if (sel) sel.value = pageSymbol;
  }
  App.route = route;
  for (const [name, def] of Object.entries(ROUTES)) {
    const el = document.getElementById(def.el);
    if (!el) continue;
    el.classList.remove("active", "page-enter");
    if (name === route) {
      // Force reflow so the entrance animation replays on every switch.
      void el.offsetWidth;
      el.classList.add("active", "page-enter");
    }
  }
  document.querySelectorAll(".nav button[data-route]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.route === route);
  });
  ROUTES[route].load();
}

// ------------------------------------------------------------------ data

async function refreshAccount() {
  try {
    const acct = await API.account();
    setText("s-cash", fmtINR0(acct.available_funds));
    setText("s-margin", fmtINR0(acct.used_margin));
    setText("s-equity", fmtINR0(acct.equity));

    // Full account page (when visible).
    setText("a-capital", fmtINR0(acct.starting_capital));
    setText("a-cash", fmtINR0(acct.cash));
    setText("a-available", fmtINR0(acct.available_funds));
    setText("a-margin", fmtINR0(acct.used_margin));
    setText("a-portfolio", fmtINR0(acct.portfolio_value));
    setText("a-unrealized", signedINR(acct.unrealized_pnl));
    setText("a-equity", fmtINR0(acct.equity));
    setText("a-pnl", signedINR(acct.total_pnl));
    tint("a-pnl", acct.total_pnl);
    tint("a-unrealized", acct.unrealized_pnl);

    // Data-source marking for the account totals (Stage 5).
    const srcEl = document.getElementById("a-source");
    if (srcEl && acct.data_source) {
      srcEl.innerHTML = `Marking source: ${sourceBadge(acct.data_source.status)}`
        + (acct.data_source.source && acct.data_source.source !== "none"
          ? ` <span>via ${acct.data_source.source}</span>` : "");
    }
  } catch (err) {
    console.error("account refresh failed", err);
  }
}

async function checkHealth() {
  const els = [document.getElementById("health-badge"),
               document.getElementById("health-badge-mobile")].filter(Boolean);
  const set = (text, cls) => els.forEach((el) => { el.textContent = text; el.className = cls; });
  API.health()
    .then(() => set("● live", "badge up"))
    .catch(() => set("● offline", "badge down"));
}

// ------------------------------------------------------- mobile sidebar

function initMobileSidebar() {
  const sidebar = document.getElementById("sidebar");
  const overlay = document.getElementById("sidebar-overlay");
  const toggle = document.getElementById("sidebar-toggle");
  const closeBtn = document.getElementById("sidebar-close");
  if (!sidebar || !overlay || !toggle) return;

  const open = () => {
    sidebar.classList.add("open");
    overlay.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
  };
  const close = () => {
    sidebar.classList.remove("open");
    overlay.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  };

  toggle.addEventListener("click", () =>
    sidebar.classList.contains("open") ? close() : open());
  closeBtn?.addEventListener("click", close);
  overlay.addEventListener("click", close);

  // Close the drawer when a nav item is chosen (natural on mobile).
  sidebar.querySelectorAll(".nav button").forEach((b) =>
    b.addEventListener("click", () => {
      if (window.innerWidth <= 920) close();
    }));

  // Esc closes; reset state when resizing back to desktop.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && sidebar.classList.contains("open")) close();
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth > 920) close();
  });
}

// ------------------------------------------------------------------ polling
// Adaptive: skip when the tab is hidden; long interval on the chart page
// (charts re-fetch explicitly on control changes); normal elsewhere.

const POLL_FAST_MS = 3000;
const POLL_SLOW_MS = 15000;

function currentPollInterval() {
  if (App.docHidden) return 0;
  return App.route === "chart" ? POLL_SLOW_MS : POLL_FAST_MS;
}

function pollTick() {
  if (App.docHidden) return;
  // Never rebuild page data while the trade ticket is open — a re-render
  // underneath the modal would reset forms mid-interaction.
  if (document.querySelector(".modal-backdrop")) return;
  if (App.route === "chart") {
    // Keep the sidebar figures fresh without touching the chart canvas.
    refreshAccount();
    return;
  }
  refreshAccount();
  ROUTES[App.route].load();
}

function startPolling() {
  clearInterval(App.pollTimer);
  let interval = 0;
  const reschedule = () => {
    const next = currentPollInterval();
    if (next === interval) return;
    interval = next;
    clearInterval(App.pollTimer);
    if (!interval) return;
    App.pollTimer = setInterval(pollTick, interval);
  };
  document.addEventListener("visibilitychange", () => {
    App.docHidden = document.hidden;
    if (!App.docHidden) {
      refreshAccount();
      ROUTES[App.route].load();   // catch up immediately on return
    }
    reschedule();
  });
  reschedule();
}

// ------------------------------------------------------------------ boot

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  document.getElementById("theme-toggle").addEventListener("click", toggleTheme);

  document.querySelectorAll(".nav button[data-route]").forEach((btn) => {
    btn.addEventListener("click", () => showRoute(btn.dataset.route));
  });

  wireWatchlist();
  initMobileSidebar();
  wireQuickTradeBar();
  initChainSelectors().catch((err) => console.error("chain init failed", err));
  initChartPage();
  initMacroPage();

  refreshAccount();
  checkHealth();
  showRoute("watchlist");
  startPolling();
});
