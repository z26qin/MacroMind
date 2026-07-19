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

  function render() {
    renderRail();
    renderChanges();
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
    // Show just the date part of the base id; the full id stays in the title attr.
    meta.textContent = `vs ${data.base.id.slice(0, 10)} · ${days}d · ${headline} headline · ${data.changes.length} changes`;
    meta.title = `base: ${data.base.id} → target: ${data.target.id}`;
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
    runController.init();
  }

  // Drives the RUN button and polls /api/run/status while a run is live.
  const runController = {
    timer: null, bound: false, wasRunning: false,
    init() {
      if (this.bound) return;
      this.bound = true;
      document.querySelector("#run-btn").addEventListener("click", () => this.onClick());
      this.poll(); // reconnect to a run already in flight when the tab opens
    },
    onClick() {
      const btn = document.querySelector("#run-btn");
      if (btn.dataset.state === "failed") {
        const status = this.lastStatus || {};
        alert((status.log_tail || ["(no log)"]).join("\n"));
        this.setButton("idle", "▶ RUN LIVE");
        return;
      }
      if (btn.dataset.state === "running") return;
      const source = document.querySelector("#run-mock").checked ? "mock" : "live";
      MM.api.postRun(source).then(({ status }) => {
        if (status === 409) this.setButton("running", "⏳ 已在运行");
        this.poll();
      });
    },
    poll() {
      clearInterval(this.timer);
      this.timer = setInterval(() => MM.api.getRunStatus().then(s => this.apply(s)), 1500);
      MM.api.getRunStatus().then(s => this.apply(s));
    },
    apply(s) {
      this.lastStatus = s;
      if (s.state === "running") {
        const p = s.phase ? `${s.phase.index + 1}/${s.phase.total} ${s.phase.name}` : "…";
        this.setButton("running", `⏳ ${p}`);
        const meta = document.querySelector("#briefing-meta");
        if (s.log_tail && s.log_tail.length) {
          meta.textContent = s.log_tail[s.log_tail.length - 1].slice(0, 90);
        }
      } else {
        clearInterval(this.timer);
        if (s.state === "succeeded" && this.wasRunning) {
          this.setButton("idle", "✓ RUN LIVE");
          setTimeout(() => {
            const btn = document.querySelector("#run-btn");
            if (btn.dataset.state === "idle") this.setButton("idle", "▶ RUN LIVE");
          }, 4000);
          // Re-pull everything so briefing + other views reflect the new snapshot.
          Promise.all([MM.api.getSignals(), MM.api.getRegime()]).then(([sig, reg]) => {
            MM.state.snapshot = sig;
            MM.state.economies = sig.economies || {};
            MM.state.regimeData = reg;
            load();
          });
        } else if (s.state === "failed") {
          this.setButton("failed", "✗ FAILED · 点看日志");
          console.error("run failed:", s.error, s.log_tail);
        } else {
          this.setButton("idle", "▶ RUN LIVE");
        }
      }
      this.wasRunning = s.state === "running";
    },
    setButton(stateName, label) {
      const btn = document.querySelector("#run-btn");
      btn.dataset.state = stateName;
      btn.textContent = label;
      btn.disabled = stateName === "running";
    },
  };

  return { load, render, state };
})();
