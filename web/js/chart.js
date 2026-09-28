// Paper-Trader — Stage 6/7 chart page: canvas candlesticks + volume +
// indicator overlays (SMA/EMA) and subplots (RSI/MACD).
// Fetches normalized candles from /api/chart/{symbol}, renders with HTML5
// canvas (no charting dependency), supports interval/range selection, hover
// tooltip with indicator values, and follows the existing light/dark theme.

const IND_STORAGE_KEY = "pt-indicators";  // must precede ChartState (no TDZ)

const ChartState = {
  symbol: "NIFTY",
  interval: "5m",    // Stage 17: live intraday stack now serves 1m–1h too
  days: 10,          // quick-range selection (null when custom dates set)
  from: null,        // custom ISO date (YYYY-MM-DD) when set
  to: null,
  candles: [],
  indicators: {},    // computed indicator arrays (see indicators.js)
  status: null,
  source: null,
  hoverIndex: null,
  layout: null,      // cached pixel layout for hit-testing
  active: loadActiveIndicators(),  // persisted toggle set
  liveTimer: null,   // Stage 17: intraday auto-refresh handle
  renderer: localStorage.getItem("pt-renderer") || "canvas", // Stage 19
};

const LIVE_REFRESH_MS = 30000;  // intraday only; server cache TTL is 300s

function setRenderer(r) {
  if (r !== "canvas" && r !== "tv") return;
  ChartState.renderer = r;
  localStorage.setItem("pt-renderer", r);
  document.querySelectorAll("#chart-renderers button").forEach((b) =>
    b.classList.toggle("active", b.dataset.renderer === r));
  applyRendererVisibility();
  if (ChartState.candles.length) {
    if (r === "tv") lwRender(); else renderChart();
  }
}

function applyRendererVisibility() {
  const tv = ChartState.renderer === "tv" && lwAvailable();
  const canvas = document.getElementById("chart-canvas");
  const lw = document.getElementById("chart-lw");
  const lwRsi = document.getElementById("chart-lw-rsi");
  const lwMacd = document.getElementById("chart-lw-macd");
  const legend = document.getElementById("chart-lw-legend");
  if (canvas) canvas.hidden = tv;
  if (lw) lw.hidden = !tv;
  if (lwRsi) lwRsi.hidden = !tv || !ChartState.active.rsi14;
  if (lwMacd) lwMacd.hidden = !tv || !ChartState.active.macd;
  if (legend) legend.hidden = !tv;
}

const INDICATOR_DEFS = [
  { key: "sma20",  label: "SMA 20",  colorVar: "--ind-sma20",  panel: "price" },
  { key: "sma50",  label: "SMA 50",  colorVar: "--ind-sma50",  panel: "price" },
  { key: "sma200", label: "SMA 200", colorVar: "--ind-sma200", panel: "price" },
  { key: "ema12",  label: "EMA 12",  colorVar: "--ind-ema12",  panel: "price" },
  { key: "ema26",  label: "EMA 26",  colorVar: "--ind-ema26",  panel: "price" },
  { key: "rsi14",  label: "RSI 14",  colorVar: "--ind-rsi",    panel: "rsi" },
  { key: "macd",   label: "MACD 12/26/9", colorVar: "--ind-macd", panel: "macd" },
];

function loadActiveIndicators() {
  try {
    const saved = JSON.parse(localStorage.getItem(IND_STORAGE_KEY));
    if (saved && typeof saved === "object") return saved;
  } catch { /* corrupted storage — fall through to default */ }
  return { sma20: true };   // sensible default on first visit
}

function saveActiveIndicators() {
  localStorage.setItem(IND_STORAGE_KEY, JSON.stringify(ChartState.active));
}

// ---------------------------------------------------------------- theme colors

function chartTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    bg: v("--surface", "#121b2e"),
    grid: v("--border", "#2a3a57"),
    text: v("--text-muted", "#8fa1bd"),
    up: v("--up", "#22c55e"),
    down: v("--down", "#ef4444"),
    upSoft: v("--up-soft", "rgba(34,197,94,0.14)"),
    downSoft: v("--down-soft", "rgba(239,68,68,0.14)"),
    ind: {
      sma20: v("--ind-sma20", "#ff6b6b"),
      sma50: v("--ind-sma50", "#4ecdc4"),
      sma200: v("--ind-sma200", "#95e1d3"),
      ema12: v("--ind-ema12", "#ffa07a"),
      ema26: v("--ind-ema26", "#ffb347"),
      rsi: v("--ind-rsi", "#9b59b6"),
      macd: v("--ind-macd", "#3498db"),
      signal: v("--ind-signal", "#e74c3c"),
      guide: v("--ind-guide", "rgba(128,148,178,0.35)"),
    },
  };
}

// ---------------------------------------------------------------- date helpers

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function rangeDates() {
  const to = new Date();
  let from = new Date(to);
  if (ChartState.from && ChartState.to) {
    return { start: ChartState.from, end: ChartState.to };
  }
  from.setDate(to.getDate() - (ChartState.days || 10));
  return { start: isoDate(from), end: isoDate(to) };
}

// ---------------------------------------------------------------- data loading

async function initChartSelectors() {
  const sel = document.getElementById("chart-symbol");
  try {
    const instruments = await API.instruments();
    sel.innerHTML = instruments.instruments
      .map((i) => `<option value="${i.symbol}">${i.symbol}</option>`)
      .join("");
  } catch (err) {
    console.error("chart instruments failed", err);
  }
  sel.value = ChartState.symbol;
  sel.addEventListener("change", () => {
    ChartState.symbol = sel.value;
    loadChart();
  });
}

function chartBadge() {
  const el = document.getElementById("chart-source");
  el.innerHTML = ChartState.status ? inlineSourceBadge(ChartState.status) : "";
}

async function loadChart(opts) {
  const silent = !!(opts && opts.silent);   // timer refresh: keep scroll/hover
  const { start, end } = rangeDates();
  const empty = document.getElementById("chart-empty");
  const canvas = document.getElementById("chart-canvas");

  scheduleLiveRefresh();

  try {
    const data = await API.chart(ChartState.symbol, ChartState.interval, start, end);
    ChartState.candles = data.candles || [];
    ChartState.status = data.status;
    ChartState.source = data.source;
    chartBadge();

    document.getElementById("chart-meta").textContent =
      `${data.symbol} · ${data.interval} · ${start} → ${end}` +
      (ChartState.candles.length ? ` · ${ChartState.candles.length} candles` : "");

    if (!ChartState.candles.length) {
      applyRendererVisibility();
      empty.hidden = false;
      document.getElementById("chart-empty-detail").textContent =
        data.detail || `No candles returned (source: ${data.source || "none"}, status: ${data.status}).`;
      return;
    }
    empty.hidden = true;
    ChartState.indicators = computeIndicators(ChartState.candles, ChartState.active);
    // Hide subplots that lack enough history (RSI-14 needs 15 bars, MACD 26+).
    const nBars = ChartState.candles.length;
    if (nBars < 15) delete ChartState.active.rsi14;
    if (nBars < 26) delete ChartState.active.macd;
    if (ChartState.renderer === "tv" && lwAvailable()) {
      applyRendererVisibility();
      lwRender();
      lwWireCrosshairLegend();
    } else {
      applyRendererVisibility();
      renderChart();
    }
    updateLegendValues(null);
  } catch (err) {
    console.error("chart load failed", err);
    canvas.hidden = true;
    empty.hidden = false;
    document.getElementById("chart-empty-detail").textContent = err.message;
  }
}

// ------------------------------------------------ Stage 17: live refresh

function isIntraday() {
  return ["1m", "5m", "15m", "1h"].includes(ChartState.interval);
}

