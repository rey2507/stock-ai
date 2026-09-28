// Paper-Trader — TradingView Lightweight Charts renderer (Stage 19).
//
// Optional alternative to the canvas renderer, chosen with the Renderer
// toggle on the Chart page. Consumes the SAME ChartState candles and the
// SAME computed indicator arrays from web/js/indicators.js, so toggling
// renderers never changes the math — only the picture. Adds what the
// canvas version can't do well: native zoom/pan, crosshair OHLC legend,
// price precision formatting, and O(1) last-bar updates for live refresh.
//
// Library: vendored lightweight-charts v4.2.3 (Apache-2.0), loaded before
// this file as window.LightweightCharts.

// eslint-disable-next-line no-new-func
const LW = (() => {
  const lib = window.LightweightCharts;
  if (!lib) return null;
  return {
    lib,
    chart: null,        // main candlestick chart
    candleSeries: null,
    volSeries: null,
    overlaySeries: {},  // key -> line series (sma20, ema12, ...)
    rsiChart: null,
    rsiSeries: null,
    macdChart: null,
    macdLine: null,
    macdSignal: null,
    macdHist: null,
    lastKey: "",        // symbol|interval signature of current data
  };
})();

function lwAvailable() {
  return !!LW;
}

function lwTime(ts) {
  // lightweight-charts wants UTC seconds; our timestamps carry +05:30 /
  // +09:00 etc. offsets, so Date parsing handles them correctly.
  const t = Date.parse(ts.includes("T") ? ts : ts.replace(" ", "T") + "+05:30");
  return Math.floor((isNaN(t) ? Date.now() : t) / 1000);
}

function lwPriceDigits(candles) {
  if (!candles.length) return 2;
  const p = Math.abs(Number(candles[candles.length - 1].close) || 0);
  if (p >= 10000) return 2;
  if (p >= 100) return 2;
  return 4;
}

// Maps CSS variables to concrete colors (the library needs real strings).
function lwTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name, fb) => (css.getPropertyValue(name) || "").trim() || fb;
  return {
    bg: v("--surface", "#121b2e"),
    text: v("--text-muted", "#8fa1bd"),
    grid: v("--border", "#2a3a57"),
    up: v("--up", "#22c55e"),
    upSoft: v("--up-soft", "rgba(34,197,94,0.35)"),
    down: v("--down", "#ef4444"),
    downSoft: v("--down-soft", "rgba(239,68,68,0.35)"),
    ind: {
      sma20: v("--ind-sma20", "#ff6b6b"),
      sma50: v("--ind-sma50", "#4ecdc4"),
      sma200: v("--ind-sma200", "#95e1d3"),
      ema12: v("--ind-ema12", "#ffa07a"),
      ema26: v("--ind-ema26", "#ffb347"),
      rsi: v("--ind-rsi", "#9b59b6"),
      macd: v("--ind-macd", "#3498db"),
      signal: v("--ind-signal", "#e74c3c"),
      guide: v("--border", "#2a3a57"),
    },
  };
}

function lwBaseOptions(t, digits) {
  return {
    layout: {
      background: { type: "solid", color: t.bg },
      textColor: t.text,
      fontSize: 11,
    },
    grid: {
      vertLines: { color: t.grid, style: 2 },
      horzLines: { color: t.grid, style: 2 },
    },
    rightPriceScale: {
      borderColor: t.grid,
      scaleMargins: { top: 0.08, bottom: 0.22 },
    },
    timeScale: { borderColor: t.grid, timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 }, // normal (both axes follow crosshair)
    localization: {
      priceFormatter: (p) => Number(p).toLocaleString("en-IN",
        { minimumFractionDigits: digits, maximumFractionDigits: digits }),
    },
  };
}

function lwDestroyPanes() {
  if (!LW) return;
  if (LW.rsiChart) { try { LW.rsiChart.remove(); } catch {} LW.rsiChart = null; }
  if (LW.macdChart) { try { LW.macdChart.remove(); } catch {} LW.macdChart = null; }
}

function lwDestroy() {
  if (!LW) return;
  lwDestroyPanes();
  if (LW.chart) { try { LW.chart.remove(); } catch {} }
  LW.chart = null;
  LW.candleSeries = null;
  LW.volSeries = null;
  LW.overlaySeries = {};
  LW.lastKey = "";
}

