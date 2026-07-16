// Briefing view: what changed since the last snapshot, where the edge is,
// and why — country rail | changes feed + opportunity board | inspector.
window.MM = window.MM || {};
MM.views = MM.views || {};
MM.views.briefing = (function () {
  const state = {
    changes: null,        // /api/changes response
    railFilter: null,     // selected country (English data key) or null
    inspector: null,      // {mode: "change"|"opp"|"country", payload}
    minorOpen: false,
  };

  const LEVEL_ICON = { 1: "▲", 2: "◆", 3: "◆", 4: "·" };
  const NEGATIVE_VERDICTS = /Deteriorating|Unconfirmed/;

  function fmt(v) {
    if (typeof v !== "number") return String(v);
    return (v > 0 ? "+" : "") + v.toFixed(2);
  }

  function levelClass(c) {
    if (c.level === 1) {
      const negative = c.kind === "verdict_flip"
        ? NEGATIVE_VERDICTS.test(String(c.to))
        : (typeof c.to === "number" && c.to < c.from);
      return negative ? "change-l1 neg" : "change-l1";
    }
    return `change-l${c.level}`;
  }

  // Display line built from structured fields (Chinese country names);
  // the backend English headline is the fallback for unknown kinds.
  function changeText(c) {
    const name = MM.i18n.display(c.country);
    const T = {
      verdict_flip: () => `${name} verdict ${c.from} → ${c.to}`,
      direction_flip: () => `${name} ${c.field} 方向翻转 ${fmt(c.from)} → ${fmt(c.to)}`,
      opp_rank_move: () => `${name} 机会榜排名 ${c.from} → ${c.to}`,
      asset_rank_move: () => `${name} ${c.field} 排名 ${c.from} → ${c.to}`,
      signal_drift: () => `${name} ${c.field} ${fmt(c.from)} → ${fmt(c.to)}`,
      regime_drift: () => `${name} ${c.field} ${fmt(c.from)} → ${fmt(c.to)}`,
      evidence_change: () => `${name} ${c.field} evidence 更新`,
      provenance_flip: () => `${name} ${c.field} 数据源 ${String(c.from).split(":")[0]} → ${String(c.to).split(":")[0]}`,
      coverage_change: () => `${name} 覆盖范围变化`,
    };
    return (T[c.kind] || (() => c.headline))();
  }

  function unionCountries() {
    const signal = Object.keys(MM.state.economies || {});
    const regime = Object.keys((MM.state.regimeData || {}).countries || {});
    return [...new Set([...signal, ...regime])].sort();
  }

  function coverage(name) {
    const s = (MM.state.economies || {})[name] ? "S" : "";
    const r = ((MM.state.regimeData || {}).countries || {})[name] ? "R" : "";
    return s + r;
  }

  function countryLevel(name) {
    const rows = ((state.changes || {}).changes || []).filter(c => c.country === name);
    return rows.length ? Math.min(...rows.map(c => c.level)) : null;
  }

  // Same single ranking rule as snapshot_diff.opportunity_ranking:
  // narrative_gap desc, confirmation_score breaks ties.
  function opportunityRows() {
    return Object.values(((MM.state.regimeData || {}).countries) || {})
      .slice()
      .sort((a, b) => (b.narrative_gap - a.narrative_gap) ||
                      (b.confirmation_score - a.confirmation_score));
  }

  function render() {
    renderRail();
    renderChanges();
    renderOpps();
    renderInspector();
  }

  function renderRail() {
    const U = MM.util;
    const el = document.querySelector("#briefing-rail");
    el.innerHTML = unionCountries().map(name => {
      const level = countryLevel(name);
      const dot = level === 1 ? "var(--positive)" : level ? "var(--amber)" : "var(--line)";
      const sel = state.railFilter === name ? " selected" : "";
      return `<div class="rail-row${sel}" data-country="${U.escapeHtml(name)}">
        <span class="rail-dot" style="background:${dot}"></span>
        <span>${U.escapeHtml(MM.i18n.display(name))}</span>
        <span class="rail-cov">${coverage(name)}</span></div>`;
    }).join("");
    el.querySelectorAll(".rail-row").forEach(row => row.addEventListener("click", () => {
      const name = row.dataset.country;
      state.railFilter = state.railFilter === name ? null : name;
      state.inspector = state.railFilter ? { mode: "country", payload: name } : null;
      render();
    }));
  }

  function renderChanges() {
    const U = MM.util;
    const el = document.querySelector("#briefing-changes");
    const data = state.changes;
    if (!data) { el.innerHTML = `<div class="empty-state">加载中…</div>`; return; }
    if (data.insufficient) {
      el.innerHTML = `<div class="empty-state">需要至少两份归档快照 — 点 ▶ RUN 生成</div>`;
      return;
    }
    let rows = data.changes;
    if (state.railFilter) rows = rows.filter(c => c.country === state.railFilter);
    const items = rows.map((c, i) => {
      const sel = state.inspector?.mode === "change" && state.inspector.payload === c ? " selected" : "";
      const num = (c.kind === "signal_drift" || c.kind === "regime_drift" || c.kind === "direction_flip")
        ? `<span class="num">${fmt(c.to - c.from)}</span>` : "";
      return `<div class="change-row ${levelClass(c)}${sel}" data-idx="${i}">
        <span>${LEVEL_ICON[c.level]}</span><span>${U.escapeHtml(changeText(c))}</span>${num}</div>`;
    });
    const notes = (data.notes || []).map(n =>
      `<div class="empty-state">⚠ ${U.escapeHtml(n)}</div>`);
    const minor = `<div class="minor-fold">${data.minor_count ? `${data.minor_count} 项微小变动 · ` : ""}${data.unchanged_count} 个国家未变</div>`;
    el.innerHTML = notes.join("")
      + (items.join("") || `<div class="empty-state">本期无显著变化</div>`)
      + minor;
    el.querySelectorAll(".change-row").forEach(row => row.addEventListener("click", () => {
      state.inspector = { mode: "change", payload: rows[Number(row.dataset.idx)] };
      render();
    }));
  }

  function renderOpps() {
    const U = MM.util;
    const el = document.querySelector("#briefing-opps");
    el.innerHTML = opportunityRows().map((c, i) => {
      const warn = c.verdict === "Unconfirmed" ? " ⚠" : "";
      const sel = state.inspector?.mode === "opp" && state.inspector.payload === c.country ? " selected" : "";
      return `<div class="opp-row${sel}" data-country="${U.escapeHtml(c.country)}">
        <span class="num">${i + 1}</span>
        <span>${U.escapeHtml(MM.i18n.display(c.country))} <span style="color:var(--muted)">${U.escapeHtml(c.verdict)}${warn}</span></span>
        <span class="num">gap ${fmt(c.narrative_gap)} · conf ${fmt(c.confirmation_score)}</span></div>`;
    }).join("");
    el.querySelectorAll(".opp-row").forEach(row => row.addEventListener("click", () => {
      state.inspector = { mode: "opp", payload: row.dataset.country };
      render();
    }));
  }

  function kv(label, value) {
    return `<div class="inspector-kv"><span>${label}</span><span class="num">${value}</span></div>`;
  }

  function renderInspector() {
    const U = MM.util;
    const el = document.querySelector("#briefing-inspector");
    const ins = state.inspector;
    if (!ins) {
      el.innerHTML = `<div class="empty-state">点击变化 / 机会 / 国家查看详情</div>`;
      return;
    }
    if (ins.mode === "change") {
      const c = ins.payload;
      let html = `<div class="inspector-title">WHY — ${U.escapeHtml(MM.i18n.display(c.country))}</div>`;
      html += kv("kind", U.escapeHtml(c.kind));
      if (c.field) html += kv("field", U.escapeHtml(c.field));
      if (c.from !== undefined) html += kv("from → to", `${U.escapeHtml(fmt(c.from))} → ${U.escapeHtml(fmt(c.to))}`);
      Object.entries(c.detail || {}).forEach(([k, v]) => {
        const text = v && v.from !== undefined
          ? `${fmt(v.from)} → ${fmt(v.to)}`
          : JSON.stringify(v);
        html += kv(U.escapeHtml(k), U.escapeHtml(text));
      });
      el.innerHTML = html;
    } else if (ins.mode === "opp") {
      const c = opportunityRows().find(x => x.country === ins.payload);
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
      const name = ins.payload;
      const econ = (MM.state.economies || {})[name];
      const reg = ((MM.state.regimeData || {}).countries || {})[name];
      let html = `<div class="inspector-title">${U.escapeHtml(MM.i18n.display(name))}</div>`;
      if (econ) {
        ["fx", "rates", "equity", "real_estate"].forEach(a => {
          html += kv(a, fmt(econ.signals?.[a]?.final));
        });
        html += kv("composite", fmt(econ.composite?.final));
      }
      if (reg) {
        html += kv("verdict", U.escapeHtml(reg.verdict))
          + kv("narrative_gap", fmt(reg.narrative_gap))
          + kv("confirmation", fmt(reg.confirmation_score));
      }
      if (!econ && !reg) html += `<div class="empty-state">无数据</div>`;
      el.innerHTML = html;
    }
  }

  function renderMeta(snaps) {
    const meta = document.querySelector("#briefing-meta");
    const data = state.changes;
    if (!data || data.insufficient || !data.base) {
      meta.textContent = `SNAPSHOTS: ${snaps ? snaps.length : 0} · 尚无可对比的快照对`;
      return;
    }
    const days = Math.max(0, Math.round(
      (new Date(data.target.as_of) - new Date(data.base.as_of)) / 86400000));
    const headline = data.changes.filter(c => c.level === 1).length;
    meta.textContent = `vs ${data.base.id} · ${days}d · ${headline} headline · ${data.changes.length} changes`;
  }

  function load() {
    Promise.all([MM.api.getChanges(), MM.api.getSnapshots()]).then(([changes, snaps]) => {
      state.changes = changes;
      renderMeta(snaps);
      render();
    }).catch(err => {
      document.querySelector("#briefing-changes").innerHTML =
        `<div class="empty-state">加载失败:${MM.util.escapeHtml(String(err))}</div>`;
    });
    render(); // rail/opps render immediately from already-loaded snapshot data
  }

  return { load, render, state };
})();