function scheduleLiveRefresh() {
  if (ChartState.liveTimer) { clearInterval(ChartState.liveTimer); ChartState.liveTimer = null; }
  if (!isIntraday()) return;
  ChartState.liveTimer = setInterval(() => {
    if (App.route !== "chart" || App.docHidden) return;
    if (document.querySelector(".modal-backdrop")) return; // ticket open
    lwLiveTick();
  }, LIVE_REFRESH_MS);
}

/** Live refresh that reuses the visible renderer efficiently. */
async function lwLiveTick() {
  const { start, end } = rangeDates();
  try {
    const data = await API.chart(ChartState.symbol, ChartState.interval, start, end);
    if (!data.candles || !data.candles.length) return;
    ChartState.candles = data.candles;
    ChartState.status = data.status;
    ChartState.source = data.source;
    chartBadge();
    ChartState.indicators = computeIndicators(ChartState.candles, ChartState.active);
    if (ChartState.renderer === "tv" && lwAvailable()) {
      lwUpdateLastBar();
    } else {
      renderChart();
    }
    updateLegendValues(null);
  } catch (err) {
    console.error("live tick failed", err);
  }
}

// ------------------------------------------------------------- last price

function drawLastPriceLine(ctx, t, panel, scale) {
  const candles = ChartState.candles;
  if (!candles.length || !panel) return;
  const last = candles[candles.length - 1];
  const y = scale(parseFloat(last.close));
  if (!isFinite(y)) return;
  const prev = candles.length > 1 ? candles[candles.length - 2].close : null;
  const color = prev != null && last.close >= prev
    ? t.up : t.down;
  ctx.save();
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.85;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(panel.left, y);
  ctx.lineTo(panel.right, y);
  ctx.stroke();
  // Price tag on the right edge.
  const label = Number(last.close).toLocaleString("en-IN",
    { maximumFractionDigits: 2 });
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  const w = ctx.measureText(label).width + 10;
  ctx.fillStyle = color;
  const tagY = Math.min(Math.max(y - 8, panel.top), panel.top + panel.height - 16);
  ctx.fillRect(panel.right - w, tagY, w, 16);
  ctx.fillStyle = t.bg || "#000";
  ctx.fillText(label, panel.right - w + 5, tagY + 11.5);
  ctx.restore();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------------------------------------------------------------- panels

/** Distribute the canvas vertically across the active panels. */
function panelLayout(height, t) {
  const padB = 26;  // time axis
  const padT = 10;
  const usable = height - padB - padT;
  const gap = 10;

  const wantsRsi = !!ChartState.active.rsi14 && ChartState.indicators.rsi14
    && ChartState.indicators.rsi14.some((v) => v !== null && v !== undefined);
  const wantsMacd = !!ChartState.active.macd && ChartState.indicators.macd
    && (ChartState.indicators.macd.macdLine || []).some((v) => v !== null && v !== undefined);
  const nSub = (wantsRsi ? 1 : 0) + (wantsMacd ? 1 : 0);

  // Price panel gets at least 45% no matter what; the rest split evenly.
  const subH = nSub ? Math.max(70, (usable * 0.4 - gap * nSub) / nSub) : 0;
  const priceH = usable - subH * nSub - gap * nSub - (nSub ? 16 * nSub : 0); // 16px title strip per subplot

  const panels = [];
  let y = padT;
  panels.push({ key: "price", top: y, height: priceH });
  y += priceH;
  if (wantsRsi) {
    y += gap;
    panels.push({ key: "rsi", top: y + 14, height: subH - 14, titleTop: y });
    y += subH;
  }
  if (wantsMacd) {
    y += gap;
    panels.push({ key: "macd", top: y + 14, height: subH - 14, titleTop: y });
  }
  return { panels, padT, padB };
}

// ---------------------------------------------------------------- rendering

function renderChart() {
  const canvas = document.getElementById("chart-canvas");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);

  const ctx = canvas.getContext("2d", { alpha: false });
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const t = chartTheme();
  ctx.fillStyle = t.bg;
  ctx.fillRect(0, 0, width, height);

  const candles = ChartState.candles;
  if (!candles.length) return;

  const padR = 64;   // price axis gutter
  const plotW = width - padR - 8;
  const { panels, padB } = panelLayout(height, t);
  const pricePanel = panels.find((p) => p.key === "price");

  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  // Overlay indicators participate in the price scale so lines never clip.
  for (const key of ["sma20", "sma50", "sma200", "ema12", "ema26"]) {
    if (!ChartState.active[key]) continue;
    for (const v of ChartState.indicators[key] || []) {
      if (v !== null && v !== undefined) {
        highs.push(v);
        lows.push(v);
      }
    }
  }
  let maxP = Math.max(...highs);
  let minP = Math.min(...lows);
  const span = maxP - minP || maxP * 0.01;
  maxP += span * 0.05;
  minP -= span * 0.05;

  const maxVol = Math.max(...candles.map((c) => c.volume || 0), 1);
  const n = candles.length;
  const slot = plotW / n;
  const bodyW = Math.max(1, Math.min(14, slot * 0.65));
  const xOf = (i) => 4 + slot * (i + 0.5);

  const yPrice = (p) => pricePanel.top + pricePanel.height * (1 - (p - minP) / (maxP - minP));

  ctx.font = "10px " + getComputedStyle(document.documentElement)
    .getPropertyValue("--font-mono");
  ctx.textBaseline = "middle";

  // ---- price panel: grid + axis + volume + candles ----
  const GRID_LINES = 5;
  ctx.strokeStyle = t.grid;
  ctx.fillStyle = t.text;
  for (let g = 0; g <= GRID_LINES; g++) {
    const p = minP + ((maxP - minP) * g) / GRID_LINES;
    const y = Math.round(yPrice(p)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(plotW, y);
    ctx.stroke();
    ctx.textAlign = "left";
    ctx.fillText(p.toFixed(p > 1000 ? 0 : 2), plotW + 6, y);
  }

  // Volume strip along the bottom 18% of the price panel.
  const volH = pricePanel.height * 0.18;
  const volBase = pricePanel.top + pricePanel.height;
  for (let i = 0; i < n; i++) {
    const c = candles[i];
    const vh = ((c.volume || 0) / maxVol) * volH;
    ctx.fillStyle = c.close >= c.open ? t.upSoft : t.downSoft;
    ctx.fillRect(xOf(i) - bodyW / 2, volBase - vh, bodyW, vh);
  }

  for (let i = 0; i < n; i++) {
    const c = candles[i];
    const up = c.close >= c.open;
    const color = up ? t.up : t.down;
    const x = xOf(i);
    ctx.strokeStyle = color;
    ctx.beginPath();
    ctx.moveTo(x, yPrice(c.high));
    ctx.lineTo(x, yPrice(c.low));
    ctx.stroke();
    const yOpen = yPrice(c.open);
    const yClose = yPrice(c.close);
    const top = Math.min(yOpen, yClose);
    const h = Math.max(1, Math.abs(yClose - yOpen));
    ctx.fillStyle = color;
    ctx.fillRect(x - bodyW / 2, top, bodyW, h);
  }

  // ---- overlays: SMA/EMA lines on the price panel ----
  for (const def of INDICATOR_DEFS) {
    if (def.panel !== "price" || !ChartState.active[def.key]) continue;
    drawLine(ctx, ChartState.indicators[def.key], xOf, yPrice, t.ind[def.key], plotW);
  }

  // ---- Stage 17: dashed last-price line + right-edge price tag ----
  drawLastPriceLine(ctx, t, { top: pricePanel.top, height: pricePanel.height, left: 0, right: plotW }, yPrice);

  // ---- RSI subplot ----
  const rsiPanel = panels.find((p) => p.key === "rsi");
  if (rsiPanel && ChartState.active.rsi14) {
    subplotTitle(ctx, "RSI 14", rsiPanel.titleTop, t.ind.rsi, t.text);
    const yRsi = (v) => rsiPanel.top + rsiPanel.height * (1 - v / 100);
    // Guide lines at 30/70 + shaded extreme zones.
    for (const level of [30, 70]) {
      const y = Math.round(yRsi(level)) + 0.5;
      ctx.strokeStyle = t.ind.guide;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    fillWhere(ctx, ChartState.indicators.rsi14, xOf, yRsi, (v) => v > 70, t.downSoft, plotW);
    fillWhere(ctx, ChartState.indicators.rsi14, xOf, yRsi, (v) => v < 30, t.upSoft, plotW);
    drawLine(ctx, ChartState.indicators.rsi14, xOf, yRsi, t.ind.rsi, plotW);
    ctx.fillStyle = t.text;
    ctx.textAlign = "left";
    ctx.fillText("70", plotW + 6, yRsi(70));
    ctx.fillText("30", plotW + 6, yRsi(30));
  }

  // ---- MACD subplot ----
  const macdPanel = panels.find((p) => p.key === "macd");
  if (macdPanel && ChartState.active.macd) {
    subplotTitle(ctx, "MACD 12/26/9", macdPanel.titleTop, t.ind.macd, t.text);
    const { macdLine, signalLine, histogram } = ChartState.indicators.macd;
    const vals = [...macdLine, ...signalLine, ...histogram]
      .filter((v) => v !== null && v !== undefined);
    const mMax = Math.max(...vals, 0.0001);
    const mMin = Math.min(...vals, -0.0001);
    const pad = (mMax - mMin) * 0.08 || 0.0001;
    const yMacd = (v) => macdPanel.top + macdPanel.height * (1 - (v - mMin) / (mMax - mMin + pad * 2));
    const y0 = Math.round(yMacd(0)) + 0.5;
    ctx.strokeStyle = t.ind.guide;
    ctx.beginPath();
    ctx.moveTo(0, y0);
    ctx.lineTo(plotW, y0);
    ctx.stroke();

    // Histogram behind the lines.
    const halfW = Math.max(1, bodyW * 0.6);
    for (let i = 0; i < n; i++) {
      const h = histogram[i];
      if (h === null || h === undefined) continue;
      const yh = yMacd(h);
      ctx.fillStyle = h >= 0 ? t.upSoft : t.downSoft;
      ctx.fillRect(xOf(i) - halfW / 2, Math.min(yh, y0), halfW, Math.abs(yh - y0));
    }
    drawLine(ctx, macdLine, xOf, yMacd, t.ind.macd, plotW);
    drawLine(ctx, signalLine, xOf, yMacd, t.ind.signal, plotW);
  }

  // ---- time axis labels ----
  ctx.fillStyle = t.text;
  ctx.textAlign = "center";
  const labelEvery = Math.max(1, Math.ceil(n / 6));
  const fmtTs = (ts) => {
    if (ChartState.interval === "1D") {
      return /\d{4}-\d{2}-\d{2}/.test(ts) ? ts.slice(0, 10) : ts;
    }
    return ts.slice(5, 16).replace("T", " ");
  };
  for (let i = 0; i < n; i += labelEvery) {
    ctx.fillText(fmtTs(candles[i].timestamp), xOf(i), height - padB / 2);
  }

  // Cache layout for tooltip hit-testing.
  ChartState.layout = { xOf, slot, n, plotW, priceTop: pricePanel.top, priceHeight: pricePanel.height };
}

function drawLine(ctx, values, xOf, yOf, color, plotW) {
  if (!values || !values.length) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  let started = false;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v === null || v === undefined) { started = false; continue; }
    const x = xOf(i);
    const y = yOf(v);
    if (!started) { ctx.moveTo(x, y); started = true; }
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.lineWidth = 1;
}

function fillWhere(ctx, values, xOf, yOf, predicate, color, plotW) {
  // Shade the band between the RSI value and the breached guide level
  // (70 for overbought, 30 for oversold) on each qualifying bar.
  ctx.fillStyle = color;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v === null || v === undefined || !predicate(v)) continue;
    const level = v > 70 ? 70 : 30;
    const yTop = Math.min(yOf(v), yOf(level));
    const h = Math.max(1, Math.abs(yOf(level) - yOf(v)));
    ctx.fillRect(xOf(i) - 1.25, yTop, 2.5, h);
  }
}

