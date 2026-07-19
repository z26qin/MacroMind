// Regime view: narrative-gap quadrant, country card, and ranking table.
window.MM = window.MM || {};
MM.views = MM.views || {};
MM.views.regime = (function () {
  function renderRegime() {
    const data = MM.state.regimeData;
    if (!data) {
      document.querySelector("#regime-view").innerHTML =
        `<div class="meta">Regime data not loaded.</div>`;
      return;
    }
    if (!MM.state.regimeSelected) {
      MM.state.regimeSelected = (data.regime_universe || [])[0] || null;
    }
    drawRegimeViews();
  }

  function drawRegimeViews() {
    drawRegimeQuadrant();
    renderRegimeTable();
    renderRegimeCard(MM.state.regimeSelected);
  }

  function drawRegimeQuadrant() {
    const regimeData = MM.state.regimeData;
    const color = MM.views.map.color;
    const svg = d3.select("#regime-quadrant-svg");
    const node = svg.node();
    const W = node.clientWidth || 460;
    const H = node.clientHeight || 340;
    const m = 36;
    svg.attr("viewBox", [0, 0, W, H]).selectAll("*").remove();

    const x = d3.scaleLinear().domain([-1, 1]).range([m, W - m]);
    const y = d3.scaleLinear().domain([-1, 1]).range([H - m, m]);

    // Opportunity zone: regime_score > narrative_score (below the y=x diagonal).
    svg.append("path")
      .attr("d", `M${x(-1)},${y(-1)} L${x(1)},${y(1)} L${x(1)},${y(-1)} Z`)
      .attr("fill", "var(--positive)").attr("opacity", 0.12);
    svg.append("line")
      .attr("x1", x(-1)).attr("y1", y(-1)).attr("x2", x(1)).attr("y2", y(1))
      .attr("stroke", "var(--amber)").attr("stroke-dasharray", "4 4");
    svg.append("line").attr("x1", x(-1)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(0)).attr("stroke", "var(--line)");
    svg.append("line").attr("x1", x(0)).attr("y1", y(-1)).attr("x2", x(0)).attr("y2", y(1)).attr("stroke", "var(--line)");

    const countries = regimeData.regime_universe.map(c => regimeData.countries[c]);
    const g = svg.selectAll("g.rg-dot").data(countries).enter().append("g")
      .attr("class", "rg-dot").style("cursor", "pointer")
      .attr("transform", d => `translate(${x(d.regime_score)},${y(d.narrative_score)})`)
      .on("click", (event, d) => {
        MM.state.regimeSelected = d.country;
        renderRegimeCard(d.country);
        highlightRegime();
      });
    g.append("circle").attr("r", 6).attr("fill", d => color(d.regime_score))
      .attr("stroke", "var(--text)").attr("stroke-width", 0.5);
    g.append("text").attr("x", 9).attr("dy", "0.32em").attr("font-size", 11)
      .attr("fill", "var(--text)").text(d => MM.i18n.display(d.country));

    svg.append("text").attr("x", W - m).attr("y", H - 10).attr("text-anchor", "end")
      .attr("font-size", 10).attr("fill", "var(--muted)").text("regime / data score →");
    svg.append("text").attr("x", 8).attr("y", m - 12)
      .attr("font-size", 10).attr("fill", "var(--muted)").text("↑ narrative re-rated");

    highlightRegime();
  }

  function highlightRegime() {
    d3.selectAll("#regime-quadrant-svg g.rg-dot circle")
      .attr("stroke-width", d => d.country === MM.state.regimeSelected ? 2.5 : 0.5);
    document.querySelectorAll("#regime-table tr[data-country]").forEach(tr => {
      tr.classList.toggle("selected-row", tr.dataset.country === MM.state.regimeSelected);
    });
  }

  function renderRegimeCard(country) {
    const U = MM.util;
    const regimeData = MM.state.regimeData;
    const color = U.makeTextScale();
    const c = regimeData && regimeData.countries[country];
    const card = document.querySelector("#regime-card");
    if (!c) { card.innerHTML = `<div class="meta">Select a country.</div>`; return; }
    const rowsHtml = obj => Object.entries(obj).map(([k, v]) =>
      `<div class="ca-row"><span>${U.escapeHtml(k.replace(/_/g, " "))}</span>`
      + `<span style="color:${color(v)}">${U.fmt(v)}</span></div>`).join("");
    const channels = rowsHtml(c.cross_asset_confirmation);
    const buckets = rowsHtml(c.buckets);
    const list = arr => arr.map(s => `<li>${U.escapeHtml(s)}</li>`).join("");
    const gate = regimeData.confirmation_min;
    const unconfirmedHint = c.verdict === "Unconfirmed" && gate != null
      ? `<div class="meta">Cross-asset confirmation ${U.fmt(c.confirmation_score)} is below the ${U.fmt(gate)} gate &mdash; setup not corroborated by markets.</div>`
      : "";
    card.innerHTML = `
      <div class="eyebrow">Regime card</div>
      <div class="panel-title">${U.escapeHtml(MM.i18n.display(c.country))} ${U.verdictBadgeHtml(c.regime_score)}</div>
      <div class="meta">Verdict: <strong>${U.escapeHtml(c.verdict)}</strong></div>
      ${unconfirmedHint}
      <div class="metric-grid">
        <div class="metric"><label>Regime</label><strong>${U.fmt(c.regime_score)}</strong></div>
        <div class="metric"><label>Narrative gap</label><strong>${U.fmt(c.narrative_gap)}</strong></div>
        <div class="metric"><label>Confirmation</label><strong>${U.fmt(c.confirmation_score)}</strong></div>
      </div>
      <div class="section-title">Structural buckets</div>
      <div class="ca-table">${buckets}</div>
      <div class="section-title">Drivers</div><ul class="rg-list">${list(c.drivers)}</ul>
      <div class="section-title">Best expressions</div><ul class="rg-list">${list(c.best_expressions)}</ul>
      <div class="section-title">Left-tail risks</div><ul class="rg-list">${list(c.left_tail_risks)}</ul>
      <div class="section-title">Cross-asset confirmation</div>
      <div class="ca-table">${channels}</div>
    `;
  }

  function renderRegimeTable() {
    const U = MM.util;
    const regimeData = MM.state.regimeData;
    const color = U.makeTextScale();
    const rows = regimeData.regime_universe
      .map(c => regimeData.countries[c])
      .slice()
      .sort((a, b) => b.narrative_gap - a.narrative_gap);
    const body = rows.map(c => `
      <tr data-country="${U.escapeHtml(c.country)}">
        <td>${U.escapeHtml(MM.i18n.display(c.country))}</td>
        <td class="num" style="color:${color(c.regime_score)}">${U.fmt(c.regime_score)}</td>
        <td class="num" style="color:${color(c.narrative_gap)}">${U.fmt(c.narrative_gap)}</td>
        <td class="num" style="color:${color(c.confirmation_score)}">${U.fmt(c.confirmation_score)}</td>
        <td>${U.escapeHtml(c.verdict)}</td>
      </tr>`).join("");
    document.querySelector("#regime-table").innerHTML = `
      <div class="section-title">Regime ranking (sorted by narrative gap)</div>
      <table class="rg-table">
        <thead><tr><th>Country</th><th>Regime</th><th>Narrative gap</th><th>Confirmation</th><th>Verdict</th></tr></thead>
        <tbody>${body}</tbody>
      </table>`;
    document.querySelectorAll("#regime-table tr[data-country]").forEach(tr => {
      tr.addEventListener("click", () => {
        MM.state.regimeSelected = tr.dataset.country;
        renderRegimeCard(MM.state.regimeSelected);
        highlightRegime();
        document.querySelector("#regime-card").scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    highlightRegime();
  }

  return { render: renderRegime };
})();
