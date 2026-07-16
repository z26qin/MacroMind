# Terminal Briefing Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 MacroMind dashboard 重造为 Terminal 深色风的"晨间交易台":Briefing 首屏(快照 diff + 机会榜 + 详情检查器)、Run 按钮实况、前端拆分零构建。

**Architecture:** 后端新增三个纯模块(`snapshot_store` 归档 / `snapshot_diff` 分级 diff / `run_manager` 后台状态机)+ 4 个新路由;前端从单文件拆为 `css/theme.css` + `js/*` 命名空间(`window.MM`),新增三栏 Briefing 视图为默认落地页。

**Tech Stack:** Python 3.11 / FastAPI / pytest;前端 vanilla JS + D3(零构建,普通 `<script>` 按序加载)。

**Spec:** `docs/superpowers/specs/2026-07-15-terminal-briefing-redesign-design.md`

**约定(所有任务适用):**
- Python 用 `python3.11`(系统 `python3` 是 3.9,太旧)。测试命令:`python3.11 -m pytest tests/<file> -v`
- 每个 Task 结束有 **⏸ USER REVIEW** 步:向用户演示/汇报该步产出,等确认后才进下一 Task(用户明确要求)
- 数据 key(国家名、API 字段)一律英文;仅 UI 展示层转中文
- 行号引用基于当前 `static/index.html`(commit b607a0b,1268 行)

---

## Task 1: `snapshot_store.py` — 快照归档

**Files:**
- Create: `snapshot_store.py`
- Test: `tests/test_snapshot_store.py`

- [ ] **Step 1.1: 写失败测试**

```python
# tests/test_snapshot_store.py
import json

import pytest

import snapshot_store


@pytest.fixture()
def working_files(tmp_path):
    signal = tmp_path / "snapshot.json"
    regime = tmp_path / "regime_snapshot.json"
    signal.write_text(json.dumps({"as_of": "2026-07-14", "economies": {}}))
    regime.write_text(json.dumps({"as_of": "2026-07-14", "countries": []}))
    return signal, regime


def _archive(tmp_path, working_files, snapshot_id):
    signal, regime = working_files
    return snapshot_store.archive_current(
        source="live",
        snapshots_dir=tmp_path / "snapshots",
        signal_path=signal,
        regime_path=regime,
        snapshot_id=snapshot_id,
    )


def test_archive_then_list_roundtrip(tmp_path, working_files):
    snapshot_id = _archive(tmp_path, working_files, "2026-07-15T120000Z")
    entries = snapshot_store.list_snapshots(tmp_path / "snapshots")
    assert snapshot_id == "2026-07-15T120000Z"
    assert [e["id"] for e in entries] == ["2026-07-15T120000Z"]
    assert entries[0]["as_of"] == "2026-07-14"
    assert entries[0]["meta"]["source"] == "live"


def test_list_ignores_partial_and_hidden_dirs(tmp_path, working_files):
    _archive(tmp_path, working_files, "2026-07-15T120000Z")
    partial = tmp_path / "snapshots" / "2026-07-16T000000Z"
    partial.mkdir()
    (partial / "snapshot.json").write_text("{}")  # regime missing -> partial
    hidden = tmp_path / "snapshots" / ".tmp-broken"
    hidden.mkdir()
    assert [e["id"] for e in snapshot_store.list_snapshots(tmp_path / "snapshots")] == [
        "2026-07-15T120000Z"
    ]


def test_archive_refuses_duplicate_id(tmp_path, working_files):
    _archive(tmp_path, working_files, "2026-07-15T120000Z")
    with pytest.raises(FileExistsError):
        _archive(tmp_path, working_files, "2026-07-15T120000Z")


def test_latest_pair_needs_two(tmp_path, working_files):
    assert snapshot_store.latest_pair(tmp_path / "snapshots") is None
    _archive(tmp_path, working_files, "2026-07-15T120000Z")
    assert snapshot_store.latest_pair(tmp_path / "snapshots") is None
    _archive(tmp_path, working_files, "2026-07-16T120000Z")
    base, target = snapshot_store.latest_pair(tmp_path / "snapshots")
    assert base["id"] == "2026-07-15T120000Z"
    assert target["id"] == "2026-07-16T120000Z"


def test_load_snapshot_shape(tmp_path, working_files):
    _archive(tmp_path, working_files, "2026-07-15T120000Z")
    loaded = snapshot_store.load_snapshot(
        "2026-07-15T120000Z", tmp_path / "snapshots"
    )
    assert loaded["id"] == "2026-07-15T120000Z"
    assert loaded["signal"]["as_of"] == "2026-07-14"
    assert loaded["regime"]["countries"] == []


def test_seed_baseline_only_when_empty(tmp_path, working_files):
    signal, regime = working_files
    kwargs = dict(
        snapshots_dir=tmp_path / "snapshots", signal_path=signal, regime_path=regime
    )
    seeded = snapshot_store.seed_baseline_if_empty(**kwargs)
    assert seeded == "2026-07-14T000000Z-baseline"
    assert snapshot_store.seed_baseline_if_empty(**kwargs) is None  # 已有归档,不再种
    entries = snapshot_store.list_snapshots(tmp_path / "snapshots")
    assert entries[0]["meta"]["source"] == "baseline"
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_snapshot_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'snapshot_store'`

- [ ] **Step 1.3: 实现 `snapshot_store.py`**

```python
"""Snapshot archive store: point-in-time copies of the working snapshots.

Each successful pipeline run archives ``snapshot.json`` + ``regime_snapshot.json``
into ``data/snapshots/<UTC timestamp>/`` together with a small ``meta.json``.
The archive directory (committed to git) is the only data source for diffs.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOTS_DIR = Path("data/snapshots")
SIGNAL_PATH = Path("snapshot.json")
REGIME_PATH = Path("regime_snapshot.json")

SIGNAL_FILE = "snapshot.json"
REGIME_FILE = "regime_snapshot.json"
META_FILE = "meta.json"


def _utc_stamp(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H%M%SZ")


def list_snapshots(snapshots_dir: Path = SNAPSHOTS_DIR) -> list[dict]:
    """Complete archives sorted oldest-first: [{id, as_of, meta}, ...]."""
    if not snapshots_dir.exists():
        return []
    entries = []
    for child in sorted(snapshots_dir.iterdir()):
        if child.name.startswith("."):
            continue  # staging dirs from interrupted archives
        signal_file = child / SIGNAL_FILE
        if not (child.is_dir() and signal_file.exists() and (child / REGIME_FILE).exists()):
            continue
        meta_file = child / META_FILE
        entries.append(
            {
                "id": child.name,
                "as_of": json.loads(signal_file.read_text())["as_of"],
                "meta": json.loads(meta_file.read_text()) if meta_file.exists() else {},
            }
        )
    return entries


def load_snapshot(snapshot_id: str, snapshots_dir: Path = SNAPSHOTS_DIR) -> dict:
    """Load one archive in the diff-engine input shape {"id", "signal", "regime"}."""
    root = snapshots_dir / snapshot_id
    return {
        "id": snapshot_id,
        "signal": json.loads((root / SIGNAL_FILE).read_text()),
        "regime": json.loads((root / REGIME_FILE).read_text()),
    }


def latest_pair(snapshots_dir: Path = SNAPSHOTS_DIR) -> tuple[dict, dict] | None:
    """(base_entry, target_entry) — the two newest archives, or None when < 2."""
    entries = list_snapshots(snapshots_dir)
    if len(entries) < 2:
        return None
    return entries[-2], entries[-1]


def archive_current(
    source: str,
    *,
    snapshots_dir: Path = SNAPSHOTS_DIR,
    signal_path: Path = SIGNAL_PATH,
    regime_path: Path = REGIME_PATH,
    now: datetime | None = None,
    extra_meta: dict | None = None,
    snapshot_id: str | None = None,
) -> str:
    """Archive the working snapshots; returns the new snapshot id.

    Copies into a dot-prefixed staging dir and renames at the end, so an
    interrupted archive can never be listed as a complete snapshot.
    """
    snapshot_id = snapshot_id or _utc_stamp(now)
    target = snapshots_dir / snapshot_id
    if target.exists():
        raise FileExistsError(f"snapshot archive already exists: {target}")
    staging = snapshots_dir / f".tmp-{snapshot_id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copyfile(signal_path, staging / SIGNAL_FILE)
    shutil.copyfile(regime_path, staging / REGIME_FILE)
    meta = {
        "id": snapshot_id,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **(extra_meta or {}),
    }
    (staging / META_FILE).write_text(json.dumps(meta, indent=2))
    staging.rename(target)
    return snapshot_id


def seed_baseline_if_empty(
    *,
    snapshots_dir: Path = SNAPSHOTS_DIR,
    signal_path: Path = SIGNAL_PATH,
    regime_path: Path = REGIME_PATH,
) -> str | None:
    """Archive the committed working snapshots as a baseline when none exist.

    Returns the new id, or None when archives already exist or the working
    files are missing. The id derives from the signal snapshot's as_of date so
    seeding is deterministic across machines.
    """
    if list_snapshots(snapshots_dir):
        return None
    if not (signal_path.exists() and regime_path.exists()):
        return None
    as_of = json.loads(signal_path.read_text())["as_of"]
    return archive_current(
        source="baseline",
        snapshots_dir=snapshots_dir,
        signal_path=signal_path,
        regime_path=regime_path,
        snapshot_id=f"{as_of[:10]}T000000Z-baseline",
        extra_meta={"seeded": True},
    )
```

- [ ] **Step 1.4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_snapshot_store.py -v`
Expected: 6 passed

- [ ] **Step 1.5: Commit**

```bash
git add snapshot_store.py tests/test_snapshot_store.py
git commit -m "feat: add snapshot archive store"
```

- [ ] **Step 1.6: ⏸ USER REVIEW** — 向用户展示归档目录结构与测试结果,确认后进 Task 2

---

## Task 2: `snapshot_diff.py` 上半 — 守卫 + L1 翻转 + L3 漂移

**Files:**
- Create: `snapshot_diff.py`
- Test: `tests/test_snapshot_diff.py`

- [ ] **Step 2.1: 写失败测试(含 fixture 工厂)**

```python
# tests/test_snapshot_diff.py
import snapshot_diff