function subplotTitle(ctx, text, y, color, textColor) {
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = color;
  ctx.font = "bold 10px " + getComputedStyle(document.documentElement)
    .getPropertyValue("--font-mono");
  ctx.fillText(text, 6, y + 9);
  ctx.fillStyle = textColor;
  ctx.font = "10px " + getComputedStyle(document.documentElement)
    .getPropertyValue("--font-mono");
  ctx.textBaseline = "middle";
}

// ---------------------------------------------------------------- legend

function renderLegend() {
  const wrap = document.getElementById("chart-indicators");
  if (!wrap) return;
  wrap.innerHTML = INDICATOR_DEFS.map((def) => {
    const on = !!ChartState.active[def.key];
    return `<label class="ind-toggle" style="--ind-color: var(${def.colorVar})">
      <input type="checkbox" data-indicator="${def.key}" ${on ? "checked" : ""} />
      <span class="ind-swatch"></span>${def.label}
    </label>`;
  }).join("");

  wrap.querySelectorAll("input[data-indicator]").forEach((box) => {
    box.addEventListener("change", () => {
      const key = box.dataset.indicator;
      if (box.checked) ChartState.active[key] = true;
      else delete ChartState.active[key];
      saveActiveIndicators();
      ChartState.indicators = computeIndicators(ChartState.candles, ChartState.active);
      if (ChartState.renderer === "tv" && lwAvailable()) {
        applyRendererVisibility();
        lwRender();
      } else {
        renderChart();
      }
      updateLegendValues(ChartState.hoverIndex);
    });
  });
}

