window.MM = window.MM || {};
MM.api = {
  json(url) {
    return fetch(url).then(r => {
      if (!r.ok) throw new Error(`${url}: ${r.status}`);
      return r.json();
    });
  },
  getSignals() { return MM.api.json("/api/signals"); },
  getRegime() { return MM.api.json("/api/regime"); },
  getHistory() { return MM.api.json("/api/history"); },
  getSnapshots() { return MM.api.json("/api/snapshots"); },
  getChanges(base) {
    return MM.api.json(base ? `/api/changes?base=${encodeURIComponent(base)}` : "/api/changes");
  },
  getRunStatus() { return MM.api.json("/api/run/status"); },
  postRun(source) {
    return fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    }).then(r => {
      if (!r.ok && r.status !== 409) throw new Error(`run: ${r.status}`);
      return r.json().then(body => ({ status: r.status, body }));
    });
  },
};