def make_regime_country(name, verdict="Neutral", regime=0.0, gap=0.0, conf=0.0):
    return {
        "country": name,
        "verdict": verdict,
        "regime_score": regime,
        "narrative_gap": gap,
        "confirmation_score": conf,
    }


def make_economy(cells=None, composite=0.0, provenance=None):
    """cells: {"equity": 0.3} -> full signal blocks with defaults."""
    signals = {}
    for asset in ("fx", "rates", "equity", "real_estate"):
        value = (cells or {}).get(asset, 0.0)
        signals[asset] = {
            "final": value,
            "rag_confidence": 0.0,
            "rag_analysis": {"evidence_count": 0, "citations": []},
        }
    return {
        "signals": signals,
        "composite": {"final": composite},
        "provenance": provenance or {},
    }


def make_side(snapshot_id="base", methodology="1.0", economies=None, countries=None):
    return {
        "id": snapshot_id,
        "signal": {
            "as_of": "2026-07-08",
            "methodology_version": methodology,
            "economies": economies or {},
        },
        "regime": {
            "methodology_version": methodology,
            "countries": countries or [],
        },
    }


def kinds(diff):
    return [(c["kind"], c["country"]) for c in diff["changes"]]


def test_verdict_flip_is_level_1():
    base = make_side(countries=[make_regime_country("Brazil", "Unconfirmed", conf=0.19)])
    target = make_side("t", countries=[make_regime_country("Brazil", "Repricing", conf=0.31)])
    diff = snapshot_diff.compute_diff(base, target)
    flips = [c for c in diff["changes"] if c["kind"] == "verdict_flip"]
    assert len(flips) == 1
    assert flips[0]["level"] == 1
    assert flips[0]["from"] == "Unconfirmed" and flips[0]["to"] == "Repricing"
    assert flips[0]["detail"]["confirmation_score"] == {"from": 0.19, "to": 0.31}


def test_direction_flip_requires_crossing_band():
    base = make_side(economies={"Brazil": make_economy({"equity": -0.20})})
    flipped = make_side("t", economies={"Brazil": make_economy({"equity": 0.20})})
    diff = snapshot_diff.compute_diff(base, flipped)
    assert ("direction_flip", "Brazil") in kinds(diff)
    # 0.14 未到 +0.15 带 -> 不算翻转,按漂移处理
    not_crossed = make_side("t", economies={"Brazil": make_economy({"equity": 0.14})})
    diff2 = snapshot_diff.compute_diff(base, not_crossed)
    assert ("direction_flip", "Brazil") not in kinds(diff2)
    assert ("signal_drift", "Brazil") in kinds(diff2)


def test_drift_threshold_boundary():
    base = make_side(economies={"Japan": make_economy({"fx": 0.0})})
    below = make_side("t", economies={"Japan": make_economy({"fx": 0.099})})
    at = make_side("t", economies={"Japan": make_economy({"fx": 0.101})})
    assert ("signal_drift", "Japan") not in kinds(snapshot_diff.compute_diff(base, below))
    assert snapshot_diff.compute_diff(base, below)["minor_count"] >= 1
    assert ("signal_drift", "Japan") in kinds(snapshot_diff.compute_diff(base, at))


def test_regime_drift():
    base = make_side(countries=[make_regime_country("Turkey", gap=0.10)])
    target = make_side("t", countries=[make_regime_country("Turkey", gap=0.45)])
    entries = [c for c in snapshot_diff.compute_diff(base, target)["changes"]
               if c["kind"] == "regime_drift"]
    assert entries and entries[0]["field"] == "narrative_gap"
    assert entries[0]["level"] == 3


def test_methodology_change_suppresses_diff():
    base = make_side(methodology="1.0",
                     economies={"Brazil": make_economy({"equity": -0.5})})
    target = make_side("t", methodology="2.0",
                       economies={"Brazil": make_economy({"equity": 0.5})})
    diff = snapshot_diff.compute_diff(base, target)
    assert diff["changes"] == []
    assert any("methodology" in n for n in diff["notes"])


def test_missing_country_reports_coverage_change():
    base = make_side(countries=[make_regime_country("Greece")])
    target = make_side("t", countries=[])
    diff = snapshot_diff.compute_diff(base, target)
    assert ("coverage_change", "Greece") in kinds(diff)


def test_unchanged_count():
    econ = {"Japan": make_economy(), "Canada": make_economy()}
    diff = snapshot_diff.compute_diff(make_side(economies=econ), make_side("t", economies=econ))
    assert diff["changes"] == []
    assert diff["minor_count"] == 0
    assert diff["unchanged_count"] == 2
```

- [ ] **Step 2.2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_snapshot_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'snapshot_diff'`

- [ ] **Step 2.3: 实现 `snapshot_diff.py`(本 Task 只到 L1/L3 + 守卫;L2/L4 的函数骨架先留空列表逻辑,Task 3 补)**

```python
"""Pure diff engine between two archived snapshots.

Input shape per side (from snapshot_store.load_snapshot):
    {"id": str, "signal": <snapshot.json dict>, "regime": <regime_snapshot.json dict>}

Change levels:
    L1 headline   — regime verdict flips, signal direction flips
    L2 rank moves — opportunity-board / per-cell cross-sectional rank moves
    L3 drift      — numeric moves >= DRIFT_MIN
    L4 context    — evidence changes, provenance live<->fallback flips, coverage

v1 thresholds are hand-picked starting points; revisit after real use.
"""

from __future__ import annotations

SIGN_FLIP_BAND = 0.15
DRIFT_MIN = 0.10
RANK_MOVE_MIN = 2
RAG_CONF_MIN = 0.20
TOP_N = 3

ASSET_KEYS = ("fx", "rates", "equity", "real_estate")
# composite is the dashboard's landing view, so it takes part in flips,
# drift, and rank moves alongside the four asset-class cells.
CELL_KEYS = ASSET_KEYS + ("composite",)


def _cell_final(economy: dict, cell: str):
    block = (
        economy.get("composite")
        if cell == "composite"
        else (economy.get("signals") or {}).get(cell)
    )
    if not isinstance(block, dict):
        return None
    return block.get("final")


def _is_live(provenance_value) -> bool:
    return isinstance(provenance_value, str) and not provenance_value.startswith(
        ("mock", "derived")
    )


def opportunity_ranking(regime: dict) -> list[str]:
    """Gap desc, confirmation desc — the one ranking rule, mirrored by the UI."""
    ranked = sorted(
        regime.get("countries", []),
        key=lambda c: (
            -(c.get("narrative_gap") or 0.0),
            -(c.get("confirmation_score") or 0.0),
        ),
    )
    return [c["country"] for c in ranked]


def _asset_ranking(signal: dict, cell: str) -> list[str]:
    rows = []
    for name, economy in (signal.get("economies") or {}).items():
        value = _cell_final(economy, cell)
        if value is not None:
            rows.append((name, value))
    rows.sort(key=lambda item: (-item[1], item[0]))
    return [name for name, _ in rows]


def compute_diff(base: dict, target: dict) -> dict:
    changes: list[dict] = []
    notes: list[str] = []
    minor_count = 0
    touched: set[str] = set()  # countries with at least one change entry
    drifted: set[str] = set()  # countries with sub-threshold numeric deltas

    base_sig, target_sig = base["signal"], target["signal"]
    base_reg, target_reg = base["regime"], target["regime"]
    result = {
        "base": {"id": base["id"], "as_of": base_sig.get("as_of")},
        "target": {"id": target["id"], "as_of": target_sig.get("as_of")},
    }

    if base_sig.get("methodology_version") != target_sig.get("methodology_version") or (
        base_reg.get("methodology_version") != target_reg.get("methodology_version")
    ):
        notes.append(
            "methodology_version changed between snapshots; "
            "value diff suppressed as not comparable"
        )
        return {**result, "changes": [], "minor_count": 0, "unchanged_count": 0, "notes": notes}

    def add(level, kind, country, headline, **fields):
        entry = {"level": level, "kind": kind, "country": country, "headline": headline}
        entry.update(fields)
        changes.append(entry)
        touched.add(country)

    def crossed(country, delta, threshold):
        """True when |delta| passes threshold; otherwise counts toward minor."""
        nonlocal minor_count
        if abs(delta) >= threshold:
            return True
        if delta != 0:
            minor_count += 1
            drifted.add(country)
        return False

    # ---- regime: verdict flips (L1) + drift (L3) + coverage (L4) ----
    base_countries = {c["country"]: c for c in base_reg.get("countries", [])}
    target_countries = {c["country"]: c for c in target_reg.get("countries", [])}
    for name in sorted(set(base_countries) | set(target_countries)):
        b, t = base_countries.get(name), target_countries.get(name)
        if b is None or t is None:
            side = "left regime universe" if t is None else "entered regime universe"
            add(4, "coverage_change", name, f"{name}: {side}",
                **{"from": b is not None, "to": t is not None})
            continue
        if b.get("verdict") != t.get("verdict"):
            add(
                1, "verdict_flip", name,
                f"{name}: {b.get('verdict')} → {t.get('verdict')}",
                **{"from": b.get("verdict"), "to": t.get("verdict")},
                detail={
                    "narrative_gap": {"from": b.get("narrative_gap"), "to": t.get("narrative_gap")},
                    "confirmation_score": {
                        "from": b.get("confirmation_score"),
                        "to": t.get("confirmation_score"),
                    },
                },
            )
        for field in ("regime_score", "narrative_gap", "confirmation_score"):
            bv, tv = b.get(field), t.get(field)
            if bv is None or tv is None:
                continue
            if crossed(name, tv - bv, DRIFT_MIN):
                add(3, "regime_drift", name,
                    f"{name}: {field} {bv:+.2f} → {tv:+.2f}",
                    field=field, **{"from": bv, "to": tv})

    # ---- signal: direction flips (L1) + drift (L3) + coverage (L4) ----
    base_econ = base_sig.get("economies") or {}
    target_econ = target_sig.get("economies") or {}
    for name in sorted(set(base_econ) | set(target_econ)):
        b, t = base_econ.get(name), target_econ.get(name)
        if b is None or t is None:
            side = "left signal universe" if t is None else "entered signal universe"
            add(4, "coverage_change", name, f"{name}: {side}",
                **{"from": b is not None, "to": t is not None})
            continue
        for cell in CELL_KEYS:
            bv, tv = _cell_final(b, cell), _cell_final(t, cell)
            if bv is None or tv is None:
                continue
            flipped = (bv <= -SIGN_FLIP_BAND and tv >= SIGN_FLIP_BAND) or (
                bv >= SIGN_FLIP_BAND and tv <= -SIGN_FLIP_BAND
            )
            if flipped:
                add(1, "direction_flip", name,
                    f"{name} {cell}: {bv:+.2f} → {tv:+.2f}",
                    field=cell, **{"from": bv, "to": tv})
            elif crossed(name, tv - bv, DRIFT_MIN):
                add(3, "signal_drift", name,
                    f"{name} {cell}: {bv:+.2f} → {tv:+.2f}",
                    field=cell, **{"from": bv, "to": tv})

    _add_rank_moves(base_sig, target_sig, base_reg, target_reg, add)      # Task 3
    _add_context_changes(base_econ, target_econ, add, crossed)           # Task 3

    changes.sort(key=lambda c: (c["level"], c["country"], c["kind"], str(c.get("field") or "")))
    all_names = set(base_countries) | set(target_countries) | set(base_econ) | set(target_econ)
    unchanged_count = len(all_names - touched - drifted)
    return {**result, "changes": changes, "minor_count": minor_count,
            "unchanged_count": unchanged_count, "notes": notes}


def _add_rank_moves(base_sig, target_sig, base_reg, target_reg, add):
    """L2 rank moves — implemented in Task 3."""


def _add_context_changes(base_econ, target_econ, add, crossed):
    """L4 evidence + provenance — implemented in Task 3."""
```