const IND_LEGEND_KEYS = ["sma20", "sma50", "sma200", "ema12", "ema26"];

/** Live values row under the legend (or tooltip supplement on hover). */
function updateLegendValues(hoverIdx) {
  const el = document.getElementById("chart-ind-values");
  if (!el) return;
  const idx = hoverIdx ?? ChartState.candles.length - 1;
  if (idx < 0 || !ChartState.candles.length) { el.textContent = ""; return; }

  const parts = [];
  for (const key of IND_LEGEND_KEYS) {
    if (!ChartState.active[key]) continue;
    const v = (ChartState.indicators[key] || [])[idx];
    if (v != null) parts.push(`${key.toUpperCase()}: ${v.toFixed(2)}`);
  }
  if (ChartState.active.rsi14) {
    const v = (ChartState.indicators.rsi14 || [])[idx];
    if (v != null) parts.push(`RSI: ${v.toFixed(1)}`);
  }
  if (ChartState.active.macd) {
    const { macdLine, signalLine } = ChartState.indicators.macd || {};
    const m = (macdLine || [])[idx];
    const s = (signalLine || [])[idx];
    if (m != null) parts.push(`MACD: ${m.toFixed(2)}`);
    if (s != null) parts.push(`Signal: ${s.toFixed(2)}`);
  }
  el.textContent = parts.join("   ·   ");
}

