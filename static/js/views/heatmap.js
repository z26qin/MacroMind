// Heatmap view: economy x asset-class grid.
window.MM = window.MM || {};
MM.views = MM.views || {};
MM.views.heatmap = (function () {
  const HEATMAP_ROWS = [
    { key: "composite", label: "Composite" },
    { key: "fx", label: "FX" },
    { key: "rates", label: "Rates" },
    { key: "equity", label: "Equity" },
    { key: "real_estate", label: "Real estate" },
  ];

  function heatmapValue(entry, rowKey) {
    if (!entry) return null;
    return rowKey === "composite" ? entry.composite.final : entry.signals[rowKey].final;
  }

  function highlightHeatmapColumn(economy) {
    document.querySelectorAll("#heatmap .hm-cell").forEach(cell => {
      cell.classList.toggle("selected-col", !!economy && cell.dataset.economy === economy);
    });
  }

  function drawHeatmap() {
    const U = MM.util;
    const economies = MM.state.economies;
    const universe = MM.state.snapshot.universe || [];
    const grid = document.querySelector("#heatmap");
    grid.style.gridTemplateColumns = `120px repeat(${universe.length}, minmax(64px, 1fr))`;

    let html = `<div class="hm-corner"></div>`;
    for (const economy of universe) {
      const iso3 = economies[economy] ? economies[economy].iso3 : economy;
      html += `<div class="hm-colhead" title="${U.escapeHtml(economy)}">${U.escapeHtml(iso3)}</div>`;
    }
    for (const row of HEATMAP_ROWS) {
      html += `<div class="hm-rowhead">${U.escapeHtml(row.label)}</div>`;
      for (const economy of universe) {
        const value = heatmapValue(economies[economy], row.key);
        const verdict = U.signalVerdict(value);
        const bg = value == null ? "var(--nodata)" : MM.views.map.color(value);
        const text = value == null ? "&ndash;" : `${verdict.glyph} ${U.fmt(value)}`;
        const conviction = (row.key !== "composite" && economies[economy])
          ? economies[economy].signals[row.key].conviction : null;
        const convText = (conviction && conviction.band !== "na" && U.convictionMeta[conviction.band])
          ? ` · ${U.convictionMeta[conviction.band].label}` : "";
        const title = `${economy} · ${row.label}: ${verdict.label}${value == null ? "" : " " + U.fmt(value)}${convText}`;
        html += `<div class="hm-cell" data-economy="${U.escapeHtml(economy)}" data-asset="${row.key}"`
          + ` style="background:${bg}" title="${U.escapeHtml(title)}">${text}</div>`;
      }
    }
    grid.innerHTML = html;

    grid.querySelectorAll(".hm-cell").forEach(cell => {
      cell.addEventListener("click", () => {
        MM.state.selectedCountry = cell.dataset.economy;
        MM.views.map.renderPanel(MM.state.selectedCountry);
        highlightHeatmapColumn(MM.state.selectedCountry);
      });
    });
    highlightHeatmapColumn(MM.state.selectedCountry);
  }

  return { draw: drawHeatmap, highlight: highlightHeatmapColumn };
})();