- [ ] **Step 2.4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_snapshot_diff.py -v`
Expected: 7 passed

- [ ] **Step 2.5: Commit**

```bash
git add snapshot_diff.py tests/test_snapshot_diff.py
git commit -m "feat: snapshot diff engine — guards, verdict/direction flips, drift"
```

- [ ] **Step 2.6: ⏸ USER REVIEW** — 展示 diff 输出样例(用两份真实归档跑一次)与测试结果

---

## Task 3: `snapshot_diff.py` 下半 — L2 排位 + L4 背景

**Files:**
- Modify: `snapshot_diff.py`(填充 `_add_rank_moves` / `_add_context_changes`)
- Test: `tests/test_snapshot_diff.py`(追加)

- [ ] **Step 3.1: 追加失败测试**

```python
# 追加到 tests/test_snapshot_diff.py

def test_opportunity_rank_move_and_top_crossing():
    base = make_side(countries=[
        make_regime_country("Argentina", gap=0.60, conf=0.4),
        make_regime_country("Brazil", gap=0.50),
        make_regime_country("Turkey", gap=0.40),
        make_regime_country("Greece", gap=0.30),
    ])
    # Greece 0.30 -> 0.55: rank 4 -> 2(跨入前3 + 移动2位)
    target = make_side("t", countries=[
        make_regime_country("Argentina", gap=0.60, conf=0.4),
        make_regime_country("Brazil", gap=0.50),
        make_regime_country("Turkey", gap=0.40),
        make_regime_country("Greece", gap=0.55),
    ])
    moves = [c for c in snapshot_diff.compute_diff(base, target)["changes"]
             if c["kind"] == "opp_rank_move"]
    greece = [m for m in moves if m["country"] == "Greece"]
    assert greece and greece[0]["from"] == 4 and greece[0]["to"] == 2
    assert greece[0]["level"] == 2
    # Turkey 3 -> 4: 跨出前3也要报,即使只移动1位
    turkey = [m for m in moves if m["country"] == "Turkey"]
    assert turkey and turkey[0]["from"] == 3 and turkey[0]["to"] == 4


def test_asset_rank_move_needs_two_positions():
    base = make_side(economies={
        "Brazil": make_economy({"equity": 0.30}),
        "Japan": make_economy({"equity": 0.20}),
        "Canada": make_economy({"equity": 0.10}),
    })
    # Canada equity 0.10 -> 0.40: rank 3 -> 1
    target = make_side("t", economies={
        "Brazil": make_economy({"equity": 0.30}),
        "Japan": make_economy({"equity": 0.20}),
        "Canada": make_economy({"equity": 0.40}),
    })
    moves = [c for c in snapshot_diff.compute_diff(base, target)["changes"]
             if c["kind"] == "asset_rank_move" and c["country"] == "Canada"]
    assert moves and moves[0]["from"] == 3 and moves[0]["to"] == 1


def test_evidence_change_on_count_or_citations():
    econ_base = make_economy()
    econ_target = make_economy()
    econ_target["signals"]["equity"]["rag_analysis"] = {
        "evidence_count": 2,
        "citations": [{"uri": "doc://cb-minutes-0711"}],
    }
    diff = snapshot_diff.compute_diff(
        make_side(economies={"Brazil": econ_base}),
        make_side("t", economies={"Brazil": econ_target}),
    )
    entries = [c for c in diff["changes"] if c["kind"] == "evidence_change"]
    assert entries and entries[0]["level"] == 4
    assert entries[0]["detail"]["citations_added"] == ["doc://cb-minutes-0711"]


def test_rag_confidence_move():
    econ_base = make_economy()
    econ_target = make_economy()
    econ_target["signals"]["fx"]["rag_confidence"] = 0.25
    diff = snapshot_diff.compute_diff(
        make_side(economies={"Japan": econ_base}),
        make_side("t", economies={"Japan": econ_target}),
    )
    assert any(c["kind"] == "evidence_change" and c.get("field") == "fx"
               for c in diff["changes"])


def test_provenance_flip_live_to_fallback():
    econ_base = make_economy(provenance={"gdp_growth": "world_bank:2025"})
    econ_target = make_economy(provenance={"gdp_growth": "mock"})
    diff = snapshot_diff.compute_diff(
        make_side(economies={"Canada": econ_base}),
        make_side("t", economies={"Canada": econ_target}),
    )
    flips = [c for c in diff["changes"] if c["kind"] == "provenance_flip"]
    assert flips and "live → fallback" in flips[0]["headline"]
    # derived: 前缀不算 live,mock->derived 不报翻转
    econ_derived = make_economy(provenance={"gdp_growth": "derived:x"})
    diff2 = snapshot_diff.compute_diff(
        make_side(economies={"Canada": make_economy(provenance={"gdp_growth": "mock"})}),
        make_side("t", economies={"Canada": econ_derived}),
    )
    assert not [c for c in diff2["changes"] if c["kind"] == "provenance_flip"]
```

- [ ] **Step 3.2: 跑测试确认新增用例失败**

Run: `python3.11 -m pytest tests/test_snapshot_diff.py -v`
Expected: Task 2 的 7 个 PASS,新增 5 个 FAIL(空实现不产出 L2/L4 条目)

- [ ] **Step 3.3: 填充两个函数**

```python
# 替换 snapshot_diff.py 底部两个占位函数

def _add_rank_moves(base_sig, target_sig, base_reg, target_reg, add):
    """L2: opportunity-board and per-cell cross-sectional rank moves."""
    opp_base = opportunity_ranking(base_reg)
    opp_target = opportunity_ranking(target_reg)
    for name in sorted(set(opp_base) & set(opp_target)):
        pb, pt = opp_base.index(name), opp_target.index(name)
        crossed_top = (pb < TOP_N) != (pt < TOP_N)
        if crossed_top or abs(pt - pb) >= RANK_MOVE_MIN:
            add(2, "opp_rank_move", name,
                f"{name}: opportunity rank {pb + 1} → {pt + 1}",
                **{"from": pb + 1, "to": pt + 1})

    for cell in CELL_KEYS:
        rank_base = _asset_ranking(base_sig, cell)
        rank_target = _asset_ranking(target_sig, cell)
        for name in sorted(set(rank_base) & set(rank_target)):
            pb, pt = rank_base.index(name), rank_target.index(name)
            if abs(pt - pb) >= RANK_MOVE_MIN:
                add(2, "asset_rank_move", name,
                    f"{name} {cell}: rank {pb + 1} → {pt + 1}",
                    field=cell, **{"from": pb + 1, "to": pt + 1})


def _add_context_changes(base_econ, target_econ, add, crossed):
    """L4: evidence/citation changes, rag-confidence moves, provenance flips."""
    for name in sorted(set(base_econ) & set(target_econ)):
        b, t = base_econ[name], target_econ[name]
        for asset in ASSET_KEYS:
            b_cell = (b.get("signals") or {}).get(asset) or {}
            t_cell = (t.get("signals") or {}).get(asset) or {}
            b_ra = b_cell.get("rag_analysis") or {}
            t_ra = t_cell.get("rag_analysis") or {}
            b_cits = {str(c.get("uri") or c) for c in (b_ra.get("citations") or [])}
            t_cits = {str(c.get("uri") or c) for c in (t_ra.get("citations") or [])}
            if b_ra.get("evidence_count", 0) != t_ra.get("evidence_count", 0) or b_cits != t_cits:
                add(4, "evidence_change", name,
                    f"{name} {asset}: evidence "
                    f"{b_ra.get('evidence_count', 0)} → {t_ra.get('evidence_count', 0)}",
                    field=asset,
                    detail={
                        "citations_added": sorted(t_cits - b_cits),
                        "citations_removed": sorted(b_cits - t_cits),
                    })
            b_conf, t_conf = b_cell.get("rag_confidence"), t_cell.get("rag_confidence")
            if b_conf is not None and t_conf is not None:
                if crossed(name, t_conf - b_conf, RAG_CONF_MIN):
                    add(4, "evidence_change", name,
                        f"{name} {asset}: rag_confidence {b_conf:.2f} → {t_conf:.2f}",
                        field=asset, **{"from": b_conf, "to": t_conf})
        b_prov = b.get("provenance") or {}
        t_prov = t.get("provenance") or {}
        for key in sorted(set(b_prov) & set(t_prov)):
            if _is_live(b_prov[key]) != _is_live(t_prov[key]):
                direction = "live → fallback" if _is_live(b_prov[key]) else "fallback → live"
                add(4, "provenance_flip", name,
                    f"{name} {key}: {direction}",
                    field=key, **{"from": b_prov[key], "to": t_prov[key]})