// ---------------------------------------------------------------- tooltip

function wireChartTooltip() {
  const canvas = document.getElementById("chart-canvas");
  const tip = document.getElementById("chart-tooltip");

  canvas.addEventListener("mousemove", (e) => {
    const layout = ChartState.layout;
    if (!layout || !ChartState.candles.length) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const idx = Math.floor((x - 4) / layout.slot);
    if (idx < 0 || idx >= layout.n) { tip.hidden = true; updateLegendValues(null); return; }

    ChartState.hoverIndex = idx;
    const c = ChartState.candles[idx];
    const up = c.close >= c.open;
    const cls = up ? "tt-up" : "tt-down";
    const indRows = [];

    for (const key of IND_LEGEND_KEYS) {
      if (!ChartState.active[key]) continue;
      const v = (ChartState.indicators[key] || [])[idx];
      if (v != null) {
        const def = INDICATOR_DEFS.find((d) => d.key === key);
        indRows.push(`<span style="color:${chartTheme().ind[key]}">${def.label}: ${v.toFixed(2)}</span>`);
      }
    }
    if (ChartState.active.rsi14) {
      const v = (ChartState.indicators.rsi14 || [])[idx];
      if (v != null) indRows.push(`<span style="color:${chartTheme().ind.rsi}">RSI: ${v.toFixed(1)}</span>`);
    }
    if (ChartState.active.macd) {
      const { macdLine, signalLine } = ChartState.indicators.macd || {};
      const m = (macdLine || [])[idx];
      const s = (signalLine || [])[idx];
      if (m != null) indRows.push(`<span style="color:${chartTheme().ind.macd}">MACD: ${m.toFixed(2)}</span>`);
      if (s != null) indRows.push(`<span style="color:${chartTheme().ind.signal}">Signal: ${s.toFixed(2)}</span>`);
    }

    tip.innerHTML =
      `<div><strong>${c.timestamp.replace("T", " ").slice(0, 16)}</strong></div>` +
      `O <span class="${cls}">${c.open.toFixed(2)}</span>` +
      ` H <span class="${cls}">${c.high.toFixed(2)}</span><br/>` +
      `L <span class="${cls}">${c.low.toFixed(2)}</span>` +
      ` C <span class="${cls}">${c.close.toFixed(2)}</span><br/>` +
      `V ${c.volume != null ? c.volume.toLocaleString("en-IN") : "—"}` +
      (indRows.length ? `<hr/>${indRows.join("<br/>")}` : "");

    tip.hidden = false;
    const tipX = Math.min(e.clientX - rect.left + 14, rect.width - 180);
    tip.style.left = `${tipX}px`;
    tip.style.top = `${e.clientY - rect.top + 12}px`;
    updateLegendValues(idx);
  });

  canvas.addEventListener("mouseleave", () => {
    tip.hidden = true;
    updateLegendValues(null);
  });
}

