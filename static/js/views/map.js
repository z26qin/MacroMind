// World map view: choropleth, tooltip, and the economy detail panel.
window.MM = window.MM || {};
MM.views = MM.views || {};
MM.views.map = (function () {
  const svg = d3.select("#map");
  const panel = document.querySelector("#panel");
  const tooltip = document.querySelector("#tooltip");

  // Map fill scale reads theme tokens so both themes stay in tune;
  // rebuilt via refreshTheme() when the user toggles light/dark.
  function makeColorScale() {
    const css = getComputedStyle(document.documentElement);
    return d3.scaleLinear()
      .domain([-1, 0, 1])
      .range([
        css.getPropertyValue("--negative").trim(),
        css.getPropertyValue("--scale-mid").trim(),
        css.getPropertyValue("--positive").trim(),
      ])
      .clamp(true);
  }
  let color = makeColorScale();

  function renderPanel(countryName) {
    const U = MM.util;
    const economyName = U.resolveEconomyName(countryName);
    const entry = economyName ? MM.state.economies[economyName] : null;

    if (!entry) {
      panel.innerHTML = `
        <div class="eyebrow">Map country</div>
        <div class="panel-title">${U.escapeHtml(MM.i18n.display(countryName))}</div>
        <div class="meta">No signal available.</div>
      `;
      return;
    }

    const selectedValue = U.signalValue(entry, MM.state.selectedView);
    const assets = Object.entries(entry.signals).map(([asset, item]) => `
      <div class="asset ${MM.state.selectedView === asset ? "highlight" : ""}">
        <div class="asset-head">
          <span>${U.assetLabels[asset]}</span>
          <span>${U.verdictBadgeHtml(item.final)} ${U.fmt(item.final)} ${U.convictionBadgeHtml(item.conviction)}</span>
        </div>
        <div class="bar-track">
          <div class="bar-zero"></div>
          <div class="bar" style="${U.barStyle(item.final)}"></div>
        </div>
        ${U.convictionLine(item.conviction)}
        <p class="driver-reason">${U.formatReason(item.driver)}</p>
        ${U.narrativeHtml(item.rag_analysis, item.rag_summary)}
        <details>
          <summary>Show math</summary>
          <p>Final ${U.fmt(item.final)} · deterministic ${U.fmt(item.deterministic)} · RAG ${U.fmt(item.rag)}</p>
          <div class="driver-grid">
            ${U.renderDriverList("Top positive", item.top_positive_drivers || [])}
            ${U.renderDriverList("Top negative", item.top_negative_drivers || [])}
          </div>
        </details>
      </div>
    `).join("");

    panel.innerHTML = `
      <div class="eyebrow">Map country</div>
      <div class="panel-title">${U.escapeHtml(MM.i18n.display(countryName))}</div>
      <div class="meta">Economy: ${U.escapeHtml(MM.i18n.display(entry.country))}<br>As of: ${U.escapeHtml(MM.state.snapshot.as_of || "Unknown")} · Methodology: ${U.escapeHtml(MM.state.snapshot.methodology_version || "Unknown")}</div>
      <div class="selected-signal">
        ${U.viewLabel(MM.state.selectedView)} signal ${U.verdictBadgeHtml(selectedValue)}
        <strong>${U.fmt(selectedValue)}</strong>
      </div>
      ${U.historyBlock(economyName, MM.state.selectedView)}
      <div class="section-title">Composite</div>
      <div class="metric-grid ${MM.state.selectedView === "composite" ? "highlight" : ""}">
        <div class="metric"><label>Deterministic</label><strong>${U.fmt(entry.composite.deterministic)}</strong></div>
        <div class="metric"><label>RAG</label><strong>${U.fmt(entry.composite.rag)}</strong></div>
        <div class="metric"><label>Final</label><strong>${U.fmt(entry.composite.final)}</strong> ${U.verdictBadgeHtml(entry.composite.final)}</div>
      </div>
      <div class="meta">Deterministic is the quantitative rule-based signal. RAG is the qualitative narrative overlay. Final is the blended signal.</div>
      <div class="section-title">Asset classes</div>
      ${assets}
    `;
  }

  function draw() {
    const U = MM.util;
    const node = svg.node();
    const width = node.clientWidth;
    const height = node.clientHeight;
    svg.attr("viewBox", [0, 0, width, height]);

    const projection = d3.geoNaturalEarth1().fitSize(
      [width, height],
      { type: "FeatureCollection", features: MM.state.countries }
    );
    const path = d3.geoPath(projection);

    const selection = svg.selectAll("path.country")
      .data(MM.state.countries, d => d.properties.name);

    selection.enter()
      .append("path")
      .attr("class", "country")
      .on("mousemove", (event, d) => {
        const name = d.properties.name;
        const economyName = U.resolveEconomyName(name);
        const entry = economyName ? MM.state.economies[economyName] : null;
        const value = U.signalValue(entry, MM.state.selectedView);
        tooltip.style.opacity = 1;
        tooltip.style.left = `${event.clientX}px`;
        tooltip.style.top = `${event.clientY}px`;
        const conviction = (entry && MM.state.selectedView !== "composite")
          ? entry.signals[MM.state.selectedView].conviction : null;
        const convBadge = U.convictionBadgeHtml(conviction);
        tooltip.innerHTML = `<strong>${U.escapeHtml(MM.i18n.display(name))}</strong><br>${economyName ? `Economy: ${U.escapeHtml(MM.i18n.display(economyName))}<br>${U.viewLabel(MM.state.selectedView)}: ${U.verdictBadgeHtml(value)} ${U.fmt(value)}${convBadge ? "<br>" + convBadge : ""}` : "No data"}`;
      })
      .on("mouseleave", () => { tooltip.style.opacity = 0; })
      .on("click", (event, d) => {
        MM.state.selectedCountry = d.properties.name;
        renderPanel(MM.state.selectedCountry);
        updateColors();
      })
      .merge(selection)
      .attr("data-country", d => d.properties.name)
      .attr("data-economy", d => U.resolveEconomyName(d.properties.name) || "")
      .attr("data-covered", d => U.resolveEconomyName(d.properties.name) ? "true" : "false")
      .attr("d", path);

    selection.exit().remove();
    updateColors();
  }

  function updateColors() {
    const U = MM.util;
    const noData = getComputedStyle(document.documentElement).getPropertyValue("--nodata");
    svg.selectAll("path.country")
      .attr("fill", d => {
        const economyName = U.resolveEconomyName(d.properties.name);
        const entry = economyName ? MM.state.economies[economyName] : null;
        const value = U.signalValue(entry, MM.state.selectedView);
        return value == null ? noData : MM.views.map.color(value);
      })
      .attr("data-signal", d => {
        const economyName = U.resolveEconomyName(d.properties.name);
        const entry = economyName ? MM.state.economies[economyName] : null;
        const value = U.signalValue(entry, MM.state.selectedView);
        return value == null ? "" : String(value);
      })
      .classed("selected", d => d.properties.name === MM.state.selectedCountry);
  }

  return {
    draw,
    updateColors,
    renderPanel,
    get color() { return color; },
    refreshTheme() { color = makeColorScale(); },
  };
})();