```

- [ ] **Step 3.4: 全量跑测试**

Run: `python3.11 -m pytest tests/test_snapshot_diff.py tests/test_snapshot_store.py -v`
Expected: 全部 PASS(12 + 6)

- [ ] **Step 3.5: Commit**

```bash
git add snapshot_diff.py tests/test_snapshot_diff.py
git commit -m "feat: diff engine rank moves and context changes"
```

- [ ] **Step 3.6: ⏸ USER REVIEW** — 用真实快照演示完整 diff JSON

---

## Task 4: `run_manager.py` + signal CLI `--quality-out`

**Files:**
- Create: `run_manager.py`
- Modify: `signal_engine.py:139-153`(CLI 加 `--quality-out`)
- Test: `tests/test_run_manager.py`

- [ ] **Step 4.1: signal CLI 加 quality 输出旗标**

`signal_engine.py` 的 `__main__` 块替换为:

```python
if __name__ == "__main__":
    import argparse
    import json as _json
    from pathlib import Path as _Path

    parser = argparse.ArgumentParser(description="Generate the macro signal snapshot.")
    parser.add_argument(
        "--source",
        choices=("mock", "live"),
        default="mock",
        help="Data source: mock fallback or live staged adapters.",
    )
    parser.add_argument(
        "--quality-out",
        default=None,
        help="Optional path to write a small quality-gate summary JSON.",
    )
    args = parser.parse_args()
    cache = default_news_cache() if args.source == "live" else None
    result = run_signal_pipeline(source=args.source, news_cache=cache)
    if args.quality_out:
        report = result.quality
        summary = None
        if report is not None:
            summary = {
                "status": report.status.value,
                "accepted": report.accepted_observation_count,
                "blocked": report.blocked_observation_count,
            }
        _Path(args.quality_out).write_text(_json.dumps(summary))
    print(f"Wrote {SNAPSHOT_PATH} (source={args.source})")
```

验证不回归:`python3.11 -m pytest tests/test_signal_engine.py -q` → PASS;
`python3.11 signal_engine.py --source mock --quality-out .cache/quality.json` → 打印 `Wrote snapshot.json (source=mock)`,`.cache/quality.json` 内容为 `null`(mock 无 quality report)。

- [ ] **Step 4.2: 写 run_manager 失败测试**

```python
# tests/test_run_manager.py
import sys
import time

import pytest

import run_manager


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    run_manager._reset_for_tests()
    # phase 1/2 用轻量真 subprocess;phase 3/4 打桩避免碰真实文件
    monkeypatch.setitem(
        run_manager.PHASE_COMMANDS, "signal_pipeline",
        lambda source: [sys.executable, "-c", "print('signal ok')"],
    )
    monkeypatch.setitem(
        run_manager.PHASE_COMMANDS, "regime_engine",
        lambda source: [sys.executable, "-c", "print('regime ok')"],
    )
    monkeypatch.setattr(run_manager, "_archive_and_diff",
                        lambda source: {"snapshot_id": "test-id", "headline_count": 0})
    yield