/** Build (or rebuild) all LW series from ChartState. */
function lwRender() {
  if (!LW || !ChartState.candles.length) return;
  const host = document.getElementById("chart-lw");
  if (!host) return;
  const t = lwTheme();
  const digits = lwPriceDigits(ChartState.candles);
  const sig = `${ChartState.symbol}|${ChartState.interval}|${ChartState.candles.length}`;

  if (!LW.chart || LW.lastKey.split("|").slice(0, 2).join("|")
      !== `${ChartState.symbol}|${ChartState.interval}`) {
    lwDestroy();
    LW.chart = LW.lib.createChart(host, {
      ...lwBaseOptions(t, digits),
      width: host.clientWidth,
      height: host.clientHeight,
      autoSize: false,
    });
    LW.candleSeries = LW.chart.addCandlestickSeries({
      upColor: t.up, downColor: t.down, wickUpColor: t.up, wickDownColor: t.down,
      borderVisible: false,
      priceLineVisible: true, priceLineStyle: 2, // dashed last-price line
      lastValueVisible: true,
    });
    LW.volSeries = LW.chart.addHistogramSeries({
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      priceScaleMargins: { top: 0.82, bottom: 0 },
    });
    LW.chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });
    LW.lastKey = "";
  }

  // --- candles + volume (full replace; cheap for our bar counts) ---
  const data = ChartState.candles.map((c) => ({
    time: lwTime(c.timestamp),
    open: c.open, high: c.high, low: c.low, close: c.close,
  }));
  const vols = ChartState.candles.map((c, i) => ({
    time: data[i].time,
    value: c.volume || 0,
    color: c.close >= c.open ? t.upSoft : t.downSoft,
  }));
  LW.candleSeries.setData(data);
  LW.volSeries.setData(vols);
  if (!ChartState.candles.length) return;

  // --- overlay indicators (same computed arrays the canvas draws) ---
  const overlayDefs = INDICATOR_DEFS.filter((d) => d.panel === "price");
  for (const def of overlayDefs) {
    const on = !!ChartState.active[def.key];
    if (on && !LW.overlaySeries[def.key]) {
      LW.overlaySeries[def.key] = LW.chart.addLineSeries({
        color: t.ind[def.key], lineWidth: 2, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
      });
    }
    if (!on) {
      if (LW.overlaySeries[def.key]) {
        LW.chart.removeSeries(LW.overlaySeries[def.key]);
        delete LW.overlaySeries[def.key];
      }
      continue;
    }
    const vals = ChartState.indicators[def.key] || [];
    const pts = [];
    for (let i = 0; i < vals.length; i++) {
      const v = vals[i];
      if (v !== null && v !== undefined) pts.push({ time: data[i].time, value: v });
    }
    LW.overlaySeries[def.key].setData(pts);
  }

  LW.chart.timeScale().fitContent();
  LW.lastKey = sig;

  lwRenderRsi(t, data);
  lwRenderMacd(t, data);
}

function lwRenderRsi(t, data) {
  const host = document.getElementById("chart-lw-rsi");
  const active = !!ChartState.active.rsi14
    && (ChartState.indicators.rsi14 || []).some((v) => v != null);
  if (!active) { if (LW.rsiChart) { lwDestroyPanes(); } return; }
  if (!host) return;
  if (!LW.rsiChart) {
    LW.rsiChart = LW.lib.createChart(host, {
      ...lwBaseOptions(t, 2),
      width: host.clientWidth, height: host.clientHeight,
      rightPriceScale: { borderColor: t.grid, scaleMargins: { top: 0.1, bottom: 0.1 } },
      timeScale: { borderColor: t.grid, timeVisible: true, visible: false },
      handleScroll: false, handleScale: false,
    });
    LW.rsiSeries = LW.rsiChart.addLineSeries({
      color: t.ind.rsi, lineWidth: 2, priceLineVisible: false,
    });
    LW.rsiSeries.createPriceLine({ price: 70, color: t.ind.guide, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "70" });
    LW.rsiSeries.createPriceLine({ price: 30, color: t.ind.guide, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "30" });
  }
  const pts = [];
  (ChartState.indicators.rsi14 || []).forEach((v, i) => {
    if (v != null) pts.push({ time: data[i].time, value: v });
  });
  LW.rsiSeries.setData(pts);
  LW.rsiChart.timeScale().fitContent();
}

function lwRenderMacd(t, data) {
  const host = document.getElementById("chart-lw-macd");
  const m = ChartState.indicators.macd;
  const active = !!ChartState.active.macd && m
    && (m.macdLine || []).some((v) => v != null);
  if (!active) { if (LW.macdChart) { lwDestroyPanes(); } return; }
  if (!host) return;
  if (!LW.macdChart) {
    LW.macdChart = LW.lib.createChart(host, {
      ...lwBaseOptions(t, 2),
      width: host.clientWidth, height: host.clientHeight,
      timeScale: { borderColor: t.grid, timeVisible: true, visible: false },
      handleScroll: false, handleScale: false,
    });
    LW.macdHist = LW.macdChart.addHistogramSeries({ priceLineVisible: false });
    LW.macdLine = LW.macdChart.addLineSeries({ color: t.ind.macd, lineWidth: 2, priceLineVisible: false });
    LW.macdSignal = LW.macdChart.addLineSeries({ color: t.ind.signal, lineWidth: 2, priceLineVisible: false });
  }
  const hist = [], line = [], signal = [];
  (m.macdLine || []).forEach((v, i) => {
    if (v != null) line.push({ time: data[i].time, value: v });
  });
  (m.signalLine || []).forEach((v, i) => {
    if (v != null) signal.push({ time: data[i].time, value: v });
  });
  (m.histogram || []).forEach((v, i) => {
    if (v != null) hist.push({
      time: data[i].time, value: v,
      color: v >= 0 ? t.upSoft : t.downSoft,
    });
  });
  LW.macdLine.setData(line);
  LW.macdSignal.setData(signal);
  LW.macdHist.setData(hist);
  LW.macdChart.timeScale().fitContent();
}