// ---------------------------------------------------------------- controls

function wireChartControls() {
  const setSeg = (container, attr, value) => {
    container.querySelectorAll("button").forEach((b) =>
      b.classList.toggle("active", b.dataset[attr] === String(value)));
  };

  document.getElementById("chart-intervals").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-interval]");
    if (!btn) return;
    ChartState.interval = btn.dataset.interval;
    setSeg(e.currentTarget, "interval", ChartState.interval);
    loadChart();
  });

  // Reflect the persisted/default interval on the segmented control at boot.
  setSeg(document.getElementById("chart-intervals"), "interval", ChartState.interval);

  document.getElementById("chart-ranges").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-days]");
    if (!btn) return;
    ChartState.days = Number(btn.dataset.days);
    ChartState.from = ChartState.to = null;   // custom range cleared
    setSeg(e.currentTarget, "days", ChartState.days);
    loadChart();
  });

  document.getElementById("chart-apply").addEventListener("click", () => {
    const from = document.getElementById("chart-from").value;
    const to = document.getElementById("chart-to").value;
    if (!from || !to || from > to) {
      alert("Pick a valid custom range (from ≤ to).");
      return;
    }
    ChartState.from = from;
    ChartState.to = to;
    loadChart();
  });

  document.getElementById("chart-refresh").addEventListener("click", loadChart);

  // Stage 19: renderer toggle (Canvas ↔ TradingView Lightweight Charts).
  document.querySelectorAll("#chart-renderers button").forEach((btn) => {
    btn.addEventListener("click", () => setRenderer(btn.dataset.renderer));
  });
  setRenderer(ChartState.renderer);

  // Re-render when the theme toggles (colors come from CSS variables).
  new MutationObserver(() => {
    if (ChartState.candles.length && !document.getElementById("page-chart").hidden) {
      if (ChartState.renderer === "tv" && lwAvailable()) lwOnThemeChange();
      else renderChart();
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  window.addEventListener("resize", () => {
    if (!ChartState.candles.length) return;
    if (ChartState.renderer !== "tv") renderChart();
    // LW panes resize via their own window listener.
  });
}

function initChartPage() {
  renderLegend();
  initChartSelectors();
  wireChartControls();
  wireChartTooltip();
  wireChartClickPopup();
}

// ---------------------------------------------------------------- click-to-trade

function wireChartClickPopup() {
  const canvas = document.getElementById("chart-canvas");
  const card = document.querySelector(".chart-card");
  if (!canvas || !card) return;

  const popup = document.createElement("div");
  popup.id = "chart-click-popup";
  popup.className = "chart-click-popup";
  popup.hidden = true;
  popup.innerHTML = `
    <button class="btn small" id="chart-buy-btn">Buy</button>
    <button class="btn small" id="chart-sell-btn">Sell</button>
  `;
  card.appendChild(popup);

  const style = document.createElement("style");
  style.textContent = `
    #chart-click-popup {
      position: absolute;
      display: flex;
      gap: 4px;
      padding: 4px 6px;
      background: var(--surface-3, #1f2c46);
      border: 1px solid var(--border, #2a3a57);
      border-radius: var(--radius-sm, 6px);
      z-index: 10;
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    }
    #chart-click-popup button {
      padding: 3px 10px;
      font-size: 12px;
      cursor: pointer;
      border: none;
      border-radius: 4px;
      font-weight: 600;
    }
    #chart-buy-btn {
      background: var(--accent-strong, #2563eb);
      color: var(--accent-contrast, #ffffff);
    }
    #chart-sell-btn {
      background: var(--down-soft, rgba(239,68,68,0.14));
      color: var(--down, #ef4444);
    }
  `;
  document.head.appendChild(style);

  document.getElementById("chart-buy-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    openTradeModal({ symbol: ChartState.symbol, instrumentType: "EQ", presetSide: "BUY" });
    popup.hidden = true;
  });

  document.getElementById("chart-sell-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    openTradeModal({ symbol: ChartState.symbol, instrumentType: "EQ", presetSide: "SELL" });
    popup.hidden = true;
  });

  canvas.addEventListener("click", (e) => {
    const layout = ChartState.layout;
    if (!layout || !ChartState.candles.length) {
      popup.hidden = true;
      return;
    }

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const { priceTop, priceHeight, slot, n, plotW } = layout;

    if (y < priceTop || y > priceTop + priceHeight || x < 0 || x > plotW) {
      popup.hidden = true;
      return;
    }

    const idx = Math.floor((x - 4) / slot);
    if (idx < 0 || idx >= n) {
      popup.hidden = true;
      return;
    }

    const popupX = Math.max(0, Math.min(x + 8, plotW - 120));
    const popupY = Math.max(0, Math.min(y + 8, priceTop + priceHeight - 36));

    popup.style.left = `${popupX}px`;
    popup.style.top = `${popupY}px`;
    popup.hidden = false;
  });

  document.addEventListener("click", (e) => {
    if (!popup.contains(e.target) && e.target !== canvas) {
      popup.hidden = true;
    }
  });
}
