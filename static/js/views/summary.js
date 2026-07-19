// Summary view: cross-sectional overview — where the edge is right now.
// Left: regime opportunity board (gap-ranked) + signal composite leaderboard.
// Right: inspector with verdict inputs / country panorama.
window.MM = window.MM || {};
MM.views = MM.views || {};
MM.views.summary = (function () {
  const state = {
    selected: null, // {kind: "opp"|"signal", name}
  };

  function fmt(v) {
    if (typeof v !== "number") return String(v);
    return (v > 0 ? "+" : "") + v.toFixed(2);
  }

  // Same single ranking rule as snapshot_diff.opportunity_ranking:
  // narrative_gap desc, confirmation_score breaks ties.
  function opportunityRows() {
    return Object.values(((MM.state.regimeData || {}).countries) || {})
      .slice()
      .sort((a, b) => (b.narrative_gap - a.narrative_gap) ||
                      (b.confirmation_score - a.confirmation_score));
  }

  function signalRows() {
    const economies = MM.state.economies || {};
    return (MM.state.snapshot.universe || [])
      .map(name => ({ name, entry: economies[name] }))
      .filter(row => row.entry)
      .sort((a, b) =>
        ((b.entry.composite || {}).final ?? -9) - ((a.entry.composite || {}).final ?? -9));
  }

  function render() {
    renderOpps();
    renderSignals();
    renderInspector();
  }

  function renderOpps() {
    const U = MM.util;
    const el = document.querySelector("#summary-opps");
    el.innerHTML = opportunityRows().map((c, i) => {
      const warn = c.verdict === "Unconfirmed" ? " ⚠" : "";
      const sel = state.selected?.kind === "opp" && state.selected.name === c.country ? " selected" : "";
      return `<div class="opp-row${sel}" data-country="${U.escapeHtml(c.country)}">
        <span class="num">${i + 1}</span>
        <span>${U.escapeHtml(MM.i18n.display(c.country))} <span style="color:var(--muted)">${U.escapeHtml(c.verdict)}${warn}</span></span>
        <span class="num">gap ${fmt(c.narrative_gap)} · conf ${fmt(c.confirmation_score)}</span></div>`;
    }).join("");
    el.querySelectorAll(".opp-row").forEach(row => row.addEventListener("click", () => {
      state.selected = { kind: "opp", name: row.dataset.country };
      render();
    }));
  }

  function renderSignals() {
    const U = MM.util;
    const el = document.querySelector("#summary-signals");
    el.innerHTML = signalRows().map((row, i) => {
      const value = (row.entry.composite || {}).final;
      const sel = state.selected?.kind === "signal" && state.selected.name === row.name ? " selected" : "";
      return `<div class="opp-row${sel}" data-country="${U.escapeHtml(row.name)}">
        <span class="num">${i + 1}</span>
        <span>${U.escapeHtml(MM.i18n.display(row.name))} ${U.verdictBadgeHtml(value)}</span>
        <span class="num">${U.fmt(value)}</span></div>`;
    }).join("");
    el.querySelectorAll(".opp-row").forEach(row => row.addEventListener("click", () => {
      state.selected = { kind: "signal", name: row.dataset.country };
      render();
    }));
  }

  function kv(label, value) {
    return `<div class="inspector-kv"><span>${label}</span><span class="num">${value}</span></div>`;
  }

  function renderInspector() {
    const U = MM.util;
    const el = document.querySelector("#summary-inspector");
    const sel = state.selected;
    if (!sel) {
      el.innerHTML = `<div class="empty-state">点击任一排名行查看详情</div>`;
      return;
    }
    if (sel.kind === "opp") {
      const c = ((MM.state.regimeData || {}).countries || {})[sel.name];
      if (!c) { el.innerHTML = ""; return; }
      let html = `<div class="inspector-title">${U.escapeHtml(MM.i18n.display(c.country))} — ${U.escapeHtml(c.verdict)}</div>`;
      html += kv("regime_score", fmt(c.regime_score))
        + kv("narrative_gap", fmt(c.narrative_gap))
        + kv("confirmation", fmt(c.confirmation_score));
      const channels = c.cross_asset_confirmation || {};
      html += `<div class="inspector-title" style="margin-top:12px">Cross-asset</div>`
        + Object.entries(channels).map(([k, v]) =>
            kv(U.escapeHtml(k.replace(/_/g, " ")), fmt(v))).join("");
      const expressions = (c.best_expressions || []).slice(0, 3);
      if (expressions.length) {
        html += `<div class="inspector-title" style="margin-top:12px">Best expressions</div>`
          + expressions.map(e =>
              `<div class="inspector-kv"><span>→</span><span>${U.escapeHtml(String(e))}</span></div>`).join("");
      }
      const risks = (c.left_tail_risks || []).slice(0, 3);
      if (risks.length) {
        html += `<div class="inspector-title" style="margin-top:12px">Left-tail risks</div>`
          + risks.map(r =>
              `<div class="inspector-kv"><span>⚠</span><span>${U.escapeHtml(String(r))}</span></div>`).join("");
      }
      el.innerHTML = html;
    } else {
      const entry = (MM.state.economies || {})[sel.name];
      if (!entry) { el.innerHTML = ""; return; }
      let html = `<div class="inspector-title">${U.escapeHtml(MM.i18n.display(sel.name))} — composite ${U.fmt((entry.composite || {}).final)}</div>`;
      html += kv("deterministic", fmt((entry.composite || {}).deterministic))
        + kv("rag", fmt((entry.composite || {}).rag));
      html += `<div class="inspector-title" style="margin-top:12px">Asset classes</div>`;
      ["fx", "rates", "equity", "real_estate"].forEach(asset => {
        const cell = (entry.signals || {})[asset] || {};
        html += kv(U.assetLabels[asset], `${fmt(cell.final)}`);
      });
      const drivers = ["fx", "rates", "equity", "real_estate"]
        .map(a => (entry.signals || {})[a])
        .filter(cell => cell && cell.driver)
        .slice(0, 2);
      if (drivers.length) {
        html += `<div class="inspector-title" style="margin-top:12px">Drivers</div>`
          + drivers.map(cell =>
              `<div class="inspector-kv"><span>·</span><span>${U.escapeHtml(cell.driver)}</span></div>`).join("");
      }
      el.innerHTML = html;
    }
  }

  return { render, state };
})();
