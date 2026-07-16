// Shared state, formatting utilities, tab routing, and bootstrap.
// Load order: i18n.js, api.js, views/*.js, then this file (last).
window.MM = window.MM || {};

MM.state = {
  selectedView: "composite",
  selectedTab: "briefing",
  selectedCountry: null,   // map/heatmap selection
  countries: [],           // topojson features
  snapshot: { economies: {} },
  economies: {},
  historyData: {},
  regimeData: null,
  regimeSelected: null,
};

(function () {
  const EURO_AREA_COUNTRIES = new Set([
    "Germany",
    "France",
    "Italy",
    "Spain",
    "Netherlands",
    "Belgium",
    "Austria",
    "Portugal",
    "Greece",
    "Finland",
    "Ireland"
  ]);

  const assetLabels = {
    fx: "FX",
    rates: "Rates",
    equity: "Equity",
    real_estate: "Real estate"
  };

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  // Escape the driver sentence, then bold the key phrase after "led by"
  // (e.g. "led by <strong>policy surprise</strong>") so it's easy to spot.
  function formatReason(value) {
    return escapeHtml(value).replace(
      /led by (.+?)( and | with |,|\.|$)/i,
      (match, phrase, tail) => `led by <strong>${phrase}</strong>${tail}`
    );
  }

  function resolveEconomyName(mapCountryName) {
    const economies = MM.state.economies;
    if (EURO_AREA_COUNTRIES.has(mapCountryName)) return economies["Euro Area"] ? "Euro Area" : null;
    return Object.prototype.hasOwnProperty.call(economies, mapCountryName) ? mapCountryName : null;
  }

  function signalValue(entry, view) {
    if (!entry) return null;
    return view === "composite" ? entry.composite.final : entry.signals[view].final;
  }

  function fmt(value) {
    return value == null ? "No data" : d3.format("+.2f")(value);
  }

  function signalVerdict(value) {
    if (value == null) return { label: "No data", glyph: "", cls: "neu" };
    if (Math.abs(value) < 0.10) return { label: "Neutral", glyph: "▬", cls: "neu" };
    if (value >= 0.10) return { label: "Bullish", glyph: "▲", cls: "pos" };
    return { label: "Bearish", glyph: "▼", cls: "neg" };
  }

  function verdictBadgeHtml(value) {
    const v = signalVerdict(value);
    const glyph = v.glyph ? v.glyph + " " : "";
    return `<span class="verdict-badge badge-${v.cls}">${glyph}${escapeHtml(v.label)}</span>`;
  }

  const convictionMeta = {
    high: { label: "High conviction", glyph: "●", cls: "conv-high" },
    medium: { label: "Medium conviction", glyph: "◐", cls: "conv-medium" },
    low: { label: "Low conviction", glyph: "○", cls: "conv-low" },
  };

  function convictionBadgeHtml(conviction) {
    if (!conviction || conviction.band === "na") return "";
    const meta = convictionMeta[conviction.band];
    if (!meta) return "";
    return `<span class="conv-badge ${meta.cls}">${meta.glyph} ${escapeHtml(meta.label)}</span>`;
  }

  function convictionLine(conviction) {
    if (!conviction || conviction.band === "na") return "";
    const nl = conviction.net_lean;
    let breadth;
    if (nl >= 0.60) breadth = "drivers broadly support";
    else if (nl >= 0.20) breadth = "drivers mixed";
    else breadth = "drivers lean against the call";
    let concentration = "";
    // 0.50 is the display threshold for the "leans on" note; it is distinct
    // from the 0.60 top_driver_share cutoff that drives the Low band in
    // signal_engine._conviction_band. Keep them independent on purpose.
    if (conviction.top_driver_share > 0.50 && conviction.top_driver) {
      const pct = Math.round(conviction.top_driver_share * 100);
      concentration = ` · leans on ${escapeHtml(conviction.top_driver)} (${pct}%)`;
    }
    const narrative = {
      agrees: " · narrative agrees",
      disagrees: " · narrative disagrees",
      no_view: " · no narrative view",
    }[conviction.narrative] || "";
    return `<p class="conv-line">${breadth}${concentration}${narrative}</p>`;
  }

  function viewLabel(view) {
    return view === "composite" ? "Composite" : assetLabels[view];
  }

  function barStyle(value) {
    const pct = Math.abs(value) * 50;
    const left = value >= 0 ? 50 : 50 - pct;
    return `left:${left}%;width:${pct}%;background:${MM.views.map.color(value)};`;
  }

  function renderDriverList(title, drivers) {
    const rows = drivers.length
      ? drivers.map(item => `
        <div class="driver-item">
          <span>${escapeHtml(item.feature)}</span>
          <span>${fmt(item.contribution)}</span>
        </div>
      `).join("")
      : `<div class="driver-item"><span>None</span><span>${fmt(0)}</span></div>`;

    return `
      <div class="driver-list">
        <strong>${title}</strong>
        ${rows}
      </div>
    `;
  }

  function citationHref(uri) {
    const value = String(uri || "");
    if (/^https?:\/\//i.test(value)) return value;
    if (value.startsWith("documents/")) return `/${value}`;
    return "#";
  }

  function narrativeHtml(analysis, fallback) {
    if (!analysis) return `<p>${escapeHtml(fallback || "No narrative analysis.")}</p>`;
    if (analysis.direction === "no_view") {
      return `<div class="narrative-card"><strong>No narrative view</strong>`
        + `<div class="factor-list">No point-in-time evidence for this asset and horizon.</div></div>`;
    }
    const positive = (analysis.positive_factors || []).map(escapeHtml).join(", ") || "none";
    const negative = (analysis.negative_factors || []).map(escapeHtml).join(", ") || "none";
    const citations = (analysis.citations || []).map(citation => `
      <div class="citation">
        <a href="${escapeHtml(citationHref(citation.source_uri))}" target="_blank" rel="noopener noreferrer">${escapeHtml(citation.title)}</a>
        <div class="citation-meta">${escapeHtml(citation.source)} · event ${escapeHtml(citation.event_time)} · observed ${escapeHtml(citation.observed_at)} · rev ${escapeHtml(citation.revision)} · vintage ${escapeHtml(citation.vintage)}</div>
        <div class="citation-excerpt">${escapeHtml(citation.excerpt)}</div>
      </div>
    `).join("");
    return `<div class="narrative-card">
      <div class="narrative-head"><strong>${escapeHtml(analysis.direction)} · ${escapeHtml(analysis.horizon)}</strong><span>${Math.round(analysis.confidence * 100)}% confidence</span></div>
      <div class="factor-list">Positive: ${positive}<br>Negative: ${negative}</div>
      <details><summary>${escapeHtml(String(analysis.evidence_count))} cited evidence item(s)</summary>${citations}</details>
    </div>`;
  }

  function sparkline(series) {
    if (!series || series.length < 2) return "";
    const w = 220, h = 44, pad = 4, n = series.length;
    const x = i => pad + (i * (w - 2 * pad)) / (n - 1);
    const y = v => pad + (1 - (Math.max(-1, Math.min(1, v)) + 1) / 2) * (h - 2 * pad);
    const points = series.map((d, i) => `${x(i).toFixed(1)},${y(d.value).toFixed(1)}`).join(" ");
    const last = series[n - 1].value;
    const cls = last >= 0 ? "spark-pos" : "spark-neg";
    const zeroY = y(0).toFixed(1);
    return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-label="history sparkline">`
      + `<line class="spark-zero" x1="${pad}" y1="${zeroY}" x2="${w - pad}" y2="${zeroY}"></line>`
      + `<polyline class="${cls}" points="${points}"></polyline>`
      + `<circle class="${cls}" cx="${x(n - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="2.4"></circle>`
      + `</svg>`;
  }

  // Text color scale for numbers on panel backgrounds. Unlike the map fill
  // scale (whose zero point is a dark/neutral surface color), zero here maps
  // to --muted so near-zero values stay readable as text.
  function makeTextScale() {
    const css = getComputedStyle(document.documentElement);
    return d3.scaleLinear()
      .domain([-1, 0, 1])
      .range([
        css.getPropertyValue("--negative").trim(),
        css.getPropertyValue("--muted").trim(),
        css.getPropertyValue("--positive").trim(),
      ])
      .clamp(true);
  }

  function historyBlock(economyName, view) {
    const series = (MM.state.historyData[economyName] || {})[view] || [];
    if (series.length < 2) return "";
    const first = series[0], last = series[series.length - 1];
    return `<div class="section-title">History · ${escapeHtml(String(series.length))} snapshots</div>`
      + sparkline(series)
      + `<div class="meta">${viewLabel(view)} final: ${fmt(first.value)} (${escapeHtml(first.date)}) &rarr; ${fmt(last.value)} (${escapeHtml(last.date)})</div>`;
  }

  MM.util = {
    EURO_AREA_COUNTRIES,
    assetLabels,
    escapeHtml,
    formatReason,
    resolveEconomyName,
    signalValue,
    fmt,
    signalVerdict,
    verdictBadgeHtml,
    convictionMeta,
    convictionBadgeHtml,
    convictionLine,
    viewLabel,
    barStyle,
    renderDriverList,
    citationHref,
    narrativeHtml,
    sparkline,
    historyBlock,
    makeTextScale,
  };
})();

MM.setTab = function (tab) {
  MM.state.selectedTab = tab;
  const fullWidth = tab === "regime" || tab === "guide" || tab === "briefing";
  document.querySelector("#briefing-view").hidden = tab !== "briefing";
  document.querySelector("#map-view").hidden = tab !== "map";
  document.querySelector("#heatmap-view").hidden = tab !== "heatmap";
  document.querySelector("#regime-view").hidden = tab !== "regime";
  document.querySelector("#guide-view").hidden = tab !== "guide";
  document.querySelector("#asset-toggles").hidden = tab !== "map";
  document.querySelector("#panel").hidden = fullWidth;
  document.querySelector("main").classList.toggle("regime-full", fullWidth);
  document.querySelectorAll("button[data-tab]").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === tab));
  if (tab === "briefing") MM.views.briefing.load();
  if (tab === "map") MM.views.map.draw();
  if (tab === "heatmap") MM.views.heatmap.draw();
  if (tab === "regime") MM.views.regime.render();
};

