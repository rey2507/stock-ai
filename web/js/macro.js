// Paper-Trader — Macro Factors page (Stage 16).
// Descriptive free-data dashboard + pre-market journal. No predictions.

const MACRO_GROUPS = [
  { key: "global",    label: "Global Markets" },
  { key: "us_fut",    label: "US Futures" },
  { key: "asia",      label: "Asian Markets" },
  { key: "india",     label: "India" },
  { key: "commodity", label: "Commodities" },
  { key: "rate",      label: "Rates & Dollar" },
];

const MACRO_SIGNAL_LABEL = {
  positive: "▲ positive",
  negative: "▼ negative",
  neutral: "● neutral",
  unclear: "? unclear",
  unavailable: "— unavailable",
};

function macroSignalClass(sig) {
  return `msig msig-${sig}`;
}

function fmtMacroValue(row) {
  if (row.status !== "live" || row.value == null) return "—";
  const v = Number(row.value);
  const digits = Math.abs(v) >= 1000 ? 2 : (Math.abs(v) >= 10 ? 2 : 4);
  return v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtMacroPct(row) {
  if (row.status !== "live" || row.change_pct == null) return "—";
  const v = Number(row.change_pct);
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function macroFactorRow(row) {
  const sig = macroRowSignal(row);
  return `
    <tr class="macro-row">
      <td class="m-name">${row.name}</td>
      <td class="m-val">${fmtMacroValue(row)}</td>
      <td class="m-prev">${row.previous != null ? fmtMacroValue({ ...row, value: row.previous }) : "—"}</td>
      <td class="m-chg ${Number(row.change_pct) > 0 ? "up" : Number(row.change_pct) < 0 ? "down" : ""}">${fmtMacroPct(row)}</td>
      <td>${macroSignalBadge(sig)}</td>
      <td class="m-ts muted" title="${row.note || ""}">${row.timestamp || "—"}</td>
      <td class="m-src muted">${row.source || "—"}</td>
    </tr>`;
}

function macroSignalBadge(sig) {
  return `<span class="${macroSignalClass(sig)}">${MACRO_SIGNAL_LABEL[sig] || sig}</span>`;
}

// Mirror of the backend's _signal(), so rows can be tinted without recompute.
function macroRowSignal(row) {
  if (row.status !== "live" || row.change_pct == null) return "unavailable";
  const UNCLEAR_IDS = new Set(["gold", "fii_dii", "events"]);
  const INVERSE = new Set(["brent", "us10y", "dxy", "usdinr"]); // rising = negative
  if (UNCLEAR_IDS.has(row.id)) return "unclear";
  const pct = Number(row.change_pct);
  if (Math.abs(pct) < 0.05) return "neutral";
  const score = INVERSE.has(row.id) ? -pct : pct;
  return score > 0 ? "positive" : "negative";
}

function renderMacro(snap) {
  // ---- summary counts
  const counts = snap.summary?.counts || {};
  setText("mc-pos", counts.positive ?? 0);
  setText("mc-neg", counts.negative ?? 0);
  setText("mc-neu", counts.neutral ?? 0);
  setText("mc-unk", counts.unclear ?? 0);
  setText("mc-off", counts.unavailable ?? 0);
  const contrib = snap.summary?.contributors || {};
  const parts = Object.entries(contrib)
    .filter(([, list]) => list.length)
    .map(([k, list]) => `${k}: ${list.join(", ")}`);
  const noteEl = document.getElementById("macro-counts-note");
  if (noteEl) noteEl.textContent = parts.length ? parts.join("  ·  ") : (snap.summary?.note || "");

  // ---- generated-at + staleness
  setText("macro-generated", `Updated ${snap.generated_at || ""}`);
  const staleEl = document.getElementById("macro-stale");
  if (staleEl) {
    const anyLive = (snap.factors || []).some((f) => f.status === "live");
    staleEl.hidden = anyLive;
  }

  // ---- grouped factor tables
  const root = document.getElementById("macro-sections");
  if (!root) return;
  const groups = [];
  for (const g of MACRO_GROUPS) {
    const rows = (snap.factors || []).filter((f) => f.group === g.key);
    if (g.key === "india") {
      const vix = snap.vix;
      if (vix) rows.push(vix);
    }
    if (!rows.length) continue;
    const body = rows.map(macroFactorRow).join("");
    groups.push(`
      <div class="card macro-card">
        <div class="macro-card-title">${g.label}</div>
        <table class="macro-table">
          <thead><tr>
            <th>Factor</th><th>Current</th><th>Previous</th><th>Change</th>
            <th>Signal</th><th>As of</th><th>Source</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>`);
  }

  // ---- FII/DII block
  const fd = snap.fii_dii;
  if (fd) {
    groups.push(renderFiiDii(fd));
  }

  // ---- events
  const ev = snap.events;
  if (ev) {
    const items = (ev.items || []).slice(0, 25);
    const rows = items.length
      ? items.map((e) => `
          <tr>
            <td class="m-ts">${e.date}</td>
            <td>${e.symbol}</td>
            <td>${e.company}</td>
            <td>${e.purpose}</td>
          </tr>`).join("")
      : `<tr><td colspan="4" class="muted">No upcoming events returned by the source.</td></tr>`;
    groups.push(`
      <div class="card macro-card">
        <div class="macro-card-title">Events — corporate actions &amp; announcements
          <span class="muted" style="font-weight:400;font-size:var(--text-xs);">
            (${ev.status === "live" ? "next 7 days" : "unavailable"})</span></div>
        <table class="macro-table">
          <thead><tr><th>Date</th><th>Symbol</th><th>Company</th><th>Purpose</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`);
  }

  root.innerHTML = groups.join("");
}

function renderFiiDii(fd) {
  const sig = fiiDiiSignal(fd);
  const val = fd.value || {};
  const part = (label, p) => {
    if (!p) return `<tr><td>${label}</td><td colspan="4" class="muted">— unavailable</td></tr>`;
    const net = p.index_fut_net;
    const cls = net > 0 ? "up" : net < 0 ? "down" : "";
    return `<tr>
      <td>${label}</td>
      <td>${p.index_fut_long.toLocaleString("en-IN")}</td>
      <td>${p.index_fut_short.toLocaleString("en-IN")}</td>
      <td class="${cls}">${net > 0 ? "+" : ""}${net.toLocaleString("en-IN")}</td>
      <td>${p.index_call_long != null ? p.index_call_long.toLocaleString("en-IN") : "—"}</td>
    </tr>`;
  };
  return `
    <div class="card macro-card">
      <div class="macro-card-title">Institutional Activity — FII / DII
        ${macroSignalBadge(sig)}
        <span class="muted" style="font-weight:400;font-size:var(--text-xs);">
          trade date ${fd.trade_date || "—"} (T+1 publication)</span></div>
      <table class="macro-table">
        <thead><tr>
          <th>Participant</th><th>Index Fut Long</th><th>Index Fut Short</th>
          <th>Net Futures</th><th>Index Call Long</th>
        </tr></thead>
        <tbody>
          ${part("FII", val.fii)}
          ${part("DII", val.dii)}
        </tbody>
      </table>
      <p class="muted" style="font-size:var(--text-xs);">Net futures stance is a
        descriptive proxy — not a buy/sell recommendation.</p>
    </div>`;
}

function fiiDiiSignal(fd) {
  if (fd.status !== "live" || !fd.value || !fd.value.fii) return "unavailable";
  const net = fd.value.fii.index_fut_net;
  if (net == null) return "unclear";
  if (net > 0) return "positive";
  if (net < 0) return "negative";
  return "neutral";
}

// ---------------------------------------------------------------- journal

async function refreshJournal() {
  try {
    const data = await API.macroJournal();
    renderJournalList(data);
  } catch (err) {
    console.error("journal load failed", err);
  }
}

function renderJournalList(data) {
  const statsEl = document.getElementById("journal-stats");
  if (statsEl && data.stats) {
    const s = data.stats;
    statsEl.innerHTML =
      `<span class="jstat ok">✔ ${s.correct} correct</span>` +
      `<span class="jstat bad">✘ ${s.wrong} wrong</span>` +
      `<span class="jstat">? ${s.unclear} unclear</span>` +
      `<span class="jstat muted">… ${s.pending} pending</span>`;
  }
  const list = document.getElementById("journal-list");
  if (!list) return;
  const entries = data.entries || [];
  if (!entries.length) {
    list.innerHTML = `<p class="muted">No journal entries yet — record your first view above.</p>`;
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  const rows = entries.map((e) => {
    const outcome = e.outcome === "correct" ? `<span class="jstat ok">✔ correct</span>`
      : e.outcome === "wrong" ? `<span class="jstat bad">✘ wrong</span>`
      : e.outcome === "unclear" ? `<span class="jstat">? unclear</span>`
      : `<span class="jstat muted">… awaiting open</span>`;
    const dots = "●".repeat(e.confidence) + "○".repeat(5 - e.confidence);
    return `
      <tr>
        <td class="m-ts">${e.trade_date}${e.trade_date === today ? ' <span class="jstat">today</span>' : ""}</td>
        <td><strong>${e.expected}</strong></td>
        <td title="confidence ${e.confidence}/5">${dots}</td>
        <td class="m-reasons" title="${escapeHtml(e.reasons || "")}">${escapeHtml(e.reasons || "") || "—"}</td>
        <td>${e.actual || "—"}</td>
        <td>${outcome}</td>
        <td><button class="btn small danger j-del" data-id="${e.id}" title="delete entry">✕</button></td>
      </tr>`;
  });
  list.innerHTML = `
    <table class="macro-table">
      <thead><tr>
        <th>Date</th><th>Expected</th><th>Conf.</th><th>Reasons</th>
        <th>Actual open</th><th>Result</th><th></th>
      </tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>`;
  list.querySelectorAll(".j-del").forEach((btn) =>
    btn.addEventListener("click", () => deleteJournalEntry(btn.dataset.id)));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// --------------------------------------------- NIFTY 50 technical verdict
// (moved from the Chart page, Stage 18 — same endpoint, unchanged math)

async function loadVerdict() {
  const badge = document.getElementById("verdict-badge");
  const checksEl = document.getElementById("verdict-checks");
  try {
    const v = await API.verdict("NIFTY");
    if (!badge || !checksEl) return;

    if (v.status === "unavailable") {
      badge.textContent = "— unavailable";
      badge.className = "msig msig-unavailable";
      checksEl.innerHTML = `<li class="muted">${escapeHtml(v.detail || "Market data unavailable")}</li>`;
      return;
    }

    const cls = v.verdict === "bullish" ? "msig-positive"
      : v.verdict === "bearish" ? "msig-negative"
      : v.verdict === "neutral" ? "msig-neutral" : "msig-unclear";
    const label = { bullish: "▲ Bullish", bearish: "▼ Bearish",
                    neutral: "● Neutral", unclear: "? Unclear" }[v.verdict] || v.verdict;
    badge.textContent = `${label}${v.confidence ? ` · ${v.confidence}%` : ""}`;
    badge.className = `msig ${cls}`;

    const fill = document.getElementById("verdict-conf-fill");
    if (fill) fill.style.width = `${v.confidence || 0}%`;
    const confLabel = document.getElementById("verdict-conf-label");
    if (confLabel) confLabel.textContent =
      `confidence ${v.confidence}% · ${v.candles_used || "?"} daily candles · via ${v.source || "?"}`;

    checksEl.innerHTML = (v.checks || []).map((c) => {
      const ccls = c.signal === "bullish" ? "vc-bull"
        : c.signal === "bearish" ? "vc-bear"
        : c.signal === "neutral" ? "vc-neut" : "vc-unk";
      return `<li class="${ccls}"><span class="vc-name">${c.name}</span>
        <span class="vc-detail">${escapeHtml(c.detail)}</span></li>`;
    }).join("");

    const line = document.getElementById("verdict-oneliner");
    if (line) line.textContent = v.one_liner || "";
    const disc = document.getElementById("verdict-disclaimer");
    if (disc) disc.textContent = v.disclaimer || "";
  } catch (err) {
    console.error("verdict load failed", err);
    if (badge) { badge.textContent = "— unavailable"; badge.className = "msig msig-unavailable"; }
  }
}

// --------------------------------------------- overall NIFTY 50 verdict

async function loadOverallVerdict() {
  const badge = document.getElementById("ov-badge");
  try {
    const v = await API.overallVerdict();
    if (!badge) return;
    if (v.status === "unavailable" && v.verdict === "unclear") {
      badge.textContent = "— unavailable";
      badge.className = "msig msig-unavailable";
      return;
    }
    const cls = v.verdict === "bullish" ? "msig-positive"
      : v.verdict === "bearish" ? "msig-negative"
      : v.verdict === "neutral" ? "msig-neutral" : "msig-unclear";
    const label = { bullish: "▲ Bullish", bearish: "▼ Bearish",
                    neutral: "● Neutral", unclear: "? Unclear" }[v.verdict] || v.verdict;
    badge.textContent = `${label}${v.confidence ? ` · ${v.confidence}%` : ""}`;
    badge.className = `msig ${cls}`;

    const fill = document.getElementById("ov-conf-fill");
    if (fill) fill.style.width = `${v.confidence || 0}%`;
    const confLabel = document.getElementById("ov-conf-label");
    if (confLabel) confLabel.textContent =
      `combined confidence ${v.confidence}% · technicals ${v.technical.decided} checks + ${v.macro.decided} macro factors`;

    const techEl = document.getElementById("ov-tech");
    if (techEl) {
      const t = v.technical;
      techEl.textContent = t.verdict === "unclear"
        ? "unavailable"
        : `${t.verdict} · ${t.confidence}% · ${t.decided} checks${t.source ? ` · ${t.source}` : ""}`;
    }
    const macroEl = document.getElementById("ov-macro");
    if (macroEl) {
      const m = v.macro;
      macroEl.textContent = m.verdict === "unclear"
        ? "unavailable"
        : `${m.verdict} · ${m.confidence}% · ${m.decided} factors`;
    }
    const line = document.getElementById("ov-oneliner");
    if (line) line.textContent = v.one_liner || "";
  } catch (err) {
    console.error("overall verdict load failed", err);
    if (badge) { badge.textContent = "— unavailable"; badge.className = "msig msig-unavailable"; }
  }
}

async function deleteJournalEntry(id) {
  try {
    await API.macroJournalDelete(id);
    await refreshJournal();
  } catch (err) {
    alert(err.message || "Delete failed");
  }
}

async function submitJournal(e) {
  e.preventDefault();
  const btn = document.getElementById("j-save");
  const dateEl = document.getElementById("j-date");
  const payload = {
    trade_date: dateEl ? dateEl.value : new Date().toISOString().slice(0, 10),
    expected: document.getElementById("j-expected").value,
    confidence: Number(document.getElementById("j-confidence").value) || 3,
    reasons: document.getElementById("j-reasons").value.trim(),
  };
  if (!payload.trade_date) { alert("Pick a date"); return; }
  btn.disabled = true;
  try {
    await API.macroJournalSave(payload);
    document.getElementById("j-reasons").value = "";
    await refreshJournal();
  } catch (err) {
    alert(err.message || "Save failed");
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- refresh

async function refreshMacro() {
  if (App.route !== "macro") return;
  try {
    const snap = await API.macro();
    renderMacro(snap);
    loadVerdict();
    loadOverallVerdict();
    await refreshJournal();
  } catch (err) {
    console.error("macro refresh failed", err);
    const root = document.getElementById("macro-sections");
    if (root) root.innerHTML =
      `<div class="card macro-card"><p class="muted">Macro data unavailable: ${escapeHtml(err.message || "error")}</p></div>`;
  }
}

function initMacroPage() {
  const form = document.getElementById("journal-form");
  if (form) form.addEventListener("submit", submitJournal);
  const dateInput = document.getElementById("j-date");
  if (dateInput) dateInput.value = new Date().toISOString().slice(0, 10);
}