/** O(1) refresh: update only the last bar (used by the live timer). */
function lwUpdateLastBar() {
  if (!LW || !LW.candleSeries || !ChartState.candles.length) return;
  const n = ChartState.candles.length;
  const last = ChartState.candles[n - 1];
  const t = lwTime(last.timestamp);
  LW.candleSeries.update({
    time: t, open: last.open, high: last.high, low: last.low, close: last.close,
  });
  const theme = lwTheme();
  LW.volSeries.update({
    time: t, value: last.volume || 0,
    color: last.close >= last.open ? theme.upSoft : theme.downSoft,
  });
  // Keep overlays in sync on the last point.
  const overlayDefs = INDICATOR_DEFS.filter((d) => d.panel === "price");
  for (const def of overlayDefs) {
    const s = LW.overlaySeries[def.key];
    if (!s) continue;
    const vals = ChartState.indicators[def.key] || [];
    const v = vals[n - 1];
    if (v != null) s.update({ time: t, value: v });
  }
  if (LW.rsiSeries) {
    const r = (ChartState.indicators.rsi14 || [])[n - 1];
    if (r != null) LW.rsiSeries.update({ time: t, value: r });
  }
  const m = ChartState.indicators.macd;
  if (LW.macdLine && m) {
    const mv = (m.macdLine || [])[n - 1];
    const sv = (m.signalLine || [])[n - 1];
    const hv = (m.histogram || [])[n - 1];
    if (mv != null) LW.macdLine.update({ time: t, value: mv });
    if (sv != null) LW.macdSignal.update({ time: t, value: sv });
    if (hv != null) LW.macdHist.update({
      time: t, value: hv,
      color: hv >= 0 ? theme.upSoft : theme.downSoft,
    });
  }
}

/** Crosshair OHLC legend inside the LW price pane. */
function lwWireCrosshairLegend() {
  if (!LW || !LW.chart) return;
  const el = document.getElementById("chart-lw-legend");
  if (!el || el.dataset.wired) return;
  el.dataset.wired = "1";
  LW.chart.subscribeCrosshairMove((param) => {
    if (!param.time || !LW.candleSeries) { el.hidden = true; return; }
    const bar = param.seriesData.get(LW.candleSeries);
    if (!bar) { el.hidden = true; return; }
    const fmt = (x) => Number(x).toLocaleString("en-IN", { maximumFractionDigits: 2 });
    el.innerHTML = `O ${fmt(bar.open)}  H ${fmt(bar.high)}  L ${fmt(bar.low)}  C ${fmt(bar.close)}`;
    el.hidden = false;
  });
}

/** Theme hook + resize handling (called from chart.js on theme change). */
function lwOnThemeChange() {
  if (!LW || !LW.chart) return;
  const t = lwTheme();
  const digits = lwPriceDigits(ChartState.candles);
  LW.chart.applyOptions(lwBaseOptions(t, digits));
  LW.candleSeries.applyOptions({
    upColor: t.up, downColor: t.down, wickUpColor: t.up, wickDownColor: t.down,
  });
  if (LW.rsiChart) LW.rsiChart.applyOptions(lwBaseOptions(t, 2));
  if (LW.macdChart) LW.macdChart.applyOptions(lwBaseOptions(t, 2));
  lwRender(); // re-color histograms/overlays
}

window.addEventListener("resize", () => {
  if (!LW || !LW.chart) return;
  const host = document.getElementById("chart-lw");
  if (host && !host.hidden) {
    LW.chart.applyOptions({ width: host.clientWidth, height: host.clientHeight });
  }
  const r = document.getElementById("chart-lw-rsi");
  if (LW.rsiChart && r && !r.hidden) LW.rsiChart.applyOptions({ width: r.clientWidth, height: r.clientHeight });
  const m = document.getElementById("chart-lw-macd");
  if (LW.macdChart && m && !m.hidden) LW.macdChart.applyOptions({ width: m.clientWidth, height: m.clientHeight });
});