def wait_done(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if run_manager.get_status()["state"] in ("succeeded", "failed"):
            return run_manager.get_status()
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_successful_run_reaches_succeeded():
    assert run_manager.start_run("mock") is True
    status = wait_done()
    assert status["state"] == "succeeded"
    assert status["result"]["snapshot_id"] == "test-id"
    assert any("signal ok" in line for line in status["log_tail"])
    assert status["phase"] is None and status["finished_at"] is not None


def test_failure_propagates(monkeypatch):
    monkeypatch.setitem(
        run_manager.PHASE_COMMANDS, "regime_engine",
        lambda source: [sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    run_manager.start_run("mock")
    status = wait_done()
    assert status["state"] == "failed"
    assert "regime_engine" in status["error"]


def test_concurrent_start_rejected(monkeypatch):
    monkeypatch.setitem(
        run_manager.PHASE_COMMANDS, "signal_pipeline",
        lambda source: [sys.executable, "-c", "import time; time.sleep(0.5)"],
    )
    assert run_manager.start_run("mock") is True
    assert run_manager.start_run("mock") is False  # 已在跑 -> 拒绝
    wait_done()
    assert run_manager.start_run("mock") is True   # 结束后可再跑
    wait_done()


def test_status_is_a_copy():
    run_manager.start_run("mock")
    status = run_manager.get_status()
    status["state"] = "hacked"
    assert run_manager.get_status()["state"] != "hacked"
    wait_done()
```

Run: `python3.11 -m pytest tests/test_run_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_manager'`

- [ ] **Step 4.3: 实现 `run_manager.py`**

```python
"""Background pipeline run state machine for the dashboard Run button.

Single-user tool: one in-memory run at a time, guarded by a lock. The API
process must run as a single uvicorn worker (documented in README).
"""

from __future__ import annotations

import copy
import subprocess
import sys
import threading
from datetime import datetime, timezone

import snapshot_diff
import snapshot_store

PHASE_TIMEOUT_S = 600
LOG_TAIL_LINES = 50
PHASES = ("signal_pipeline", "regime_engine", "archive", "diff")
QUALITY_OUT_PATH = ".cache/last_quality.json"

# Commands for the subprocess phases; tests monkeypatch entries.
PHASE_COMMANDS = {
    "signal_pipeline": lambda source: [
        sys.executable, "signal_engine.py", "--source", source,
        "--quality-out", QUALITY_OUT_PATH,
    ],
    "regime_engine": lambda source: [sys.executable, "regime_engine.py"],
}

_lock = threading.Lock()
_worker: threading.Thread | None = None
_IDLE = {
    "state": "idle", "run_id": None, "source": None, "phase": None,
    "started_at": None, "finished_at": None, "log_tail": [],
    "error": None, "result": None,
}
_status = copy.deepcopy(_IDLE)


def get_status() -> dict:
    with _lock:
        return copy.deepcopy(_status)


def start_run(source: str) -> bool:
    """Kick off a run; False when one is already in flight."""
    global _worker
    with _lock:
        if _status["state"] == "running":
            return False
        now = datetime.now(timezone.utc)
        _status.update(copy.deepcopy(_IDLE))
        _status.update(
            state="running",
            run_id=now.strftime("%Y-%m-%dT%H%M%SZ"),
            source=source,
            started_at=now.isoformat(),
        )
    _worker = threading.Thread(target=_run, args=(source,), daemon=True)
    _worker.start()
    return True


def _reset_for_tests() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        _worker.join(timeout=5)
    with _lock:
        _status.clear()
        _status.update(copy.deepcopy(_IDLE))
    _worker = None


def _append_log(text: str) -> None:
    with _lock:
        for line in text.splitlines():
            if line.strip():
                _status["log_tail"].append(line)
        del _status["log_tail"][:-LOG_TAIL_LINES]


def _set_phase(index: int) -> None:
    with _lock:
        _status["phase"] = {"index": index, "total": len(PHASES), "name": PHASES[index]}
    _append_log(f"[phase {index + 1}/{len(PHASES)}] {PHASES[index]}")


def _run_subprocess(name: str, source: str) -> None:
    proc = subprocess.run(
        PHASE_COMMANDS[name](source),
        capture_output=True, text=True, timeout=PHASE_TIMEOUT_S,
    )
    if proc.stdout:
        _append_log(proc.stdout)
    if proc.stderr:
        _append_log(proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"{name} exited with code {proc.returncode}")


def _read_quality_meta() -> dict:
    import json
    from pathlib import Path

    path = Path(QUALITY_OUT_PATH)
    if not path.exists():
        return {}
    try:
        quality = json.loads(path.read_text())
    except ValueError:
        return {}
    return {"quality": quality} if quality else {}


def _archive_and_diff(source: str) -> dict:
    """Phases 3+4; separated so tests can stub filesystem effects."""
    snapshot_id = snapshot_store.archive_current(source=source, extra_meta=_read_quality_meta())
    result = {"snapshot_id": snapshot_id, "headline_count": 0}
    pair = snapshot_store.latest_pair()
    if pair is not None:
        base_entry, target_entry = pair
        diff = snapshot_diff.compute_diff(
            snapshot_store.load_snapshot(base_entry["id"]),
            snapshot_store.load_snapshot(target_entry["id"]),
        )
        result["headline_count"] = sum(1 for c in diff["changes"] if c["level"] == 1)
    return result


def _run(source: str) -> None:
    try:
        _set_phase(0)
        _run_subprocess("signal_pipeline", source)
        _set_phase(1)
        _run_subprocess("regime_engine", source)
        _set_phase(2)  # archive+diff split across two phase labels for the UI
        result = None
        _set_phase(3)
        result = _archive_and_diff(source)
        with _lock:
            _status.update(
                state="succeeded", phase=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
                result=result,
            )
    except Exception as exc:  # noqa: BLE001 — 所有失败进状态机,不炸线程
        _append_log(f"ERROR: {exc}")
        with _lock:
            _status.update(
                state="failed", phase=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
            )
```

注意 `_archive_and_diff` 同时覆盖 phase 2(archive)与 phase 3(diff)——UI 上两个 phase 标签快速闪过即可,不值得为拆它加接缝。

- [ ] **Step 4.4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_run_manager.py tests/test_signal_engine.py -v`
Expected: 全 PASS

- [ ] **Step 4.5: Commit**

```bash
git add run_manager.py signal_engine.py tests/test_run_manager.py
git commit -m "feat: background run manager with phase status and quality meta"
```

- [ ] **Step 4.6: ⏸ USER REVIEW** — 演示 `start_run("mock")` 真实跑完一轮的状态流转

---

## Task 5: API 路由 — `/api/snapshots` `/api/changes` `/api/run` `/api/run/status`

**Files:**
- Modify: `main.py`
- Test: `tests/test_snapshot_api.py`

- [ ] **Step 5.1: 写失败测试**

```python
# tests/test_snapshot_api.py
import json

import pytest
from fastapi.testclient import TestClient

import main
import run_manager
import snapshot_store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    signal = tmp_path / "snapshot.json"
    regime = tmp_path / "regime_snapshot.json"
    signal.write_text(json.dumps(
        {"as_of": "2026-07-14", "methodology_version": "1.0", "economies": {}}
    ))
    regime.write_text(json.dumps({"methodology_version": "1.0", "countries": []}))
    monkeypatch.setattr(snapshot_store, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(snapshot_store, "SIGNAL_PATH", signal)
    monkeypatch.setattr(snapshot_store, "REGIME_PATH", regime)
    run_manager._reset_for_tests()
    return TestClient(main.app)


def test_snapshots_endpoint_seeds_baseline(client):
    body = client.get("/api/snapshots").json()
    assert len(body) == 1
    assert body[0]["meta"]["source"] == "baseline"


def test_changes_insufficient_then_available(client):
    body = client.get("/api/changes").json()
    assert body["insufficient"] is True and body["changes"] == []
    snapshot_store.archive_current(source="live", snapshot_id="2026-07-16T120000Z")
    body = client.get("/api/changes").json()
    assert body["insufficient"] is False
    assert body["target"]["id"] == "2026-07-16T120000Z"


def test_changes_with_explicit_base(client):
    client.get("/api/snapshots")  # seed
    snapshot_store.archive_current(source="live", snapshot_id="2026-07-16T120000Z")
    snapshot_store.archive_current(source="live", snapshot_id="2026-07-17T120000Z")
    body = client.get("/api/changes", params={"base": "2026-07-16T120000Z"}).json()
    assert body["base"]["id"] == "2026-07-16T120000Z"
    assert body["target"]["id"] == "2026-07-17T120000Z"


def test_changes_unknown_base_is_404(client):
    client.get("/api/snapshots")
    snapshot_store.archive_current(source="live", snapshot_id="2026-07-16T120000Z")
    assert client.get("/api/changes", params={"base": "nope"}).status_code == 404


def test_run_endpoint_starts_and_conflicts(client, monkeypatch):
    calls = []
    monkeypatch.setattr(run_manager, "start_run", lambda source: calls.append(source) or True)
    assert client.post("/api/run", json={"source": "mock"}).status_code == 202
    assert calls == ["mock"]
    monkeypatch.setattr(run_manager, "start_run", lambda source: False)
    assert client.post("/api/run", json={"source": "mock"}).status_code == 409


def test_run_rejects_bad_source(client):
    assert client.post("/api/run", json={"source": "prod"}).status_code == 422


def test_run_status_shape(client):
    body = client.get("/api/run/status").json()
    assert body["state"] == "idle"
```

Run: `python3.11 -m pytest tests/test_snapshot_api.py -v`
Expected: FAIL(路由不存在 → 404)

- [ ] **Step 5.2: 在 `main.py` 追加路由**

在 `from history import compute_history` 之后追加 import:

```python
from typing import Literal

from pydantic import BaseModel

import run_manager
import snapshot_diff
import snapshot_store
```

文件底部追加:

```python
class RunRequest(BaseModel):
    source: Literal["live", "mock"] = "live"


@app.get("/api/snapshots")
def get_snapshots():
    snapshot_store.seed_baseline_if_empty(
        snapshots_dir=snapshot_store.SNAPSHOTS_DIR,
        signal_path=snapshot_store.SIGNAL_PATH,
        regime_path=snapshot_store.REGIME_PATH,
    )
    return JSONResponse(snapshot_store.list_snapshots(snapshot_store.SNAPSHOTS_DIR))


@app.get("/api/changes")
def get_changes(base: str | None = None):
    snapshot_store.seed_baseline_if_empty(
        snapshots_dir=snapshot_store.SNAPSHOTS_DIR,
        signal_path=snapshot_store.SIGNAL_PATH,
        regime_path=snapshot_store.REGIME_PATH,
    )
    entries = snapshot_store.list_snapshots(snapshot_store.SNAPSHOTS_DIR)
    if len(entries) < 2:
        return JSONResponse({
            "insufficient": True, "base": None, "target": None,
            "changes": [], "minor_count": 0, "unchanged_count": 0,
            "notes": ["fewer than two archived snapshots; run the pipeline"],
        })
    target_id = entries[-1]["id"]
    if base is None:
        base_id = entries[-2]["id"]
    else:
        known = {e["id"] for e in entries}
        if base not in known:
            raise HTTPException(status_code=404, detail=f"unknown snapshot id: {base}")
        base_id = base
    diff = snapshot_diff.compute_diff(
        snapshot_store.load_snapshot(base_id, snapshot_store.SNAPSHOTS_DIR),
        snapshot_store.load_snapshot(target_id, snapshot_store.SNAPSHOTS_DIR),
    )
    return JSONResponse({"insufficient": False, **diff})


@app.post("/api/run", status_code=202)
def post_run(request: RunRequest):
    if not run_manager.start_run(request.source):
        raise HTTPException(status_code=409, detail="a run is already in progress")
    return {"started": True, "source": request.source}


@app.get("/api/run/status")
def get_run_status():
    return JSONResponse(run_manager.get_status())
```

注意:`seed_baseline_if_empty` / `list_snapshots` 显式传模块属性而非用默认参数——这样测试 monkeypatch `snapshot_store.SNAPSHOTS_DIR` 等模块变量即可生效。

- [ ] **Step 5.3: 全量回归**

Run: `python3.11 -m pytest tests/ -q`
Expected: 全 PASS(现有 22 个测试文件 + 新 4 个,零回归)

- [ ] **Step 5.4: Commit**

```bash
git add main.py tests/test_snapshot_api.py
git commit -m "feat: snapshots, changes, and run API routes"
```

- [ ] **Step 5.5: ⏸ USER REVIEW** — 起 dev server,浏览器/curl 演示 4 个新路由真实响应

---

## Task 6: 前端拆分迁移(行为不变、旧主题)

**Files:**
- Create: `static/css/theme.css`、`static/js/i18n.js`、`static/js/api.js`、`static/js/app.js`、`static/js/views/map.js`、`static/js/views/heatmap.js`、`static/js/views/regime.js`
- Modify: `static/index.html`

无自动化测试;完成后浏览器人工验证(Step 6.8 清单)。迁移原则:**函数体逐字搬运**,只做两类机械改写:①闭包共享变量 → `MM.state.*`;②跨文件调用 → `MM.util.*` / `MM.views.*`。

- [ ] **Step 6.1: 抽出 CSS**

`static/index.html` 第 7–501 行 `<style>` 内容原样移入 `static/css/theme.css`;`<head>` 里替换为 `<link rel="stylesheet" href="/static/css/theme.css">`。

- [ ] **Step 6.2: 创建 `static/js/i18n.js`**

```javascript
// Display-layer country names. Data keys stay English everywhere.
window.MM = window.MM || {};
MM.i18n = {
  countryNames: {
    "United States of America": "美国",
    "Canada": "加拿大",
    "China": "中国",
    "Japan": "日本",
    "Brazil": "巴西",
    "Euro Area": "欧元区",
    "Argentina": "阿根廷",
    "Greece": "希腊",
    "Turkey": "土耳其",
  },
  // Fallback to the English key so unknown names degrade readably.
  display(name) { return MM.i18n.countryNames[name] || name; },
};
```

- [ ] **Step 6.3: 创建 `static/js/api.js`**

```javascript
window.MM = window.MM || {};
MM.api = {
  json(url) { return fetch(url).then(r => { if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json(); }); },
  getSignals() { return MM.api.json("/api/signals"); },
  getRegime() { return MM.api.json("/api/regime"); },
  getHistory() { return MM.api.json("/api/history"); },
  getSnapshots() { return MM.api.json("/api/snapshots"); },
  getChanges(base) { return MM.api.json(base ? `/api/changes?base=${encodeURIComponent(base)}` : "/api/changes"); },
  getRunStatus() { return MM.api.json("/api/run/status"); },
  postRun(source) {
    return fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    }).then(r => { if (!r.ok && r.status !== 409) throw new Error(`run: ${r.status}`); return r.json().then(body => ({ status: r.status, body })); });
  },
};
```

- [ ] **Step 6.4: 创建 `static/js/app.js`(共享状态 + util + 路由 + bootstrap)**

骨架如下;`MM.util` 内函数从 `index.html` **原样搬运**(源行号注明),搬运时把 `selectedView/selectedCountry/economies/historyData` 等闭包变量改写为 `MM.state.*`:

```javascript
window.MM = window.MM || {};

MM.state = {
  selectedView: "composite",
  selectedTab: "map",
  selectedCountry: null,   // map/heatmap 选中
  countries: [],           // topojson features
  snapshot: { economies: {} },
  economies: {},
  historyData: {},
  regimeData: null,
  regimeSelected: null,
};

MM.util = {
  // 以下函数从 index.html 原样搬运(行号为 commit b607a0b):
  // escapeHtml (747), formatReason (758), resolveEconomyName (765),
  // signalValue (770), fmt (775), signalVerdict (779), verdictBadgeHtml (786),
  // convictionMeta (792, 常量), convictionBadgeHtml (798), convictionLine (805),
  // viewLabel (828), barStyle (832), renderDriverList (838), citationHref (856),
  // narrativeHtml (863), sparkline (885), historyBlock (901),
  // EURO_AREA_COUNTRIES (700, 常量), assetLabels (714, 常量)
};

MM.setTab = function (tab) {
  // index.html 1070-1085 的 setTab 逻辑搬运至此,追加 briefing 视图行:
  MM.state.selectedTab = tab;
  document.querySelector("#briefing-view")?.toggleAttribute("hidden", tab !== "briefing");
  document.querySelector("#map-view").hidden = tab !== "map";
  document.querySelector("#heatmap-view").hidden = tab !== "heatmap";
  document.querySelector("#regime-view").hidden = tab !== "regime";
  document.querySelector("#guide-view").hidden = tab !== "guide";
  document.querySelectorAll("button[data-tab]").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === tab));
  if (tab === "map") MM.views.map.draw();
  if (tab === "heatmap") MM.views.heatmap.draw();
  if (tab === "regime") MM.views.regime.render();
};