window.macroDashboardDebug = {
  get selectedView() { return MM.state.selectedView; },
  get selectedCountry() { return MM.state.selectedCountry; },
  get selectedTab() { return MM.state.selectedTab; },
  get regimeCountry() { return MM.state.regimeSelected; },
  get universe() { return MM.state.snapshot.universe || []; },
  resolveEconomyName: (name) => MM.util.resolveEconomyName(name),
  signalVerdict: (value) => MM.util.signalVerdict(value),
};

window.addEventListener("DOMContentLoaded", () => {
  // Theme toggle: dark is the default; "light" is stamped on <html>.
  const savedTheme = localStorage.getItem("mmTheme");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  document.querySelector("#theme-toggle")?.addEventListener("click", () => {
    const root = document.documentElement;
    const next = root.dataset.theme === "light" ? "" : "light";
    if (next) root.dataset.theme = next; else delete root.dataset.theme;
    localStorage.setItem("mmTheme", next);
    MM.views.map.refreshTheme();
    MM.setTab(MM.state.selectedTab);
    if (MM.state.selectedCountry) MM.views.map.renderPanel(MM.state.selectedCountry);
  });

  document.querySelectorAll("button[data-view]").forEach(button => {
    button.addEventListener("click", () => {
      MM.state.selectedView = button.dataset.view;
      document.querySelectorAll("button[data-view]").forEach(item =>
        item.classList.toggle("active", item === button));
      MM.views.map.updateColors();
      if (MM.state.selectedCountry) MM.views.map.renderPanel(MM.state.selectedCountry);
    });
  });
  document.querySelectorAll("button[data-tab]").forEach(button =>
    button.addEventListener("click", () => MM.setTab(button.dataset.tab)));

  Promise.all([
    d3.json("https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json"),
    MM.api.getSignals(),
    MM.api.getRegime(),
  ]).then(([world, signalData, regimeData]) => {
    MM.state.snapshot = signalData;
    MM.state.economies = signalData.economies || {};
    MM.state.regimeData = regimeData;
    MM.state.countries = topojson.feature(world, world.objects.countries).features;
    MM.setTab(MM.state.selectedTab); // land on briefing with data ready
    window.addEventListener("resize", () => {
      if (MM.state.selectedTab === "map") MM.views.map.draw();
    });
  }).catch(error => {
    document.querySelector("#panel").innerHTML =
      `<div class="eyebrow">Load error</div><div class="panel-title">Dashboard failed to load</div><div class="meta">${MM.util.escapeHtml(error)}</div>`;
  });

  MM.api.getHistory().then(data => {
    MM.state.historyData = (data && data.history) || {};
    if (MM.state.selectedCountry) MM.views.map.renderPanel(MM.state.selectedCountry);
  }).catch(() => { /* history is optional; dashboard works without it */ });
});
