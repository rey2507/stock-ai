// Paper-Trader — Stage 5 data-source badges.
// Renders source/status metadata (LIVE | STALE | SIMULATED | UNAVAILABLE | MIXED)
// as color-coded badges. Loaded before the page modules; pure helpers, no fetches.

const BADGE_DEFS = {
  live:       { label: "LIVE",      cls: "src-live" },
  derived:    { label: "DERIVED",   cls: "src-derived" },
  stale:      { label: "STALE",     cls: "src-stale" },
  simulated:  { label: "SIM",       cls: "src-sim" },
  mixed:      { label: "MIXED",     cls: "src-mixed" },
  unavailable:{ label: "UNAVAILABLE", cls: "src-unavail" },
  none:       { label: "—",         cls: "src-sim" },
};

function sourceBadge(status) {
  const def = BADGE_DEFS[status] || BADGE_DEFS.unavailable;
  return `<span class="src-badge ${def.cls}" title="data source: ${status}">${def.label}</span>`;
}

// Compact inline badge for table rows (chain rows, positions).
function inlineSourceBadge(status) {
  if (!status || status === "none") return "";
  return sourceBadge(status);
}