window.addEventListener("DOMContentLoaded", () => {
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
    MM.views.map.draw();
    window.addEventListener("resize", () => MM.views.map.draw());
  }).catch(error => {
    document.querySelector("#panel").innerHTML =
      `<div class="eyebrow">Load error</div><div class="meta">${MM.util.escapeHtml(error)}</div>`;
  });

  MM.api.getHistory().then(data => {
    MM.state.historyData = (data && data.history) || {};
    if (MM.state.selectedCountry) MM.views.map.renderPanel(MM.state.selectedCountry);
  }).catch(() => {});
});
```

(bootstrap 相比现状多拉了 `/api/regime` ——Briefing 与 regime tab 共用,原 renderRegime 里的懒加载去掉。)

- [ ] **Step 6.5: 创建三个视图文件**

每个文件的模式(以 map 为例):

```javascript
window.MM = window.MM || {};
MM.views = MM.views || {};
MM.views.map = (function () {
  const svg = d3.select("#map");
  const tooltip = document.querySelector("#tooltip");
  const panel = document.querySelector("#panel");
  const color = d3.scaleLinear().domain([-1, 0, 1])
    .range(["#b94b4b", "#e6e5da", "#3d8b5a"]).clamp(true);

  // 从 index.html 原样搬运:renderPanel (910), draw (969), updateColors (1218)
  // 改写:selectedCountry -> MM.state.selectedCountry 等;historyBlock/sparkline
  // 等工具调用 -> MM.util.*

  return { draw, updateColors, renderPanel, color };
})();
```

- `static/js/views/heatmap.js`:搬 `HEATMAP_ROWS` (1013)、`heatmapValue` (1021)、`highlightHeatmapColumn` (1026)、`drawHeatmap` (1032) → 导出 `{ draw: drawHeatmap, highlight: highlightHeatmapColumn }`;色标复用 `MM.views.map.color`
- `static/js/views/regime.js`:搬 `renderRegime` (1089)、`drawRegimeViews` (1101)、`drawRegimeQuadrant` (1107)、`highlightRegime` (1146)、`renderRegimeCard` (1154)、`renderRegimeTable` (1188) → 导出 `{ render: renderRegime }`;`regimeData/regimeSelected` → `MM.state.*`,数据改从 `MM.state.regimeData` 读(不再自己 fetch)

- [ ] **Step 6.6: 改写 `static/index.html`**

`<body>` 的 HTML 结构不动(guide 内容保持内联);删除整个旧 `<script>` 闭包(第 699 行起),替换为:

```html
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script src="https://cdn.jsdelivr.net/npm/topojson-client@3"></script>
<script src="/static/js/i18n.js"></script>
<script src="/static/js/api.js"></script>
<script src="/static/js/views/map.js"></script>
<script src="/static/js/views/heatmap.js"></script>
<script src="/static/js/views/regime.js"></script>
<script src="/static/js/app.js"></script>
```

(d3/topojson 的 CDN 标签沿用现有引入方式;`window.macroDashboardDebug`(736)搬进 app.js 末尾,内部读 `MM.state`。)

- [ ] **Step 6.7: 起 dev server 验证**

Run: `python3.11 -m uvicorn main:app --port 8000`(或已有 launch 配置)
浏览器验证清单(与重构前逐项对照):
1. Map:地图渲染、hover tooltip、点国家出详情面板、sparkline 出现
2. 资产切换(Composite/FX/…):地图颜色变化、面板刷新
3. Heatmap:网格渲染、点列高亮、与地图选中联动
4. Regime:象限图 + 卡片 + 表格、点行联动
5. Guide:静态内容完整
6. Console 无报错

- [ ] **Step 6.8: Commit**

```bash
git add static/
git commit -m "refactor: split dashboard into zero-build modules under MM namespace"
```

- [ ] **Step 6.9: ⏸ USER REVIEW** — 让用户过一遍四个 tab 确认无行为回归

---

## Task 7: Terminal 深色主题 + 中文国名

**Files:**
- Modify: `static/css/theme.css`(token 重写 + 组件调整)、`static/js/views/map.js`、`static/js/views/heatmap.js`、`static/js/views/regime.js`、`static/js/app.js`、`static/index.html`(header 加主题切换按钮)

- [ ] **Step 7.1: theme.css token 重写**

`:root` 替换为深色 token(旧浅色值整体移入 `[data-theme="light"]` 块;删除原 `@media (prefers-color-scheme: dark)` 块):

```css
:root {
  --bg: #0a0e0a;
  --panel: #10150f;
  --text: #d7ded4;
  --muted: #6c746a;
  --line: #2a2f2a;
  --nodata: #1c231c;
  --water: #0d120d;
  --active: #e8b339;          /* amber 主强调 */
  --active-text: #0a0e0a;
  --shadow: 0 18px 45px rgba(0, 0, 0, 0.45);
  --positive: #4ade80;
  --negative: #f87171;
  --warn: #e8b339;
  --amber: #e8b339;
  --mono: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
}

[data-theme="light"] {
  --bg: #f7f8f5; --panel: #ffffff; --text: #20231f; --muted: #6c746a;
  --line: #d8ddd3; --nodata: #d1d5d0; --water: #e9efe7;
  --active: #20231f; --active-text: #ffffff;
  --shadow: 0 18px 45px rgba(32, 35, 31, 0.12);
  --positive: #3d8b5a; --negative: #b94b4b; --warn: #b07d1a; --amber: #8a6d1f;
}
```

追加 Terminal 排版规则:

```css
h1 { font-size: 18px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--amber); }
.num, td.num, .hm-cell, .legend-labels { font-family: var(--mono); font-variant-numeric: tabular-nums; }
button.active { background: var(--active); color: var(--active-text); border-color: var(--active); }
.legend-scale { background: linear-gradient(90deg, var(--negative), var(--nodata), var(--positive)); }
```

Heatmap 单元格改"深底 + 色彩文字/边框"(替换原 `.hm-cell` 填色策略):

```css
.hm-cell {
  background: var(--panel);
  border: 1px solid var(--cell-color, var(--line));
  color: var(--cell-color, var(--text));
  font-family: var(--mono);
}
```

- [ ] **Step 7.2: JS 侧配色与主题切换**

- `map.js` / `heatmap.js` 色标改从 CSS 变量取端点色,并在主题切换时重建:

```javascript
function makeColorScale() {
  const css = getComputedStyle(document.documentElement);
  return d3.scaleLinear().domain([-1, 0, 1])
    .range([css.getPropertyValue("--negative").trim(),
            css.getPropertyValue("--nodata").trim(),
            css.getPropertyValue("--positive").trim()])
    .clamp(true);
}
```

- `heatmap.js` 的 `drawHeatmap` 中,单元格从 `style="background:${color(v)}"` 改为 `style="--cell-color:${color(v)}"`
- `regime.js`:象限 dashed 分界线 stroke 改 `var(--amber)`(读 computed style 同上)
- `app.js` 加主题切换:header 放 `<button id="theme-toggle">◐</button>`,点击在 `<html>` 上 toggle `data-theme="light"`,存 `localStorage.mmTheme`,启动时恢复;切换后调 `MM.views.map.draw()` + 当前 tab 重绘以刷新色标

- [ ] **Step 7.3: 中文国名落点**

用 `MM.i18n.display()` 包住以下渲染点(数据 key 不动):
- `map.js` renderPanel 标题、tooltip 文本
- `heatmap.js` 列头(经济体名)
- `regime.js` renderRegimeCard 标题、renderRegimeTable 国家列、象限图散点 label
- Map 图例文字 `Bearish/Bullish` 保留英文(术语)

- [ ] **Step 7.4: 浏览器验证**

清单:深色下四个 tab 全部可读;主题切换往返无残留;中文国名在面板/热力图/regime 表出现;heatmap 单元格文字清晰可读;console 无报错。对照 Task 6 清单确认无行为回归。

- [ ] **Step 7.5: Commit**

```bash
git add static/
git commit -m "feat: terminal dark theme with light fallback and Chinese country names"
```

- [ ] **Step 7.6: ⏸ USER REVIEW** — 用户过一遍深色/浅色两套外观

---

## Task 8: Briefing 三栏视图

**Files:**
- Modify: `static/index.html`(tab 按钮 + DOM 骨架)、`static/css/theme.css`(briefing 样式)、`static/js/app.js`(默认 tab 改 briefing)
- Create: `static/js/views/briefing.js`

- [ ] **Step 8.1: index.html 骨架**

header 的 view-tabs 里 Map 前插入 `<button class="active" data-tab="briefing">Briefing</button>`(Map 按钮去掉 `class="active"`);`<main>` 里 `#map-view` 前插入:

```html
<div id="briefing-view">
  <div class="briefing-bar">
    <span id="briefing-meta" class="mono"></span>
    <span class="briefing-bar-right">
      <label class="mock-toggle"><input type="checkbox" id="run-mock"> mock</label>
      <button id="run-btn">▶ RUN LIVE</button>
    </span>
  </div>
  <div class="briefing-grid">
    <aside id="briefing-rail"></aside>
    <section id="briefing-center">
      <div class="section-title">CHANGES</div>
      <div id="briefing-changes"></div>
      <div class="section-title">OPPORTUNITY BOARD</div>
      <div id="briefing-opps"></div>
    </section>
    <aside id="briefing-inspector"></aside>
  </div>
</div>
```

- [ ] **Step 8.2: theme.css 追加 briefing 样式**

```css
#briefing-view { min-height: calc(100vh - 125px); }
.briefing-bar {
  display: flex; justify-content: space-between; align-items: center;
  border: 1px solid var(--line); border-radius: 8px; background: var(--panel);
  padding: 10px 14px; margin-bottom: 12px;
  font-family: var(--mono); font-size: 12px; color: var(--muted);
}
.briefing-bar-right { display: flex; gap: 10px; align-items: center; }
#run-btn { border-color: var(--amber); color: var(--amber); font-family: var(--mono); }
#run-btn[data-state="running"] { opacity: 0.75; cursor: wait; }
#run-btn[data-state="failed"] { border-color: var(--negative); color: var(--negative); }
.briefing-grid {
  display: grid; grid-template-columns: 170px 1.4fr 1.2fr; gap: 12px; align-items: start;
}
#briefing-rail, #briefing-center, #briefing-inspector {
  border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 12px;
}
.rail-row {
  display: flex; align-items: center; gap: 8px; padding: 6px 8px;
  border-radius: 6px; cursor: pointer; font-size: 13px;
}
.rail-row.selected, .change-row.selected, .opp-row.selected { background: rgba(232,179,57,0.12); }
.rail-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.rail-cov { margin-left: auto; font-family: var(--mono); font-size: 10px; color: var(--muted); }
.change-row, .opp-row {
  display: flex; gap: 8px; align-items: baseline; padding: 7px 8px;
  border-radius: 6px; cursor: pointer; font-size: 12.5px; line-height: 1.45;
}
.change-row .num, .opp-row .num { margin-left: auto; font-family: var(--mono); }
.change-l1 { border-left: 3px solid var(--positive); font-weight: 650; }
.change-l1.neg { border-left-color: var(--negative); }
.change-l2, .change-l3 { border-left: 3px solid var(--amber); }
.change-l4 { border-left: 3px solid var(--line); color: var(--muted); }
.minor-fold { color: var(--muted); font-size: 12px; cursor: pointer; padding: 6px 8px; }
.inspector-title { font-size: 11px; letter-spacing: 0.1em; color: var(--amber); margin-bottom: 8px; }
.inspector-kv { display: flex; justify-content: space-between; font-size: 12.5px; padding: 3px 0; }
.inspector-kv .num { font-family: var(--mono); }
.empty-state { color: var(--muted); font-size: 13px; padding: 18px; text-align: center; }
@media (max-width: 1000px) { .briefing-grid { grid-template-columns: 1fr; } }
```

- [ ] **Step 8.3: 实现 `static/js/views/briefing.js`**

```javascript
window.MM = window.MM || {};
MM.views = MM.views || {};
MM.views.briefing = (function () {
  const state = {
    changes: null,        // /api/changes 响应
    railFilter: null,     // 选中国家(英文 key)或 null
    inspector: null,      // {mode: "change"|"opp"|"country", payload}
    minorOpen: false,
  };

  const LEVEL_ICON = { 1: "▲", 2: "◆", 3: "◆", 4: "·" };

  function levelClass(change) {
    if (change.level === 1) {
      const negative = change.kind === "verdict_flip"
        ? String(change.to).match(/Deteriorating|Unconfirmed/)
        : (change.to < change.from);
      return negative ? "change-l1 neg" : "change-l1";
    }
    return `change-l${change.level}`;
  }

  // 展示行文案:优先用结构化字段拼中文国名,headline 兜底
  function changeText(c) {
    const name = MM.i18n.display(c.country);
    const T = {
      verdict_flip: () => `${name} verdict ${c.from} → ${c.to}`,
      direction_flip: () => `${name} ${c.field} 方向翻转 ${fmt(c.from)} → ${fmt(c.to)}`,
      opp_rank_move: () => `${name} 机会榜 ${c.from} → ${c.to}`,
      asset_rank_move: () => `${name} ${c.field} 排名 ${c.from} → ${c.to}`,
      signal_drift: () => `${name} ${c.field} ${fmt(c.from)} → ${fmt(c.to)}`,
      regime_drift: () => `${name} ${c.field} ${fmt(c.from)} → ${fmt(c.to)}`,
      evidence_change: () => `${name} ${c.field} evidence 更新`,
      provenance_flip: () => `${name} ${c.field} 数据源翻转`,
      coverage_change: () => `${name} 覆盖范围变化`,
    };
    return (T[c.kind] || (() => c.headline))();
  }

  function fmt(v) { return typeof v === "number" ? (v > 0 ? "+" : "") + v.toFixed(2) : v; }

  function unionCountries() {
    const signal = Object.keys(MM.state.economies || {});
    const regime = ((MM.state.regimeData || {}).countries || []).map(c => c.country);
    return [...new Set([...signal, ...regime])].sort();
  }

  function coverage(name) {
    const s = (MM.state.economies || {})[name] ? "S" : "";
    const r = ((MM.state.regimeData || {}).countries || []).some(c => c.country === name) ? "R" : "";
    return s + r;
  }

  function countryLevel(name) {
    const rows = ((state.changes || {}).changes || []).filter(c => c.country === name);
    return rows.length ? Math.min(...rows.map(c => c.level)) : null;
  }

  function opportunityRows() {
    // 与 snapshot_diff.opportunity_ranking 同一条规则:gap 降序、confirmation 破并列
    return (((MM.state.regimeData || {}).countries) || [])
      .slice()
      .sort((a, b) => (b.narrative_gap - a.narrative_gap) ||
                      (b.confirmation_score - a.confirmation_score));
  }

  function render() {
    renderRail(); renderChanges(); renderOpps(); renderInspector();
  }

  function renderRail() {
    const el = document.querySelector("#briefing-rail");
    el.innerHTML = unionCountries().map(name => {
      const level = countryLevel(name);
      const dot = level === 1 ? "var(--positive)" : level ? "var(--amber)" : "var(--line)";
      const sel = state.railFilter === name ? " selected" : "";
      return `<div class="rail-row${sel}" data-country="${MM.util.escapeHtml(name)}">
        <span class="rail-dot" style="background:${dot}"></span>
        <span>${MM.i18n.display(name)}</span>
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
      return `<div class="change-row ${levelClass(c)}${sel}" data-idx="${i}">
        <span>${LEVEL_ICON[c.level]}</span><span>${MM.util.escapeHtml(changeText(c))}</span></div>`;
    });
    const notes = (data.notes || []).map(n =>
      `<div class="empty-state">⚠ ${MM.util.escapeHtml(n)}</div>`);
    const minor = data.minor_count
      ? `<div class="minor-fold">▸ ${data.minor_count} 项微小变动 · ${data.unchanged_count} 未变</div>`
      : `<div class="minor-fold">${data.unchanged_count} 未变</div>`;
    el.innerHTML = notes.join("") + (items.join("") || `<div class="empty-state">本期无显著变化</div>`) + minor;
    el.querySelectorAll(".change-row").forEach(row => row.addEventListener("click", () => {
      state.inspector = { mode: "change", payload: rows[Number(row.dataset.idx)] };
      render();
    }));
  }

  function renderOpps() {
    const el = document.querySelector("#briefing-opps");
    el.innerHTML = opportunityRows().map((c, i) => {
      const warn = c.verdict === "Unconfirmed" ? " ⚠" : "";
      const sel = state.inspector?.mode === "opp" && state.inspector.payload === c.country ? " selected" : "";
      return `<div class="opp-row${sel}" data-country="${MM.util.escapeHtml(c.country)}">
        <span class="num">${i + 1}</span>
        <span>${MM.i18n.display(c.country)} <span style="color:var(--muted)">${c.verdict}${warn}</span></span>
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
    const el = document.querySelector("#briefing-inspector");
    const ins = state.inspector;
    if (!ins) { el.innerHTML = `<div class="empty-state">点击变化 / 机会 / 国家查看详情</div>`; return; }
    if (ins.mode === "change") {
      const c = ins.payload;
      let html = `<div class="inspector-title">WHY — ${MM.i18n.display(c.country)}</div>`;
      html += kv("kind", c.kind) + (c.field ? kv("field", c.field) : "");
      if (c.from !== undefined) html += kv("from → to", `${fmt(c.from)} → ${fmt(c.to)}`);
      Object.entries(c.detail || {}).forEach(([k, v]) => {
        html += kv(k, v && v.from !== undefined ? `${fmt(v.from)} → ${fmt(v.to)}` : MM.util.escapeHtml(JSON.stringify(v)));
      });
      el.innerHTML = html;
    } else if (ins.mode === "opp") {
      const c = opportunityRows().find(x => x.country === ins.payload);
      if (!c) { el.innerHTML = ""; return; }
      let html = `<div class="inspector-title">${MM.i18n.display(c.country)} — ${c.verdict}</div>`;
      html += kv("regime_score", fmt(c.regime_score)) + kv("narrative_gap", fmt(c.narrative_gap)) +
              kv("confirmation", fmt(c.confirmation_score));
      const channels = c.cross_asset_confirmation || {};
      html += `<div class="inspector-title" style="margin-top:10px">CROSS-ASSET</div>` +
        Object.entries(channels).map(([k, v]) =>
          kv(k, typeof v === "number" ? fmt(v) : (v ? "✓" : "✗"))).join("");
      (c.best_expressions || []).slice(0, 3).forEach(e => {
        html += `<div class="inspector-kv"><span>→</span><span>${MM.util.escapeHtml(
          typeof e === "string" ? e : JSON.stringify(e))}</span></div>`;
      });
      el.innerHTML = html;
    } else {
      const name = ins.payload;
      const econ = (MM.state.economies || {})[name];
      const reg = (((MM.state.regimeData || {}).countries) || []).find(c => c.country === name);
      let html = `<div class="inspector-title">${MM.i18n.display(name)}</div>`;
      if (econ) {
        ["fx", "rates", "equity", "real_estate"].forEach(a => {
          html += kv(a, fmt(econ.signals?.[a]?.final));
        });
        html += kv("composite", fmt(econ.composite?.final));
      }
      if (reg) html += kv("verdict", reg.verdict) + kv("narrative_gap", fmt(reg.narrative_gap));
      el.innerHTML = html;
    }
  }

  function load() {
    Promise.all([MM.api.getChanges(), MM.api.getSnapshots()]).then(([changes, snaps]) => {
      state.changes = changes;
      const meta = document.querySelector("#briefing-meta");
      if (changes.insufficient || !changes.base) {
        meta.textContent = `SNAPSHOTS: ${snaps.length}`;
      } else {
        const days = Math.round(
          (new Date(changes.target.as_of) - new Date(changes.base.as_of)) / 86400000);
        meta.textContent = `vs ${changes.base.id} · ${days}d · L1 ${
          changes.changes.filter(c => c.level === 1).length}`;
      }
      render();
    }).catch(err => {
      document.querySelector("#briefing-changes").innerHTML =
        `<div class="empty-state">加载失败:${MM.util.escapeHtml(err)}</div>`;
    });
  }

  return { load, render, state };
})();
```

- [ ] **Step 8.4: app.js 接线**

- `MM.state.selectedTab` 初值改 `"briefing"`;bootstrap 数据就绪后调 `MM.setTab("briefing")` + `MM.views.briefing.load()`
- `MM.setTab` 中加:`if (tab === "briefing") MM.views.briefing.load();`
- index.html 的 script 列表在 regime.js 后加 `<script src="/static/js/views/briefing.js"></script>`

- [ ] **Step 8.5: 浏览器验证**

清单:落地即 Briefing;9 国轨(中文名 + S/R/SR 徽章);变化流有内容(基线 vs 手跑一次 mock 的两份归档);点国家轨过滤 + 出全景详情;点变化行出 WHY;机会榜 6 行、点行出三输入 + 跨资产;其余 4 个 tab 不回归;console 无报错。

- [ ] **Step 8.6: Commit**

```bash
git add static/
git commit -m "feat: three-pane briefing view with changes feed and opportunity board"
```

- [ ] **Step 8.7: ⏸ USER REVIEW** — 用户完整体验 Briefing 首屏

---

## Task 9: Run 按钮接线 + README

**Files:**
- Modify: `static/js/views/briefing.js`(runController)、`README.md`

- [ ] **Step 9.1: briefing.js 追加 runController**

在 IIFE 内追加,`load()` 末尾调 `runController.init()`(幂等,用标志位只绑一次):

```javascript
  const runController = {
    timer: null, bound: false,
    init() {
      if (this.bound) return;
      this.bound = true;
      document.querySelector("#run-btn").addEventListener("click", () => this.start());
      this.poll(); // 页面打开时若已有 run 在跑,立即接上状态
    },
    start() {
      const source = document.querySelector("#run-mock").checked ? "mock" : "live";
      MM.api.postRun(source).then(({ status }) => {
        if (status === 409) this.setButton("running", "已在运行");
        this.poll();
      });
    },
    poll() {
      clearInterval(this.timer);
      this.timer = setInterval(() => MM.api.getRunStatus().then(s => this.apply(s)), 1500);
      MM.api.getRunStatus().then(s => this.apply(s));
    },
    apply(s) {
      if (s.state === "running") {
        const p = s.phase ? `${s.phase.index + 1}/${s.phase.total} ${s.phase.name}` : "…";
        this.setButton("running", `⏳ ${p}`);
      } else {
        clearInterval(this.timer);
        if (s.state === "succeeded" && this.wasRunning) {
          this.setButton("idle", "▶ RUN LIVE");
          // 重新拉全量数据,刷新 Briefing 与其他视图
          Promise.all([MM.api.getSignals(), MM.api.getRegime()]).then(([sig, reg]) => {
            MM.state.snapshot = sig; MM.state.economies = sig.economies || {};
            MM.state.regimeData = reg;
            load();
          });
        } else if (s.state === "failed") {
          this.setButton("failed", "✗ FAILED(点按钮看日志)");
          console.error("run failed:", s.error, s.log_tail);
        } else {
          this.setButton("idle", "▶ RUN LIVE");
        }
      }
      this.wasRunning = s.state === "running";
      const meta = document.querySelector("#briefing-meta");
      if (s.state === "running" && s.log_tail.length) {
        meta.textContent = s.log_tail[s.log_tail.length - 1].slice(0, 80);
      }
    },
    setButton(stateName, label) {
      const btn = document.querySelector("#run-btn");
      btn.dataset.state = stateName;
      btn.textContent = label;
      btn.disabled = stateName === "running";
    },
  };
```

failed 态点按钮的行为:`start()` 开头加 `if (btn.dataset.state === "failed") { alert(最近 log_tail.join("\n")); }` 后重置为 idle(简单粗暴够用,v1 不做浮层)。

- [ ] **Step 9.2: 浏览器端到端验证**

1. 点 mock 勾选 + ▶ RUN:按钮进入 `⏳ 1/4 signal_pipeline` → … → 成功后变化流刷新,`data/snapshots/` 多一个目录
2. 运行中再点按钮:无第二次启动(409 路径)
3. 人为制造失败(临时把 `PHASE_COMMANDS["regime_engine"]` 指向不存在的脚本):按钮到 `✗ FAILED`,点击可见日志
4. 恢复后再跑一轮成功

- [ ] **Step 9.3: README 更新**

Architecture 列表追加三行(`snapshot_store.py` / `snapshot_diff.py` / `run_manager.py` 一句话职责);Run 章节追加:新 4 路由说明、`data/snapshots/` 归档、**必须单 worker 跑 uvicorn**(run_manager 内存状态)、GH Actions 可选启用指引(指向 spec 附录)。

- [ ] **Step 9.4: 全量回归 + Commit**

Run: `python3.11 -m pytest tests/ -q` → 全 PASS

```bash
git add static/ README.md
git commit -m "feat: wire run button with live status polling; document new pipeline surface"
```

- [ ] **Step 9.5: ⏸ USER REVIEW** — 用户亲手点一次 RUN(mock 或 live)看全流程

---

## Task 10(P2,可选): 键盘最小集

**Files:**
- Modify: `static/js/app.js`

- [ ] **Step 10.1: keymap 实现**

app.js 追加:

```javascript
const KEY_HELP = [
  ["j / k", "国家轨上下移动 (Briefing)"],
  ["1-5", "资产切换 Composite/FX/Rates/Equity/RE"],
  ["?", "开关此帮助"],
];

function moveRail(delta) {
  if (MM.state.selectedTab !== "briefing") return;
  const rows = [...document.querySelectorAll("#briefing-rail .rail-row")];
  if (!rows.length) return;
  const names = rows.map(r => r.dataset.country);
  const current = names.indexOf(MM.views.briefing.state.railFilter);
  const next = Math.max(0, Math.min(names.length - 1, current + delta));
  rows[next].click();
}

const VIEW_KEYS = { "1": "composite", "2": "fx", "3": "rates", "4": "equity", "5": "real_estate" };

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea") || e.metaKey || e.ctrlKey) return;
  if (e.key === "j") moveRail(1);
  else if (e.key === "k") moveRail(-1);
  else if (VIEW_KEYS[e.key]) document.querySelector(`button[data-view="${VIEW_KEYS[e.key]}"]`)?.click();
  else if (e.key === "?") toggleHelp();
});

function toggleHelp() {
  let el = document.querySelector("#key-help");
  if (el) { el.remove(); return; }
  el = document.createElement("div");
  el.id = "key-help";
  el.style.cssText = "position:fixed;bottom:20px;right:20px;background:var(--panel);border:1px solid var(--amber);border-radius:8px;padding:14px 18px;z-index:99;font-size:12.5px;";
  el.innerHTML = KEY_HELP.map(([k, d]) =>
    `<div class="inspector-kv"><span class="num" style="color:var(--amber)">${k}</span><span style="margin-left:14px">${d}</span></div>`).join("");
  document.body.appendChild(el);
}
```

- [ ] **Step 10.2: 浏览器验证 + Commit**

验证:Briefing 下 j/k 走轨、数字键切资产、? 出浮层、输入框内按键不劫持。

```bash
git add static/js/app.js
git commit -m "feat: minimal keyboard navigation (j/k, 1-5, ?)"
```

- [ ] **Step 10.3: ⏸ USER REVIEW** — 最终验收

---

## Self-Review 记录

- **Spec 覆盖:** §1 拆分/路由(T5-T6)、§2 diff(T2-T3)、§3 run(T4-T5、T9)、§4 Briefing 三栏+机会榜+详情(T8)、§4 Run UX(T9)、§5 主题+中文名(T7)、§6 测试(各 task 内嵌)、P2(T10)、GH Actions(spec 附录,按需启用,无独立 task)✓
- **quality 摘要:** spec §4 header 的 quality chip 由 `--quality-out` → meta.json 提供(T4);Briefing header v1 显示 log tail/L1 计数,quality chip 在 meta 可用后由 T8 的 `#briefing-meta` 展示 snapshots meta——若用户想要更显眼的位置,review 时提出
- **类型一致性:** `compute_diff` 输入 `{"id","signal","regime"}` = `load_snapshot` 输出(T1↔T2);`opportunity_ranking` 排序键与 briefing.js `opportunityRows` 一致(T3↔T8);`PHASE_COMMANDS`/`_archive_and_diff` 名称在 T4 实现与测试一致 ✓
- **占位符扫描:** 无 TBD;T6 的"原样搬运"均带源行号与改写规则,属机械操作而非留白 ✓
